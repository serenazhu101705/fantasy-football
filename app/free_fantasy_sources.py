"""Free fallback fantasy-football data sources.

This module intentionally keeps these sources separate from FantasyPros:

- FantasyCalc: market-derived redraft value/rank from real trades. This is NOT
  expert consensus ECR and is NOT a weekly projection.
- Sleeper: free player metadata including injury/practice/depth-chart fields.
  This is NOT a projection/ranking provider.

The goal is graceful degradation when FantasyPros is rate-limited or missing a
player, without pretending that a fallback signal is the same type of evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math
import re
import time
import unicodedata
from urllib.parse import urlencode
from urllib.request import Request, urlopen


CACHE_DIR = Path("data/cache/free_sources")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FANTASYCALC_URL = "https://api.fantasycalc.com/values/current"
SLEEPER_PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"

FANTASYCALC_TTL_SECONDS = 3 * 60 * 60
SLEEPER_TTL_SECONDS = 24 * 60 * 60
HTTP_TIMEOUT_SECONDS = 12

_MEMORY = {}


def normalize_name(value):
    if value is None:
        return ""

    name = unicodedata.normalize("NFKD", str(value))
    name = "".join(
        character
        for character in name
        if not unicodedata.combining(character)
    )
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", " ", name)
    return " ".join(name.split())


def _id_key(value):
    if value is None:
        return None
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        value = str(value).strip()
        return value or None


def _safe_number(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number.is_integer():
        return int(number)
    return round(number, 3)


def _cache_age_seconds(path):
    if not path.exists():
        return None
    return max(0.0, time.time() - path.stat().st_mtime)


def _read_json(path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def _write_json(path, value):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(value, handle)
        tmp.replace(path)
    except Exception:
        pass


def _fetch_json(url, params=None):
    if params:
        url = f"{url}?{urlencode(params)}"

    request = Request(
        url,
        headers={
            "User-Agent": "fantasy-football-advisor/1.0",
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        payload = response.read().decode("utf-8")
        return json.loads(payload)


def _load_with_cache(cache_key, path, ttl_seconds, fetcher):
    """Return (data, metadata), preferring fresh cache then network then stale."""

    if cache_key in _MEMORY:
        return _MEMORY[cache_key]

    age = _cache_age_seconds(path)
    cached = _read_json(path) if path.exists() else None

    if cached is not None and age is not None and age < ttl_seconds:
        result = (
            cached,
            {
                "cache_status": "FRESH_CACHE",
                "cache_age_hours": round(age / 3600.0, 2),
            },
        )
        _MEMORY[cache_key] = result
        return result

    try:
        fresh = fetcher()
        _write_json(path, fresh)
        result = (
            fresh,
            {
                "cache_status": "LIVE",
                "cache_age_hours": 0.0,
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
        _MEMORY[cache_key] = result
        return result
    except Exception as exc:
        if cached is not None:
            stale_age = _cache_age_seconds(path)
            result = (
                cached,
                {
                    "cache_status": "STALE_CACHE",
                    "cache_age_hours": (
                        round(stale_age / 3600.0, 2)
                        if stale_age is not None
                        else None
                    ),
                    "live_error": str(exc),
                },
            )
            _MEMORY[cache_key] = result
            return result

        result = (
            None,
            {
                "cache_status": "UNAVAILABLE",
                "live_error": str(exc),
            },
        )
        _MEMORY[cache_key] = result
        return result


def get_fantasycalc_player(
    player,
    *,
    num_teams=12,
    num_qbs=1,
    ppr=1.0,
):
    """Return FantasyCalc redraft market data for one player.

    FantasyCalc is market-derived. Never relabel these fields as FantasyPros ECR
    or as a weekly projection.
    """

    ppr_param = int(ppr) if float(ppr).is_integer() else float(ppr)
    params = {
        "isDynasty": "false",
        "numQbs": int(num_qbs),
        "numTeams": int(num_teams),
        "ppr": ppr_param,
    }

    cache_name = (
        f"fantasycalc_redraft_{int(num_teams)}t_"
        f"{int(num_qbs)}qb_ppr{str(ppr_param).replace('.', '_')}.json"
    )
    path = CACHE_DIR / cache_name
    cache_key = ("fantasycalc", tuple(sorted(params.items())))

    data, metadata = _load_with_cache(
        cache_key,
        path,
        FANTASYCALC_TTL_SECONDS,
        lambda: _fetch_json(FANTASYCALC_URL, params=params),
    )

    if not isinstance(data, list):
        return {
            "available": False,
            "source": "FantasyCalc",
            **metadata,
        }

    wanted_espn = _id_key(player.get("espn_id"))
    wanted_name = normalize_name(player.get("name"))
    wanted_position = str(player.get("position") or "").upper()

    match = None

    if wanted_espn:
        for row in data:
            info = row.get("player") or {}
            if _id_key(info.get("espnId")) == wanted_espn:
                match = row
                break

    if match is None and wanted_name:
        candidates = []
        for row in data:
            info = row.get("player") or {}
            if normalize_name(info.get("name")) != wanted_name:
                continue
            position = str(info.get("position") or "").upper()
            if wanted_position and position and position != wanted_position:
                continue
            candidates.append(row)

        if len(candidates) == 1:
            match = candidates[0]

    if match is None:
        return {
            "available": False,
            "source": "FantasyCalc",
            "reason": "player_not_found",
            **metadata,
        }

    info = match.get("player") or {}
    return {
        "available": True,
        "source": "FantasyCalc",
        "signal_type": "REDRAFT_MARKET_VALUE",
        "name": info.get("name"),
        "team": info.get("maybeTeam"),
        "position": info.get("position"),
        "espn_id": _id_key(info.get("espnId")),
        "sleeper_id": _id_key(info.get("sleeperId")),
        "value": _safe_number(match.get("value")),
        "redraft_value": _safe_number(match.get("redraftValue")),
        "overall_rank": _safe_number(match.get("overallRank")),
        "position_rank": _safe_number(match.get("positionRank")),
        "trend_30_day": _safe_number(match.get("trend30Day")),
        "note": "Market-derived redraft signal; not ECR and not a projection.",
        **metadata,
    }


def get_sleeper_player(player):
    """Return free Sleeper player/injury/practice metadata for one player."""

    position = str(player.get("position") or "").upper()
    if position == "D/ST":
        position = "DEF"

    params = {"active": "true"}
    if position in {"QB", "RB", "WR", "TE", "K"}:
        params["position"] = position

    cache_suffix = position if "position" in params else "all"
    path = CACHE_DIR / f"sleeper_players_{cache_suffix}.json"
    cache_key = ("sleeper_players", cache_suffix)

    data, metadata = _load_with_cache(
        cache_key,
        path,
        SLEEPER_TTL_SECONDS,
        lambda: _fetch_json(SLEEPER_PLAYERS_URL, params=params),
    )

    if not isinstance(data, dict):
        return {
            "available": False,
            "source": "Sleeper",
            **metadata,
        }

    wanted_espn = _id_key(player.get("espn_id"))
    wanted_name = normalize_name(player.get("name"))
    wanted_team = str(player.get("nfl_team") or "").upper()

    match_id = None
    match = None

    if wanted_espn:
        for sleeper_id, row in data.items():
            if _id_key(row.get("espn_id")) == wanted_espn:
                match_id = sleeper_id
                match = row
                break

    if match is None and wanted_name:
        candidates = []
        for sleeper_id, row in data.items():
            full_name = row.get("full_name") or " ".join(
                part
                for part in (row.get("first_name"), row.get("last_name"))
                if part
            )
            if normalize_name(full_name) != wanted_name:
                continue

            row_team = str(row.get("team") or "").upper()
            if wanted_team and row_team and row_team != wanted_team:
                continue
            candidates.append((sleeper_id, row))

        if len(candidates) == 1:
            match_id, match = candidates[0]

    if match is None:
        return {
            "available": False,
            "source": "Sleeper",
            "reason": "player_not_found",
            **metadata,
        }

    full_name = match.get("full_name") or " ".join(
        part
        for part in (match.get("first_name"), match.get("last_name"))
        if part
    )

    return {
        "available": True,
        "source": "Sleeper",
        "signal_type": "PLAYER_STATUS_METADATA",
        "sleeper_id": _id_key(match_id),
        "espn_id": _id_key(match.get("espn_id")),
        "name": full_name or None,
        "team": match.get("team"),
        "status": match.get("status"),
        "injury_status": match.get("injury_status"),
        "injury_body_part": match.get("injury_body_part"),
        "injury_notes": match.get("injury_notes"),
        "injury_start_date": match.get("injury_start_date"),
        "practice_participation": match.get("practice_participation"),
        "depth_chart_order": _safe_number(match.get("depth_chart_order")),
        "depth_chart_position": match.get("depth_chart_position"),
        "search_rank": _safe_number(match.get("search_rank")),
        "note": "Status/injury/depth-chart metadata; not a projection or ECR.",
        **metadata,
    }


def get_free_fallback_signals(
    player,
    *,
    num_teams=12,
    num_qbs=1,
    ppr=1.0,
):
    """Return both free fallback sources in one compact structure."""

    return {
        "fantasycalc": get_fantasycalc_player(
            player,
            num_teams=num_teams,
            num_qbs=num_qbs,
            ppr=ppr,
        ),
        "sleeper": get_sleeper_player(player),
    }
