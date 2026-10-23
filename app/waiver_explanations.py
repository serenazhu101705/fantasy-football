"""Explain how an available waiver player fits the *current* ESPN team.

Read-only, deterministic, source-attributed summaries. Transaction suggestions
are exclusively from the existing waiver evaluator; we never invent a drop or
claim a projection gain based on incomplete lineup coverage.
"""
from __future__ import annotations

import math
import re

ACTIONABLE = {"STRONG_ADD", "ADD", "STASH", "STREAMER"}
CATEGORIES = {
    "STRONG_ADD": "Strong add",
    "ADD": "Recommended add",
    "STASH": "Long-term stash",
    "STREAMER": "Weekly streamer",
}
POSITIONS = {"ALL", "QB", "RB", "WR", "TE", "K", "D/ST"}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def _key(player):
    if not isinstance(player, dict):
        return ""
    pid = player.get("espn_id")
    if pid is not None:
        return "id:" + str(pid)
    return "name:" + re.sub(r"[^a-z0-9]", "", str(player.get("name") or "").lower())


def _owned(context):
    roster = context.get("roster") or []
    ids = {str(p.get("espn_id")) for p in roster if p.get("espn_id") is not None}
    names = {re.sub(r"[^a-z0-9]", "", str(p.get("name") or "").lower()) for p in roster}
    return ids, names


def _available(context, p):
    if not isinstance(p, dict) or not p.get("name") or p.get("rostered") is True:
        return False
    ids, names = _owned(context)
    pid = p.get("espn_id")
    slug = re.sub(r"[^a-z0-9]", "", str(p.get("name") or "").lower())
    return (pid is None or str(pid) not in ids) and slug not in names


def _best_transactions(context):
    """Only vetted positive moves, keyed by player identity, honoring evaluator rank."""
    output = {}
    for row in context.get("waiver_transactions") or []:
        if not isinstance(row, dict) or row.get("recommendation") not in ACTIONABLE:
            continue
        candidate = row.get("add") or {}
        drop = row.get("drop") or {}
        if not _available(context, candidate):
            continue
        # A proposed drop must still be on the user's current roster.
        roster_keys = {_key(p) for p in context.get("roster") or []}
        if not drop or _key(drop) not in roster_keys:
            continue
        output.setdefault(_key(candidate), row)
    return output


def _projection_sources(player):
    src = {}
    espn = _number(player.get("espn_projected_points"))
    if espn is None:
        espn = _number(player.get("espn_projection"))
    fp = _number(player.get("fp_points"))
    if fp is None:
        fp = _number(player.get("fantasypros_projection"))
    if fp is None:
        fp = _number(player.get("fp_ppr"))
    if espn is not None:
        src["ESPN"] = round(espn, 2)
    if fp is not None:
        src["FantasyPros"] = round(fp, 2)
    return src


def _roster_fit(context, position):
    count = sum(p.get("position") == position for p in context.get("roster") or [])
    if count == 0:
        return f"You currently have no {position} on the loaded roster; this would fill a positional gap."
    if position in ("QB", "TE", "K", "D/ST"):
        if count == 1:
            return f"You have 1 {position}; adding another would provide an alternative for matchups, injuries or byes."
        return f"You already have {count} {position} players; adding one would compete for a roster spot rather than fill an empty position."
    if position in ("RB", "WR"):
        if count <= 2:
            return f"You have {count} {position}s; adding another would strengthen depth behind your required starters."
        return f"You have {count} {position}s; this pickup would compete for a starting/FLEX role or improve bench depth."
    return "Position-specific roster fit is unavailable."


