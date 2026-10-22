"""Fantasy-relevant, attributed news and an on-demand roster-specific news feed.

Public RSS and ESPN athlete news: no FantasyPros quota, Claude calls or page scraping.
Missing coverage is explicitly marked missing rather than inventing an article.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from threading import RLock
from time import monotonic
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
import json
import re

SOURCES = (
    ("ESPN", "https://www.espn.com/espn/rss/nfl/news"),
    ("RotoWire", "https://www.rotowire.com/rss/news.php?sport=NFL"),
    ("FantasyPros Articles", "https://www.fantasypros.com/content/nfl/feed/"),
)
_LOCK = RLock()
_CACHE = None
_PLAYER_CACHE: dict[int, tuple[float, list]] = {}
_CACHE_SECONDS = 15 * 60
_PLAYER_SECONDS = 60 * 60
_MAX_XML_BYTES = 1_000_000

# Exclude straight NFL business/entertainment headlines unless the story itself
# has an actionable on-field / player-availability angle.
_FANTASY = re.compile(
    r'\b(fantasy|start[ /-]?sit|waiver|FAAB|sleepers?|injur(?:y|ies|ed)|questionable|'
    r'doubtful|ruled out|inactive|active|practice|limited|game[ -]?time|'
    r'targets?|snaps?|touches|carries|rushing|receiving|receptions?|'
    r'passing|yardage|yards?|touchdowns?|backfield|depth chart|'
    r'workload|breakout|usage|rankings?|projections?|outlook|'
    r'quarterback|running back|wide receiver|tight end|'
    r'week\s+\d+|DFS|PPR|redraft|lineup|roster|matchup|'
    r'expected to (?:play|start|miss)|returns? (?:to|from) practice|'
    r'placed on (?:IR|injured reserve))\b', re.I)
_OFF_TOPIC = re.compile(
    r'\b(TV|broadcast|announc(?:er|ing)|CBS (?:contract|deal)|'
    r'commentator|studio analyst|retirement ceremony|ownership stake|'
    r'podcast deal|broadcast deal|hall of fame ceremony)\b', re.I)


def _plain(value):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def _date(value):
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, IndexError):
        return None


def _valid_link(value):
    link = str(value or "").strip()
    parsed = urlparse(link)
    return link if parsed.scheme == "https" and parsed.hostname else None


def is_fantasy_relevant(title, summary=""):
    """Conservative headline gate. Don't filter on generic team/NFL terms."""
    if _OFF_TOPIC.search(title or ""):
        return False
    return bool(_FANTASY.search(title or "") or _FANTASY.search(summary or ""))


def _parse_rss(xml, source):
    root = ET.fromstring(xml)
    items = []
    for item in root.findall(".//channel/item")[:80]:
        title = _plain(item.findtext("title"))
        link = _valid_link(item.findtext("link"))
        if not title or not link:
            continue
        items.append({"title": title[:200], "summary": _plain(item.findtext("description"))[:240],
                      "url": link, "source": source, "published_at": _date(item.findtext("pubDate"))})
    return items


def _fetch_url(url):
    req = Request(url, headers={"User-Agent": "FantasyCoachLocal/1.0 (attributed article reader)",
                                "Accept": "application/rss+xml, application/xml, application/json, text/xml"})
    with urlopen(req, timeout=5) as response:
        raw = response.read(_MAX_XML_BYTES + 1)
    if len(raw) > _MAX_XML_BYTES:
        raise ValueError("Feed response exceeds size limit")
    return raw


def _get_feed(*, fetcher=None, refresh=False):
    global _CACHE
    fn = fetcher or _fetch_url
    with _LOCK:
        if not refresh and fetcher is None and _CACHE and monotonic() - _CACHE[0] < _CACHE_SECONDS:
            return {**_CACHE[1], "cached": True}
    items, errors = [], []
    for name, url in SOURCES:
        try:
            items.extend(_parse_rss(fn(url), name))
        except Exception:
            errors.append(name)
    dedup = {}
    for entry in items:
        dedup.setdefault(entry["url"], entry)
    all_items = sorted(dedup.values(), key=lambda x: x["published_at"] or "", reverse=True)
    relevant = [entry for entry in all_items if is_fantasy_relevant(entry["title"], entry["summary"])]
    payload = {"status": "available" if relevant else "unavailable", "articles": relevant,
               "sources": [name for name, _ in SOURCES], "unavailable_sources": errors,
               "filtered_nonfantasy": len(all_items) - len(relevant),
               "note": "Only fantasy-relevant headlines are shown. Article links lead to the publisher."}
    if fetcher is None:
        with _LOCK:
            if relevant:
                _CACHE = (monotonic(), payload)
            elif _CACHE:
                stale = _CACHE[1]
                return {**stale, "cached": True, "stale": True,
                        "note": "News sources couldn't be refreshed; using cached fantasy headlines."}
    return {**payload, "cached": False}


