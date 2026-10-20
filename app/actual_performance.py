"""Actual pregame player performance context from the local nflverse cache.

This module is deliberately independent of FantasyPros.  It exposes factual,
completed-game statistics that Claude can use when projections/rankings are
missing or rate-limited.

Leakage rule:
    For a target Week N decision, only rows with week < N are used.  This keeps
    the evidence aligned with what was knowable before that week's game.
"""

from pathlib import Path
import math
import re
import unicodedata

import pandas as pd
import polars as pl


RAW_CACHE_DIR = Path("data/cache/nflverse")
_RAW_SEASON_CACHE = {}


TEAM_ALIASES = {
    "ARZ": "ARI", "ARI": "ARI",
    "GBP": "GB", "GB": "GB",
    "JAC": "JAX", "JAX": "JAX",
    "KCC": "KC", "KC": "KC",
    "LVR": "LV", "OAK": "LV", "LV": "LV",
    "LA": "LAR", "LAR": "LAR",
    "NEP": "NE", "NE": "NE",
    "NOS": "NO", "NO": "NO",
    "SFO": "SF", "SF": "SF",
    "TBB": "TB", "TB": "TB",
    "WAS": "WSH", "WSH": "WSH",
}


NUMERIC_COLUMNS = [
    "fantasy_points_ppr",
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "targets",
    "receptions",
    "receiving_yards",
    "receiving_tds",
    "target_share",
    "air_yards_share",
]


def normalize_name(value):
    if value is None:
        return ""
    name = unicodedata.normalize("NFKD", str(value))
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", name)
    return re.sub(r"\s+", " ", name).strip()


def normalize_team(value):
    if value is None:
        return None
    team = str(value).upper().strip()
    if not team or team in {"FA", "NONE"}:
        return None
    return TEAM_ALIASES.get(team, team)


def _clean_number(value, digits=3):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _load_raw_season(season):
    season = int(season)
    if season in _RAW_SEASON_CACHE:
        return _RAW_SEASON_CACHE[season]

    path = RAW_CACHE_DIR / f"player_stats_{season}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing nflverse player-stat cache: {path}. "
            "Run app/weekly_ml_update.py to refresh current-season stats."
        )

    raw = pl.read_parquet(path)
    df = pd.DataFrame(raw.to_dicts())

    if "passing_interceptions" not in df.columns:
        if "interceptions" in df.columns:
            df["passing_interceptions"] = df["interceptions"]
        else:
            df["passing_interceptions"] = 0.0

    if "season_type" in df.columns:
        df = df[df["season_type"] == "REG"].copy()

    if "player_display_name" not in df.columns:
        raise RuntimeError("nflverse cache is missing player_display_name")

    for column in ["season", "week"] + NUMERIC_COLUMNS:
        if column not in df.columns:
            df[column] = 0.0
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["normalized_name"] = df["player_display_name"].apply(normalize_name)
    _RAW_SEASON_CACHE[season] = df
    return df


def _match_player_rows(player, season, week):
    df = _load_raw_season(season)
    name_key = normalize_name(player.get("name"))
    position = str(player.get("position") or "").upper()

    rows = df[
        (df["season"] == int(season))
        & (df["week"] < int(week))
        & (df["normalized_name"] == name_key)
    ].copy()

    if rows.empty:
        return rows

    if "position" in rows.columns and position:
        same_position = rows[rows["position"].astype(str).str.upper() == position]
        if not same_position.empty:
            rows = same_position

    # Resolve rare same-name collisions by identifying the player_id connected
    # to the current team, but then retain all of that player's rows so a trade
    # does not erase games played for a previous team.
    if "player_id" in rows.columns:
        ids = [x for x in rows["player_id"].dropna().unique().tolist() if str(x)]
        if len(ids) > 1:
            current_team = normalize_team(player.get("nfl_team"))
            team_col = next((c for c in ("recent_team", "team") if c in rows.columns), None)
            if current_team and team_col:
                matching = rows[
                    rows[team_col].apply(normalize_team) == current_team
                ]
                matching_ids = [
                    x for x in matching["player_id"].dropna().unique().tolist() if str(x)
                ]
                if len(matching_ids) == 1:
                    rows = rows[rows["player_id"] == matching_ids[0]]

    return rows.sort_values("week")


def _mean(rows, column):
    if column not in rows.columns or rows.empty:
        return None
    value = pd.to_numeric(rows[column], errors="coerce").mean()
    return _clean_number(value)


def _sum(rows, column):
    if column not in rows.columns or rows.empty:
        return None
    value = pd.to_numeric(rows[column], errors="coerce").fillna(0.0).sum()
    return _clean_number(value)


def _base_summary(rows):
    return {
        "games_used": int(len(rows)),
        "ppr_per_game": _mean(rows, "fantasy_points_ppr"),
    }


