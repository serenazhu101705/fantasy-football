from pathlib import Path
import re
import unicodedata

import pandas as pd
import polars as pl


# ==================================================
# Paths
# ==================================================

PLAYER_WEEKS_PATH = Path(
    "data/training/player_weeks.parquet"
)

ECR_PATH = Path(
    "data/training/historical_weekly_ecr.parquet"
)

OUTPUT_PATH = Path(
    "data/training/player_weeks_with_ecr.parquet"
)


# ==================================================
# Name normalization
# ==================================================

def normalize_name(
    value,
):
    """
    Normalize names so nflverse player names can be
    matched to historical FantasyPros names.
    """

    if value is None:
        return ""

    name = str(
        value
    ).strip()

    name = unicodedata.normalize(
        "NFKD",
        name,
    )

    name = "".join(
        character
        for character in name
        if not unicodedata.combining(
            character
        )
    )

    name = name.lower()

    # Remove punctuation.
    name = re.sub(
        r"[^a-z0-9\s]",
        "",
        name,
    )

    # Remove common suffixes.
    name = re.sub(
        r"\b(jr|sr|ii|iii|iv|v)\b",
        "",
        name,
    )

    # Collapse whitespace.
    name = re.sub(
        r"\s+",
        " ",
        name,
    ).strip()

    return name


# ==================================================
# Load
# ==================================================

def load_parquet_as_pandas(
    path,
):

    if not path.exists():

        raise FileNotFoundError(
            f"Missing file: {path}"
        )

    raw = pl.read_parquet(
        path
    )

    return pd.DataFrame(
        raw.to_dicts()
    )


# ==================================================
# ECR feature engineering
# ==================================================

def prepare_ecr(
    ecr,
):
    """
    Prepare historical weekly ECR for merging.

    ECR is a rank:
        lower = better

    We also create a bounded rank score:
        higher = better

    That gives the ML model both forms.
    """

    ecr = ecr.copy()

    numeric_columns = [
        "fp_weekly_ecr",
        "fp_weekly_sd",
        "fp_weekly_best",
        "fp_weekly_worst",
    ]

    for column in numeric_columns:

        ecr[
            column
        ] = pd.to_numeric(
            ecr[
                column
            ],
            errors="coerce",
        )

    # ----------------------------------------------
    # Rank score
    #
    # ECR 1  -> 1.000
    # ECR 10 -> ~0.526
    # ECR 25 -> ~0.294
    # ECR 50 -> ~0.169
    #
    # Same style as the rank transform already used
    # elsewhere in the project.
    # ----------------------------------------------

    ecr[
        "fp_weekly_ecr_score"
    ] = (
        1.0
        /
        (
            1.0
            + 0.1
            * (
                ecr[
                    "fp_weekly_ecr"
                ]
                - 1.0
            )
        )
    )

    # ----------------------------------------------
    # Expert disagreement range
    #
    # Larger = experts disagree more.
    # ----------------------------------------------

    ecr[
        "fp_weekly_range"
    ] = (
        ecr[
            "fp_weekly_worst"
        ]
        - ecr[
            "fp_weekly_best"
        ]
    )

    # Only keep columns needed by the model.
    ecr = ecr[
        [
            "season",
            "week",
            "normalized_name",
            "position",

            "scrape_date",
            "game_date",

            "fp_weekly_ecr",
            "fp_weekly_ecr_score",
            "fp_weekly_sd",
            "fp_weekly_best",
            "fp_weekly_worst",
            "fp_weekly_range",
        ]
    ].copy()

    return ecr


# ==================================================
# Merge
# ==================================================

def merge_datasets(
    player_weeks,
    ecr,
):
    """
    Merge historical player-week stats with the
    leakage-safe FantasyPros weekly ECR snapshot.

    We keep ALL player-week rows initially so we can
    measure coverage accurately.

    The training script can later restrict itself to
    rows with ECR.
    """

    player_weeks = (
        player_weeks.copy()
    )

    player_weeks[
        "normalized_name"
    ] = player_weeks[
        "player_display_name"
    ].apply(
        normalize_name
    )

    merged = player_weeks.merge(
        ecr,
        how="left",
        on=[
            "season",
            "week",
            "normalized_name",
            "position",
        ],
        validate="many_to_one",
    )

    merged[
        "has_weekly_ecr"
    ] = (
        merged[
            "fp_weekly_ecr"
        ].notna()
    )

    return merged


# ==================================================
# Diagnostics
# ==================================================

