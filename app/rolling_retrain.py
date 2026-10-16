from datetime import datetime, timezone
from pathlib import Path

import joblib
import pandas as pd

from espn_client import get_league

from train_ecr_model import (
    load_dataset,
    get_ecr_rows,
    build_ecr_model,
    ECR_MODEL_FEATURES,
    ECR_NUMERIC_FEATURES,
    CATEGORICAL_FEATURES,
    ECR_FEATURES,
    TARGET,
)


# ==================================================
# Configuration
# ==================================================

CURRENT_SEASON = 2026

MODEL_DIR = Path(
    "data/models"
)

LATEST_MODEL_PATH = (
    MODEL_DIR
    / "weekly_ppr_model_ecr_latest.joblib"
)


# ==================================================
# Training cutoff
# ==================================================

def get_training_cutoff():
    """
    Determine the latest week that is safe to use for
    training.

    If ESPN says the current fantasy week is Week 6,
    then Weeks 1-5 are considered completed historical
    data.

    We NEVER train on the current week because its
    games may still be in progress.
    """

    league = get_league()

    current_week = int(
        league.current_week
    )

    completed_through_week = (
        current_week
        - 1
    )

    return {
        "current_week":
            current_week,

        "completed_through_week":
            completed_through_week,
    }


# ==================================================
# Build leakage-safe rolling training set
# ==================================================

def build_rolling_training_set(
    df,
    completed_through_week,
):
    """
    Construct the training dataset that would be known
    before the current week begins.

    Include:

        all seasons before 2026

        PLUS

        2026 rows through the most recently completed
        week

    Example:

        Current week = 6

        Training data =
            2021
            2022
            2023
            2024
            2025
            2026 Weeks 1-5

    Week 6 is NEVER included.
    """

    ecr_df = get_ecr_rows(
        df
    )

    # --------------------------------------------------
    # Only rows with actual completed outcomes may train
    # the model.
    # --------------------------------------------------

    ecr_df = ecr_df[
        ecr_df[
            TARGET
        ].notna()
    ].copy()

    # --------------------------------------------------
    # Historical seasons are always safe.
    # --------------------------------------------------

    historical_mask = (
        ecr_df[
            "season"
        ]
        < CURRENT_SEASON
    )

    # --------------------------------------------------
    # Current-season data is safe only through the last
    # completed week.
    # --------------------------------------------------

    current_season_mask = (
        (
            ecr_df[
                "season"
            ]
            == CURRENT_SEASON
        )
        &
        (
            ecr_df[
                "week"
            ]
            <= completed_through_week
        )
    )

    training_df = ecr_df[
        historical_mask
        | current_season_mask
    ].copy()

    training_df = (
        training_df
        .sort_values(
            [
                "season",
                "week",
            ]
        )
        .reset_index(
            drop=True
        )
    )

    return training_df


# ==================================================
# Diagnostics
# ==================================================

def print_training_summary(
    training_df,
    current_week,
    completed_through_week,
):
    """
    Print exactly what data the rolling model is about
    to learn from.
    """

    print(
        "\n"
        + "=" * 72
    )

    print(
        "ROLLING ECR MODEL TRAINING"
    )

    print(
        "=" * 72
    )

    print(
        f"Current ESPN week: "
        f"{current_week}"
    )

    print(
        f"Training through: "
        f"2026 Week "
        f"{completed_through_week}"
    )

    print(
        f"Training rows: "
        f"{len(training_df):,}"
    )

    if len(
        training_df
    ) == 0:

        return

    print(
        "\nROWS BY SEASON"
    )

    season_counts = (
        training_df
        .groupby(
            "season"
        )
        .size()
    )

    for (
        season,
        count,
    ) in season_counts.items():

        print(
            f"  {int(season)}: "
            f"{count:,}"
        )

    current_rows = training_df[
        training_df[
            "season"
        ]
        == CURRENT_SEASON
    ]

    if len(
        current_rows
    ) > 0:

        print(
            "\n2026 ROWS BY WEEK"
        )

        week_counts = (
            current_rows
            .groupby(
                "week"
            )
            .size()
        )

        for (
            week,
            count,
        ) in week_counts.items():

            print(
                f"  Week "
                f"{int(week)}: "
                f"{count:,}"
            )

    else:

        print(
            "\nNo completed 2026 ECR rows "
            "available yet."
        )


# ==================================================
# Train rolling model
# ==================================================

