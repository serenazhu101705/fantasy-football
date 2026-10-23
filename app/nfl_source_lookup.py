"""Read-only, source-attributed research for NFL questions.

Prefers verified nflverse statistics, falls back to ESPN's public league
leaders when the local nflverse table is unavailable, and provides concise
ESPN/Google News/RSS evidence for open-ended NFL questions. No paid APIs,
write operations, credential handling, or arbitrary URL fetches.

All network calls go to predeclared HTTPS hosts and have short timeouts.
Articles are untrusted headlines, not verified statistics.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from time import monotonic
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
import json
import re
from threading import RLock
from typing import Any

from nfl_leaderboards import METRICS, get_nfl_leaderboard, parse_leaderboard_request

_ESPN_LEADERS = 'https://site.api.espn.com/apis/site/v3/sports/football/nfl/leaders'
_ESPN_NEWS = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/news'
_GOOGLE_RSS = 'https://news.google.com/rss/search'
_FANTASYPROS_RSS = 'https://www.fantasypros.com/content/nfl/feed/'
_HEADERS = {'User-Agent': 'FantasyCoach/1.0 (personal NFL research)', 'Accept': 'application/json, application/rss+xml'}
_MAX_RESPONSE = 1_000_000
_LOCK = RLock()
_CACHE: dict[tuple, tuple[float, dict]] = {}
_TTL_SECONDS = 15 * 60


def _fetch_bytes(url: str, *, timeout: int = 5) -> bytes:
    """Only fetch from whitelisted, hard-coded origins to avoid arbitrary URLs."""
    if not (url.startswith(_ESPN_LEADERS + '?') or
            url.startswith(_ESPN_NEWS + '?') or
            url.startswith(_GOOGLE_RSS + '?') or
            url == _FANTASYPROS_RSS):
        raise ValueError('Unapproved NFL research URL')
    with urlopen(Request(url, headers=_HEADERS), timeout=timeout) as resp:
        contents = resp.read(_MAX_RESPONSE + 1)
    if len(contents) > _MAX_RESPONSE:
        raise ValueError('Research response too large')
    return contents


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        x = float(re.sub(r'[,\s]', '', str(value)))
        return x if abs(x) < 1e10 and x == x else None
    except (ValueError, TypeError):
        return None


def _metric_label_matches(label: str, stat: str) -> bool:
    """Never read a category's first entry without checking its identity."""
    label = re.sub(r'([a-z])([A-Z])', r'\1 \2', str(label)).lower()
    compact = re.sub(r'[^a-z0-9]', '', label)
    aliases = {
        'passing_yards': ('passingyards', 'passyards', 'passyds'),
        'rushing_yards': ('rushingyards', 'rushyards', 'rushyds'),
        'receiving_yards': ('receivingyards', 'recyards', 'receivingyds'),
        'passing_tds': ('passingtouchdowns', 'passingtd', 'passingtds'),
        'rushing_tds': ('rushingtouchdowns', 'rushingtd', 'rushingtds'),
        'receiving_tds': ('receivingtouchdowns', 'receivingtd', 'receivingtds'),
        'receptions': ('receptions', 'catches'),
        'targets': ('targets',),
        'completions': ('passingcompletions', 'completions'),
        'carries': ('rushingattempts', 'rushattempts', 'carries'),
        'passing_interceptions': ('passinginterceptions', 'interceptionsthrown', 'passinginterceptionsthrown'),
        'passing_attempts': ('passingattempts', 'passattempts'),
        'defensive_sacks': ('sacks', 'defensivesacks', 'totalsacks'),
        'defensive_interceptions': ('defensiveinterceptions', 'interceptions', 'int'),
    }
    return compact in aliases.get(stat, ())


