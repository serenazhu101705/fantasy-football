from pathlib import Path

import nflreadpy as nfl
import pandas as pd
import polars as pl

import time


# ==================================================
# Configuration
# ==================================================

SEASONS = [
    2021,
    2022,
    2023,
    2024,
    2025,
    2026,
]

POSITIONS = (
    "QB",
    "RB",
    "WR",
    "TE",
)

OUTPUT_PATH = Path(
    "data/training/player_weeks.parquet"
)

RAW_CACHE_DIR = Path(
    "data/cache/nflverse"
)

CURRENT_SEASON = max(
    SEASONS
)

CURRENT_SEASON_CACHE_TTL_HOURS = 24

# ==================================================
# Column helpers
# ==================================================

def first_existing_column(
    df,
    candidates,
):
    """
    Return the first column name that exists.

    This lets us tolerate small nflverse naming changes
    between dataset versions.
    """

    for column in candidates:

        if column in df.columns:
            return column

    return None


def add_missing_numeric_column(
    df,
    column,
):
    """
    Add a numeric column filled with zero if nflverse
    does not provide it.

    This is useful for optional historical stat fields.
    """

    if column not in df.columns:

        df[
            column
        ] = 0.0

    return df


# ==================================================
# Load raw nflverse data
# ==================================================
def should_use_cached_season(
    cache_path,
    season,
    force_refresh=False,
):
    """
    Decide whether a cached nflverse season is fresh
    enough to use.

    Completed seasons:
        cache forever

    Current season:
        refresh after 24 hours

    force_refresh=True:
        always refresh
    """

    if force_refresh:
        return False

    if not cache_path.exists():
        return False

    # ----------------------------------------------
    # Completed seasons never change.
    # ----------------------------------------------

    if season < CURRENT_SEASON:
        return True

    # ----------------------------------------------
    # Current season can change every week.
    # Refresh once the cache is older than the TTL.
    # ----------------------------------------------

    modified_time = (
        cache_path.stat().st_mtime
    )

    age_seconds = (
        time.time()
        - modified_time
    )

    ttl_seconds = (
        CURRENT_SEASON_CACHE_TTL_HOURS
        * 60
        * 60
    )

    return (
        age_seconds
        < ttl_seconds
    )

def load_raw_player_weeks(
    force_refresh=False,
    force_refresh_current=False,
):
    """
    Load nflverse weekly player stats.

    Cache policy:

        completed seasons -> cached forever

        current season -> refreshed after
                          24 hours

        force_refresh=True -> refresh everything
    """

    RAW_CACHE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    season_frames = []

    for season in SEASONS:

        cache_path = (
            RAW_CACHE_DIR
            / f"player_stats_{season}.parquet"
        )

        season_force_refresh = (
            force_refresh
            or (
                force_refresh_current
                and season
                == CURRENT_SEASON
            )
        )

        use_cache = (
            should_use_cached_season(
                cache_path=cache_path,
                season=season,
                force_refresh=(
                    season_force_refresh
                ),
            )
        )

        # ------------------------------------------
        # Fresh cache
        # ------------------------------------------

        if use_cache:

            print(
                f"[CACHE] nflverse "
                f"player stats {season}"
            )

            season_df = (
                pl.read_parquet(
                    cache_path
                )
            )

        # ------------------------------------------
        # Missing / stale cache
        # ------------------------------------------

        else:

            if cache_path.exists():

                print(
                    f"[STALE] nflverse "
                    f"player stats {season}"
                )

            else:

                print(
                    f"[MISS] nflverse "
                    f"player stats {season}"
                )

            print(
                f"[API] nflverse "
                f"player stats {season}"
            )

            season_df = (
                nfl.load_player_stats(
                    seasons=[
                        season
                    ],
                    summary_level="week",
                )
            )

            season_df.write_parquet(
                cache_path
            )

            print(
                f"[SAVED] {cache_path}"
            )

        season_frames.append(
            season_df
        )

    # ----------------------------------------------
    # Combine all seasons
    # ----------------------------------------------

    raw = pl.concat(
        season_frames,
        how="diagonal_relaxed",
    )

    # ----------------------------------------------
    # Convert to pandas
    # ----------------------------------------------

    df = pd.DataFrame(
        raw.to_dicts()
    )

    print(
        f"Loaded {len(df):,} raw "
        f"player-week rows."
    )

    return df
# ==================================================
# Standardize nflverse columns
# ==================================================

