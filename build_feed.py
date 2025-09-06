# -*- coding: utf-8 -*-
# きら星ポータル 非公式RSS（邪神ちゃんのみ）
import re, time, os
import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from urllib.parse import urljoin
from datetime import datetime
from dateutil import tz
from email.utils import format_datetime

BASE = "https://kirapo.jp/"
HEADERS = {"User-Agent": "KirapoRSS/1.1 (GitHub-Actions)"}
TIMEOUT = 20

# 対象タイトル（邪神ちゃんのみ）
TARGET_TITLE_PAGES = ["https://kirapo.jp/meteor/titles/jyashin"]
# ビューアのパス頭（作品ごとに異なるのでタイトルURLから推測）
VIEW_PREFIX = "/pt/meteor/jyashin/"

DATE_JA_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
CHAPTER_RE = re.compile(r"第\s*\d+\s*話[^\n]*")  # 例: 第279話 インパクト爆誕

def get_soup(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def extract_latest_from_title_page(soup: BeautifulSoup, title_url: str, now: datetime):
    # 作品名（h1/h2）
    work_title = (soup.find(["h1","h2"]).get_text(strip=True)
                  if soup.find(["h1","h2"]) else "作品")

    # 更新日（ページ内最初の YYYY年M月D日）
    txt = soup.get_text(" ", strip=True)
    m = DATE_JA_RE.search(txt)
    dt = (datetime(*map(int, m.groups()), tzinfo=tz.gettz("Asia/Tokyo"))
          if m else now)

    # 章タイトル（最初に出てくる「第xxx話 …」）
    m2 = CHAPTER_RE.search(txt)
    chapter_title = m2.group(0).strip() if m2 else f"{work_title} 最新話"

    # 最新話へのリンク（VIEW_PREFIX を優先。なければタイトルページ）
    a = soup.select_one(f'a[href^="{VIEW_PREFIX}"]') or soup.find("a", string=re.compile("最新話を読む"))
    link = urljoin(BASE, a["href"]) if (a and a.get("href")) else title_url

    # description/content 用のHTML
    body_html = f'<p>{dt.strftime("%Y-%m-%d")} <a href="{link}">{chapter_title}</a></p>'
    return work_title, chapter_title, link, dt, body_html

def main():
    tz_jst = tz.gettz("Asia/Tokyo")
    now = datetime.now(tz=tz_jst)

    items = []
    for title_url in TARGET_TITLE_PAGES:
        try:
            tsoup = get_soup(title_url)
            work_title, chapter_title, link, dt, body_html = extract_latest_from_title_page(tsoup, title_url, now)
            # 邪神ちゃん単独フィードなので <item> の title は章タイトルで十分
            items.append((dt, chapter_title, link, body_html))
        except Exception as e:
            print(f"[warn] skip {title_url}: {e}")
        time.sleep(0.3)  # 低負荷

    # 新しい順
    items.sort(key=lambda x: x[0], reverse=True)

    # チャンネル
    fg = FeedGenerator()
    fg.title("きら星ポータル 非公式RSS")
    fg.link(href=BASE, rel='alternate')
    fg.description("邪神ちゃんだけ配信（非公式）")
    fg.language("ja")
    fg.pubDate(format_datetime(now))
    fg.lastBuildDate(format_datetime(now.astimezone(tz.gettz("UTC"))))

    # 最新話 1件のみでもOK（複数化したければ items を複数追加）
    for dt, chapter_title, link, body_html in items:
        fe = fg.add_entry()
        fe.id(link)
        fe.guid(link, permalink=True)
        fe.title(chapter_title)   # 例: 第279話 インパクト爆誕
        fe.link(href=link)
        fe.description(body_html)
        fe.content(content=body_html, type="CDATA")
        fe.pubDate(format_datetime(dt))

    os.makedirs("public", exist_ok=True)
    fg.rss_file("public/feed.xml", pretty=True)

    # index.html（feed.xmlへ即リダイレクト）
    index_html = """<!doctype html>
<meta charset="utf-8">
<title>きら星ポータル 非公式RSS（邪神ちゃんのみ）</title>
<link rel="alternate" type="application/rss+xml" title="きら星ポータル 非公式RSS（邪神ちゃんのみ）" href="./feed.xml">
<meta http-equiv="refresh" content="0; url=./feed.xml">
<p>自動的に <a href="./feed.xml">feed.xml</a> へ移動します。</p>
"""
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(index_html)

    print(f"OK: {len(items)} item(s) -> public/feed.xml (+ index.html)")

if __name__ == "__main__":
    main()
