"""Explain the optimizer's *actual* selection rule and eligible roster alternatives.

No model call. This is a transparent, single-slot counterfactual, not a claim
that the optimizer has considered next week's schedules, or that ECR is mandatory.
"""
from __future__ import annotations
from math import isfinite


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return round(value, 2) if isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _source_values(player):
    a = _num(player.get("espn_projection"))
    b = _num(player.get("fantasypros_projection"))
    return {key: value for key, value in (("ESPN", a), ("FantasyPros", b)) if value is not None}


def _mean_projection(player):
    values = list(_source_values(player).values())
    return round(sum(values) / len(values), 2) if values else None


def _eligible(p, slot):
    pos = str(p.get("position") or "").upper()
    key = slot.rstrip("0123456789").upper()
    return (pos in {"RB", "WR", "TE"} if key in {"FLEX", "RB/WR/TE", "RB/WR", "WR/TE"}
            else pos == "D/ST" if key in {"D/ST", "DST"} else pos == key)


def explain_lineup(lineup_data, roster):
    lineup = lineup_data.get("lineup") or {}
    selected = {str(p.get("espn_id") or p.get("name") or "").lower()
                for p in lineup.values() if isinstance(p, dict)}
    alternatives = [p for p in (roster or []) if
                    str(p.get("espn_id") or p.get("name") or "").lower() not in selected
                    and p.get("can_play_this_week") is not False
                    and not p.get("on_bye_week")]
    explanations = {}
    for slot, player in lineup.items():
        if not isinstance(player, dict):
            continue
        chosen = _mean_projection(player)
        eligible = sorted((p for p in alternatives if _eligible(p, slot)),
                          key=lambda x: _mean_projection(x) if _mean_projection(x) is not None else float("-inf"),
                          reverse=True)
        best = eligible[0] if eligible else None
        other = _mean_projection(best) if best else None
        delta = round(chosen-other, 2) if chosen is not None and other is not None else None
        if best is None:
            reason = "No other eligible, available player on this roster for a direct swap into this slot."
        elif delta is None:
            reason = "One or both projection baselines are unavailable; no numeric advantage is claimed."
        elif delta > 0.5:
            reason = f"{player['name']} is projected {delta:.2f} points higher than {best['name']}, the best eligible nonstarter for a direct swap."
        elif delta >= 0:
            reason = f"{player['name']} leads {best['name']} by only {delta:.2f} projected points; the advisory tie-break could matter."
        else:
            reason = f"{best['name']} projects {abs(delta):.2f} points higher for a direct swap. The optimizer's overall lineup objective can differ; review this choice."
        explanations[slot] = {
            "player": player["name"], "projection": chosen,
            "projection_sources": _source_values(player),
            "weekly_ecr_available": player.get("weekly_ecr") is not None,
            "best_eligible_nonstarter": {"name": best.get("name"), "projection": other,
                                        "sources": _source_values(best)} if best else None,
            "projection_advantage": delta, "reason": reason,
        }
    return {
        "method": "Maximize team total using the mean of available ESPN/FantasyPros projections, plus at most 0.50 points per player of advisory tie-break. Respect fixed QB/RB/WR/TE/FLEX/DST/K slots and play eligibility.",
        "slot_explanations": explanations,
        "comparison_note": "Each alternative is a feasible direct one-player swap, not a global reoptimization or guaranteed points gain. FLEX is selected after required positions are filled.",
        "rankings_note": "Missing FantasyPros weekly/ROS ECR means expert-consensus ranking is unavailable. ESPN projections, FantasyPros projections, injury status and usage can still be available and are not the same as missing player data.",
        "projected_total": lineup_data.get("projected_total"),
        "projected_coverage": lineup_data.get("projection_coverage"),
    }
