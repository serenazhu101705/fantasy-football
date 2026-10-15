from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import polars as pl

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import (
    mean_absolute_error,
    mean_squared_error,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


# ==================================================
# Paths
# ==================================================

DATASET_PATH = Path(
    "data/training/player_weeks.parquet"
)

MODEL_PATH = Path(
    "data/models/weekly_ppr_model.joblib"
)


# ==================================================
# Feature configuration
# ==================================================

NUMERIC_FEATURES = [
    "week",
    "games_played_before",

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

CATEGORICAL_FEATURES = [
    "position",
]

TARGET = "target_ppr"


# ==================================================
# Load dataset
# ==================================================

def load_dataset():
    """
    Load our leakage-safe player-week dataset.

    Use Polars first so we do not require pyarrow for
    pandas.read_parquet().
    """

    if not DATASET_PATH.exists():

        raise FileNotFoundError(
            f"Training dataset not found: "
            f"{DATASET_PATH}\n"
            f"Run app/training_data.py first."
        )

    print(
        f"Loading dataset from "
        f"{DATASET_PATH}..."
    )

    raw = pl.read_parquet(
        DATASET_PATH
    )

    df = pd.DataFrame(
        raw.to_dicts()
    )

    print(
        f"Loaded {len(df):,} rows."
    )

    return df


# ==================================================
# Metrics
# ==================================================

def calculate_metrics(
    actual,
    predicted,
):
    """
    Standard regression metrics.

    Lower is better for both.
    """

    mae = mean_absolute_error(
        actual,
        predicted,
    )

    rmse = (
        mean_squared_error(
            actual,
            predicted,
        )
        ** 0.5
    )

    return {
        "mae": mae,
        "rmse": rmse,
    }


def print_metrics(
    name,
    actual,
    predicted,
):
    metrics = calculate_metrics(
        actual,
        predicted,
    )

    print(
        f"{name:30} "
        f"MAE {metrics['mae']:6.3f}   "
        f"RMSE {metrics['rmse']:6.3f}"
    )

    return metrics


# ==================================================
# Baselines
# ==================================================

def evaluate_baselines(
    df,
    label,
):
    """
    Compare against extremely simple forecasting
    strategies.

    A useful ML model should beat these.
    """

    actual = df[
        TARGET
    ]

    print(
        f"\n=== {label} BASELINES ==="
    )

    # --------------------------------------------------
    # Baseline 1:
    # predict exactly what the player scored last game
    # --------------------------------------------------

    previous_game = (
        df[
            "prev_game_ppr"
        ]
        .fillna(
            0.0
        )
    )

    previous_metrics = (
        print_metrics(
            "Previous game PPR",
            actual,
            previous_game,
        )
    )

    # --------------------------------------------------
    # Baseline 2:
    # last-three-game average
    # --------------------------------------------------

    last_three = (
        df[
            "l3_ppr_avg"
        ]
        .fillna(
            0.0
        )
    )

    last_three_metrics = (
        print_metrics(
            "Last-3 PPR average",
            actual,
            last_three,
        )
    )

    # --------------------------------------------------
    # Baseline 3:
    # season-to-date average
    # --------------------------------------------------

    season_average = (
        df[
            "season_ppr_avg"
        ]
        .fillna(
            0.0
        )
    )

    season_metrics = (
        print_metrics(
            "Season PPR average",
            actual,
            season_average,
        )
    )

    return {
        "previous_game":
            previous_metrics,

        "last_three":
            last_three_metrics,

        "season_average":
            season_metrics,
    }


# ==================================================
# Model
# ==================================================

def build_model():
    """
    Version 1 learned model.

    HistGradientBoostingRegressor is a good first model
    for our tabular data because it can learn nonlinear
    relationships and interactions without requiring a
    neural network.
    """

    preprocessor = ColumnTransformer(
        transformers=[
            (
                "numeric",
                "passthrough",
                NUMERIC_FEATURES,
            ),
            (
                "position",
                OneHotEncoder(
                    handle_unknown="ignore",
                    sparse_output=False,
                ),
                CATEGORICAL_FEATURES,
            ),
        ],
        remainder="drop",
    )

    regressor = (
        HistGradientBoostingRegressor(
            learning_rate=0.05,
            max_iter=300,
            max_leaf_nodes=31,
            min_samples_leaf=30,
            l2_regularization=1.0,
            random_state=42,
        )
    )

    model = Pipeline(
        steps=[
            (
                "preprocessor",
                preprocessor,
            ),
            (
                "model",
                regressor,
            ),
        ]
    )

    return model


# ==================================================
# Position-level diagnostics
# ==================================================

def print_position_metrics(
    df,
    predictions,
    label,
):
    """
    A model can look good overall while being terrible
    at one position.

    Always inspect QB/RB/WR/TE separately.
    """

    result = df.copy()

    result[
        "prediction"
    ] = predictions

    print(
        f"\n=== {label} BY POSITION ==="
    )

    for position in (
        "QB",
        "RB",
        "WR",
        "TE",
    ):

        position_df = result[
            result[
                "position"
            ] == position
        ]

        if len(
            position_df
        ) == 0:
            continue

        metrics = calculate_metrics(
            position_df[
                TARGET
            ],
            position_df[
                "prediction"
            ],
        )

        print(
            f"{position:3} "
            f"n={len(position_df):5,}   "
            f"MAE {metrics['mae']:6.3f}   "
            f"RMSE {metrics['rmse']:6.3f}"
        )


# ==================================================
# Example predictions
# ==================================================

def print_prediction_examples(
    df,
    predictions,
    count=20,
):
    """
    Inspect some actual predictions rather than relying
    only on aggregate metrics.
    """

    result = df.copy()

    result[
        "predicted_ppr"
    ] = predictions

    result[
        "absolute_error"
    ] = (
        result[
            "predicted_ppr"
        ]
        - result[
            TARGET
        ]
    ).abs()

    columns = [
        "player_display_name",
        "position",
        "season",
        "week",
        "l3_ppr_avg",
        "season_ppr_avg",
        "predicted_ppr",
        TARGET,
        "absolute_error",
    ]

    print(
        "\n=== SAMPLE 2026 PREDICTIONS ==="
    )

    # Use the most recent rows rather than random rows
    # so the output is easier to reason about.
    examples = (
        result[
            columns
        ]
        .tail(
            count
        )
    )

    print(
        examples.to_string(
            index=False,
            formatters={
                "l3_ppr_avg":
                    lambda x: f"{x:.2f}",

                "season_ppr_avg":
                    lambda x: f"{x:.2f}",

                "predicted_ppr":
                    lambda x: f"{x:.2f}",

                TARGET:
                    lambda x: f"{x:.2f}",

                "absolute_error":
                    lambda x: f"{x:.2f}",
            },
        )
    )


# ==================================================
# Train
# ==================================================

def main():

    df = load_dataset()

    # --------------------------------------------------
    # Time-based splits
    # --------------------------------------------------

    train_df = df[
        df[
            "split"
        ] == "train"
    ].copy()

    validation_df = df[
        df[
            "split"
        ] == "validation"
    ].copy()

    test_df = df[
        df[
            "split"
        ] == "test"
    ].copy()

    print(
        "\n=== DATA SPLITS ==="
    )

    print(
        f"Train:      "
        f"{len(train_df):,}"
    )

    print(
        f"Validation: "
        f"{len(validation_df):,}"
    )

    print(
        f"Test:       "
        f"{len(test_df):,}"
    )

    # ==================================================
    # Baselines
    # ==================================================

    validation_baselines = (
        evaluate_baselines(
            validation_df,
            "2025 VALIDATION",
        )
    )

    test_baselines = (
        evaluate_baselines(
            test_df,
            "2026 TEST",
        )
    )

    # ==================================================
    # Stage 1
    #
    # Train using 2021-2024.
    # Evaluate on completely unseen 2025.
    # ==================================================

    print(
        "\n=== TRAIN MODEL: 2021-2024 ==="
    )

    validation_model = build_model()

    validation_features = (
        NUMERIC_FEATURES
        + CATEGORICAL_FEATURES
    )

    validation_model.fit(
        train_df[
            validation_features
        ],
        train_df[
            TARGET
        ],
    )

    validation_predictions = (
        validation_model.predict(
            validation_df[
                validation_features
            ]
        )
    )

    print(
        "\n=== 2025 VALIDATION RESULTS ==="
    )

    validation_ml_metrics = (
        print_metrics(
            "ML model",
            validation_df[
                TARGET
            ],
            validation_predictions,
        )
    )

    print_position_metrics(
        validation_df,
        validation_predictions,
        "2025 VALIDATION",
    )

    # ==================================================
    # Stage 2
    #
    # Once validation is complete, retrain using every
    # completed season:
    #
    # 2021-2025
    #
    # Then evaluate on unseen 2026.
    # ==================================================

    print(
        "\n=== TRAIN FINAL MODEL: 2021-2025 ==="
    )

    final_training_df = df[
        df[
            "season"
        ] <= 2025
    ].copy()

    final_model = build_model()

    final_model.fit(
        final_training_df[
            validation_features
        ],
        final_training_df[
            TARGET
        ],
    )

    test_predictions = (
        final_model.predict(
            test_df[
                validation_features
            ]
        )
    )

    print(
        "\n=== 2026 TEST RESULTS ==="
    )

    test_ml_metrics = (
        print_metrics(
            "ML model",
            test_df[
                TARGET
            ],
            test_predictions,
        )
    )

    print_position_metrics(
        test_df,
        test_predictions,
        "2026 TEST",
    )

    # ==================================================
    # Direct comparison
    # ==================================================

    print(
        "\n=== MODEL VS BASELINE ==="
    )

    baseline_mae = (
        test_baselines[
            "last_three"
        ][
            "mae"
        ]
    )

    ml_mae = (
        test_ml_metrics[
            "mae"
        ]
    )

    improvement = (
        baseline_mae
        - ml_mae
    )

    improvement_pct = (
        (
            improvement
            / baseline_mae
        )
        * 100.0
        if baseline_mae > 0
        else 0.0
    )

    print(
        f"Last-3 baseline MAE: "
        f"{baseline_mae:.3f}"
    )

    print(
        f"ML model MAE:        "
        f"{ml_mae:.3f}"
    )

    print(
        f"MAE improvement:     "
        f"{improvement:+.3f}"
    )

    print(
        f"Percent improvement: "
        f"{improvement_pct:+.2f}%"
    )

    if ml_mae < baseline_mae:

        print(
            "\nPASS: ML beats the "
            "last-3 PPR baseline."
        )

    else:

        print(
            "\nFAIL: ML does not yet beat the "
            "last-3 PPR baseline."
        )

        print(
            "We should improve the model/features "
            "before using it in live decisions."
        )

    # ==================================================
    # Sample predictions
    # ==================================================

    print_prediction_examples(
        test_df,
        test_predictions,
    )

    # ==================================================
    # Save model
    # ==================================================

    MODEL_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    model_package = {
        "model":
            final_model,

        "numeric_features":
            NUMERIC_FEATURES,

        "categorical_features":
            CATEGORICAL_FEATURES,

        "target":
            TARGET,

        "training_seasons":
            [
                2021,
                2022,
                2023,
                2024,
                2025,
            ],

        "test_season":
            2026,

        "validation_metrics":
            validation_ml_metrics,

        "test_metrics":
            test_ml_metrics,
    }

    joblib.dump(
        model_package,
        MODEL_PATH,
    )

    print(
        f"\nSaved model to: "
        f"{MODEL_PATH}"
    )


if __name__ == "__main__":
    main()