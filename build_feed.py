# -*- coding: utf-8 -*-
"""
きら星ポータル 非公式RSS（邪神ちゃんドロップキック）
- 作品ページ（タイトルページ）からビューア直リンク /pt/... を抽出し、最新N件をRSS化
- 章番号で昇順（古い→新しい）に整列
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
from email.utils import format_datetime
from feedgen.feed import FeedGenerator
from requests.adapters import HTTPAdapter, Retry

# ======= 設定 ====================================

BASE = "https://kirapo.jp/"

TARGET_TITLE_PAGES = [
    "https://kirapo.jp/meteor/titles/jyashin",
]

VIEW_PREFIX_MAP = {
    "https://kirapo.jp/meteor/titles/jyashin": "/pt/meteor/jyashin/",
}

MAX_ITEMS = 5
DELAY_TITLE_PAGE = 0.3
DELAY_AFTER_BUILD = 0.1

HEADERS = {"User-Agent": "KirapoRSS/1.4 (GitHub-Actions; +https://github.com/)"}
TIMEOUT = 20

PUBLIC_DIR = "public"
FEED_NAME = "feed.xml"

CHANNEL_TITLE = "邪神ちゃんドロップキック"
CHANNEL_DESC_HTML = (
    '邪神ちゃんドロップキック 最新話（非公式RSS）｜出典: '
    '<a href="https://kirapo.jp/">きら星ポータル</a>'
)

# =================================================

DATE_JA_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
CHAPTER_LINE_RE = re.compile(r"^第\s*\d+\s*話[^\n]*")
CHAPTER_IN_TEXT_RE = re.compile(r"第\s*\d+\s*話[^\n　]*")
CH_NO_RE = re.compile(r"第\s*(\d+)\s*話")  # 章番号抽出

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

        # ノイズ語を多く含む場合、後で正規化に任せつつ近傍や行から補完
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

        dt = base_dt
        body_html = f'<p>{dt.strftime("%Y-%m-%d")} <a href="{link}">{chapter_title}</a></p>'
        items.append((dt, chapter_title, link, body_html))

    return items


def build_feed(items: List[Tuple[datetime, str, str, str]], now: datetime) -> None:
    os.makedirs(PUBLIC_DIR, exist_ok=True)

    fg = FeedGenerator()
    fg.title(CHANNEL_TITLE)
    fg.link(href=BASE, rel="alternate")
    fg.description(CHANNEL_DESC_HTML)
    fg.language("ja")
    fg.pubDate(format_datetime(now))
    fg.lastBuildDate(format_datetime(now.astimezone(tz.gettz("UTC"))))

    # pubDateに微オフセット（古い→新しい順で +1分ずつ）
    for idx, (dt, item_title, link, body_html) in enumerate(items):
        body_html_with_source = (
            body_html + '<br>出典: <a href="https://kirapo.jp/">きら星ポータル</a>'
        )
        dt_adj = dt + timedelta(minutes=idx)

        fe = fg.add_entry()
        fe.id(link)
        fe.guid(link, permalink=True)
        fe.title(item_title)
        fe.link(href=link)
        fe.description(body_html_with_source)
        fe.content(content=body_html_with_source, type="CDATA")
        fe.pubDate(format_datetime(dt_adj))

    fg.rss_file(os.path.join(PUBLIC_DIR, FEED_NAME), pretty=True)

    index_html = f"""<!doctype html>
<meta charset="utf-8">
<title>{CHANNEL_TITLE}</title>
<link rel="alternate" type="application/rss+xml" title="{CHANNEL_TITLE}" href="./{FEED_NAME}">
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

    # ---- 並び順：章番号で昇順（古い→新しい） ----
    def _ch_no_or_date(item):
        dt, title, link, body = item
        m = CH_NO_RE.search(title)
        if m:
            return (int(m.group(1)), dt)
        return (10**9, dt)  # 番号なしは最後

    all_items.sort(key=_ch_no_or_date)

    build_feed(all_items, now)
    time.sleep(DELAY_AFTER_BUILD)
    print(f"OK: {len(all_items)} item(s) -> {os.path.join(PUBLIC_DIR, FEED_NAME)} (+ index.html)")


if __name__ == "__main__":
    main()
