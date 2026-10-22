"""Read-only ESPN league intelligence and transparent matchup math.

No Claude calls, private ESPN credentials, or invented win probabilities. An
ESPN projection is a point estimate, *not* a calibrated likelihood of winning.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from collections import defaultdict
from math import isfinite
from threading import RLock
from time import monotonic

_CACHE_LOCK = RLock()
_MATCHUP_CACHE: dict[tuple[int, int], tuple[float, list]] = {}
_MATCHUP_TTL_SECONDS = 90
_PROBABILITY_CACHE: dict[tuple[int, int], tuple[float, dict]] = {}
_OPPONENT_CACHE: dict[tuple[int, int], tuple[float, dict]] = {}



def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        return round(number, 2) if isfinite(number) else None
    except (ValueError, TypeError):
        return None


def _team_id(team):
    if isinstance(team, int):
        return team
    return getattr(team, "team_id", None)


def _team_name(team):
    return getattr(team, "team_name", "Unknown") if team is not None else "BYE"


def _short_team(team):
    return {"id": _team_id(team), "name": _team_name(team)} if team is not None else None


def _week(league, week=None):
    week = week if week is not None else getattr(league, "current_week", 1)
    if not isinstance(week, int) or isinstance(week, bool) or not 1 <= week <= 18:
        raise ValueError("week must be an integer between 1 and 18")
    return week


def _teams(league):
    return list(getattr(league, "teams", []) or [])


def _get_box_scores(league, week):
    key = (id(league), week)
    with _CACHE_LOCK:
        item = _MATCHUP_CACHE.get(key)
        if item and monotonic() - item[0] < (30 if week == _week(league) else _MATCHUP_TTL_SECONDS):
            return item[1]
    # Do not silently cache a failed request as an empty scoreboard.
    scores = list(league.box_scores(week=week))
    with _CACHE_LOCK:
        _MATCHUP_CACHE[key] = (monotonic(), scores)
    return scores


def clear_cache():
    with _CACHE_LOCK:
        _MATCHUP_CACHE.clear()
        _PROBABILITY_CACHE.clear()
        _OPPONENT_CACHE.clear()


def standings(league):
    """Summary is from ESPN's current loaded league object, not extrapolated."""
    return {"teams": [
        {"team_id": _team_id(t), "name": _team_name(t),
         "wins": getattr(t, "wins", None), "losses": getattr(t, "losses", None),
         "ties": getattr(t, "ties", None), "rank": getattr(t, "standing", None),
         "points_for": _num(getattr(t, "points_for", None)),
         "points_against": _num(getattr(t, "points_against", None)),
         "waiver_priority": getattr(t, "waiver_rank", None)}
        for t in sorted(_teams(league), key=lambda t: getattr(t, "standing", 999) or 999)
    ]}


def faab(league):
    settings = getattr(league, "settings", None)
    enabled = bool(getattr(settings, "faab", False))
    budget = _num(getattr(settings, "acquisition_budget", None)) if enabled else None
    return {
        "enabled": enabled, "initial_budget": budget,
        "minimum_bid": getattr(settings, "minimum_bid", None) if enabled else None,
        "teams": [{
            "team_id": _team_id(t), "name": _team_name(t),
            "spent": _num(getattr(t, "acquisition_budget_spent", None)) if enabled else None,
            "remaining": round(max(0, budget - getattr(t, "acquisition_budget_spent", 0)), 2)
                         if enabled and budget is not None and _num(getattr(t, "acquisition_budget_spent", None)) is not None else None,
            "waiver_priority": getattr(t, "waiver_rank", None),
        } for t in _teams(league)],
        "note": "Remaining balance is inferred as initial budget minus recorded spending; check ESPN for trades or budget adjustments." if enabled else "This league does not use FAAB, according to loaded ESPN settings.",
    }


