from functools import lru_cache

import nflreadpy as nfl
import polars as pl


SEASON = 2026


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


def get_recent_usage(player_name, current_week, last_n=3):
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

    summary = df.select(
        pl.len().alias("games"),

        pl.col("carries").mean().alias("carries_per_game"),
        pl.col("targets").mean().alias("targets_per_game"),
        pl.col("receptions").mean().alias("receptions_per_game"),

        pl.col("rushing_yards").mean().alias("rush_yards_per_game"),
        pl.col("receiving_yards").mean().alias("rec_yards_per_game"),

        pl.col("rushing_tds").sum().alias("rushing_tds"),
        pl.col("receiving_tds").sum().alias("receiving_tds"),

        pl.col("fantasy_points_ppr").mean().alias("ppr_points_per_game"),

        pl.col("target_share").mean().alias("target_share"),
        pl.col("air_yards_share").mean().alias("air_yards_share"),
    )

    return summary.to_dicts()[0]


if __name__ == "__main__":
    usage = get_recent_usage(
        "Jahmyr Gibbs",
        current_week=5,
        last_n=3,
    )

    print(usage)