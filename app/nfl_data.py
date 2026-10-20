from functools import lru_cache

import nflreadpy as nfl
import polars as pl


SEASON = 2026

def normalize_nfl_team(
    value,
):
    """
    Normalize team abbreviations across ESPN and
    nflverse.
    """

    if value is None:
        return None

    team = str(
        value
    ).upper().strip()

    aliases = {
        # Rams
        "LA": "LAR",
        "LAR": "LAR",

        # Commanders
        "WAS": "WSH",
        "WSH": "WSH",

        # Jaguars
        "JAC": "JAX",
        "JAX": "JAX",

        # Raiders
        "OAK": "LV",
        "LVR": "LV",
        "LV": "LV",
    }

    return aliases.get(
        team,
        team,
    )

@lru_cache(maxsize=1)
def get_weekly_player_stats():
    """
    Load weekly NFL player stats once per program run.
    """
    return nfl.load_player_stats(
        seasons=[SEASON],
        summary_level="week",
    )


def get_recent_player_stats(player_name, current_week, last_n=3):
    """
    Return the player's most recent completed games.
    """
    df = get_weekly_player_stats()

    return (
        df.filter(
            (pl.col("player_display_name") == player_name)
            & (pl.col("week") < current_week)
        )
        .sort("week", descending=True)
        .head(last_n)
        .sort("week")
    )


def get_recent_usage(player_name, current_week, last_n=3, reception_points=1.0):
    """
    Return useful averages/totals over the player's last N games.
    """

    df = get_recent_player_stats(
        player_name=player_name,
        current_week=current_week,
        last_n=last_n,
    )

    if df.height == 0:
        return None

    try:
        rp = float(reception_points)
    except (TypeError, ValueError):
        rp = 1.0

    # nflverse exposes standard and PPR fantasy points. Derive the current
    # league's reception scoring from standard + receptions * points/catch.
    if "fantasy_points" in df.columns:
        league_points = (
            pl.col("fantasy_points").fill_null(0.0)
            + pl.col("receptions").fill_null(0.0) * rp
        )
    else:
        league_points = (
            pl.col("fantasy_points_ppr").fill_null(0.0)
            - pl.col("receptions").fill_null(0.0)
            + pl.col("receptions").fill_null(0.0) * rp
        )

    summary = df.select(
        pl.len().alias("games"),

        pl.col("carries").mean().alias("carries_per_game"),
        pl.col("targets").mean().alias("targets_per_game"),
        pl.col("receptions").mean().alias("receptions_per_game"),

        pl.col("rushing_yards").mean().alias("rush_yards_per_game"),
        pl.col("receiving_yards").mean().alias("rec_yards_per_game"),

        pl.col("rushing_tds").sum().alias("rushing_tds"),
        pl.col("receiving_tds").sum().alias("receiving_tds"),

        league_points.mean().alias("fantasy_points_per_game"),
        pl.lit(rp).alias("reception_points"),

        pl.col("target_share").mean().alias("target_share"),
        pl.col("air_yards_share").mean().alias("air_yards_share"),
    )

    result = summary.to_dicts()[0]
    # Backward-compatible alias for old callers. New code should use
    # fantasy_points_per_game because the league may not be full PPR.
    result["ppr_points_per_game"] = result.get("fantasy_points_per_game")
    return result

@lru_cache(maxsize=1)
def get_schedule():
    """
    Load the NFL schedule once per program run.
    """

    return nfl.load_schedules(
        seasons=[SEASON],
    )


def team_has_game(
    nfl_team,
    current_week,
):
    """
    Return True if the team has a game this week.

    Return False if the schedule is available and the
    team definitely does not play.

    Return None if the schedule itself could not tell us.
    """

    if (
        nfl_team is None
        or current_week is None
    ):
        return None

    team = normalize_nfl_team(
        nfl_team
    )

    schedule = get_schedule()

    week_games = schedule.filter(
        pl.col("week")
        == current_week
    )

    # If nflverse has no Week N schedule rows at all,
    # do NOT assume every team is on bye.
    if week_games.height == 0:
        return None

    home_teams = {
        normalize_nfl_team(
            value
        )
        for value in (
            week_games[
                "home_team"
            ].to_list()
        )
    }

    away_teams = {
        normalize_nfl_team(
            value
        )
        for value in (
            week_games[
                "away_team"
            ].to_list()
        )
    }

    teams_playing = (
        home_teams
        | away_teams
    )

    return (
        team
        in teams_playing
    )


def is_team_on_bye(
    nfl_team,
    current_week,
):
    """
    Return True only when the schedule clearly shows
    that the team does not play this week.
    """

    has_game = team_has_game(
        nfl_team,
        current_week,
    )

    if has_game is None:
        return False

    return not has_game

if __name__ == "__main__":

    for team in (
        "DET",
        "CIN",
        "MIN",
        "MIA",
        "LAR",
        "WSH",
        "PIT",
    ):

        print(
            team,
            "bye =",
            is_team_on_bye(
                team,
                current_week=6,
            ),
        )