def parse_espn_leaders(payload: dict, metric: str, *, season: int, limit: int = 5) -> dict:
    """Extract names + values from a *matched* ESPN category, or refuse.

    ESPN's undocumented response shape can change. Match category names and
    validate individual values instead of assuming any leading number means yards.
    """
    if metric not in METRICS:
        return {'status': 'unsupported', 'reason': 'Unsupported metric'}
    matches = []
    queue = [(payload, 0)]
    while queue:
        node, depth = queue.pop()
        if depth > 7:
            continue
        if isinstance(node, dict):
            candidates = [node.get(k) for k in ('displayName', 'name', 'shortDisplayName', 'type', 'abbreviation', 'statName')]
            if any(_metric_label_matches(k, metric) for k in candidates if k):
                for field in ('leaders', 'athletes', 'entries', 'items'):
                    values = node.get(field)
                    if isinstance(values, list):
                        matches.extend(values)
            for val in node.values():
                if isinstance(val, (list, dict)):
                    queue.append((val, depth + 1))
        elif isinstance(node, list):
            for val in node[:100]:
                if isinstance(val, (list, dict)):
                    queue.append((val, depth + 1))
    extracted = []
    seen = set()
    for obj in matches:
        if not isinstance(obj, dict):
            continue
        athlete = obj.get('athlete') or obj.get('player') or {}
        if not isinstance(athlete, dict):
            athlete = {}
        name = str(athlete.get('displayName') or athlete.get('fullName') or
                   obj.get('displayName') or obj.get('athleteName') or '').strip()
        value = _num(obj.get('value'))
        if value is None:
            value = _num(obj.get('displayValue'))
        if not name or value is None or name in seen:
            continue
        seen.add(name)
        team = obj.get('team') or athlete.get('team') or {}
        team_name = team.get('abbreviation') if isinstance(team, dict) else None
        extracted.append({'name': name, 'team': team_name, 'value': value})
    extracted.sort(key=lambda row: (-row['value'], row['name']))
    extracted = extracted[:max(1, min(limit, 20))]
    return {'status': 'available' if extracted else 'unavailable', 'season': season,
            'stat': metric, 'stat_label': METRICS[metric][0], 'unit': METRICS[metric][2],
            'through_week': None, 'source': 'ESPN NFL statistical leaders',
            'source_url': _ESPN_LEADERS + '?' + urlencode({'season': season, 'seasontype': 2}),
            'note': 'ESPN public statistical leaderboard. The API did not provide a verified through-week marker.',
            'leaders': [{'rank': idx, **row} for idx, row in enumerate(extracted, 1)]}


def espn_leaderboard(metric: str, *, season: int, limit: int = 5, fetcher=None) -> dict:
    url = _ESPN_LEADERS + '?' + urlencode({'season': season, 'seasontype': 2})
    try:
        doc = json.loads((fetcher or _fetch_bytes)(url))
        return parse_espn_leaders(doc, metric, season=season, limit=limit)
    except Exception as exc:
        return {'status': 'unavailable', 'source': 'ESPN NFL statistical leaders',
                'reason': f'ESPN leaderboard retrieval failed ({type(exc).__name__}).'}


def verified_leaderboard(question: str, *, season: int = 2026, nflverse=None, espn=None) -> dict:
    """Lookup ordered: nflverse -> ESPN. Never silently switch seasons."""
    parsed = parse_leaderboard_request(question)
    if parsed is None:
        return {'status': 'unsupported', 'reason': 'Not a recognized NFL leaderboard question.'}
    requested = parsed.get('requested_season')
    if requested and requested != season:
        return {'status': 'unavailable', 'season': requested, 'attempted_sources': [],
                'reason': f'{requested} was requested but only {season} is loaded. Historical data was not substituted.'}
    from_nflverse = (nflverse or get_nfl_leaderboard)(question, season=season)
    if from_nflverse.get('status') == 'available':
        return {**from_nflverse, 'attempted_sources': ['nflverse']}
    from_espn = (espn or espn_leaderboard)(parsed['metric'], season=season, limit=parsed['limit'])
    if from_espn.get('status') == 'available':
        return {**from_espn, 'attempted_sources': ['nflverse', 'ESPN']}
    return {'status': 'unavailable', 'season': season, 'stat': parsed['metric'],
            'attempted_sources': ['nflverse', 'ESPN'],
            'reason': 'Neither nflverse nor ESPN returned a verifiable leaderboard for this statistic.',
            'source_errors': {'nflverse': from_nflverse.get('reason'), 'ESPN': from_espn.get('reason')}}


def _strip_html(value: str) -> str:
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]*>', ' ', str(value or '')))).strip()[:280]