def train_rolling_model(
    training_df,
):
    """
    Train a fresh ECR-enhanced model using every safe
    historical row available through the most recently
    completed week.
    """

    if len(
        training_df
    ) == 0:

        raise ValueError(
            "No valid rows available for "
            "rolling model training."
        )

    model = build_ecr_model()

    print(
        "\nTraining rolling "
        "ECR-enhanced model..."
    )

    model.fit(
        training_df[
            ECR_MODEL_FEATURES
        ],
        training_df[
            TARGET
        ],
    )

    return model


# ==================================================
# Save model versions
# ==================================================

def save_model(
    model,
    training_df,
    current_week,
    completed_through_week,
):
    """
    Save two copies:

    1. Immutable versioned model

       Example:
       weekly_ppr_model_ecr_through_2026_w5.joblib

    2. Latest rolling model

       weekly_ppr_model_ecr_latest.joblib

    The versioned models let us reproduce historical
    predictions later.
    """

    MODEL_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    versioned_path = (
        MODEL_DIR
        / (
            "weekly_ppr_model_ecr_"
            f"through_{CURRENT_SEASON}_"
            f"w{completed_through_week}.joblib"
        )
    )

    training_seasons = sorted(
        int(
            season
        )
        for season in (
            training_df[
                "season"
            ]
            .dropna()
            .unique()
        )
    )

    package = {
        "model":
            model,

        "numeric_features":
            ECR_NUMERIC_FEATURES,

        "categorical_features":
            CATEGORICAL_FEATURES,

        "ecr_features":
            ECR_FEATURES,

        "model_features":
            ECR_MODEL_FEATURES,

        "target":
            TARGET,

        "training_seasons":
            training_seasons,

        "current_season":
            CURRENT_SEASON,

        "current_week_at_training":
            current_week,

        "trained_through_week":
            completed_through_week,

        "training_rows":
            len(
                training_df
            ),

        "created_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "model_role":
            "shadow",

        "live_decision_weight":
            0.0,
    }

    # --------------------------------------------------
    # Immutable version
    # --------------------------------------------------

    joblib.dump(
        package,
        versioned_path,
    )

    # --------------------------------------------------
    # Most recent rolling version
    # --------------------------------------------------

    joblib.dump(
        package,
        LATEST_MODEL_PATH,
    )

    print(
        f"\nSaved versioned model:"
    )

    print(
        f"  {versioned_path}"
    )

    print(
        "\nUpdated latest rolling model:"
    )

    print(
        f"  {LATEST_MODEL_PATH}"
    )

    return {
        "versioned_path":
            versioned_path,

        "latest_path":
            LATEST_MODEL_PATH,
    }


# ==================================================
# Main
# ==================================================

def main():

    # --------------------------------------------------
    # Determine what weeks are safe.
    # --------------------------------------------------

    cutoff = get_training_cutoff()

    current_week = (
        cutoff[
            "current_week"
        ]
    )

    completed_through_week = (
        cutoff[
            "completed_through_week"
        ]
    )

    # --------------------------------------------------
    # Load the existing leakage-safe ECR dataset.
    # --------------------------------------------------

    df = load_dataset()

    # --------------------------------------------------
    # Build rolling training set.
    # --------------------------------------------------

    training_df = (
        build_rolling_training_set(
            df=df,
            completed_through_week=(
                completed_through_week
            ),
        )
    )

    print_training_summary(
        training_df=training_df,
        current_week=current_week,
        completed_through_week=(
            completed_through_week
        ),
    )

    # --------------------------------------------------
    # Train.
    # --------------------------------------------------

    model = train_rolling_model(
        training_df
    )

    # --------------------------------------------------
    # Save.
    # --------------------------------------------------

    save_model(
        model=model,
        training_df=training_df,
        current_week=current_week,
        completed_through_week=(
            completed_through_week
        ),
    )

    print(
        "\n"
        + "=" * 72
    )

    print(
        "ROLLING RETRAIN COMPLETE"
    )

    print(
        "=" * 72
    )

    print(
        f"This model may use actual results only "
        f"through 2026 Week "
        f"{completed_through_week}."
    )

    print(
        f"It is therefore safe to generate "
        f"predictions for Week "
        f"{current_week}."
    )

    print(
        "\nThe rolling model remains in SHADOW "
        "MODE for now."
    )

    print(
        "It does not yet change live weekly_score "
        "or lineup decisions."
    )


if __name__ == "__main__":
    main()