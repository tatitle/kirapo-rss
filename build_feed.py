# -*- coding: utf-8 -*-
"""
きら星ポータル 非公式RSS（邪神ちゃんドロップキック）
- 作品ページ（タイトルページ）からビューア直リンク /pt/... を抽出し、最新N件をRSS化
- GitHub Actions / ローカル双方で動作
"""

import os
import re
import time
from datetime import datetime
from typing import List, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import tz
from email.utils import format_datetime
from feedgen.feed import FeedGenerator
from requests.adapters import HTTPAdapter, Retry

# ======= 設定（必要ならここだけ調整）====================================

BASE = "https://kirapo.jp/"

# 対象タイトル（邪神ちゃんドロップキック）
TARGET_TITLE_PAGES = [
    "https://kirapo.jp/meteor/titles/jyashin",
]

# ビューア直リンクの接頭辞（作品ごとに異なる）
# 例: タイトルURLのスラッグから /pt/<label>/<slug>/ を導出できますが、
# 明示指定しておく方が確実です。
VIEW_PREFIX_MAP = {
    "https://kirapo.jp/meteor/titles/jyashin": "/pt/meteor/jyashin/",
}

# 取得する最大話数（新しい順に上から）
MAX_ITEMS = 5

# 低負荷のための待機（秒）: 作品ページ取得ごと / アイテム抽出後
DELAY_TITLE_PAGE = 0.3
DELAY_AFTER_BUILD = 0.1

# User-Agent とタイムアウト
HEADERS = {"User-Agent": "KirapoRSS/1.3 (GitHub-Actions; +https://github.com/)"}
TIMEOUT = 20

# 出力先（GitHub Pages は public/ 以下が配信ルート）
PUBLIC_DIR = "public"
FEED_NAME = "feed.xml"

# チャンネル表示
CHANNEL_TITLE = "邪神ちゃんドロップキック"
CHANNEL_DESC_HTML = (
    '邪神ちゃんドロップキック 最新話（非公式RSS）｜出典: '
    '<a href="https://kirapo.jp/">きら星ポータル</a>'
)

# ======================================================================

DATE_JA_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
CHAPTER_LINE_RE = re.compile(r"^第\s*\d+\s*話[^\n]*")  # 行頭「第◯◯話 …」
CHAPTER_IN_TEXT_RE = re.compile(r"第\s*\d+\s*話[^\n　]*")  # aテキスト内から章名だけ抽出

# 既存の CHAPTER_IN_TEXT_RE の下あたりに追加
NOISE_WORDS = ("最新話を読む", "第1話を読む", "読む", "単行本", "特集", "試し読み", "掲載期間")

def normalize_chapter_title(raw: str) -> str:
    s = raw.strip()

    # 「掲載期間：…」「… 読む」など以降を落とす
    s = re.sub(r"(掲載期間[:：].*)$", "", s)
    s = re.sub(r"(最新話を読む|第1話を読む|読む)\s*$", "", s)

    # 「第◯話 …」だけを抜き出し（区切り記号で打ち切り）
    m = re.search(r"(第\s*\d+\s*話\s*[^｜|/／\-\–—\[\(（＜<　]*?)\s*$", s)
    if m:
        s = m.group(1)

    return s.strip()


TZ_JST = tz.gettz("Asia/Tokyo")


def make_session() -> requests.Session:
    """再試行つきセッション（指数バックオフ）"""
    sess = requests.Session()
    retries = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    return sess


def get_soup(session: requests.Session, url: str) -> BeautifulSoup:
    r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def parse_site_date_anywhere(txt: str, default_dt: datetime) -> datetime:
    """
    ページ内から「YYYY年M月D日」を拾って pubDate に利用。
    章単位の日付が無い場合が多いので、ページ先頭に近い最初の一致を採用。
    見つからなければ default_dt（実行時刻）を返す。
    """
    m = DATE_JA_RE.search(txt)
    if not m:
        return default_dt
    y, mo, d = map(int, m.groups())
    return datetime(y, mo, d, 0, 0, 0, tzinfo=TZ_JST)


def extract_work_title(soup: BeautifulSoup, fallback: str = "作品") -> str:
    h = soup.find(["h1", "h2"])
    if h and h.get_text(strip=True):
        return h.get_text(strip=True)
    return fallback


def pick_chapter_lines(txt: str) -> List[str]:
    """
    改行を保持したテキストから、行頭「第◯◯話 …」の行だけ抽出。
    上から並んだ順序＝新しい順であることが多い。
    """
    lines = []
    for line in txt.splitlines():
        if CHAPTER_LINE_RE.match(line):
            lines.append(line.strip())
    return lines


