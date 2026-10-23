"""Post-week fantasy matchup recap, never generated before NFL week is final.

Polling the status is free. An explicitly requested recap uses one Claude
call and is saved to a local file so re-opening a completed week is free.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import RLock
import json
import os


class RecapEmptyResponseError(RuntimeError):
    """Provider returned no user-visible recap; no result should be cached."""

    def __init__(self, stop_reason: str, block_types: list[str]):
        self.stop_reason = stop_reason
        self.block_types = block_types
        super().__init__(f"Claude returned no visible weekly recap (stop_reason={stop_reason}).")

_LOCK = RLock()
_DIR = Path.home() / ".cache" / "fantasy_coach" / "weekly_recaps"


def _file(season, week):
    return _DIR / f"recap_{int(season)}_{int(week):02d}.json"


def recap_eligibility(game_info, *, expected_min_games=10):
    """Do not mistake a truncated ESPN response for a finished NFL week."""
    if game_info.get("status") != "available":
        return "game_status_unavailable", 0
    games = game_info.get("games") or {}
    # Each NFL event appears once per NFL team in game_info.
    states = [v.get("state") for v in games.values()]
    count = len(states) // 2
    if count < expected_min_games or len(states) != 2 * count:
        return "schedule_incomplete", count
    if any(state != "FINAL" for state in states):
        return "games_in_progress", count
    return "ready", count


def recap_status(league, team_id, week=None, *, game_info=None, strategy=None, recap_dir=None):
    from league_intelligence import _week, matchup_strategy
    from matchup_live import nfl_game_statuses
    week = _week(league, week)
    season = int(getattr(league, "year", 0) or getattr(league, "season", 0) or 2026)
    data = game_info if game_info is not None else nfl_game_statuses(league, week)
    status, count = recap_eligibility(data)
    key = (recap_dir or _DIR) / f"recap_{season}_{week:02d}.json"
    cached = key.is_file()
    if cached:
        return {"week": week, "season": season, "status": "ready", "cached": True,
                "nfl_games_seen": count, "note": "A previous final-week recap is saved locally."}
    if status != "ready":
        return {"week": week, "season": season, "status": status,
                "nfl_games_seen": count, "cached": cached,
                "note": "The recap unlocks after ESPN confirms every NFL game for this week is final."}
    if strategy is None:
        strategy = matchup_strategy(league, team_id, week)
    if strategy.get("status") != "available":
        return {"week": week, "season": season, "status": "matchup_unavailable", "cached": cached,
                "note": "Your fantasy matchup has not loaded."}
    if (strategy.get("my_team") or {}).get("points_scored") is None or (strategy.get("opponent") or {}).get("points_scored") is None:
        return {"week": week, "season": season, "status": "fantasy_scores_pending", "cached": cached,
                "note": "NFL games are final, but ESPN Fantasy has not finalized both teams' scores."}
    return {"week": week, "season": season, "status": "ready", "cached": cached,
            "nfl_games_seen": count, "note": "All NFL games final. Ready for a Week recap."}


def _facts(strategy):
    mine, other = strategy["my_team"], strategy["opponent"]
    def players(side):
        return sorted(({"name": p.get("name"), "slot": p.get("slot"),
                        "actual": p.get("actual_points"), "projected_current": p.get("projected_points"),
                        "starter": bool(p.get("starter"))}
                       for p in (side.get("roster") or []) if p.get("name")),
                      key=lambda p: (not p["starter"], -float(p["actual"] or 0)))[:22]
    return {"week": strategy["week"], "my_team": mine["name"], "opponent": other["name"],
            "my_final": mine["points_scored"], "opponent_final": other["points_scored"],
            "my_players": players(mine), "opponent_players": players(other),
            "note": "Current ESPN projections are not guaranteed to be saved pregame projections. Do not claim hindsight lineup changes were feasible or locked players could be swapped."}


def generate_weekly_recap(league, team_id, client, model, week=None, *, status_fn=recap_status,
                          strategy_fn=None, recap_dir=None):
    from league_intelligence import matchup_strategy
    gate = status_fn(league, team_id, week, recap_dir=recap_dir)
    if gate["status"] != "ready":
        raise ValueError(gate["note"])
    season, week = gate["season"], gate["week"]
    destination = (recap_dir or _DIR) / f"recap_{season}_{week:02d}.json"
    with _LOCK:
        if destination.is_file():
            cached = json.loads(destination.read_text(encoding="utf-8"))
            return {**cached, "cached": True, "input_tokens": 0, "output_tokens": 0}
        strategy = (strategy_fn or matchup_strategy)(league, team_id, week)
        facts = _facts(strategy)
        prompt = (
            "Write a concise weekly fantasy football recap for this manager, ~200-300 words. "
            "Include the result (win/loss/margin), important starter contributions, "
            "players whose actual points materially differed from their currently reported projections ONLY "
            "if you explicitly identify those projections as current snapshots (not historical pregame), "
            "bench performance where useful, and 2 actionable next-week takeaways. "
            "Use ONLY supplied ESPN facts. No invented pregame predictions, news, injury statuses, "
            "start/sit regret that assumes swaps were possible, or game statistics not in the facts. "
            "If a player score is missing don't treat it as zero.\nESPN final matchup facts: "
            + json.dumps(facts, separators=(",", ":"))
        )
        # Sonnet 5.5 otherwise defaults to adaptive thinking. A small response
        # budget can be consumed by hidden thinking without any recap text.
        # This is a straightforward summary of supplied facts, so request no
        # up-front thinking and allow sufficient room for a 200-300 word reply.
        # Never retry automatically: every Claude attempt can incur charges.
        kwargs = {
            "model": model,
            "max_tokens": 1400,
            "messages": [{"role": "user", "content": prompt}],
        }
        if str(model).startswith("claude-sonnet-5-5"):
            kwargs["thinking"] = {"type": "between_tools"}
            kwargs["output_config"] = {"effort": "low"}
        response = client.messages.create(**kwargs)
        blocks = getattr(response, "content", []) or []
        narrative = "".join(
            str(getattr(block, "text", "") or "")
            for block in blocks if getattr(block, "type", "") == "text"
        ).strip()
        if not narrative:
            stop_reason = str(getattr(response, "stop_reason", "unknown"))
            block_types = [str(getattr(block, "type", "unknown")) for block in blocks]
            if os.getenv("FANTASY_DEBUG") == "1":
                print(f"[WEEKLY RECAP] empty_response stop_reason={stop_reason} "
                      f"content_types={block_types} model={model} "
                      f"output_tokens={getattr(getattr(response, 'usage', None), 'output_tokens', 'n/a')}")
            raise RecapEmptyResponseError(stop_reason, block_types)
        result = {"season": season, "week": week, "analysis": narrative,
                  "source": "Claude analysis of finalized ESPN fantasy matchup",
                  "generated_at": datetime.now(timezone.utc).isoformat(),
                  "cached": False,
                  "input_tokens": getattr(getattr(response, "usage", None), "input_tokens", 0),
                  "output_tokens": getattr(getattr(response, "usage", None), "output_tokens", 0)}
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
        temporary.replace(destination)
        return result
