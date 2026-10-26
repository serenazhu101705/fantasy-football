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
        "season_stats_source", "season_stats_complete", "season_stats_as_of_week",
        "available_weeks_stat_totals", "stat_coverage_weeks", "current_week",
        "last_completed_game", "recent_games"
    )}


def summarize_player(profile, client, model, *, league=None):
    # The extra context comes from a verified team schedule, not Claude guessing.
    from analyst_context import analyst_context
    facts = _facts(profile)
    facts['schedule_interpretation'] = analyst_context(profile, league=league)
    body = json.dumps(facts, sort_keys=True, separators=(",", ":"), default=str)
    cache_key = sha256(f"analyst-v1:{model}:{body}".encode()).hexdigest()
    with _CACHE_LOCK:
        cached = _SUMMARY_CACHE.get(cache_key)
        if cached and monotonic() - cached[0] < _TTL:
            return {**cached[1], "cached": True, "input_tokens": 0, "output_tokens": 0,
                    "note": "Cached analyst report; no new Claude call."}
    prompt = (
        "You are an evidence-first fantasy football analyst writing for a full-PPR manager. "
        "Analyze instead of listing statistics. Use only the supplied factual input. "
        "Write 4 compact sections with headings: Season Outlook, Last Game Analysis, "
        "Next Game Preview, Fantasy Verdict. Aim for 220-320 words, with clear takeaways. "
        "SEASON: explain trajectory, consistency, workload evidence, and whether scoring "
        "seems sustainable; use recent completed games without counting BYE weeks as games. "
        "LAST GAME: explain why performance exceeded/fell short of projection and what "
        "matters for next week; avoid making unsupported causal claims. "
        "NEXT GAME: discuss verified opponent and projection if actually upcoming; "
        "otherwise say opponent/projection are unavailable. Provide a reasoned qualitative "
        "range of outcomes, not fabricated numerical predictions. For RB/WR, emphasize "
        "snap share, carries/routes, targets, red zone and backfield/receiver competition "
        "ONLY where verified inputs support them. Never invent expected snap share. "
        "VERDICT: concise start/flex/bench/watch recommendation, explaining confidence and "
        "major risk. Do not treat a bye week as zero performance, missed game, injury, or "
        "unavailable stats. No speculative injury reports, opponent defense rankings, "
        "future TDs, target shares or coaching decisions. If verified information is "
        "insufficient, explicitly state what evidence would change your take. "
        "Do not reproduce long stat lines. Use 2-4 key numbers at most per section. "
        "Input: " + body
    )
    params = {"model": model, "max_tokens": 1100,
              "messages": [{"role": "user", "content": prompt}]}
    if str(model).startswith("claude-sonnet-5-5"):
        params["thinking"] = {"type": "between_tools"}
    response = client.messages.create(**params)
    analysis = "".join(getattr(block, "text", "") for block in response.content
                       if getattr(block, "type", "") == "text").strip()
    if not analysis:
        stop_reason = str(getattr(response, "stop_reason", "unknown"))
        raise ValueError(f"Claude returned no visible player analysis (stop_reason={stop_reason}).")
    result = {"analysis": analysis, "source": "Claude analyst commentary grounded in ESPN and verified team schedule",
              "cached": False,
              "input_tokens": getattr(getattr(response, "usage", None), "input_tokens", 0),
              "output_tokens": getattr(getattr(response, "usage", None), "output_tokens", 0)}
    with _CACHE_LOCK:
        if len(_SUMMARY_CACHE) >= 128:
            _SUMMARY_CACHE.clear()
        _SUMMARY_CACHE[cache_key] = (monotonic(), result)
    return result


def clear_analysis_cache():
    with _CACHE_LOCK:
        _SUMMARY_CACHE.clear()