def extract_items_from_title_page(
    soup: BeautifulSoup,
    title_url: str,
    view_prefix: str,
    now: datetime,
) -> List[Tuple[datetime, str, str, str]]:
    """
    タイトルページから、ビューア直リンクと章タイトルを最大MAX_ITEMS件取得して返す。
    - view_prefix への「部分一致」も許可
    - 見つからない場合は『最新話を読む』や章名テキストからフォールバック
    """
    # 改行保持テキスト
    txt = soup.get_text("\n", strip=True)

    # ページ記載の日付（なければ now）
    base_dt = parse_site_date_anywhere(txt, default_dt=now)

    # 章タイトル候補（行頭「第…話 …」）
    chapter_lines = pick_chapter_lines(txt)

    # --- 1) ビューア直リンクを収集（部分一致OK） ---
    anchors = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if view_prefix in href:                   # ← startswith ではなく “in”
            full = urljoin(BASE, href)
            if full not in seen:
                seen.add(full)
                anchors.append((a, full))

    # --- 2) 見つからなければ『最新話を読む』を拾う ---
    if not anchors:
        a_latest = soup.find("a", string=re.compile("最新話を読む"))
        if a_latest and a_latest.get("href"):
            full = urljoin(BASE, a_latest["href"])
            anchors.append((a_latest, full))

    # --- 3) それでも無ければ、章名テキストにリンクが付いているaを拾う ---
    if not anchors:
        for a in soup.find_all("a", href=True):
            if CHAPTER_IN_TEXT_RE.search(a.get_text(strip=True) or ""):
                full = urljoin(BASE, a["href"])
                if full not in seen:
                    seen.add(full)
                    anchors.append((a, full))

    # アンカーが1つも無ければ空（上位で警告してスキップ）
    if not anchors:
        return []

    # 上から MAX_ITEMS 件だけアイテム化
    items = []
    seen_titles = set()
    count = min(MAX_ITEMS, len(anchors))
    line_idx = 0

    for i in range(count):
        a, link = anchors[i]
        raw_text = (a.get_text(strip=True) or "").strip()

        # 1) aテキストから章名候補
        m = CHAPTER_IN_TEXT_RE.search(raw_text)
        chapter_title = m.group(0).strip() if m else None

        # 2) 無ければ近傍→行リスト
        if not chapter_title:
            parent = a.parent
            hops = 0
            while parent is not None and hops < 3 and not chapter_title:
                t = parent.get_text(" ", strip=True)
                m2 = CHAPTER_IN_TEXT_RE.search(t or "")
                if m2:
                    chapter_title = m2.group(0).strip()
                    break
                parent = parent.parent
                hops += 1
        if not chapter_title and line_idx < len(chapter_lines):
            chapter_title = chapter_lines[line_idx]
            line_idx += 1

        if not chapter_title:
            continue

        # 3) 正規化＋ノイズ除外
        chapter_title = normalize_chapter_title(chapter_title)
        if not chapter_title:
            continue
        if "第1話" in chapter_title:
            # 「第1話を読む」などはノイズとして除外
            continue
        if any(w in raw_text for w in NOISE_WORDS) and "第" not in chapter_title:
            continue

        # 重複除外
        if chapter_title in seen_titles:
            continue
        seen_titles.add(chapter_title)

        dt = base_dt
        body_html = f'<p>{dt.strftime("%Y-%m-%d")} <a href="{link}">{chapter_title}</a></p>'
        items.append((dt, chapter_title, link, body_html))

    return items


