"""Actual pregame player performance + resolved week history from nflverse.

This module is deliberately independent of FantasyPros. It exposes factual,
completed-game statistics and explains weeks with no player-stat row by using
nflverse schedule, weekly roster, injury/practice report, and snap-count data.

Leakage rule:
    For a target Week N decision, only weeks < N are used.
"""

from pathlib import Path
import math
import re
import unicodedata

import pandas as pd
import polars as pl

from week_resolution import resolve_week_history


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
    "fantasy_points",
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

ZERO_PLAY_STATES = {
    "PLAYED_ZERO_FANTASY_STATS",
    "PLAYED_SPECIAL_TEAMS_ONLY",
}


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
    if not team or team in {"FA", "NONE", "NAN"}:
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

    # Resolve rare same-name collisions by current team, then keep all rows for
    # that player's ID so an in-season trade does not erase earlier games.
    if "player_id" in rows.columns:
        ids = [x for x in rows["player_id"].dropna().unique().tolist() if str(x)]
        if len(ids) > 1:
            current_team = normalize_team(player.get("nfl_team"))
            team_col = next((c for c in ("team", "recent_team") if c in rows.columns), None)
            if current_team and team_col:
                matching = rows[rows[team_col].apply(normalize_team) == current_team]
                matching_ids = [
                    x for x in matching["player_id"].dropna().unique().tolist() if str(x)
                ]
                if len(matching_ids) == 1:
                    rows = rows[rows["player_id"] == matching_ids[0]]

    # Defensive dedupe: one player-week should contribute once.
    if "week" in rows.columns:
        rows = rows.sort_values("week").drop_duplicates(subset=["week"], keep="last")

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


def _add_league_fantasy_points(rows, reception_points):
    rows = rows.copy()
    try:
        rp = float(reception_points)
    except (TypeError, ValueError):
        rp = 1.0

    receptions = pd.to_numeric(rows.get("receptions", 0.0), errors="coerce").fillna(0.0)
    if "fantasy_points" in rows.columns:
        standard = pd.to_numeric(rows["fantasy_points"], errors="coerce")
    else:
        standard = pd.Series(float("nan"), index=rows.index)

    if "fantasy_points_ppr" in rows.columns:
        ppr_values = pd.to_numeric(rows["fantasy_points_ppr"], errors="coerce")
        standard_missing = standard.isna().all()
        suspicious_zero_standard = (
            standard.fillna(0.0).abs().sum() == 0
            and ppr_values.fillna(0.0).abs().sum() > 0
        )
        if standard_missing or suspicious_zero_standard:
            standard = ppr_values - receptions

    rows["league_fantasy_points"] = standard.fillna(0.0) + receptions * rp
    return rows, rp