def schedule(league, team_id=None):
    """One matchup per week, in chronological order, no reversed duplicates."""
    index = {_team_id(t): t for t in _teams(league)}
    if team_id is not None and team_id not in index:
        raise ValueError("Unknown team ID")
    rows = []
    seen = set()
    for team in sorted(_teams(league), key=lambda x: _team_id(x) or 0):
        tid = _team_id(team)
        opponents = getattr(team, "schedule", []) or []
        scores = getattr(team, "scores", []) or []
        outcomes = getattr(team, "outcomes", []) or []
        for ix, opponent in enumerate(opponents):
            oppid = _team_id(opponent)
            is_bye = oppid is None or oppid == tid
            if team_id is not None and tid != team_id and oppid != team_id:
                continue
            pair = (tid,) if is_bye else tuple(sorted((tid, oppid)))
            key = (ix + 1, pair)
            if key in seen:
                continue
            seen.add(key)
            # Show the requested team in the first column for filtered schedules.
            first = index[team_id] if team_id in (tid, oppid) else team
            other_id = None if is_bye else (oppid if _team_id(first) == tid else tid)
            second = index.get(other_id) if other_id is not None else None
            first_scores = getattr(first, "scores", []) or []
            second_scores = getattr(second, "scores", []) or [] if second is not None else []
            first_outcomes = getattr(first, "outcomes", []) or []
            rows.append({
                "week": ix + 1, "team_id": _team_id(first), "team": _team_name(first),
                "opponent_id": other_id, "opponent": _team_name(second) if second else None,
                "score": _num(first_scores[ix]) if ix < len(first_scores) else None,
                "opponent_score": _num(second_scores[ix]) if ix < len(second_scores) else None,
                "result": first_outcomes[ix] if ix < len(first_outcomes) else None,
                "bye": is_bye,
            })
    rows.sort(key=lambda row: (row["week"], row["team_id"] or -1, row["opponent_id"] or -1))
    return {"schedule": rows, "weeks": sorted({row["week"] for row in rows})}


def nfl_opponents(league, week=None):
    """ESPN professional schedule mapped by NFL abbreviation. No FantasyPros calls.

    If the league adapter cannot retrieve the schedule, leave opponents unknown
    rather than inventing a matchup or hiding bye weeks.
    """
    week = _week(league, week)
    key = (id(league), week)
    with _CACHE_LOCK:
        cached = _OPPONENT_CACHE.get(key)
        if cached and monotonic() - cached[0] < 600:
            return cached[1]
    from espn_api.football.constant import PRO_TEAM_MAP
    pro_schedule = league._get_pro_schedule(week)
    result = {}
    for pro_id, value in pro_schedule.items():
        opponent_id = value[0]
        own = PRO_TEAM_MAP.get(pro_id)
        opponent = PRO_TEAM_MAP.get(opponent_id)
        if own and opponent:
            result[str(own).upper()] = str(opponent).upper()
    with _CACHE_LOCK:
        _OPPONENT_CACHE[key] = (monotonic(), result)
    return result


def _probability_fraction(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) and 0 <= number <= 1 else None


