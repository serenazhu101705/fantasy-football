"""Current-week opponent + defense-vs-position matchup context.

This module is intentionally descriptive only. It does NOT change weekly_score,
projections, the optimizer, or waiver decisions.

The defensive matchup metric uses nflverse PPR fantasy points allowed to a
position in games BEFORE the target week. Higher points allowed = easier
fantasy matchup. This is a context signal, not a projection.
"""

from pathlib import Path
import math

import nflreadpy as nfl
import pandas as pd
import polars as pl


RAW_CACHE_DIR = Path("data/cache/nflverse")

_RAW_SEASON_CACHE = {}
_SCHEDULE_CACHE = {}
_MATCHUP_CACHE = {}


def normalize_team(value):
    if value is None:
        return None

    team = str(value).upper().strip()
    team = team.replace("@", "")

    if not team or team in {"FA", "NONE", "NAN"}:
        return None

    aliases = {
        "ARZ": "ARI",
        "ARI": "ARI",
        "GBP": "GB",
        "GB": "GB",
        "JAC": "JAX",
        "JAX": "JAX",
        "KCC": "KC",
        "KC": "KC",
        "LVR": "LV",
        "OAK": "LV",
        "LV": "LV",
        "LA": "LAR",
        "LAR": "LAR",
        "NEP": "NE",
        "NE": "NE",
        "NOS": "NO",
        "NO": "NO",
        "SFO": "SF",
        "SF": "SF",
        "TBB": "TB",
        "TB": "TB",
        "WAS": "WSH",
        "WSH": "WSH",
    }

    return aliases.get(team, team)


def _safe_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def _load_raw_season(season):
    season = int(season)

    if season in _RAW_SEASON_CACHE:
        return _RAW_SEASON_CACHE[season]

    path = RAW_CACHE_DIR / f"player_stats_{season}.parquet"

    if not path.exists():
        raise FileNotFoundError(
            f"Missing nflverse cache: {path}. Run app/weekly_ml_update.py first."
        )

    raw = pl.read_parquet(path)
    df = pd.DataFrame(raw.to_dicts())

    required = [
        "season",
        "week",
        "position",
        "fantasy_points_ppr",
        "opponent_team",
    ]

    missing = [column for column in required if column not in df.columns]
    if missing:
        raise RuntimeError(
            "nflverse player cache is missing matchup columns: "
            + ", ".join(missing)
        )

    if "season_type" in df.columns:
        df = df[df["season_type"] == "REG"].copy()

    df["season"] = pd.to_numeric(df["season"], errors="coerce")
    df["week"] = pd.to_numeric(df["week"], errors="coerce")
    df["fantasy_points_ppr"] = pd.to_numeric(
        df["fantasy_points_ppr"], errors="coerce"
    )
    df["position"] = df["position"].astype(str).str.upper()
    df["normalized_opponent"] = df["opponent_team"].apply(normalize_team)

    _RAW_SEASON_CACHE[season] = df
    return df


def _load_schedule(season):
    season = int(season)

    if season in _SCHEDULE_CACHE:
        return _SCHEDULE_CACHE[season]

    raw = nfl.load_schedules(seasons=[season])
    schedule = pd.DataFrame(raw.to_dicts())

    if "game_type" in schedule.columns:
        schedule = schedule[schedule["game_type"] == "REG"].copy()

    schedule["season"] = pd.to_numeric(schedule["season"], errors="coerce")
    schedule["week"] = pd.to_numeric(schedule["week"], errors="coerce")
    schedule["home_team_norm"] = schedule["home_team"].apply(normalize_team)
    schedule["away_team_norm"] = schedule["away_team"].apply(normalize_team)

    _SCHEDULE_CACHE[season] = schedule
    return schedule


def _schedule_opponent(team, season, week):
    team = normalize_team(team)
    if team is None:
        return None

    schedule = _load_schedule(season)
    games = schedule[
        (schedule["season"] == int(season))
        & (schedule["week"] == int(week))
        & (
            (schedule["home_team_norm"] == team)
            | (schedule["away_team_norm"] == team)
        )
    ]

    if games.empty:
        return None

    game = games.iloc[0]

    if game["home_team_norm"] == team:
        return game["away_team_norm"]

    return game["home_team_norm"]


