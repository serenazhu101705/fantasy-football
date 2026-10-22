"""Legal weekly lineup selection using available league-scoring projections.

ESPN and FantasyPros projections are already expressed in the league's scoring
format by the enrichment layer. Missing providers are not treated as zero-point
projections. The older composite ``weekly_score`` remains advisory, rather than
being allowed to overrule a large, observable difference in projected points.
"""

from itertools import combinations
from math import isfinite


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (ValueError, TypeError):
        return None


def projection_sources(player):
    """Provider projections known at decision time (no generated estimates)."""
    if player is None:
        return {}
    sources = {}
    espn = _number(player.get("espn_projected_points"))
    fantasypros = _number(player.get("fp_points"))
    if fantasypros is None:
        fantasypros = _number(player.get("fp_ppr"))
    if espn is not None:
        sources["ESPN"] = espn
    if fantasypros is not None:
        sources["FantasyPros"] = fantasypros
    return sources


def projected_points(player):
    """Mean available provider projections; no missing-source zero penalty.

    Standalone by design: older offline tests extract this function directly.
    """
    if player is None:
        return 0.0

    def valid_number(value):
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = float(value)
            return parsed if parsed == parsed and abs(parsed) != float("inf") else None
        except (ValueError, TypeError):
            return None

    espn = valid_number(player.get("espn_projected_points"))
    fp = valid_number(player.get("fp_points"))
    if fp is None:
        fp = valid_number(player.get("fp_ppr"))
    known = [value for value in (espn, fp) if value is not None]
    return sum(known) / len(known) if known else 0.0


def weekly_advisory_value(player):
    if player is None:
        return 0.0
    return _number(player.get("weekly_score")) or 0.0


def lineup_value(player):
    """Points-first selection score; advisory can break only near-ties.

    The composite's maximum contribution is 0.50 projected points, so it cannot
    put a significantly lower-projected WR ahead of a healthy proven starter.
    When ALL projection sources are missing for a player, use a visibly marked
    advisory proxy to avoid pretending an absent projection is a measured zero.
    """
    if player is None:
        return 0.0
    advisory = weekly_advisory_value(player)
    if not projection_sources(player):
        return advisory * 0.10
    return projected_points(player) + min(0.50, max(0.0, advisory) * 0.005)


def is_available(player):
    availability = player.get("availability") or {}
    return bool(availability.get("can_play_this_week", True))


def best_player(players):
    return max(players, key=lineup_value) if players else None


def optimize_lineup(roster):
    """Optimize 1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX, 1 DST, 1 K.

    Return the old weekly lineup score for waiver transaction compatibility.
    ``selection_score`` is the new points-first objective, while the displayed
    ``projected_total`` is a projection-only consensus (never weekly_score).
    """
    available = [p for p in roster if is_available(p)]
    groups = {
        position: [p for p in available if p.get("position") == position]
        for position in ("QB", "RB", "WR", "TE", "D/ST", "K")
    }
    empty = {
        "lineup": None,
        "lineup_score": 0.0,
        "selection_score": 0.0,
        "projected_total": 0.0,
        "projection_complete": False,
        "projection_coverage": "0/9",
        "selection_method": "projection_consensus_with_advisory_tiebreak",
    }
    if any(len(groups[position]) < required for position, required in
           (("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("D/ST", 1), ("K", 1))):
        return empty

    qb = best_player(groups["QB"])
    dst = best_player(groups["D/ST"])
    kicker = best_player(groups["K"])
    best_lineup = None
    best_total = float("-inf")

    for rb_combo in combinations(groups["RB"], 2):
        for wr_combo in combinations(groups["WR"], 2):
            for te in groups["TE"]:
                used_ids = {p.get("espn_id", p.get("name")) for p in (*rb_combo, *wr_combo, te)}
                flex_options = [
                    p for p in (*groups["RB"], *groups["WR"], *groups["TE"])
                    if p.get("espn_id", p.get("name")) not in used_ids
                ]
                flex = best_player(flex_options)
                if flex is None:
                    continue
                lineup = {
                    "QB": qb, "RB1": rb_combo[0], "RB2": rb_combo[1],
                    "WR1": wr_combo[0], "WR2": wr_combo[1], "TE": te,
                    "FLEX": flex, "D/ST": dst, "K": kicker,
                }
                total = sum(lineup_value(p) for p in lineup.values())
                if total > best_total:
                    best_total, best_lineup = total, lineup

    if best_lineup is None:
        return empty
    players = list(best_lineup.values())
    available_projections = sum(bool(projection_sources(p)) for p in players)
    return {
        "lineup": best_lineup,
        "lineup_score": round(sum(weekly_advisory_value(p) for p in players), 2),
        "selection_score": round(best_total, 2),
        "projected_total": round(sum(projected_points(p) for p in players), 2),
        "projection_complete": available_projections == len(players),
        "projection_coverage": f"{available_projections}/{len(players)}",
        "selection_method": "projection_consensus_with_advisory_tiebreak",
    }