def standardize_columns(
    df,
):
    """
    Normalize the handful of columns needed by our
    first ML dataset.
    """

    df = df.copy()

    # --------------------------------------------------
    # Required columns
    # --------------------------------------------------

    required = [
        "player_id",
        "player_display_name",
        "position",
        "season",
        "week",
        "fantasy_points_ppr",
    ]

    missing_required = [
        column
        for column in required
        if column not in df.columns
    ]

    if missing_required:

        raise RuntimeError(
            "nflverse is missing required columns: "
            + ", ".join(
                missing_required
            )
        )

    # --------------------------------------------------
    # Team column
    #
    # Some nflverse versions have historically used
    # recent_team while current datasets may use team.
    # --------------------------------------------------

    team_column = first_existing_column(
        df,
        [
            "team",
            "recent_team",
        ],
    )

    if team_column is None:

        df[
            "team"
        ] = None

    elif team_column != "team":

        df[
            "team"
        ] = df[
            team_column
        ]

    # --------------------------------------------------
    # Opponent
    # --------------------------------------------------

    if (
        "opponent_team"
        not in df.columns
    ):

        df[
            "opponent_team"
        ] = None

    # --------------------------------------------------
    # Passing interceptions
    #
    # nflverse has used both names in historical
    # versions of the stats dataset.
    # --------------------------------------------------

    interceptions_column = (
        first_existing_column(
            df,
            [
                "passing_interceptions",
                "interceptions",
            ],
        )
    )

    if interceptions_column is None:

        df[
            "passing_interceptions"
        ] = 0.0

    elif (
        interceptions_column
        != "passing_interceptions"
    ):

        df[
            "passing_interceptions"
        ] = df[
            interceptions_column
        ]

    # --------------------------------------------------
    # Optional numerical statistics
    # --------------------------------------------------

    numeric_columns = [
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

    for column in numeric_columns:

        df = add_missing_numeric_column(
            df,
            column,
        )

    # --------------------------------------------------
    # Regular season only
    # --------------------------------------------------

    if "season_type" in df.columns:

        df = df[
            df[
                "season_type"
            ] == "REG"
        ].copy()

    # --------------------------------------------------
    # Fantasy-relevant offensive positions only
    # --------------------------------------------------

    df = df[
        df[
            "position"
        ].isin(
            POSITIONS
        )
    ].copy()

    # --------------------------------------------------
    # Remove rows without a usable player identity
    # --------------------------------------------------

    df = df[
        df[
            "player_id"
        ].notna()
    ].copy()

    df = df[
        df[
            "player_display_name"
        ].notna()
    ].copy()

    # --------------------------------------------------
    # Normalize numerical types
    # --------------------------------------------------

    stat_columns = (
        numeric_columns
        + [
            "fantasy_points_ppr",
            "season",
            "week",
        ]
    )

    for column in stat_columns:

        df[
            column
        ] = pd.to_numeric(
            df[
                column
            ],
            errors="coerce",
        )

    # Current week's target must actually exist.
    df = df[
        df[
            "fantasy_points_ppr"
        ].notna()
    ].copy()

    # Usage statistics sometimes contain NaN or inf.
    # For our first version, treat unavailable usage
    # measurements as zero.
    for column in numeric_columns:

        df[
            column
        ] = (
            df[
                column
            ]
            .replace(
                [
                    float("inf"),
                    float("-inf"),
                ],
                pd.NA,
            )
            .fillna(
                0.0
            )
        )

    df[
        "season"
    ] = df[
        "season"
    ].astype(
        int
    )

    df[
        "week"
    ] = df[
        "week"
    ].astype(
        int
    )

    return df


# ==================================================
# Historical feature engineering
# ==================================================

def add_history_features(
    df,
):
    """
    Create features using ONLY information from games
    before the row being predicted.

    Example:

        Row = Player X, Week 6

    Features can use:
        Weeks 1-5

    Features CANNOT use:
        anything from Week 6

    This shift-before-rolling rule prevents target
    leakage.
    """

    df = df.copy()

    # Chronological order is critical.
    df = df.sort_values(
        [
            "player_id",
            "season",
            "week",
        ]
    ).reset_index(
        drop=True
    )

    group_columns = [
        "player_id",
        "season",
    ]

    grouped = df.groupby(
        group_columns,
        sort=False,
    )

    # --------------------------------------------------
    # How many games has this player already played
    # this season BEFORE this row?
    # --------------------------------------------------

    df[
        "games_played_before"
    ] = grouped.cumcount()

    # --------------------------------------------------
    # Previous-game PPR
    # --------------------------------------------------

    df[
        "prev_game_ppr"
    ] = grouped[
        "fantasy_points_ppr"
    ].shift(
        1
    )

    # --------------------------------------------------
    # Last-three-game averages
    #
    # IMPORTANT:
    # shift(1) happens BEFORE rolling(3).
    #
    # Therefore the current game's statistics are never
    # included in its own features.
    # --------------------------------------------------

    rolling_stats = [
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

    for column in rolling_stats:

        feature_name = (
            "l3_ppr_avg"
            if column
            == "fantasy_points_ppr"
            else f"l3_{column}_avg"
        )

        df[
            feature_name
        ] = grouped[
            column
        ].transform(
            lambda series: (
                series
                .shift(
                    1
                )
                .rolling(
                    window=3,
                    min_periods=1,
                )
                .mean()
            )
        )

    # --------------------------------------------------
    # Season-to-date PPR average
    #
    # Again, shift first, so Week N only sees Weeks < N.
    # --------------------------------------------------

    df[
        "season_ppr_avg"
    ] = grouped[
        "fantasy_points_ppr"
    ].transform(
        lambda series: (
            series
            .shift(
                1
            )
            .expanding(
                min_periods=1
            )
            .mean()
        )
    )

    # --------------------------------------------------
    # Season-to-date opportunity
    #
    # Useful distinction between:
    #
    # hot fantasy scoring
    # vs
    # sustained workload
    # --------------------------------------------------

    df[
        "opportunities"
    ] = (
        df[
            "carries"
        ]
        + df[
            "targets"
        ]
    )

    grouped = df.groupby(
        group_columns,
        sort=False,
    )

    df[
        "l3_opportunities_avg"
    ] = grouped[
        "opportunities"
    ].transform(
        lambda series: (
            series
            .shift(
                1
            )
            .rolling(
                window=3,
                min_periods=1,
            )
            .mean()
        )
    )

    # --------------------------------------------------
    # Target
    #
    # This is what the model will eventually learn to
    # predict.
    # --------------------------------------------------

    df[
        "target_ppr"
    ] = df[
        "fantasy_points_ppr"
    ]

    return df


# ==================================================
# Train / validation / test split
# ==================================================

def add_dataset_split(
    df,
):
    """
    Time-based split.

    We deliberately do NOT randomly split player-weeks.

        2021-2024 -> train
        2025      -> validation
        2026      -> test

    This better represents the real task:
    predicting future football from past football.
    """

    df = df.copy()

    df[
        "split"
    ] = "unused"

    df.loc[
        df[
            "season"
        ] <= 2024,
        "split",
    ] = "train"

    df.loc[
        df[
            "season"
        ] == 2025,
        "split",
    ] = "validation"

    df.loc[
        df[
            "season"
        ] == 2026,
        "split",
    ] = "test"

    return df


# ==================================================
# Final dataset
# ==================================================

def build_training_dataset(
    force_refresh_current=False,
):
    """
    Build a leakage-safe player-week dataset.

    Output contains ONLY:

        metadata
        historical features
        target

    We intentionally do not retain the current week's
    raw box-score statistics as feature columns.
    """

    df = load_raw_player_weeks(
        force_refresh_current=(
            force_refresh_current
        )
    )

    df = standardize_columns(
        df
    )

    print(
        f"After filtering to QB/RB/WR/TE: "
        f"{len(df):,} rows."
    )

    df = add_history_features(
        df
    )

    df = add_dataset_split(
        df
    )

    # --------------------------------------------------
    # First game of a player's season has no same-season
    # history.
    #
    # For Version 1 we exclude those rows rather than
    # inventing preseason / prior-season features.
    # --------------------------------------------------

    df = df[
        df[
            "games_played_before"
        ] >= 1
    ].copy()

    # --------------------------------------------------
    # Explicit feature list
    #
    # Keeping this list explicit makes leakage much less
    # likely when we train the model.
    # --------------------------------------------------

    feature_columns = [
        "prev_game_ppr",
        "l3_ppr_avg",
        "season_ppr_avg",

        "l3_completions_avg",
        "l3_attempts_avg",
        "l3_passing_yards_avg",
        "l3_passing_tds_avg",
        "l3_passing_interceptions_avg",

        "l3_carries_avg",
        "l3_rushing_yards_avg",
        "l3_rushing_tds_avg",

        "l3_targets_avg",
        "l3_receptions_avg",
        "l3_receiving_yards_avg",
        "l3_receiving_tds_avg",

        "l3_target_share_avg",
        "l3_air_yards_share_avg",

        "l3_opportunities_avg",
    ]

    metadata_columns = [
        "player_id",
        "player_display_name",
        "position",
        "team",
        "opponent_team",
        "season",
        "week",
        "games_played_before",
    ]

    final_columns = (
        metadata_columns
        + feature_columns
        + [
            "target_ppr",
            "split",
        ]
    )

    dataset = df[
        final_columns
    ].copy()

    # --------------------------------------------------
    # Defensive leakage check
    # --------------------------------------------------

    forbidden_columns = {
        "carries",
        "targets",
        "receptions",
        "rushing_yards",
        "receiving_yards",
        "passing_yards",
        "fantasy_points_ppr",
    }

    accidentally_included = (
        forbidden_columns
        & set(
            dataset.columns
        )
    )

    if accidentally_included:

        raise RuntimeError(
            "Potential target leakage! "
            "Current-week raw statistics appeared "
            "in the model dataset: "
            + ", ".join(
                sorted(
                    accidentally_included
                )
            )
        )

    # --------------------------------------------------
    # Save
    # --------------------------------------------------

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------
    # Convert pandas values to ordinary Python values
    # before constructing the Polars DataFrame.
    #
    # This avoids requiring pyarrow for pandas nullable
    # dtypes such as Int64.
    # --------------------------------------------------

    dataset_for_save = (
        dataset
        .astype(object)
        .where(
            pd.notna(
                dataset
            ),
            None,
        )
    )

    polars_dataset = pl.DataFrame(
        dataset_for_save.to_dict(
            orient="list"
        )
    )

    polars_dataset.write_parquet(
        OUTPUT_PATH
    )

    return (
        dataset,
        feature_columns,
    )

# ==================================================
# Diagnostics
# ==================================================

def print_dataset_summary(
    dataset,
    feature_columns,
):
    """
    Give us enough information to verify the dataset
    before training any model.
    """

    print(
        "\n=== TRAINING DATASET ==="
    )

    print(
        f"Rows: "
        f"{len(dataset):,}"
    )

    print(
        f"Features: "
        f"{len(feature_columns)}"
    )

    print(
        f"Saved to: "
        f"{OUTPUT_PATH}"
    )

    print(
        "\n=== SPLIT COUNTS ==="
    )

    split_counts = (
        dataset[
            "split"
        ]
        .value_counts()
    )

    print(
        split_counts.to_string()
    )

    print(
        "\n=== POSITION COUNTS ==="
    )

    position_counts = (
        dataset[
            "position"
        ]
        .value_counts()
    )

    print(
        position_counts.to_string()
    )

    print(
        "\n=== SEASON COUNTS ==="
    )

    season_counts = (
        dataset[
            "season"
        ]
        .value_counts()
        .sort_index()
    )

    print(
        season_counts.to_string()
    )

    print(
        "\n=== FEATURE COLUMNS ==="
    )

    for feature in feature_columns:

        print(
            feature
        )

    print(
        "\n=== SAMPLE ROWS ==="
    )

    sample_columns = [
        "player_display_name",
        "position",
        "season",
        "week",
        "games_played_before",
        "prev_game_ppr",
        "l3_ppr_avg",
        "season_ppr_avg",
        "l3_targets_avg",
        "l3_carries_avg",
        "l3_opportunities_avg",
        "target_ppr",
        "split",
    ]

    print(
        dataset[
            sample_columns
        ]
        .tail(
            20
        )
        .to_string(
            index=False
        )
    )

    # --------------------------------------------------
    # Quick leakage sanity check
    # --------------------------------------------------

    print(
        "\n=== LEAKAGE CHECK ==="
    )

    print(
        "All model features are shifted so they use "
        "only games BEFORE the target week."
    )

    print(
        "Current-week box-score statistics are not "
        "included as feature columns."
    )


# ==================================================
# Entry point
# ==================================================

def main():

    dataset, feature_columns = (
        build_training_dataset()
    )

    print_dataset_summary(
        dataset,
        feature_columns,
    )


if __name__ == "__main__":
    main()