def espn_win_probabilities(league, week=None):
    """Read *ESPN's* fantasy winProbability, not NFL game-level odds.

    The current espn-api BoxScore discards this raw response field; this one
    authenticated, cached, read-only request uses its own ESPN request client.
    """
    week = _week(league, week)
    if week > _week(league):
        return {"status": "unavailable", "source": "ESPN Fantasy",
                "note": "ESPN live win probability is not available for future weeks.", "teams": {}}
    key = (id(league), week)
    with _CACHE_LOCK:
        cached = _PROBABILITY_CACHE.get(key)
        if cached and monotonic() - cached[0] < (35 if week == _week(league) else _MATCHUP_TTL_SECONDS):
            return cached[1]
    result = {"status": "unavailable", "source": "ESPN Fantasy winProbability",
              "note": "ESPN did not supply a verifiable fantasy matchup win probability.", "teams": {}}
    try:
        matchup_period = getattr(league, "currentMatchupPeriod", week)
        periods = getattr(getattr(league, "settings", None), "matchup_periods", {}) or {}
        for period, scoring_weeks in periods.items():
            if week in scoring_weeks:
                matchup_period = int(period)
                break
        filters = {"schedule": {"filterMatchupPeriodIds": {"value": [matchup_period]}}}
        data = league.espn_request.league_get(
            params={"view": ["mMatchupScore", "mScoreboard"], "scoringPeriodId": week},
            headers={"x-fantasy-filter": json.dumps(filters)},
        )
        teams = {}
        for matchup in data.get("schedule", []):
            sides = []
            for side in ("home", "away"):
                item = matchup.get(side)
                if not isinstance(item, dict):
                    continue
                tid, fraction = item.get("teamId"), _probability_fraction(item.get("winProbability"))
                if tid is not None:
                    sides.append((int(tid), fraction))
            # Never create 0% from a missing field. Only mirror if exactly one
            # side is known and there are exactly two opponents.
            if len(sides) == 2:
                (a, ap), (b, bp) = sides
                if ap is None and bp is not None:
                    ap = round(1 - bp, 6)
                if bp is None and ap is not None:
                    bp = round(1 - ap, 6)
                if ap is not None and bp is not None:
                    teams[a] = round(ap * 100, 2)
                    teams[b] = round(bp * 100, 2)
        if teams:
            result = {"status": "available", "source": "ESPN Fantasy winProbability",
                      "note": "ESPN's live estimated chances; not the NFL teams' win probabilities.",
                      "teams": teams}
    except Exception:
        result["note"] = "ESPN win probability could not be retrieved. Projection margin remains available."
    with _CACHE_LOCK:
        _PROBABILITY_CACHE[key] = (monotonic(), result)
    return result

def _player_row(p):
    slot = getattr(p, "slot_position", None) or getattr(p, "lineupSlot", None)
    position = getattr(p, "position", None)
    return {
        "espn_id": getattr(p, "playerId", None), "name": getattr(p, "name", "Unknown"),
        "position": position, "nfl_team": getattr(p, "proTeam", None),
        "slot": slot, "starter": slot not in ("BE", "IR", "FA", None),
        "projected_points": _num(getattr(p, "projected_points", None)),
        "actual_points": _num(getattr(p, "points", None)),
        "opponent": getattr(p, "pro_opponent", None) if getattr(p, "pro_opponent", None) not in (None, "None", "") else None,
        "injury_status": getattr(p, "injuryStatus", None),
        "on_bye": bool(getattr(p, "on_bye_week", False)),
        "game_played_pct": _num(getattr(p, "game_played", None)),
    }


def _entry(b, side):
    team = getattr(b, f"{side}_team", None)
    if team is None:
        return None
    return {
        "team_id": _team_id(team), "name": _team_name(team),
        "points_scored": _num(getattr(b, f"{side}_score", None)),
        "projected_final": _num(getattr(b, f"{side}_projected", None)),
        "roster": [_player_row(p) for p in (getattr(b, f"{side}_lineup", None) or [])],
    }


def matchups(league, week=None):
    week = _week(league, week)
    if week > _week(league):
        # Current espn-api box_scores silently uses the current week when the
        # requested week is in the future; never relabel today's data as future.
        return {"week": week, "status": "future_projections_unavailable",
                "source": "ESPN box_scores", "matchups": [],
                "note": "Future-week box-score projections aren't available from this ESPN adapter. Use the season schedule instead."}
    scores = _get_box_scores(league, week)
    return {"week": week, "source": "ESPN box_scores",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "matchups": [{"home": _entry(b, "home"), "away": _entry(b, "away"),
                          "playoff": bool(getattr(b, "is_playoff", False))} for b in scores]}


