"""Verified *current-season* NFL player stats from existing nflverse weekly data.

Unlike ESPN's public /athletes/{id}/stats endpoint (which can return career
aggregates even with a season query), season and week are explicit columns here.
No Anthropic/FantasyPros requests. Missing data is never replaced with career stats.
"""
from __future__ import annotations

from pathlib import Path
import re
import unicodedata
from math import isfinite


def _normalize_name(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c)).lower()
    value = re.sub(r"[^a-z0-9\s]", "", value)
    value = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _normalize_team(value):
    team = str(value or "").upper().strip()
    return {"LA":"LAR", "JAC":"JAX", "WAS":"WSH", "ARZ":"ARI"}.get(team, team)

_LABELS = (
    ("completions", "Pass completions", "passing"),
    ("attempts", "Pass attempts", "passing"),
    ("passing_yards", "Passing yards", "passing"),
    ("passing_tds", "Passing touchdowns", "passing"),
    ("passing_interceptions", "Interceptions thrown", "passing"),
    ("carries", "Carries", "rushing"),
    ("rushing_yards", "Rushing yards", "rushing"),
    ("rushing_tds", "Rushing touchdowns", "rushing"),
    ("targets", "Targets", "receiving"),
    ("receptions", "Receptions", "receiving"),
    ("receiving_yards", "Receiving yards", "receiving"),
    ("receiving_tds", "Receiving touchdowns", "receiving"),
)


def _empty():
    return {"season_stats": [], "weeks_covered": [], "recent_games": [],
            "stats_source": "unavailable", "verified_season": False}


def _clean_value(value):
    try:
        if value is None:
            return 0.0
        return float(value)
    except (ValueError, TypeError):
        return 0.0



def weekly_game_log(rows, position=None, reception_points=1.0):
    """Verified player-week stats and nflverse standard/PPR fantasy scoring.

    Never pretend nflverse-derived points reproduce non-reception custom ESPN
    bonuses. ESPN Fantasy points, if available, supersede these in the profile.
    """
    pos = str(position or "").upper()
    groups = ({"passing", "rushing"} if pos == "QB" else
              {"rushing", "receiving"} if pos == "RB" else
              {"receiving", "rushing"} if pos in {"WR", "TE"} else
              {"passing", "rushing", "receiving"})
    result = []
    for r in sorted(rows, key=lambda x: int(x["week"]), reverse=True):
        stats = []
        for key, label, group in _LABELS:
            if group not in groups or key not in r:
                continue
            val = _clean_value(r.get(key))
            if abs(val) > 0.0001:
                stats.append({"key": key, "label": label, "value": round(val, 2), "category": group})
        catches = _clean_value(r.get("receptions"))
        points = None
        try:
            standard = float(r["fantasy_points"]) if r.get("fantasy_points") is not None else None
            ppr = float(r["fantasy_points_ppr"]) if r.get("fantasy_points_ppr") is not None else None
            # Older nflverse caches can have an all-zero placeholder in the
            # standard column. Use the actual PPR field if the zero is clearly
            # inconsistent with the supplied receptions and PPR points.
            if standard is not None and ppr is not None and standard == 0 and ppr > catches + 0.01:
                standard = None
            if standard is not None and isfinite(standard):
                points = standard + float(reception_points) * catches
            elif ppr is not None and isfinite(ppr):
                points = ppr - (1.0 - float(reception_points)) * catches
            if points is not None and not isfinite(points):
                points = None
        except (TypeError, ValueError):
            pass
        item = {"week": int(r["week"]), "fantasy_points": round(points, 2) if points is not None else None,
                "fantasy_points_source": "nflverse reception-adjusted fantasy scoring" if points is not None else None,
                "stats": stats, "source": "nflverse weekly player stats"}
        opponent = r.get("opponent") or r.get("opponent_team")
        if opponent:
            item["opponent"] = str(opponent)
        result.append(item)
    return result


def summarize_weekly_rows(rows, season, week, position=None, reception_points=1.0):
    """Aggregate only verified season/week rows, for easy offline testing.

    ``week`` is an exclusive cutoff: never include future or not-yet-final
    target-week games in a pregame player profile.
    """
    if not rows:
        return _empty()
    accepted = {}
    for row in rows:
        try:
            row_season, row_week = int(row.get("season")), int(row.get("week"))
        except (TypeError, ValueError):
            continue
        if row_season != int(season) or not 1 <= row_week < int(week):
            continue
        if str(row.get("season_type") or "REG").upper() not in ("REG", "2"):
            continue
        accepted[row_week] = row  # protect against duplicated player-week records
    if not accepted:
        return _empty()
    chosen = list(accepted.values())
    pos = (position or "").upper()
    selected_groups = {"passing", "rushing"} if pos == "QB" else (
        {"rushing", "receiving"} if pos == "RB" else
        {"receiving", "rushing"} if pos in {"WR", "TE"} else
        {"passing", "rushing", "receiving"})
    result = []
    for key, label, group in _LABELS:
        if group not in selected_groups:
            continue
        if not any(key in r for r in chosen):
            continue
        total = sum(_clean_value(r.get(key)) for r in chosen)
        if abs(total) < 0.0001:
            continue
        result.append({"key": key, "label": label,
                       "value": round(total, 2), "category": group})
    if not result:
        return _empty()
    return {"season_stats": result, "weeks_covered": sorted(accepted),
            "recent_games": weekly_game_log(chosen, position, reception_points),
            "stats_source": f"nflverse {int(season)} regular season (completed weeks)",
            "verified_season": True}


def current_season_player_stats(name, position, nfl_team, season, week, *, rows=None, reception_points=1.0):
    """Load cached nflverse weekly rows, matching player identity conservatively.

    ``rows`` injection supports deterministic tests. No live data source is
    required for regression tests. One player-profile visit never calls Claude.
    """
    key = _normalize_name(name)
    if not key:
        return _empty()
    if rows is None:
        data = None
        # Prefer the already-downloaded parquet used by the coach.
        path = Path("data/cache/nflverse") / f"player_stats_{int(season)}.parquet"
        if path.exists():
            try:
                from actual_performance import _load_raw_season
                data = _load_raw_season(int(season)).to_dict("records")
            except Exception:
                pass
        if data is None:
            try:
                from nfl_data import get_weekly_player_stats
                data = get_weekly_player_stats().to_dicts()
            except Exception:
                data = []
        rows = data
    matches = []
    for row in rows:
        if not isinstance(row, dict) or _normalize_name(row.get("player_display_name")) != key:
            continue
        row_pos = str(row.get("position") or "").upper()
        if position and row_pos and row_pos != str(position).upper():
            continue
        matches.append(row)
    if not matches:
        return _empty()
    # Resolve namesakes by team where possible. Keep all weeks for the same
    # nflverse player_id if they changed teams in-season.
    identities = {str(r.get("player_id")) for r in matches if r.get("player_id")}
    if len(identities) > 1:
        team = _normalize_team(nfl_team)
        current = [r for r in matches if _normalize_team(r.get("recent_team") or r.get("team")) == team]
        chosen_ids = {str(r.get("player_id")) for r in current if r.get("player_id")}
        if len(chosen_ids) != 1:
            return _empty()  # never aggregate two players with same name
        chosen_id = next(iter(chosen_ids))
        matches = [r for r in matches if str(r.get("player_id")) == chosen_id]
    return summarize_weekly_rows(matches, season, week, position, reception_points)