def news_feed(*, limit=24, fetcher=None, refresh=False):
    payload = _get_feed(fetcher=fetcher, refresh=refresh)
    return {**payload, "articles": payload["articles"][:limit]}


def _name_matches(name, title, summary=""):
    clean = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    parts = clean.split()
    if len(parts) < 2:
        return False
    haystack = re.sub(r"[^a-z0-9 ]", " ", f"{title} {summary}".lower())
    haystack = re.sub(r"\s+", " ", haystack)
    # Accept full names or surname plus first initial, but not "Brown" alone.
    full = " ".join(parts)
    return bool(re.search(r"\b" + re.escape(full) + r"\b", haystack)) or bool(
        len(parts[-1]) >= 4 and re.search(r"\b" + re.escape(parts[0][0]) + r"\s+" + re.escape(parts[-1]) + r"\b", haystack)
    )


def _espn_athlete_articles(player_id, *, fetcher):
    raw = fetcher(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/athletes/{int(player_id)}/news?limit=12")
    data = json.loads(raw)
    result = []
    for article in data.get("articles", [])[:12]:
        title = _plain(article.get("headline") or article.get("title"))
        links = article.get("links") or {}
        web = links.get("web") or {}
        url = _valid_link(web.get("href") or article.get("link"))
        summary = _plain(article.get("story") or article.get("description"))[:240]
        date = article.get("published") or article.get("lastModified")
        if not title or not url or not is_fantasy_relevant(title, summary):
            continue
        try:
            when = datetime.fromisoformat(str(date).replace("Z", "+00:00")).astimezone(timezone.utc)
            if datetime.now(timezone.utc) - when > timedelta(days=45):
                continue
            published = when.isoformat()
        except (ValueError, TypeError, AttributeError):
            published = None
        result.append({"title": title[:200], "summary": summary, "url": url,
                       "source": "ESPN Player News", "published_at": published})
    return result


def _athlete_news_cached(player_id, *, fetcher=None):
    with _LOCK:
        item = _PLAYER_CACHE.get(player_id)
        if fetcher is None and item and monotonic() - item[0] < _PLAYER_SECONDS:
            return item[1]
    try:
        articles = _espn_athlete_articles(player_id, fetcher=fetcher or _fetch_url)
    except Exception:
        articles = []
    if fetcher is None:
        with _LOCK:
            _PLAYER_CACHE[player_id] = (monotonic(), articles)
    return articles


def roster_news(roster, *, feed_fetcher=None, athlete_fetcher=None, max_players=32):
    """Best real story for every rostered player when available; never manufacture one.

    Only called when 'My Team News' is opened. Athlete requests are bounded and
    hourly cached. One failed source cannot hide the rest.
    """
    feed = _get_feed(fetcher=feed_fetcher)
    player_rows = []
    unresolved = []
    for player in list(roster or [])[:max_players]:
        name = str(player.get("name") or "").strip()
        if not name:
            continue
        matches = [a for a in feed["articles"] if _name_matches(name, a["title"], a["summary"])]
        matches = sorted(matches, key=lambda a: ("injur" in a["title"].lower() or "practice" in a["title"].lower()) if str(player.get("injury_status") or "").upper() not in {"", "ACTIVE", "HEALTHY"} else False, reverse=True)
        row = {"name": name, "position": player.get("position"),
               "injury_status": player.get("injury_status"),
               "espn_id": player.get("espn_id"), "articles": matches[:2],
               "status": "available" if matches else "not_found"}
        player_rows.append(row)
        if not matches and isinstance(player.get("espn_id"), int) and player["espn_id"] > 0:
            unresolved.append(row)
    # Network calls are bounded by actual roster size and only on explicit tab open.
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_map = {pool.submit(_athlete_news_cached, row["espn_id"], fetcher=athlete_fetcher): row for row in unresolved}
        for future in as_completed(future_map):
            row = future_map[future]
            try:
                row["articles"] = future.result()[:2]
            except Exception:
                row["articles"] = []
            row["status"] = "available" if row["articles"] else "not_found"
    return {"status": "available" if player_rows else "unavailable", "players": player_rows,
            "matched_players": sum(bool(row["articles"]) for row in player_rows),
            "total_players": len(player_rows),
            "note": "Stories are real publisher links. Not every player has a recent fantasy-relevant article; missing coverage is labeled explicitly. ESPN player lookup is cached for one hour.",
            "feed_status": feed["status"]}