def _short_term(p, move, week):
    sources = _projection_sources(p)
    wk = f"Week {week}" if week is not None else "This week"
    if p.get("espn_on_bye") or p.get("on_bye_week"):
        return f"{wk}: on bye, so no immediate game contribution is expected.", sources
    injury = str(p.get("injury_status") or "").upper()
    if injury and injury not in {"ACTIVE", "HEALTHY", "NORMAL", "NONE"}:
        risk = f" Listed as {injury}; verify availability before claiming."
    elif (p.get("availability") or {}).get("can_play_this_week") is False:
        risk = " Not cleared to play this week; treat as a stash."
    else:
        risk = ""
    if sources:
        detail = " and ".join(f"{provider} {value:.1f}" for provider, value in sources.items())
        message = f"{wk} projected fantasy points: {detail}." + risk
    else:
        message = f"No verified {wk.lower()} fantasy-point projection was loaded for this player." + risk
    if move is not None:
        before = move.get("before_lineup") or {}
        after = move.get("after_lineup") or {}
        delta = _number(move.get("projected_points_delta"))
        # The optimizer uses zero for missing projections. Don't present a
        # computed delta as a measured fantasy-point gain unless both lineups
        # are fully covered by provider projections.
        if delta is not None and before.get("projection_complete") is True and after.get("projection_complete") is True:
            sign = "+" if delta > 0 else ""
            message += f" Evaluated add/drop changes the full projected lineup by {sign}{delta:.1f} points."
        elif delta is not None:
            message += " Exact lineup-point impact isn't verified because at least one starter lacks a complete projection."
    return message, sources


def _roster_comparison(context, player, sources):
    """Real ESPN-vs-ESPN same-position comparison, NOT a lineup delta."""
    pos = player.get("position")
    candidate_espn = sources.get("ESPN")
    comparison = []
    for current in context.get("roster") or []:
        if current.get("position") != pos:
            continue
        projection = _projection_sources(current).get("ESPN")
        if projection is not None:
            comparison.append((projection, str(current.get("name") or "a rostered player")))
    if candidate_espn is None or not comparison:
        return "An apples-to-apples ESPN projection comparison with your roster is unavailable."
    bottom_pts, bottom_name = min(comparison)
    delta = candidate_espn - bottom_pts
    if abs(delta) < 0.05:
        preface = f"ESPN projects {player.get('name')} about the same as {bottom_name} ({bottom_pts:.1f} points)."
    elif delta > 0:
        preface = f"ESPN projects {player.get('name')} {delta:.1f} points above your lowest-projected {pos}, {bottom_name} ({bottom_pts:.1f})."
    else:
        preface = f"ESPN projects {player.get('name')} {abs(delta):.1f} points below your lowest-projected {pos}, {bottom_name} ({bottom_pts:.1f})."
    return preface + " This compares positional depth, not a guaranteed lineup or add/drop gain."


def _long_term(p, move):
    rank = p.get("fantasypros_ros_rank")
    if rank is None:
        rank = p.get("leaguelogs_ros_rank")
    rank_n = _number(rank)
    parts = []
    if rank_n is not None and rank_n > 0:
        provider = "FantasyPros" if p.get("fantasypros_ros_rank") is not None else "LeagueLogs"
        parts.append(f"{provider} rest-of-season rank: #{int(rank_n)} (rank scope depends on provider).")
    market = _number(p.get("leaguelogs_market_value"))
    if market is None:
        market = _number((p.get("signals") or {}).get("leaguelogs", {}).get("market_value"))
    if market is not None:
        parts.append(f"LeagueLogs market-value signal: {market:,.0f} (relative indicator, not fantasy points).")
    if move is not None:
        delta = _number(move.get("ros_delta"))
        if delta is not None and (move.get("add") or {}).get("position") == (move.get("drop") or {}).get("position"):
            parts.append(f"Same-position ROS evaluator change versus the suggested drop: {delta:+.1f} advisory score (not fantasy points).")
    if not parts:
        parts.append("No verified rest-of-season ranking or market-value signal loaded; long-term upgrade is unconfirmed.")
    return " ".join(parts)