def build_feed(items, now):
    os.makedirs(PUBLIC_DIR, exist_ok=True)

    # --- 通常のRSS (feed.xml) ---
    fg = FeedGenerator()
    fg.title(CHANNEL_TITLE)
    fg.link(href=BASE, rel="alternate")
    fg.description(CHANNEL_DESC_HTML)  # HTML可
    fg.language("ja")
    fg.pubDate(format_datetime(now))
    fg.lastBuildDate(format_datetime(now.astimezone(tz.gettz("UTC"))))

    for dt, item_title, link, body_html in items:
        body_html_with_source = (
            body_html + '<br>出典: <a href="https://kirapo.jp/">きら星ポータル</a>'
        )
        fe = fg.add_entry()
        fe.id(link)
        fe.guid(link, permalink=True)
        fe.title(item_title)
        fe.link(href=link)
        fe.description(body_html_with_source)
        fe.content(content=body_html_with_source, type="CDATA")
        fe.pubDate(format_datetime(dt))

    fg.rss_file(os.path.join(PUBLIC_DIR, FEED_NAME), pretty=True)

    # --- 互換RSS (compat.xml)：プレーンテキストのみ / content:encoded なし ---
    fg2 = FeedGenerator()
    fg2.title(CHANNEL_TITLE)
    fg2.link(href=BASE, rel="alternate")
    # HTML抜きの説明（テキストのみ）
    fg2.description("邪神ちゃんドロップキック 最新話（非公式RSS） | 出典: https://kirapo.jp/")
    fg2.language("ja")
    fg2.pubDate(format_datetime(now))
    fg2.lastBuildDate(format_datetime(now.astimezone(tz.gettz("UTC"))))

    for dt, item_title, link, body_html in items:
        # テキストのみ（タグ除去の簡易版）
        text_desc = re.sub(r"<[^>]+>", "", body_html) + " / 出典: https://kirapo.jp/"
        fe2 = fg2.add_entry()
        fe2.id(link)
        fe2.guid(link, permalink=True)
        fe2.title(item_title)
        fe2.link(href=link)
        fe2.description(text_desc)
        fe2.pubDate(format_datetime(dt))

    fg2.rss_file(os.path.join(PUBLIC_DIR, "compat.xml"), pretty=True)

    # --- Atom (atom.xml)：Atomの方が安定なクライアント用 ---
    fa = FeedGenerator()
    fa.id("https://tatitle.github.io/kirapo-rss/")  # 固定ID（適当でOK）
    fa.title(CHANNEL_TITLE)
    fa.link(href="https://tatitle.github.io/kirapo-rss/atom.xml", rel="self")
    fa.link(href=BASE, rel="alternate")
    fa.subtitle("邪神ちゃんドロップキック 最新話（非公式RSS） | 出典: https://kirapo.jp/")
    fa.language("ja")
    fa.updated(now.astimezone(tz.gettz("UTC")))

    for dt, item_title, link, body_html in items:
        ent = fa.add_entry()
        ent.id(link)
        ent.title(item_title)
        ent.link(href=link)
        ent.updated(dt.astimezone(tz.gettz("UTC")))
        # AtomはcontentにHTML可（type='html'）
        ent.content(body_html + '<br>出典: <a href="https://kirapo.jp/">きら星ポータル</a>', type='html')

    fa.atom_file(os.path.join(PUBLIC_DIR, "atom.xml"))

    # --- index.html（リンクを3種とも表示） ---
    index_html = f"""<!doctype html>
<meta charset="utf-8">
<title>{CHANNEL_TITLE}</title>
<p>購読リンク：</p>
<ul>
  <li><a href="./{FEED_NAME}">RSS (通常)</a></li>
  <li><a href="./compat.xml">RSS 互換 (プレーンテキスト)</a></li>
  <li><a href="./atom.xml">Atom</a></li>
</ul>
<meta http-equiv="refresh" content="0; url=./{FEED_NAME}">
<p>自動的に <a href="./{FEED_NAME}">{FEED_NAME}</a> へ移動します。</p>
"""
    with open(os.path.join(PUBLIC_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)


def main():
    now = datetime.now(tz=TZ_JST)
    session = make_session()

    all_items: List[Tuple[datetime, str, str, str]] = []

    for title_url in TARGET_TITLE_PAGES:
        try:
            soup = get_soup(session, title_url)
            view_prefix = VIEW_PREFIX_MAP.get(title_url)
            if not view_prefix:
                # 自動推測（titles/<slug> を pt/<label>/<slug>/ へ）
                # 例: https://kirapo.jp/meteor/titles/jyashin なら /pt/meteor/jyashin/
                m = re.search(r"https://kirapo\.jp/([^/]+)/titles/([^/]+)", title_url)
                if m:
                    label, slug = m.group(1), m.group(2)
                    view_prefix = f"/pt/{label}/{slug}/"
                else:
                    # どうしても推測できない場合はスキップ
                    print(f"[warn] VIEW_PREFIX not found for {title_url}")
                    continue

            items = extract_items_from_title_page(soup, title_url, view_prefix, now)
            all_items.extend(items)
            time.sleep(DELAY_TITLE_PAGE)
        except Exception as e:
            print(f"[warn] skip {title_url}: {e}")

    # 新しい順（pubDate）で整列
    all_items.sort(key=lambda x: x[0], reverse=True)

    # 出力
    build_feed(all_items, now)
    time.sleep(DELAY_AFTER_BUILD)
    print(f"OK: {len(all_items)} item(s) -> {os.path.join(PUBLIC_DIR, FEED_NAME)} (+ index.html)")


if __name__ == "__main__":
    main()