def _articles_from_espn(data: dict) -> list[dict]:
    result = []
    for article in data.get('articles') or []:
        title = str(article.get('headline') or article.get('title') or '').strip()
        urls = article.get('links') or {}
        web = urls.get('web') if isinstance(urls, dict) else {}
        link = (web.get('href') if isinstance(web, dict) else None) or article.get('link')
        if title and isinstance(link, str) and link.startswith('https://'):
            result.append({'title': _strip_html(title), 'summary': _strip_html(article.get('description')),
                           'url': link, 'source': 'ESPN NFL News',
                           'published_at': article.get('published') or article.get('lastModified')})
    return result


def _articles_from_rss(data: bytes, *, source='Google News RSS (publisher headline)') -> list[dict]:
    root = ET.fromstring(data)
    result = []
    for item in root.findall('.//channel/item')[:30]:
        title, url = _strip_html(item.findtext('title')), (item.findtext('link') or '').strip()
        if not title or not url.startswith('https://'):
            continue
        published = None
        try:
            published = parsedate_to_datetime(item.findtext('pubDate')).astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError, AttributeError):
            pass
        result.append({'title': title, 'summary': _strip_html(item.findtext('description')),
                       'url': url, 'source': source, 'published_at': published})
    return result

_STOP = {'what', 'which', 'who', 'when', 'where', 'why', 'how', 'the', 'with', 'have', 'this', 'that', 'from',
         'does', 'are', 'for', 'and', 'in', 'nfl', 'football', 'league', 'about', 'latest', 'tell', 'me',
         'has', 'did', 'any', 'there', 'week', 'season', 'now', 'today', 'update', 'updates'}


def _relevance(article: dict, question: str) -> int:
    keywords = {x for x in re.findall(r'[a-z]{3,}', question.lower()) if x not in _STOP}
    title = str(article.get('title') or '').lower()
    summary = str(article.get('summary') or '').lower()
    return sum(3 if re.search(r'\b' + re.escape(w) + r'\b', title) else
               1 if re.search(r'\b' + re.escape(w) + r'\b', summary) else 0 for w in keywords)


def search_nfl_sources(question: str, *, season: int = 2026, fetcher=None, cache=True) -> dict:
    """Fetch evidence for general questions. Source headlines are never numeric proof."""
    question = str(question or '').strip()[:240]
    if not question:
        return {'status': 'unavailable', 'reason': 'Empty question', 'sources_checked': []}
    if parse_leaderboard_request(question):
        answer = verified_leaderboard(question, season=season)
        return {'kind': 'leaderboard', **answer}
    key = (question.casefold(), season)
    with _LOCK:
        cached = _CACHE.get(key) if cache and fetcher is None else None
        if cached and monotonic() - cached[0] < _TTL_SECONDS:
            return {**cached[1], 'cached': True}
    load = fetcher or _fetch_bytes
    tried = []
    errors = []
    articles = []
    espn_url = _ESPN_NEWS + '?limit=40'
    tried.append('ESPN NFL News')
    try:
        articles.extend(_articles_from_espn(json.loads(load(espn_url))))
    except Exception as exc:
        errors.append(f'ESPN NFL News: {type(exc).__name__}')
    tried.append('FantasyPros Articles RSS')
    try:
        articles.extend(_articles_from_rss(load(_FANTASYPROS_RSS), source='FantasyPros Articles'))
    except Exception as exc:
        errors.append(f'FantasyPros Articles RSS: {type(exc).__name__}')
    query = question + ' NFL'
    google_url = _GOOGLE_RSS + '?' + urlencode({'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})
    tried.append('Google News RSS')
    try:
        articles.extend(_articles_from_rss(load(google_url)))
    except Exception as exc:
        errors.append(f'Google News RSS: {type(exc).__name__}')
    dedup = {}
    for entry in articles:
        if _relevance(entry, question) > 0:
            dedup.setdefault(entry['url'], entry)
    matches = sorted(dedup.values(), key=lambda a: (_relevance(a, question), a.get('published_at') or ''), reverse=True)[:6]
    result = {'kind': 'news_evidence', 'status': 'available' if matches else 'unavailable',
              'question': question, 'season': season, 'sources_checked': tried,
              'errors': errors, 'articles': matches, 'cached': False,
              'note': ('These are attributed external headlines only; they do not prove numeric league statistics, '
                       'player availability, or any article claim. Quote the publisher and provide the link, '
                       'or say the available evidence cannot establish the answer.')}
    if cache and fetcher is None:
        with _LOCK:
            _CACHE[key] = (monotonic(), result)
    return result