def explain_candidate(context, p, move=None):
    """UI-safe attribution-rich explanation; never invent a transaction."""
    pos = str(p.get("position") or "?")
    week = context.get("current_week")
    label = CATEGORIES.get((move or {}).get("recommendation"), "Watchlist · not a recommended add")
    short_term, sources = _short_term(p, move, week)
    drop = (move or {}).get("drop") or {}
    tradeoff = (
        f"Evaluator recommends adding {p['name']} and dropping {drop['name']}. "
        + str(move.get("recommendation_reason") or "")
        if move and drop.get("name")
        else "No add/drop cleared the evaluator's threshold for this player. Don't drop someone solely because they're high in the free-agent list."
    )
    return {
        "player": {
            "espn_id": p.get("espn_id"), "name": p.get("name"), "position": p.get("position"),
            "team": p.get("team") or p.get("nfl_team"),
            "espn_projection": sources.get("ESPN"), "fantasypros_projection": sources.get("FantasyPros"),
            "injury_status": p.get("injury_status"),
            "on_bye_week": bool(p.get("espn_on_bye") or p.get("on_bye_week")),
        },
        "category": (move or {}).get("recommendation") or "WATCHLIST",
        "label": label,
        "actionable": move is not None,
        "suggested_drop": ({"espn_id": drop.get("espn_id"), "name": drop.get("name"), "position": drop.get("position")} if move and drop else None),
        "fit": _roster_fit(context, pos),
        "roster_comparison": _roster_comparison(context, p, sources),
        "this_week": short_term,
        "rest_of_season": _long_term(p, move),
        "decision": tradeoff,
        "projection_sources": sources,
        "evidence_note": "ESPN-owned roster and free-agent status; evaluator-derived add/drop only. Missing data is not estimated. No Claude call.",
    }


def personalized_waivers(context, *, position="ALL", limit=5):
    """Rank confirmed transaction upgrades first, then explicitly-marked watchlist."""
    position = str(position or "ALL").strip().upper()
    if position not in POSITIONS:
        raise ValueError("Unsupported waiver position")
    limit = max(1, min(int(limit), 12))
    candidates = context.get("waiver_candidates") or []
    available = [p for p in candidates if _available(context, p)
                 and (position == "ALL" or p.get("position") == position)]
    unique = {_key(p): p for p in available}
    moves = _best_transactions(context)
    seen = set()
    selected = []
    # Existing evaluator orders best transactions first, and it accounts for
    # protected drops, lineup utility, ROS, and roster balance.
    for row in context.get("waiver_transactions") or []:
        key = _key(row.get("add") or {})
        if key in unique and key in moves and key not in seen and moves[key] is row:
            selected.append(explain_candidate(context, unique[key], row))
            seen.add(key)
            if len(selected) == limit:
                break
    if len(selected) < limit:
        # Watchlist ordering remains an availability prescreen (NOT a claim
        # these players improve the current roster).
        other = [p for key, p in unique.items() if key not in seen]
        other.sort(key=lambda p: (
            -(_number(p.get("waiver_prescreen_score")) or -1),
            -(_number(p.get("espn_projected_points")) or -1),
            str(p.get("name") or "").lower(),
        ))
        selected.extend(explain_candidate(context, p) for p in other[:limit - len(selected)])
    count = sum(row["actionable"] for row in selected)
    return {
        "requested_position": position,
        "recommendations": selected,
        "actionable_count": count,
        "loaded_candidates": len(unique),
        "week": context.get("current_week"),
        "note": (
            "Ranked add/drop proposals come from the existing roster-aware evaluator. "
            "Other players are explicitly marked Watchlist; they are not verified improvements. "
            "Only the previously enriched ESPN waiver shortlist is considered (not all NFL free agents). "
            "No new paid-provider or Claude requests."
        ),
    }


def browser_explanations(context, selected):
    """Attach analysis to every visible paginated free agent (max 50)."""
    moves = _best_transactions(context)
    return [explain_candidate(context, p, moves.get(_key(p))) for p in selected]
