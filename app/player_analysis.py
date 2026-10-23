"""Short, explicitly requested Claude summaries of grounded ESPN player data.

No calls when a player card opens. Per-player/week/stat-snapshot caching prevents
repeat clicks from spending credits for the same analysis.
"""
from __future__ import annotations

from hashlib import sha256
import json
from threading import RLock
from time import monotonic

_SUMMARY_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = RLock()
_TTL = 60 * 60


def _facts(profile):
    return {key: profile.get(key) for key in (
        "name", "nfl_team", "position", "injury_status", "week",
        "season_fantasy_points", "season_fantasy_ppg", "bio",
        "season_stats_source", "season_stats_complete",
        "available_weeks_stat_totals", "stat_coverage_weeks", "current_week",
        "last_completed_game", "recent_games"
    )}


def summarize_player(profile, client, model):
    facts = _facts(profile)
    body = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    cache_key = sha256(f"{model}:{body}".encode()).hexdigest()
    with _CACHE_LOCK:
        cached = _SUMMARY_CACHE.get(cache_key)
        if cached and monotonic() - cached[0] < _TTL:
            return {**cached[1], "cached": True, "input_tokens": 0, "output_tokens": 0,
                    "note": "Cached answer; no new Claude call."}
    prompt = (
        "Write 3 brief sections (Season, Last completed game, This week), totaling 90-140 words. "
        "Summarize this NFL player's fantasy context using ONLY the supplied ESPN data. "
        "Identify their position and NFL team, season fantasy production, last completed "
        "game fantasy points and key actual stats, this week's projection and available projected stats. "
        "If any field is missing, say it isn't available; never make up injury news, "
        "opponents, NFL depth chart, catches, yards, game result, or predicted outcomes. "
        "Don't claim a game is complete unless it is labeled last_completed_game. "
        "Keep it useful for a fantasy manager.\n\nESPN facts: " + body
    )
    response = client.messages.create(model=model, max_tokens=340,
        messages=[{"role": "user", "content": prompt}])
    analysis = "".join(getattr(block, "text", "") for block in response.content if getattr(block, "type", "") == "text").strip()
    if not analysis:
        raise ValueError("Claude returned no player analysis")
    result = {"analysis": analysis, "source": "Claude summary of ESPN factual data",
              "cached": False, "input_tokens": getattr(getattr(response, "usage", None), "input_tokens", 0),
              "output_tokens": getattr(getattr(response, "usage", None), "output_tokens", 0)}
    with _CACHE_LOCK:
        if len(_SUMMARY_CACHE) >= 128:
            _SUMMARY_CACHE.clear()
        _SUMMARY_CACHE[cache_key] = (monotonic(), result)
    return result


def clear_analysis_cache():
    with _CACHE_LOCK:
        _SUMMARY_CACHE.clear()
