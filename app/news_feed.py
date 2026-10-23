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
from urllib.parse import urlparse, urlencode, urlunparse
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
_PLAYER_CACHE: dict[tuple, tuple[float, list]] = {}
_CACHE_SECONDS = 15 * 60
_PLAYER_SECONDS = 15 * 60  # News can change quickly around practice and kickoffs
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
        publisher = _plain(item.findtext("source")) if source == "Google News" else ""
        items.append({"title": title[:200], "summary": _plain(item.findtext("description"))[:240],
                      "url": link, "source": (publisher + " · Google News" if publisher else source),
                      "published_at": _date(item.findtext("pubDate"))})
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


def news_feed_page(*, page=1, page_size=10, source="ALL", fetcher=None):
    """Paginate across the entire available cached RSS set, after filtering."""
    if not isinstance(page, int) or page < 1 or not isinstance(page_size, int) or not 1 <= page_size <= 50:
        raise ValueError("Invalid news pagination")
    payload = _get_feed(fetcher=fetcher)
    sources = payload["sources"]
    if source != "ALL" and source not in sources:
        raise ValueError("Unknown news source")
    pool = [item for item in payload["articles"] if source == "ALL" or item["source"] == source]
    total = len(pool)
    return {**payload,
            "articles": pool[(page - 1) * page_size:page * page_size],
            "page": page, "page_size": page_size,
            "total": total, "total_pages": max(1, (total + page_size - 1) // page_size),
            "selected_source": source}


def _name_matches(name, title, summary=""):
    # A fantasy D/ST is a team, not a person, and articles use the team name.
    if re.search(r"(?:D/ST|DST|defen[sc]e)", name or "", re.I):
        team = re.sub(r"\s*(?:D/ST|DST|defen[sc]e)\s*", "", name, flags=re.I).strip().lower()
        hay = (title + " " + summary).lower()
        return bool(team and team in hay and re.search(r"\b(defen[sc]e|dst|d/st|sack|turnover|fantasy)\b", hay))
    clean = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    parts = clean.split()
    if len(parts) < 2:
        return False
    haystack = re.sub(r"[^a-z0-9 ]", " ", f"{title} {summary}".lower())
    haystack = re.sub(r"\s+", " ", haystack)
    # Accept full names or surname plus first initial, but not "Brown" alone.
    # ESPN names may include Jr./Sr. and spaced initials; publishers often omit
    # the suffix or spell A.J. as AJ. Do not accept just the surname.
    if parts[-1] in {"jr", "sr", "ii", "iii", "iv"} and len(parts) > 2:
        parts = parts[:-1]
    full = " ".join(parts)
    variants = {full}
    if len(parts) >= 3 and len(parts[0]) == len(parts[1]) == 1:
        variants.add("".join(parts[:2]) + " " + " ".join(parts[2:]))
    if len(parts) >= 3 and parts[0] == "wan" and parts[1] == "dale":
        variants.add("wandale " + " ".join(parts[2:]))
    for variant in variants:
        if re.search(r"\b" + re.escape(variant) + r"\b", haystack):
            return True
    return bool(len(parts[-1]) >= 4 and
                re.search(r"\b" + re.escape(parts[0][0]) + r"\s+" + re.escape(parts[-1]) + r"\b", haystack))


def _espn_athlete_articles(player_id, *, fetcher):
    raw = fetcher(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/athletes/{int(player_id)}/news?limit=12")
    data = json.loads(raw)
    result = []
    articles = data.get("articles", [])
    if not isinstance(articles, list):
        articles = []
    for article in articles[:12]:
        title = _plain(article.get("headline") or article.get("title"))
        links = article.get("links") or {}
        web = links.get("web") or {}
        url = _valid_link(web.get("href") or article.get("link"))
        summary = _plain(article.get("story") or article.get("description"))[:240]
        date = article.get("published") or article.get("lastModified")
        # Athlete-specific ESPN stories need not contain an explicit 'fantasy'
        # keyword, but can't be unrelated off-field news.
        if not title or not url or _OFF_TOPIC.search(title):
            continue
        if not (_PLAYER_NEWS_CONTEXT.search(title + " " + summary) or is_fantasy_relevant(title, summary)):
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


def _athlete_news_cached(player_id, *, fetcher=None, refresh=False):
    with _LOCK:
        item = _PLAYER_CACHE.get(player_id)
        if not refresh and fetcher is None and item and monotonic() - item[0] < _PLAYER_SECONDS:
            return item[1]
    try:
        articles = _espn_athlete_articles(player_id, fetcher=fetcher or _fetch_url)
    except Exception:
        articles = []
    if fetcher is None:
        with _LOCK:
            _PLAYER_CACHE[player_id] = (monotonic(), articles)
    return articles




_PLAYER_NEWS_CONTEXT = re.compile(
    r"\b(fantasy|injur|practice|questionable|touchdowns?|targets?|carries|"
    r"receiv|pass|rushing|rush|yards?|start|sit|week\s+\d+|waiver|"
    r"performance|outlook|rankings?|lineup|playing|inactive|report|"
    r"return|backfield|projection|analys|stats|snap|breakout|depth|"
    r"traded|benched)\b", re.I
)


def _google_news_url(name, *, is_search=False):
    target = name
    if re.search(r"(?:D/ST|DST)", name, re.I):
        target = re.sub(r"\s*(?:D/ST|DST)\s*", "", name, flags=re.I).strip() + " defense"
    query = f'"{target}" fantasy football NFL'
    base = "https://news.google.com/search" if is_search else "https://news.google.com/rss/search"
    return base + "?" + urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})


