import asyncio
import re
from dataclasses import dataclass

import feedparser

from config import FEEDS

SUMMARY_MAX_LEN = 400
_TAG_RE = re.compile(r"<[^>]+>")
_IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


@dataclass
class Article:
    source: str
    title: str
    link: str
    published: str
    entry_id: str
    author: str = ""
    summary: str = ""
    image: str = ""


def _strip_html(raw: str) -> str:
    text = _TAG_RE.sub("", raw or "")
    return re.sub(r"\s+", " ", text).strip()


def _truncate(text: str, limit: int = SUMMARY_MAX_LEN) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _extract_image(entry) -> str:
    media_content = entry.get("media_content") or []
    for m in media_content:
        if m.get("url"):
            return m["url"]

    media_thumbnail = entry.get("media_thumbnail") or []
    for m in media_thumbnail:
        if m.get("url"):
            return m["url"]

    for link in entry.get("links") or []:
        if str(link.get("type", "")).startswith("image/"):
            return link.get("href", "")

    html_blob = entry.get("summary", "")
    content_list = entry.get("content") or []
    if content_list:
        html_blob += content_list[0].get("value", "")
    match = _IMG_SRC_RE.search(html_blob)
    if match:
        return match.group(1)
    return ""


def _parse_feed(url: str):
    return feedparser.parse(url)


async def fetch_feed_articles(name: str, url: str) -> list[Article]:
    loop = asyncio.get_running_loop()
    parsed = await loop.run_in_executor(None, _parse_feed, url)
    articles = []
    for entry in parsed.entries:
        entry_id = entry.get("id") or entry.get("link")
        if not entry_id:
            continue
        summary = _truncate(_strip_html(entry.get("summary", "")))
        articles.append(
            Article(
                source=name,
                title=entry.get("title", "Untitled"),
                link=entry.get("link", ""),
                published=entry.get("published", ""),
                entry_id=entry_id,
                author=entry.get("author", ""),
                summary=summary,
                image=_extract_image(entry),
            )
        )
    return articles


async def fetch_category(category: str) -> list[Article]:
    feeds = FEEDS.get(category, [])
    results = await asyncio.gather(
        *(fetch_feed_articles(f["name"], f["url"]) for f in feeds),
        return_exceptions=True,
    )
    articles: list[Article] = []
    for r in results:
        if isinstance(r, Exception):
            continue
        articles.extend(r)
    return articles
