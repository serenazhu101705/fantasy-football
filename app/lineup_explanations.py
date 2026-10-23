"""Explain each optimized starter against the strongest *unselected* eligible player.

Both the optimizer and explanation use available league-scoring provider projections.
These are direct one-player substitutions, not a rerun of global optimization.
"""
from __future__ import annotations
from math import isfinite
import re
import unicodedata


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return round(value, 2) if isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _source_values(player):
    # Browser roster rows and optimizer rows use the same compact field names.
    a = _num(player.get("espn_projection", player.get("espn_projected_points")))
    b = _num(player.get("fantasypros_projection", player.get("fp_points", player.get("fp_ppr"))))
    return {key: value for key, value in (("ESPN", a), ("FantasyPros", b)) if value is not None}


def _mean_projection(player):
    values = list(_source_values(player).values())
    return round(sum(values) / len(values), 2) if values else None


def _player_name(value):
    value = unicodedata.normalize("NFKD", str(value or "")).lower()
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in value if not unicodedata.combining(c)))


def _same_player(a, b):
    # The browser lineup receives restored espn_id fields, while the compact
    # roster used by Claude intentionally omits IDs. Names provide the necessary
    # fallback, and position/team protect against common-name collisions.
    ida, idb = a.get("espn_id"), b.get("espn_id")
    if ida is not None and idb is not None:
        return str(ida) == str(idb)
    if _player_name(a.get("name")) != _player_name(b.get("name")):
        return False
    if not _player_name(a.get("name")):
        return False
    posa, posb = a.get("position"), b.get("position")
    ta, tb = a.get("team"), b.get("team")
    return (not posa or not posb or posa == posb) and (not ta or not tb or ta == tb)


def _eligible(p, slot):
    pos = str(p.get("position") or "").upper()
    key = slot.rstrip("0123456789").upper()
    return (pos in {"RB", "WR", "TE"} if key in {"FLEX", "RB/WR/TE", "RB/WR", "WR/TE"}
            else pos == "D/ST" if key in {"D/ST", "DST"} else pos == key)


def _can_start(player):
    if player.get("can_play_this_week") is False or player.get("on_bye_week"):
        return False
    if str(player.get("lineup_slot") or "").upper() in {"IR", "RES", "NA"}:
        return False
    if str(player.get("injury_status") or "").upper() in {"OUT", "IR", "INJURY_RESERVE", "SUSPENDED"}:
        return False
    return True


def explain_lineup(lineup_data, roster):
    lineup = lineup_data.get("lineup") or {}
    starters = [p for p in lineup.values() if isinstance(p, dict)]
    # Exclude ALL starters before comparing positions. A starter at RB1 is not
    # a legitimate 'next available' alternative to the RB2 starter.
    alternatives = [p for p in (roster or []) if isinstance(p, dict)
                    and _can_start(p)
                    and not any(_same_player(p, starter) for starter in starters)]
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
        delta = round(chosen - other, 2) if chosen is not None and other is not None else None
        if best is None:
            reason = "No other eligible, available nonstarter on your roster for a direct swap into this slot."
        elif delta is None:
            reason = f"{best['name']} is the next eligible nonstarter, but one or both projections are unavailable."
        elif delta > 0.5:
            reason = f"{player['name']} projects {delta:.2f} points above {best['name']}, the best eligible nonstarter."
        elif delta >= 0:
            reason = f"{player['name']} projects only {delta:.2f} points above {best['name']}; the advisory tie-break may matter."
        else:
            reason = (f"{best['name']} projects {abs(delta):.2f} points higher in a direct swap. "
                      "The full-lineup objective or advisory tie-break may explain this; review before starting.")
        explanations[slot] = {
            "player": player.get("name"), "projection": chosen,
            "projection_sources": _source_values(player),
            "weekly_ecr_available": player.get("weekly_ecr") is not None,
            "best_eligible_nonstarter": ({"name": best.get("name"), "projection": other,
                                          "sources": _source_values(best)} if best else None),
            "projection_advantage": delta, "reason": reason,
        }
    return {
        "method": ("Maximize total starting-lineup projection from available ESPN/FantasyPros forecasts, "
                   "with at most 0.50 points of advisory tie-break per player. "
                   "Every starter occupies an eligible position; projections reflect your league scoring."),
        "slot_explanations": explanations,
        "comparison_note": ("Alternatives are the highest-projected eligible players NOT selected anywhere "
                            "in the optimal lineup. A direct swap is not the same as rerunning the entire optimization."),
        "rankings_note": ("Missing FantasyPros weekly or ROS ECR means only expert-consensus RANKINGS are missing. "
                          "Missing an ECR ranking is not the same as missing projections, injuries or actual production."),
        "projected_total": lineup_data.get("projected_total"),
        "projected_coverage": lineup_data.get("projection_coverage"),
    }
