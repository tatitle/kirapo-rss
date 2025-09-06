# -*- coding: utf-8 -*-
import re, time, os
import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from urllib.parse import urljoin
from datetime import datetime
from dateutil import tz
from email.utils import format_datetime

BASE = "https://kirapo.jp/"
HEADERS = {"User-Agent": "KirapoRSS/1.0 (GitHub-Actions)"}
TIMEOUT = 20

# 作品ページのパス（必要に応じて追加/削除OK）
TITLE_PREFIXES = (
    "/etoile/titles/",
    "/meteor/titles/",
    "/polaris/titles/",
    "/amble/titles/",
    "/astil/titles/",
    "/zlett/titles/",
)

DATE_JA_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")

def get_soup(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def is_title_link(href: str) -> bool:
    return any(href.startswith(p) for p in TITLE_PREFIXES)

def parse_update_date_from_title_page(soup: BeautifulSoup, default_dt: datetime) -> datetime:
    txt = soup.get_text(" ", strip=True)
    m = DATE_JA_RE.search(txt)
    if not m:
        return default_dt
    y, mo, d = map(int, m.groups())
    return datetime(y, mo, d, 0, 0, 0, tzinfo=tz.gettz("Asia/Tokyo"))

def extract_title_from_title_page(soup: BeautifulSoup, fallback: str) -> str:
    h = soup.find(["h1", "h2"])
    if h and h.get_text(strip=True):
        return h.get_text(strip=True)
    return fallback

def main():
    tz_jst = tz.gettz("Asia/Tokyo")
    now = datetime.now(tz=tz_jst)

    top = get_soup(BASE)

    # トップから作品ページへのリンクだけ収集
    links, seen = [], set()
    for a in top.select("a[href]"):
        href = (a.get("href") or "").strip()
        if is_title_link(href):
            full = urljoin(BASE, href)
            if full not in seen:
                seen.add(full)
                links.append(full)

    # 低負荷：最大60件＋遅延
    links = links[:60]
    items = []

    for link in links:
        try:
            tsoup = get_soup(link)
            work_title = extract_title_from_title_page(tsoup, fallback=link)
            dt = parse_update_date_from_title_page(tsoup, default_dt=now)
            body_html = f'<p>{dt.strftime("%Y-%m-%d")} <a href="{link}">{work_title}</a></p>'
            items.append((dt, work_title, link, body_html))
        except Exception as e:
            print(f"[warn] skip {link}: {e}")
        time.sleep(0.3)

    items.sort(key=lambda x: x[0], reverse=True)

    fg = FeedGenerator()
    fg.title("きら星ポータル 非公式RSS")
    fg.link(href=BASE, rel='alternate')
    fg.description("きら星ポータルの作品更新情報（非公式）")
    fg.language("ja")
    fg.pubDate(format_datetime(now))
    fg.lastBuildDate(format_datetime(now.astimezone(tz.gettz("UTC"))))

    for dt, work_title, link, body_html in items:
        fe = fg.add_entry()
        fe.id(link)
        fe.guid(link, permalink=True)
        fe.title(work_title)
        fe.link(href=link)
        fe.description(body_html)
        fe.content(content=body_html, type="CDATA")
        fe.pubDate(format_datetime(dt))

    os.makedirs("public", exist_ok=True)
    fg.rss_file("public/feed.xml", pretty=True)
    print(f"OK: {len(items)} items -> public/feed.xml")

if __name__ == "__main__":
    main()