def _search_player_news(name, *, fetcher, max_items=20):
    """Query a player-specific RSS search rather than hoping a general feed
    happens to mention all 17 rostered players. Read headlines only.
    """
    raw = fetcher(_google_news_url(name))
    rows = _parse_rss(raw, "Google News")
    relevant = []
    for entry in rows:
        if not _name_matches(name, entry["title"], entry["summary"]):
            continue
        if _OFF_TOPIC.search(entry["title"]):
            continue
        if not (_PLAYER_NEWS_CONTEXT.search(entry["title"]) or is_fantasy_relevant(entry["title"], entry["summary"])):
            continue
        relevant.append(entry)
    return relevant[:max_items]


def _player_search_cached(name, *, fetcher=None, refresh=False):
    key = ("search", name.lower())
    with _LOCK:
        entry = _PLAYER_CACHE.get(key)
        if not refresh and fetcher is None and entry and monotonic() - entry[0] < _PLAYER_SECONDS:
            return entry[1]
    try:
        articles = _search_player_news(name, fetcher=fetcher or _fetch_url)
    except Exception:
        articles = []
    if fetcher is None:
        with _LOCK:
            _PLAYER_CACHE[key] = (monotonic(), articles)
    return articles


def _normalized_title(title):
    """Ignore punctuation and common publisher suffixes when checking duplicates."""
    raw = re.sub(r"\s+[-|]\s+(?:CBS Sports|Yahoo Sports|ESPN|NBC Sports|FantasyPros|"
                 r"RotoWire|FTN(?: Fantasy)?|Sports Illustrated)\s*$", "", str(title or ""), flags=re.I)
    return " ".join(re.findall(r"[a-z0-9]+", raw.casefold()))


def _canonical_link(link):
    parsed = urlparse(str(link or ""))
    if not parsed.hostname:
        return ""
    return urlunparse((parsed.scheme.lower(), parsed.hostname.lower(),
                       parsed.path.rstrip("/"), "", "", ""))


def _published_datetime(article):
    value = article.get("published_at")
    if not value:
        return None
    try:
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _article_score(article, player_name, injury_status, *, now):
    """Transparent deterministic relevance + freshness score. No model calls."""
    title = str(article.get("title") or "")
    summary = str(article.get("summary") or "")
    in_title = _name_matches(player_name, title)
    in_summary = _name_matches(player_name, "", summary)
    # Athlete endpoint is keyed by ESPN ID, though exact name mention is better.
    identity_score = 34 if in_title else (10 if in_summary else (4 if article.get("source") == "ESPN Player News" else -20))
    content = title + " " + summary
    fantasy = bool(re.search(r"\b(fantasy|waiver|start.?sit|trade value|PPR|FAAB|"
                             r"projections?|rankings?|targets?|snaps?|usage|touches|"
                             r"workload|backfield|depth chart)\b", title, re.I))
    action = bool(_PLAYER_NEWS_CONTEXT.search(title))
    injury_news = bool(re.search(r"\b(injur(?:y|ed|ies)|questionable|doubtful|"
                                 r"ruled out|inactive|practice|limited|IR|return)\b", content, re.I))
    is_injured = str(injury_status or "").upper() not in {"", "ACTIVE", "HEALTHY", "NORMAL"}
    published = _published_datetime(article)
    if published:
        age_days = max(0.0, (now - published).total_seconds() / 86400)
        freshness = max(-60.0, 32.0 - 5.0 * age_days)
    else:
        freshness = -12.0  # Unverified recency must not outrank breaking reports.
    score = identity_score + (9 if fantasy else 0) + (4 if action else 0) + freshness
    if is_injured and injury_news:
        score += 12
    # Prefer the freshest genuinely relevant story. A provider's name must not
    # outrank the same headline published later elsewhere.
    if "Google News" not in str(article.get("source") or ""):
        score += 0.05
    return round(score, 3)


