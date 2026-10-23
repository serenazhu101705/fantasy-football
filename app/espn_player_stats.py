"""Read-only ESPN public NFL season statistics and game logs.

Unlike espn-api roster ``Player.stats`` (which may have only one loaded week),
ESPN's public athlete stats endpoint exposes season totals. Each response is
cached per player/season. This is factual NFL data, never fantasy scoring.
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from time import monotonic
from urllib.request import Request, urlopen
import json
import re

BASE = "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes"
_CACHE = {}
_LOCK = RLock()
_TTL = 1800
_PRIMARY = {
    "passing": {"GP", "CMP", "ATT", "YDS", "TD", "INT", "PCT", "QBR", "RTG"},
    "rushing": {"GP", "CAR", "ATT", "YDS", "AVG", "TD", "LNG"},
    "receiving": {"GP", "REC", "TGT", "TGTS", "YDS", "AVG", "TD", "LNG", "YAC"},
    "kicking": {"FG", "FGA", "FG%", "XPM", "XPA", "XP%", "PTS"},
    "defensive": {"SACK", "INT", "FR", "FF", "TD", "TACK"},
}
_LABELS = {
    "GP": "Games played", "GS": "Games started", "CMP": "Completions",
    "ATT": "Attempts", "CAR": "Carries", "YDS": "Yards", "TD": "Touchdowns",
    "INT": "Interceptions", "REC": "Receptions", "TGT": "Targets", "TGTS": "Targets",
    "AVG": "Yards per attempt", "YAC": "Yards after catch", "LNG": "Longest gain",
    "FG": "Field goals made", "FGA": "Field goals attempted", "XPM": "Extra points made",
    "XPA": "Extra points attempted", "FR": "Fumble recoveries", "FF": "Forced fumbles",
    "SACK": "Sacks", "PCT": "Completion percentage", "RTG": "Passer rating",
}


def _download(url):
    req = Request(url, headers={"User-Agent": "FantasyCoach/1.0 (local stats)", "Accept": "application/json"})
    with urlopen(req, timeout=4) as response:
        return json.load(response)


def _val(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip().rstrip("%")
    try:
        return round(float(value), 2)
    except (ValueError, TypeError):
        return None


def _stat_label(code, category, glossary):
    name = (glossary.get(code) or _LABELS.get(code) or code).strip()
    context = category.lower()
    if code == "ATT":
        return "Pass attempts" if "pass" in context else "Carries" if "rush" in context else "Attempts"
    if code in {"YDS", "TD", "AVG", "LNG", "INT"}:
        prefix = "Passing" if "pass" in context else "Rushing" if "rush" in context else "Receiving" if "receiv" in context else ""
        return f"{prefix} {name.lower()}".strip().capitalize()
    return name


def parse_season_stats(data):
    """ESPN categories[].labels/totals are parallel arrays, not raw fantasy IDs."""
    if not isinstance(data, dict):
        return []
    glossary = {str(v.get("abbreviation") or "").upper(): str(v.get("displayName") or "")
                for v in data.get("glossary", []) if isinstance(v, dict)}
    rows = []
    for cat in data.get("categories", []):
        if not isinstance(cat, dict):
            continue
        name = str(cat.get("name") or cat.get("displayName") or "")
        labels, totals = cat.get("labels") or [], cat.get("totals") or []
        # Never use averages when the view claims to show season totals.
        for code, value in zip(labels, totals):
            abbreviation = str(code).upper().strip()
            number = _val(value)
            if not abbreviation or number is None:
                continue
            if len(rows) >= 36:
                break
            if abbreviation in {"GP", "GS"} and any(r["key"].endswith(":GP") for r in rows):
                continue
            key = f"{name.lower()}:{abbreviation}"
            rows.append({"key": key, "label": _stat_label(abbreviation, name, glossary),
                         "value": number, "category": name})
    return rows


def parse_gamelog(data):
    if not isinstance(data, dict):
        return []
    events = data.get("events") or {}
    if not isinstance(events, dict):
        events = {}
    games = []
    seen = set()
    global_labels = data.get("labels") or []
    for season_type in data.get("seasonTypes", []):
        text = str(season_type.get("displayName") or "").lower()
        if text and "regular" not in text:
            continue
        for cat in season_type.get("categories", []):
            labels = cat.get("labels") or global_labels
            for row in cat.get("events", []):
                event_id = str(row.get("eventId") or "")
                if not event_id:
                    continue
                event = events.get(event_id, {})
                # ESPN labels might be a flat list or category-local labels.
                stats = []
                for name, amount in zip(labels, row.get("stats") or []):
                    number = _val(amount)
                    if number is not None:
                        stats.append({"key": str(name), "label": _stat_label(str(name).upper(), cat.get("displayName") or cat.get("name") or "", {}), "value": number})
                if event_id in seen:
                    next(g for g in games if g["event_id"] == event_id)["stats"].extend(stats)
                    continue
                # A game result indicates a played game; no future fixtures in last-game summaries.
                if not event.get("gameResult") and not event.get("completed"):
                    continue
                seen.add(event_id)
                opponent = event.get("opponent") or {}
                games.append({"event_id": event_id, "game_date": event.get("gameDate") or event.get("date"),
                              "opponent": opponent.get("abbreviation") if isinstance(opponent, dict) else None,
                              "stats": stats, "fantasy_points": None, "source": "ESPN NFL gamelog"})
    games.sort(key=lambda x: x.get("game_date") or "", reverse=True)
    return games[:8]


def public_player_data(player_id, season, fetcher=None):
    """Best-effort independent endpoints; both may be unavailable.

    A supplied fetcher is for offline tests, never cached. Live requests are
    per-player and per-season cached for 30 minutes, including failed queries.
    """
    if not isinstance(player_id, int) or player_id <= 0 or not 2000 <= int(season) <= 2100:
        return {"season_stats": [], "recent_games": [], "stats_source": "unavailable"}
    key = (player_id, int(season))
    with _LOCK:
        item = _CACHE.get(key)
        if fetcher is None and item and monotonic() - item[0] < _TTL:
            return item[1]
    fn = fetcher or _download
    base = f"{BASE}/{player_id}"
    stats = []
    games = []
    for suffix, parse in ((f"stats?season={int(season)}&seasontype=2", parse_season_stats),
                          (f"gamelog?season={int(season)}", parse_gamelog)):
        try:
            result = parse(fn(f"{base}/{suffix}"))
        except Exception:
            result = []
        if suffix.startswith("stats"):
            stats = result
        else:
            games = result
    result = {"season_stats": stats, "recent_games": games,
              "stats_source": "ESPN NFL public season totals" if stats else "unavailable"}
    if fetcher is None:
        with _LOCK:
            if len(_CACHE) > 400:
                _CACHE.clear()
            _CACHE[key] = (monotonic(), result)
    return result


def clear_public_player_cache():
    with _LOCK:
        _CACHE.clear()