def _position_summary(rows, position):
    summary = _base_summary(rows)

    if position == "QB":
        summary.update({
            "pass_attempts_per_game": _mean(rows, "attempts"),
            "completions_per_game": _mean(rows, "completions"),
            "passing_yards_per_game": _mean(rows, "passing_yards"),
            "passing_tds_total": _sum(rows, "passing_tds"),
            "interceptions_total": _sum(rows, "passing_interceptions"),
            "carries_per_game": _mean(rows, "carries"),
            "rushing_yards_per_game": _mean(rows, "rushing_yards"),
            "rushing_tds_total": _sum(rows, "rushing_tds"),
        })

    elif position == "RB":
        carries = pd.to_numeric(rows.get("carries"), errors="coerce").fillna(0.0)
        targets = pd.to_numeric(rows.get("targets"), errors="coerce").fillna(0.0)
        summary.update({
            "carries_per_game": _mean(rows, "carries"),
            "rushing_yards_per_game": _mean(rows, "rushing_yards"),
            "rushing_tds_total": _sum(rows, "rushing_tds"),
            "targets_per_game": _mean(rows, "targets"),
            "receptions_per_game": _mean(rows, "receptions"),
            "receiving_yards_per_game": _mean(rows, "receiving_yards"),
            "receiving_tds_total": _sum(rows, "receiving_tds"),
            "opportunities_per_game": _clean_number((carries + targets).mean()),
        })

    elif position in {"WR", "TE"}:
        summary.update({
            "targets_per_game": _mean(rows, "targets"),
            "receptions_per_game": _mean(rows, "receptions"),
            "receiving_yards_per_game": _mean(rows, "receiving_yards"),
            "receiving_tds_total": _sum(rows, "receiving_tds"),
            "target_share_avg": _mean(rows, "target_share"),
            "air_yards_share_avg": _mean(rows, "air_yards_share"),
        })

    return {k: v for k, v in summary.items() if v is not None}


def _game_log(rows, position):
    records = []
    for _, row in rows.tail(3).iterrows():
        item = {
            "week": int(row["week"]),
            "ppr": _clean_number(row.get("fantasy_points_ppr")),
        }
        if position == "QB":
            item.update({
                "attempts": _clean_number(row.get("attempts")),
                "pass_yards": _clean_number(row.get("passing_yards")),
                "pass_tds": _clean_number(row.get("passing_tds")),
                "ints": _clean_number(row.get("passing_interceptions")),
                "carries": _clean_number(row.get("carries")),
                "rush_yards": _clean_number(row.get("rushing_yards")),
            })
        elif position == "RB":
            item.update({
                "carries": _clean_number(row.get("carries")),
                "rush_yards": _clean_number(row.get("rushing_yards")),
                "targets": _clean_number(row.get("targets")),
                "receptions": _clean_number(row.get("receptions")),
                "rec_yards": _clean_number(row.get("receiving_yards")),
            })
        elif position in {"WR", "TE"}:
            item.update({
                "targets": _clean_number(row.get("targets")),
                "receptions": _clean_number(row.get("receptions")),
                "rec_yards": _clean_number(row.get("receiving_yards")),
                "rec_tds": _clean_number(row.get("receiving_tds")),
                "target_share": _clean_number(row.get("target_share")),
                "air_yards_share": _clean_number(row.get("air_yards_share")),
            })
        records.append({k: v for k, v in item.items() if v is not None})
    return records


def _delta(last3, season, key):
    a = last3.get(key)
    b = season.get(key)
    if a is None or b is None:
        return None
    return _clean_number(a - b)


def get_actual_performance(player, season, week):
    """Return factual completed-game stats for Claude's decision context."""
    position = str(player.get("position") or "").upper()
    if position not in {"QB", "RB", "WR", "TE"}:
        return {
            "available": False,
            "source": "nflverse",
            "reason": "Actual-performance profile currently covers QB/RB/WR/TE only.",
        }

    try:
        rows = _match_player_rows(player, season, week)
        if rows.empty:
            return {
                "available": False,
                "source": "nflverse",
                "through_week": int(week) - 1,
                "reason": "No completed pre-target-week nflverse rows matched this player.",
            }

        season_summary = _position_summary(rows, position)
        last3_rows = rows.tail(3)
        last3_summary = _position_summary(last3_rows, position)

        trend_keys = ["ppr_per_game"]
        if position == "QB":
            trend_keys += ["pass_attempts_per_game", "passing_yards_per_game", "carries_per_game"]
        elif position == "RB":
            trend_keys += ["carries_per_game", "targets_per_game", "opportunities_per_game"]
        else:
            trend_keys += ["targets_per_game", "receiving_yards_per_game", "target_share_avg"]

        deltas = {
            f"{key}_delta": _delta(last3_summary, season_summary, key)
            for key in trend_keys
        }
        deltas = {k: v for k, v in deltas.items() if v is not None}

        weeks = [int(x) for x in rows["week"].dropna().tolist()]

        return {
            "available": True,
            "source": "nflverse",
            "through_week": int(week) - 1,
            "games_used": int(len(rows)),
            "weeks_used": weeks,
            "season": season_summary,
            "last_3": last3_summary,
            "last_3_vs_season": deltas,
            "last_3_games": _game_log(rows, position),
            "note": (
                "These are actual completed-game statistics, not projections or rankings. "
                "For a Week N decision only weeks < N are included."
            ),
        }

    except Exception as exc:
        return {
            "available": False,
            "source": "nflverse",
            "reason": str(exc),
        }
