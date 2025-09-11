# -*- coding: utf-8 -*-
"""
きら星ポータル 非公式Atomフィード（邪神ちゃんドロップキック）
- タイトルページから /pt/... の各話リンクを抽出して最新N件をAtom化
- 章番号で昇順（古→新）整列、各エントリ updated は上から+1分ずつ
"""

import os
import re
import time
from datetime import datetime, timedelta
from typing import List, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import tz
from feedgen.feed import FeedGenerator
from requests.adapters import HTTPAdapter, Retry

# ======= 設定 =======

BASE = "https://kirapo.jp/"

TARGET_TITLE_PAGES = [
    "https://kirapo.jp/meteor/titles/jyashin",  # 邪神ちゃんドロップキック
]

VIEW_PREFIX_MAP = {
    "https://kirapo.jp/meteor/titles/jyashin": "/pt/meteor/jyashin/",
}

MAX_ITEMS = 5
DELAY_TITLE_PAGE = 0.3
DELAY_AFTER_BUILD = 0.1

HEADERS = {"User-Agent": "KirapoRSS/Atom/1.0 (+https://github.com/)"}
TIMEOUT = 20

PUBLIC_DIR = "public"
ATOM_NAME = "atom.xml"

CHANNEL_TITLE = "邪神ちゃんドロップキック"
CHANNEL_SUBTITLE = "邪神ちゃんドロップキック 最新話（非公式Atom） | 出典: https://kirapo.jp/"

# =====================

DATE_JA_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
CHAPTER_LINE_RE = re.compile(r"^第\s*\d+\s*話[^\n]*")        # 行頭「第◯◯話 …」
CHAPTER_IN_TEXT_RE = re.compile(r"第\s*\d+\s*話[^\n　]*")    # aテキスト内「第◯◯話 …」
CH_NO_RE = re.compile(r"第\s*(\d+)\s*話")                   # 章番号
NOISE_WORDS = ("最新話を読む", "第1話を読む", "読む", "単行本", "特集", "試し読み", "掲載期間")

TZ_JST = tz.gettz("Asia/Tokyo")


def make_session() -> requests.Session:
    sess = requests.Session()
    retries = Retry(
        total=4, connect=4, read=4,
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
    m = DATE_JA_RE.search(txt)
    if not m:
        return default_dt
    y, mo, d = map(int, m.groups())
    return datetime(y, mo, d, 0, 0, 0, tzinfo=TZ_JST)


def pick_chapter_lines(txt: str) -> List[str]:
    lines = []
    for line in txt.splitlines():
        if CHAPTER_LINE_RE.match(line):
            lines.append(line.strip())
    return lines


def normalize_chapter_title(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"(掲載期間[:：].*)$", "", s)
    s = re.sub(r"(最新話を読む|第1話を読む|読む)\s*$", "", s)
    m = re.search(r"(第\s*\d+\s*話\s*[^｜|/／\-\–—\[\(（＜<　]*?)\s*$", s)
    if m:
        s = m.group(1)
    return s.strip()


def extract_items_from_title_page(
    soup: BeautifulSoup,
    title_url: str,
    view_prefix: str,
    now: datetime,
) -> List[Tuple[datetime, str, str, str]]:
    txt = soup.get_text("\n", strip=True)
    base_dt = parse_site_date_anywhere(txt, default_dt=now)
    chapter_lines = pick_chapter_lines(txt)

    anchors = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if view_prefix in href:
            full = urljoin(BASE, href)
            if full not in seen:
                seen.add(full)
                anchors.append((a, full))

    if not anchors:
        a_latest = soup.find("a", string=re.compile("最新話を読む"))
        if a_latest and a_latest.get("href"):
            full = urljoin(BASE, a_latest["href"])
            anchors.append((a_latest, full))

    if not anchors:
        for a in soup.find_all("a", href=True):
            if CHAPTER_IN_TEXT_RE.search(a.get_text(strip=True) or ""):
                full = urljoin(BASE, a["href"])
                if full not in seen:
                    seen.add(full)
                    anchors.append((a, full))

    if not anchors:
        return []

    items = []
    seen_titles = set()
    count = min(MAX_ITEMS, len(anchors))
    line_idx = 0

    for i in range(count):
        a, link = anchors[i]
        raw_text = (a.get_text(strip=True) or "").strip()

        m = CHAPTER_IN_TEXT_RE.search(raw_text)
        chapter_title = m.group(0).strip() if m else None

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

        chapter_title = normalize_chapter_title(chapter_title)
        if not chapter_title:
            continue
        if "第1話" in chapter_title:
            continue
        if chapter_title in seen_titles:
            continue
        seen_titles.add(chapter_title)

        body_html = f'<p><a href="{link}">{chapter_title}</a></p>'
        items.append((base_dt, chapter_title, link, body_html))

    return items


def build_feed(items: List[Tuple[datetime, str, str, str]], now: datetime) -> None:
    os.makedirs(PUBLIC_DIR, exist_ok=True)

    fa = FeedGenerator()
    fa.id("https://tatitle.github.io/kirapo-rss/")  # 固定ID
    fa.title(CHANNEL_TITLE)
    fa.link(href="https://tatitle.github.io/kirapo-rss/atom.xml", rel="self")
    fa.link(href=BASE, rel="alternate")
    fa.subtitle(CHANNEL_SUBTITLE)
    fa.language("ja")
    fa.updated(now.astimezone(tz.gettz("UTC")))

    # Atom entry を古→新の順で +1分ずつ updated を進める
    for idx, (dt, item_title, link, body_html) in enumerate(items):
        ent = fa.add_entry()
        ent.id(link)
        ent.title(item_title)
        ent.link(href=link)
        ent.updated((dt + timedelta(minutes=idx)).astimezone(tz.gettz("UTC")))
        ent.content(body_html + '<br>出典: <a href="https://kirapo.jp/">きら星ポータル</a>', type='html')

    fa.atom_file(os.path.join(PUBLIC_DIR, ATOM_NAME))

    # ルート -> atom.xml に即リダイレクト
    index_html = f"""<!doctype html>
<meta charset="utf-8">
<title>{CHANNEL_TITLE}</title>
<link rel="alternate" type="application/atom+xml" title="{CHANNEL_TITLE}" href="./{ATOM_NAME}">
<meta http-equiv="refresh" content="0; url=./{ATOM_NAME}">
<p>自動的に <a href="./{ATOM_NAME}">{ATOM_NAME}</a> へ移動します。</p>
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
                m = re.search(r"https://kirapo\.jp/([^/]+)/titles/([^/]+)", title_url)
                if m:
                    label, slug = m.group(1), m.group(2)
                    view_prefix = f"/pt/{label}/{slug}/"
                else:
                    print(f"[warn] VIEW_PREFIX not found for {title_url}")
                    continue

            items = extract_items_from_title_page(soup, title_url, view_prefix, now)
            all_items.extend(items)
            time.sleep(DELAY_TITLE_PAGE)
        except Exception as e:
            print(f"[warn] skip {title_url}: {e}")

    # ---- 並び順：章番号で昇順（古→新） ----
    def _ch_no_or_date(item):
        dt, title, link, body = item
        m = CH_NO_RE.search(title)
        if m:
            return (int(m.group(1)), dt)
        return (10**9, dt)  # 番号なしは最後

    all_items.sort(key=_ch_no_or_date)

    build_feed(all_items, now)
    time.sleep(DELAY_AFTER_BUILD)
    print(f"OK: {len(all_items)} item(s) -> {os.path.join(PUBLIC_DIR, ATOM_NAME)} (+ index.html)")


if __name__ == "__main__":
    main()