def team_roster(league, team_id, week=None):
    """ESPN owned roster plus week-specific projections, when verifiable."""
    team = next((t for t in _teams(league) if _team_id(t) == team_id), None)
    if team is None:
        raise ValueError("Unknown team ID")
    week = _week(league, week)
    projected_by_id = {}
    projection_status = "unavailable"
    if week <= _week(league):
        try:
            for b in _get_box_scores(league, week):
                if team_id not in (_team_id(getattr(b, "home_team", None)),
                                   _team_id(getattr(b, "away_team", None))):
                    continue
                side = "home" if team_id == _team_id(getattr(b, "home_team", None)) else "away"
                for p in (getattr(b, f"{side}_lineup", None) or []):
                    projected_by_id[getattr(p, "playerId", None)] = _player_row(p)
                projection_status = "espn_box_scores"
                break
        except Exception:
            projection_status = "unavailable"
    try:
        opponent_by_team = nfl_opponents(league, week)
    except Exception:
        opponent_by_team = {}
    return {"team_id": team_id, "name": _team_name(team), "week": week,
            "projection_status": projection_status,
            "roster": [{"espn_id": getattr(p, "playerId", None),
                        "name": getattr(p, "name", None), "position": getattr(p, "position", None),
                        "nfl_team": getattr(p, "proTeam", None),
                        "slot": getattr(p, "lineupSlot", None),
                        "injury_status": getattr(p, "injuryStatus", None),
                        "weekly_slot": projected_by_id.get(getattr(p, "playerId", None), {}).get("slot"),
                        "projected_points": projected_by_id.get(getattr(p, "playerId", None), {}).get("projected_points"),
                        "opponent": (projected_by_id.get(getattr(p, "playerId", None), {}).get("opponent")
                                     or ("BYE" if str(getattr(p, "proTeam", "")).upper() not in opponent_by_team
                                         and opponent_by_team else opponent_by_team.get(str(getattr(p, "proTeam", "")).upper()))),
                        "actual_points": projected_by_id.get(getattr(p, "playerId", None), {}).get("actual_points")}
                       for p in (getattr(team, "roster", None) or [])]}


def transactions(league, limit=12):
    # Recent *completed* transactions, not pending waiver claims.
    activities = league.recent_activity(size=limit)
    rows = []
    for activity in activities:
        actions = []
        for action in (getattr(activity, "actions", []) or []):
            team, verb, player, *rest = action
            actions.append({"team_id": _team_id(team), "team": _team_name(team),
                            "type": verb, "player": getattr(player, "name", str(player)),
                            "bid": _num(rest[0]) if rest else None})
        rows.append({"timestamp_ms": getattr(activity, "date", None), "actions": actions})
    return {"transactions": rows, "status": "completed_only"}


def pending_claims():
    return {"status": "not_supported", "claims": None,
            "reason": "Unprocessed waiver claims and private bids are not reliably exposed by the supported ESPN library; no claims were queried."}


def _eligible(slot, pos):
    if not slot or not pos:
        return False
    slot = slot.upper()
    if slot in {"RB/WR/TE", "FLEX"}:
        return pos in {"RB", "WR", "TE"}
    if slot in {"RB/WR", "WR/RB"}:
        return pos in {"RB", "WR"}
    if slot in {"WR/TE", "TE/WR"}:
        return pos in {"WR", "TE"}
    if slot in {"OP", "SUPERFLEX", "QB/RB/WR/TE"}:
        return pos in {"QB", "RB", "WR", "TE"}
    return slot == pos


def _lineup_upgrades(roster, limit=5):
    benches = [p for p in roster if p["slot"] == "BE" and p["projected_points"] is not None
               and not p["on_bye"] and (p["injury_status"] or "").upper() not in {"OUT", "IR", "INJURY_RESERVE", "SUSPENSION"}]
    starters = [p for p in roster if p["starter"] and p["projected_points"] is not None]
    alternatives = []
    for bench in benches:
        for starter in starters:
            if (not _eligible(starter["slot"], bench["position"]) or
                    bench["projected_points"] <= starter["projected_points"]):
                continue
            # Don't call these guaranteed swaps: only ESPN can verify lock state.
            if starter["game_played_pct"] is not None and starter["game_played_pct"] >= 100:
                continue
            alternatives.append({"start": bench["name"], "sit": starter["name"],
                                 "slot": starter["slot"],
                                 "projected_gain": round(bench["projected_points"] - starter["projected_points"], 2),
                                 "needs_lock_check": True})
    alternatives.sort(key=lambda x: x["projected_gain"], reverse=True)
    return alternatives[:limit]