def print_summary(
    merged,
):
    """
    Inspect ECR join quality before any training.
    """

    total = len(
        merged
    )

    matched = int(
        merged[
            "has_weekly_ecr"
        ].sum()
    )

    unmatched = (
        total
        - matched
    )

    coverage = (
        matched
        / total
        if total > 0
        else 0.0
    )

    print(
        "\n"
        + "=" * 72
    )

    print(
        "PLAYER-WEEK + HISTORICAL ECR MERGE"
    )

    print(
        "=" * 72
    )

    print(
        f"Total player-weeks: "
        f"{total:,}"
    )

    print(
        f"Matched ECR:       "
        f"{matched:,}"
    )

    print(
        f"Unmatched:         "
        f"{unmatched:,}"
    )

    print(
        f"Coverage:          "
        f"{coverage:.2%}"
    )

    # ==================================================
    # Season coverage
    # ==================================================

    season_summary = (
        merged
        .groupby(
            "season"
        )
        .agg(
            rows=(
                "has_weekly_ecr",
                "size",
            ),
            matched=(
                "has_weekly_ecr",
                "sum",
            ),
        )
    )

    season_summary[
        "coverage"
    ] = (
        season_summary[
            "matched"
        ]
        / season_summary[
            "rows"
        ]
    )

    print(
        "\nCOVERAGE BY SEASON"
    )

    print(
        season_summary.to_string(
            formatters={
                "coverage":
                    lambda x:
                        f"{x:.2%}",
            }
        )
    )

    # ==================================================
    # Position coverage
    # ==================================================

    position_summary = (
        merged
        .groupby(
            "position"
        )
        .agg(
            rows=(
                "has_weekly_ecr",
                "size",
            ),
            matched=(
                "has_weekly_ecr",
                "sum",
            ),
        )
    )

    position_summary[
        "coverage"
    ] = (
        position_summary[
            "matched"
        ]
        / position_summary[
            "rows"
        ]
    )

    print(
        "\nCOVERAGE BY POSITION"
    )

    print(
        position_summary.to_string(
            formatters={
                "coverage":
                    lambda x:
                        f"{x:.2%}",
            }
        )
    )

    # ==================================================
    # Season + position
    # ==================================================

    detailed = (
        merged
        .groupby(
            [
                "season",
                "position",
            ]
        )
        .agg(
            rows=(
                "has_weekly_ecr",
                "size",
            ),
            matched=(
                "has_weekly_ecr",
                "sum",
            ),
        )
        .reset_index()
    )

    detailed[
        "coverage"
    ] = (
        detailed[
            "matched"
        ]
        / detailed[
            "rows"
        ]
    )

    print(
        "\nCOVERAGE BY SEASON + POSITION"
    )

    print(
        detailed.to_string(
            index=False,
            formatters={
                "coverage":
                    lambda x:
                        f"{x:.2%}",
            },
        )
    )

    # ==================================================
    # Match sanity check
    # ==================================================

    matched_rows = merged[
        merged[
            "has_weekly_ecr"
        ]
    ].copy()

    print(
        "\nSAMPLE MATCHED ROWS"
    )

    sample_columns = [
        "season",
        "week",
        "player_display_name",
        "position",
        "target_ppr",
        "fp_weekly_ecr",
        "fp_weekly_ecr_score",
        "fp_weekly_sd",
        "fp_weekly_range",
    ]

    print(
        matched_rows[
            sample_columns
        ]
        .tail(
            30
        )
        .to_string(
            index=False
        )
    )

    # ==================================================
    # Unmatched fantasy-relevant examples
    #
    # Sort by season average so we see important
    # unmatched players rather than obscure players.
    # ==================================================

    unmatched_rows = merged[
        ~merged[
            "has_weekly_ecr"
        ]
    ].copy()

    unmatched_rows = (
        unmatched_rows
        .sort_values(
            "season_ppr_avg",
            ascending=False,
        )
    )

    print(
        "\nTOP UNMATCHED PLAYER-WEEKS"
    )

    unmatched_columns = [
        "season",
        "week",
        "player_display_name",
        "position",
        "season_ppr_avg",
        "target_ppr",
    ]

    print(
        unmatched_rows[
            unmatched_columns
        ]
        .head(
            40
        )
        .to_string(
            index=False
        )
    )


# ==================================================
# Save
# ==================================================

def save_dataset(
    merged,
):

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Avoid pandas -> Polars nullable dtype issues.
    records = (
        merged
        .astype(
            object
        )
        .where(
            pd.notna(
                merged
            ),
            None,
        )
        .to_dict(
            orient="records"
        )
    )

    pl.DataFrame(
        records
    ).write_parquet(
        OUTPUT_PATH
    )

    print(
        f"\nSaved merged dataset to: "
        f"{OUTPUT_PATH}"
    )


# ==================================================
# Main
# ==================================================

def main():

    print(
        "Loading historical player-week dataset..."
    )

    player_weeks = (
        load_parquet_as_pandas(
            PLAYER_WEEKS_PATH
        )
    )

    print(
        f"Player-week rows: "
        f"{len(player_weeks):,}"
    )

    print(
        "Loading historical weekly ECR..."
    )

    ecr = load_parquet_as_pandas(
        ECR_PATH
    )

    print(
        f"ECR rows: "
        f"{len(ecr):,}"
    )

    ecr = prepare_ecr(
        ecr
    )

    merged = merge_datasets(
        player_weeks,
        ecr,
    )

    print_summary(
        merged
    )

    save_dataset(
        merged
    )


if __name__ == "__main__":
    main()