def _base_summary(rows):
    return {
        "games_used": int(len(rows)),
        "fantasy_points_per_game": _mean(rows, "league_fantasy_points"),
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
        carries = pd.to_numeric(rows["carries"], errors="coerce").fillna(0.0)
        targets = pd.to_numeric(rows["targets"], errors="coerce").fillna(0.0)
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


def _synthetic_zero_row(week_item, season, position):
    row = {
        "season": int(season),
        "week": int(week_item["week"]),
        "position": position,
        "team": week_item.get("team"),
        "opponent_team": week_item.get("opponent"),
    }
    for col in NUMERIC_COLUMNS:
        row[col] = 0.0
    return row


def _augment_played_rows(rows, week_history, season, position):
    """Count confirmed appearances with no stat row as zero-stat games."""
    if rows.empty:
        augmented = pd.DataFrame(columns=list(rows.columns) or ["season", "week"] + NUMERIC_COLUMNS)
    else:
        augmented = rows.copy()

    existing_weeks = {
        int(value)
        for value in augmented.get("week", pd.Series(dtype=float)).dropna().tolist()
    }

    additions = []
    for item in (week_history.get("weeks") or []):
        week = int(item["week"])
        if week in existing_weeks:
            continue
        if item.get("state") in ZERO_PLAY_STATES:
            additions.append(_synthetic_zero_row(item, season, position))

    if additions:
        augmented = pd.concat([augmented, pd.DataFrame(additions)], ignore_index=True, sort=False)

    for col in NUMERIC_COLUMNS:
        if col not in augmented.columns:
            augmented[col] = 0.0
        augmented[col] = pd.to_numeric(augmented[col], errors="coerce").fillna(0.0)

    if "week" in augmented.columns:
        augmented["week"] = pd.to_numeric(augmented["week"], errors="coerce")
        augmented = augmented.sort_values("week")

    return augmented


def _week_stat_item(row, position):
    item = {
        "fantasy_points": _clean_number(row.get("league_fantasy_points")),
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
            "rush_tds": _clean_number(row.get("rushing_tds")),
            "targets": _clean_number(row.get("targets")),
            "receptions": _clean_number(row.get("receptions")),
            "rec_yards": _clean_number(row.get("receiving_yards")),
            "rec_tds": _clean_number(row.get("receiving_tds")),
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
    return {k: v for k, v in item.items() if v is not None}


def _resolved_game_log(rows, week_history, position):
    """One compact entry for every elapsed week, not just weeks with stat rows."""
    stat_by_week = {}
    if not rows.empty:
        for _, row in rows.iterrows():
            try:
                stat_by_week[int(row["week"])] = row
            except Exception:
                pass

    output = []
    for resolved in (week_history.get("weeks") or []):
        week = int(resolved["week"])
        item = {
            "week": week,
            "state": resolved.get("state"),
            "team": resolved.get("team"),
            "opponent": resolved.get("opponent"),
        }

        row = stat_by_week.get(week)
        if row is not None:
            item.update(_week_stat_item(row, position))
        elif resolved.get("state") in ZERO_PLAY_STATES:
            item["fantasy_points"] = 0.0

        if resolved.get("state") != "PLAYED":
            item["reason"] = resolved.get("reason")

        injury = resolved.get("injury_report")
        if injury:
            compact_injury = {
                key: injury.get(key)
                for key in ("report_status", "primary_injury", "practice_status")
                if injury.get(key) is not None
            }
            if compact_injury:
                item["injury"] = compact_injury

        snaps = resolved.get("snaps")
        if snaps and resolved.get("state") != "PLAYED":
            item["snaps"] = snaps

        output.append({k: v for k, v in item.items() if v is not None})

    return output


def _delta(last3, season, key):
    a = last3.get(key)
    b = season.get(key)
    if a is None or b is None:
        return None
    return _clean_number(a - b)


def get_actual_performance(player, season, week, reception_points=1.0):
    """Return factual completed-game stats plus an explanation for every prior week."""
    position = str(player.get("position") or "").upper()
    if position not in {"QB", "RB", "WR", "TE"}:
        return {
            "available": False,
            "source": "nflverse",
            "reason": "Actual-performance profile currently covers QB/RB/WR/TE only.",
        }

    try:
        observed_rows = _match_player_rows(player, season, week)

        week_history = resolve_week_history(
            player,
            season=season,
            target_week=week,
            stat_rows=observed_rows,
        )

        rows = _augment_played_rows(
            observed_rows,
            week_history,
            season=season,
            position=position,
        )
        rows, resolved_reception_points = _add_league_fantasy_points(
            rows,
            reception_points=reception_points,
        )

        if rows.empty:
            return {
                "available": True,
                "source": "nflverse",
                "through_week": int(week) - 1,
                "games_played": 0,
                "reception_points": resolved_reception_points,
                "stat_rows_observed": int(len(observed_rows)),
                "week_coverage": {
                    "weeks_expected": week_history.get("weeks_expected"),
                    "weeks_explained": week_history.get("weeks_explained"),
                    "coverage_complete": week_history.get("coverage_complete"),
                    "unresolved_weeks": week_history.get("unresolved_weeks") or [],
                },
                "week_history": _resolved_game_log(rows, week_history, position),
                "note": "No played games were resolved before the target week.",
            }

        season_summary = _position_summary(rows, position)
        last3_rows = rows.tail(3)
        last3_summary = _position_summary(last3_rows, position)

        trend_keys = ["fantasy_points_per_game"]
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

        played_weeks = [int(x) for x in rows["week"].dropna().tolist()]

        return {
            "available": True,
            "source": "nflverse",
            "through_week": int(week) - 1,
            "games_played": int(len(rows)),
            "reception_points": resolved_reception_points,
            "stat_rows_observed": int(len(observed_rows)),
            "played_weeks": played_weeks,
            "season": season_summary,
            "last_3_played_games": last3_summary,
            "last_3_vs_season": deltas,
            "week_coverage": {
                "weeks_expected": week_history.get("weeks_expected"),
                "weeks_explained": week_history.get("weeks_explained"),
                "coverage_complete": week_history.get("coverage_complete"),
                "unresolved_weeks": week_history.get("unresolved_weeks") or [],
                "source_errors": week_history.get("source_errors"),
            },
            "week_history": _resolved_game_log(rows, week_history, position),
            "note": (
                "Fantasy-point summaries use the league's reception scoring when supplied. "
                "Season and last-3 averages use confirmed games played, not elapsed NFL weeks. "
                "Weeks without a player-stat row are cross-checked against schedule, weekly roster, "
                "official injury/practice reports, and snap counts. Only DATA_GAP means genuinely unresolved."
            ),
        }

    except Exception as exc:
        return {
            "available": False,
            "source": "nflverse",
            "reason": str(exc),
        }