def matchup_strategy(league, my_team_id, week=None):
    """Explain score-to-beat without implying certainty or making up probabilities."""
    current = matchups(league, week)
    if current.get("status") == "future_projections_unavailable":
        return {"week": current["week"], "status": current["status"],
                "note": current["note"]}
    row = next((m for m in current["matchups"] if
                (m["home"] or {}).get("team_id") == my_team_id or
                (m["away"] or {}).get("team_id") == my_team_id), None)
    if row is None:
        return {"week": current["week"], "status": "not_found", "source": current["source"],
                "note": "No ESPN matchup found for this team in this week."}
    mine = row["home"] if (row["home"] or {}).get("team_id") == my_team_id else row["away"]
    other = row["away"] if mine is row["home"] else row["home"]
    if other is None:
        return {"week": current["week"], "status": "bye", "my_team": mine,
                "opponent": None, "note": "No head-to-head opponent this week."}
    mine_proj, opp_proj = mine["projected_final"], other["projected_final"]
    delta = round(mine_proj - opp_proj, 2) if mine_proj is not None and opp_proj is not None else None
    target = round(opp_proj + 0.01, 2) if opp_proj is not None else None
    gap = round(max(0, target - mine_proj), 2) if target is not None and mine_proj is not None else None
    upgrades = _lineup_upgrades(mine["roster"])
    try:
        opponent_by_team = nfl_opponents(league, current["week"])
    except Exception:
        opponent_by_team = {}
    for side in (mine, other):
        for player in side["roster"]:
            if not player.get("opponent"):
                team = str(player.get("nfl_team") or "").upper()
                player["opponent"] = opponent_by_team.get(team)
                if not player["opponent"] and team and opponent_by_team:
                    player["opponent"] = "BYE"
    from matchup_live import enrich_live_players, head_to_head, nfl_game_statuses
    game_info = nfl_game_statuses(league, current["week"])
    enrich_live_players(mine, other, game_info)
    paired_starters = head_to_head(mine, other)
    probabilities = espn_win_probabilities(league, current["week"])
    my_probability = probabilities["teams"].get(my_team_id)
    opponent_probability = probabilities["teams"].get(other["team_id"])
    return {
        "week": current["week"], "status": "available", "as_of": current["as_of"],
        "head_to_head": paired_starters,
        "live_status": game_info["status"], "live_source": game_info["source"],
        "live_as_of": game_info["as_of"], "live_note": game_info["note"],
        "projection_source": "ESPN weekly box-score projected final totals",
        "my_team": mine, "opponent": other,
        "projected_margin": delta,
        "espn_win_probability_pct": my_probability,
        "espn_opponent_win_probability_pct": opponent_probability,
        "win_probability_status": "available" if my_probability is not None else "unavailable",
        "win_probability_source": probabilities["source"],
        "win_probability_note": probabilities["note"],
        "projected_favorite": "me" if delta is not None and delta > 0 else
                              "opponent" if delta is not None and delta < 0 else
                              "tied" if delta == 0 else "unknown",
        "target_to_exceed_opponent_projection": target,
        "extra_points_above_my_projection_to_take_lead": gap,
        "possible_lineup_upgrades": upgrades,
        "notes": [
            "Projection margin is not a win probability. ESPN projections may change with injuries and live scoring.",
            "The extra-points target assumes the opponent finishes at their current projection; it changes if they over- or underperform.",
            "Lineup upgrades compare ESPN projections only, are alternative single-player swaps, and require checking NFL kickoff and ESPN lineup locks.",
            "Projected lineup gains are not guaranteed points; don't add mutually exclusive swaps together without reoptimizing.",
        ],
    }