def get_current_opponent(player, season, week):
    """Prefer FantasyPros opponent; fall back to nflverse schedule."""

    direct = normalize_team(player.get("opponent"))
    if direct:
        return direct

    if player.get("is_on_bye") or player.get("espn_on_bye"):
        return None

    try:
        return _schedule_opponent(
            player.get("nfl_team"),
            season,
            week,
        )
    except Exception:
        return None


def _label(relative_ratio):
    if relative_ratio is None:
        return None
    if relative_ratio >= 1.15:
        return "very favorable"
    if relative_ratio >= 1.05:
        return "favorable"
    if relative_ratio > 0.95:
        return "neutral"
    if relative_ratio > 0.85:
        return "tough"
    return "very tough"


def get_matchup_context(player, season, week, last_n_games=4):
    """Return defense-vs-position context using only weeks before target week.

    Example interpretation for WR:
      ppr_allowed_to_position = total PPR scored by all WRs against that defense
      per game, averaged over the defense's most recent games before the target.

    The metric is league-agnostic PPR context from nflverse and should not be
    treated as Serena's exact ESPN scoring projection.
    """

    position = str(player.get("position") or "").upper()

    if position not in {"QB", "RB", "WR", "TE"}:
        return {
            "available": False,
            "reason": "Matchup model currently covers QB/RB/WR/TE only.",
        }

    if player.get("is_on_bye") or player.get("espn_on_bye"):
        return {
            "available": False,
            "reason": "Player is on bye this week.",
        }

    opponent = get_current_opponent(player, season, week)

    if opponent is None:
        return {
            "available": False,
            "reason": "Current opponent could not be resolved.",
        }

    cache_key = (
        int(season),
        int(week),
        opponent,
        position,
        int(last_n_games),
    )

    if cache_key in _MATCHUP_CACHE:
        return dict(_MATCHUP_CACHE[cache_key])

    try:
        df = _load_raw_season(season)

        prior = df[
            (df["season"] == int(season))
            & (df["week"] < int(week))
            & (df["position"] == position)
            & df["normalized_opponent"].notna()
            & df["fantasy_points_ppr"].notna()
        ].copy()

        if prior.empty:
            raise RuntimeError("No prior-season matchup rows were available.")

        # One row per defense-week-position: total fantasy production by all
        # opposing players at this position against that defense.
        defense_week = (
            prior.groupby(
                ["normalized_opponent", "week"],
                as_index=False,
            )["fantasy_points_ppr"]
            .sum()
            .rename(columns={"fantasy_points_ppr": "ppr_allowed"})
        )

        opponent_rows = defense_week[
            defense_week["normalized_opponent"] == opponent
        ].sort_values("week")

        if opponent_rows.empty:
            raise RuntimeError(
                f"No prior {position} matchup rows found for {opponent}."
            )

        recent_opponent_rows = opponent_rows.tail(int(last_n_games))
        allowed_avg = float(recent_opponent_rows["ppr_allowed"].mean())

        # League average across all defense-games at this position.
        league_avg = float(defense_week["ppr_allowed"].mean())

        if league_avg <= 0:
            raise RuntimeError("League matchup average was not usable.")

        ratio = allowed_avg / league_avg

        defense_averages = (
            defense_week.sort_values("week")
            .groupby("normalized_opponent", as_index=False)
            .tail(int(last_n_games))
            .groupby("normalized_opponent")["ppr_allowed"]
            .mean()
            .sort_values(ascending=False)
        )

        ranked_teams = list(defense_averages.index)
        easiest_rank = (
            ranked_teams.index(opponent) + 1
            if opponent in ranked_teams
            else None
        )

        result = {
            "available": True,
            "opponent": opponent,
            "position": position,
            "sample_games": int(len(recent_opponent_rows)),
            "ppr_allowed_to_position": round(allowed_avg, 2),
            "league_avg_ppr_allowed": round(league_avg, 2),
            "relative_to_league_pct": round((ratio - 1.0) * 100.0, 1),
            "easiest_matchup_rank": easiest_rank,
            "defenses_ranked": int(len(ranked_teams)),
            "label": _label(ratio),
            "metric_note": (
                "PPR points allowed to the entire position group per game, "
                "using only games before the target week. Higher = easier."
            ),
        }

    except Exception as exc:
        result = {
            "available": False,
            "opponent": opponent,
            "reason": str(exc),
        }

    _MATCHUP_CACHE[cache_key] = result
    return dict(result)