def _deduplicate_and_rank(candidates, player_name, injury_status, *, now=None, max_items=20):
    """Merge all providers before selection; don't stop at the first two results."""
    now = now or datetime.now(timezone.utc)
    ranked = []
    for article in candidates:
        title = str(article.get("title") or "")
        link = _valid_link(article.get("url"))
        if not title or not link or _OFF_TOPIC.search(title):
            continue
        # ESPN athlete endpoint identifies the player by ID; elsewhere require
        # explicit name match. All sources must pass the on-field news gate.
        source = str(article.get("source") or "")
        if not (_name_matches(player_name, title, str(article.get("summary") or "")) or source == "ESPN Player News"):
            continue
        if not (is_fantasy_relevant(title, article.get("summary", "")) or
                _PLAYER_NEWS_CONTEXT.search(title + " " + str(article.get("summary") or ""))):
            continue
        row = {"title": title, "summary": str(article.get("summary") or ""),
               "url": link, "source": source, "published_at": article.get("published_at")}
        row["relevance_score"] = _article_score(row, player_name, injury_status, now=now)
        ranked.append(row)

    # Keep the best candidate for the same source URL or the same story headline,
    # including identical articles linked through Google News redirects.
    ranked.sort(key=lambda a: (a["relevance_score"],
                               _published_datetime(a) or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    unique = []
    seen_titles, seen_links = set(), set()
    for row in ranked:
        title_key, link_key = _normalized_title(row["title"]), _canonical_link(row["url"])
        if not title_key or title_key in seen_titles or link_key in seen_links:
            continue
        # Publisher titles sometimes differ by a short added tagline.
        tokens = set(title_key.split())
        near_duplicate = False
        for prev in unique:
            other = set(_normalized_title(prev["title"]).split())
            if min(len(tokens), len(other)) >= 6 and len(tokens & other) / len(tokens | other) >= 0.88:
                near_duplicate = True
                break
        if near_duplicate:
            continue
        seen_titles.add(title_key)
        seen_links.add(link_key)
        unique.append(row)
        if len(unique) >= max_items:
            break
    # Relevance score is an internal ordering signal; omit arbitrary numeric ranks
    # from user-facing articles to avoid implying a calibrated prediction.
    for row in unique:
        row.pop("relevance_score", None)
    return unique


def roster_news(roster, *, feed_fetcher=None, athlete_fetcher=None,
                search_fetcher=None, max_players=32, refresh=False):
    """Search *all* sources, then rank/deduplicate before choosing top stories.

    Includes up to 20 articles per player. The frontend shows the top two with
    an expandable 'View all' option. Searches are cached for 15 minutes unless
    explicitly refreshed, and never invoke Claude or paid FantasyPros APIs.
    """
    feed = _get_feed(fetcher=feed_fetcher, refresh=refresh)
    now = datetime.now(timezone.utc)
    rows = []
    for player in list(roster or [])[:max_players]:
        name = str(player.get("name") or "").strip()
        if not name:
            continue
        rows.append({"name": name, "position": player.get("position"),
                     "coverage_url": _google_news_url(name, is_search=True),
                     "injury_status": player.get("injury_status"),
                     "espn_id": player.get("espn_id"), "articles": [],
                     "status": "not_found", "_candidates": [a for a in feed["articles"]
                                                       if _name_matches(name, a["title"], a["summary"])]})

    # Don't conditionally skip ESPN or Google just because a broad feed happened
    # to contain two older stories. Collect all and rank the combined pool.
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {}
        for row in rows:
            pid = row["espn_id"]
            if isinstance(pid, int) and pid > 0:
                future = pool.submit(_athlete_news_cached, pid, fetcher=athlete_fetcher, refresh=refresh)
                futures[future] = row
            future = pool.submit(_player_search_cached, row["name"], fetcher=search_fetcher, refresh=refresh)
            futures[future] = row
        for future in as_completed(futures):
            try:
                futures[future]["_candidates"].extend(future.result())
            except Exception:
                pass  # Failed sources cannot hide verified stories from other sources.

    for row in rows:
        row["articles"] = _deduplicate_and_rank(
            row.pop("_candidates"), row["name"], row.get("injury_status"), now=now)
        row["status"] = "available" if row["articles"] else "not_found"

    return {"status": "available" if rows else "unavailable", "players": rows,
            "matched_players": sum(bool(row["articles"]) for row in rows),
            "total_players": len(rows),
            "refreshed_at": now.isoformat(),
            "ranking_method": "Player relevance, article freshness, injury relevance, duplicate removal",
            "note": "Articles from all available feeds, ESPN player news and Google News are ranked together; the top two are shown first. Older stories and repeated headlines are deprioritized. Click View all to browse more. No Claude or paid FantasyPros calls.",
            "feed_status": feed["status"]}
