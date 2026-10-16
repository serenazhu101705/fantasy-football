from pathlib import Path

import joblib
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

    Lower is better.
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

def get_baseline_predictions(
    df,
):
    """
    Generate simple comparison predictions.

    These are intentionally simple so the ML model
    has meaningful baselines to beat.
    """

    return {
        "Previous game":
            df[
                "prev_game_ppr"
            ].fillna(
                0.0
            ),

        "Last-3 average":
            df[
                "l3_ppr_avg"
            ].fillna(
                0.0
            ),

        "Season average":
            df[
                "season_ppr_avg"
            ].fillna(
                0.0
            ),
    }


def evaluate_baselines(
    df,
    label,
):
    """
    Evaluate simple overall baselines.
    """

    actual = df[
        TARGET
    ]

    predictions = (
        get_baseline_predictions(
            df
        )
    )

    print(
        f"\n=== {label} BASELINES ==="
    )

    results = {}

    for (
        name,
        prediction,
    ) in predictions.items():

        results[
            name
        ] = print_metrics(
            name,
            actual,
            prediction,
        )

    return results


# ==================================================
# Model
# ==================================================

def build_model():
    """
    Version 1 learned weekly fantasy model.
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
# Basic ML position metrics
# ==================================================

def print_position_metrics(
    df,
    predictions,
    label,
):
    """
    Show ML MAE/RMSE by position.
    """

    result = df.copy()

    result[
        "prediction"
    ] = predictions

    print(
        f"\n=== {label} ML BY POSITION ==="
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
# NEW:
# Compare ML against baselines by position
# ==================================================

def compare_by_position(
    df,
    ml_predictions,
    label,
):
    """
    Compare the ML model against every baseline for
    QB, RB, WR, and TE separately.

    This tells us whether one universal model is
    actually helping each position.

    Positive ML vs Best % = ML is better.
    Negative ML vs Best % = ML is worse.
    """

    result = df.copy()

    result[
        "ml_prediction"
    ] = ml_predictions

    print(
        f"\n=== {label} MODEL VS BASELINES BY POSITION ==="
    )

    print(
        f"{'POS':<5}"
        f"{'N':>7}"
        f"{'PREV':>10}"
        f"{'L3':>10}"
        f"{'SEASON':>10}"
        f"{'ML':>10}"
        f"{'BEST BASE':>15}"
        f"{'ML vs BEST':>13}"
    )

    position_results = {}

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
        ].copy()

        if len(
            position_df
        ) == 0:
            continue

        actual = position_df[
            TARGET
        ]

        # ------------------------------------------
        # Baseline MAEs
        # ------------------------------------------

        previous_mae = (
            mean_absolute_error(
                actual,
                position_df[
                    "prev_game_ppr"
                ].fillna(
                    0.0
                ),
            )
        )

        last_three_mae = (
            mean_absolute_error(
                actual,
                position_df[
                    "l3_ppr_avg"
                ].fillna(
                    0.0
                ),
            )
        )

        season_mae = (
            mean_absolute_error(
                actual,
                position_df[
                    "season_ppr_avg"
                ].fillna(
                    0.0
                ),
            )
        )

        ml_mae = (
            mean_absolute_error(
                actual,
                position_df[
                    "ml_prediction"
                ],
            )
        )

        # ------------------------------------------
        # Find strongest simple baseline
        # ------------------------------------------

        baseline_maes = {
            "PREV": previous_mae,
            "L3": last_three_mae,
            "SEASON": season_mae,
        }

        best_baseline_name = min(
            baseline_maes,
            key=baseline_maes.get,
        )

        best_baseline_mae = (
            baseline_maes[
                best_baseline_name
            ]
        )

        # Positive = ML is better.
        improvement = (
            best_baseline_mae
            - ml_mae
        )

        improvement_pct = (
            (
                improvement
                / best_baseline_mae
            )
            * 100.0
            if best_baseline_mae > 0
            else 0.0
        )

        position_results[
            position
        ] = {
            "n":
                len(
                    position_df
                ),

            "previous_mae":
                previous_mae,

            "last_three_mae":
                last_three_mae,

            "season_mae":
                season_mae,

            "ml_mae":
                ml_mae,

            "best_baseline":
                best_baseline_name,

            "best_baseline_mae":
                best_baseline_mae,

            "improvement":
                improvement,

            "improvement_pct":
                improvement_pct,
        }

        best_text = (
            f"{best_baseline_name} "
            f"{best_baseline_mae:.3f}"
        )

        improvement_text = (
            f"{improvement_pct:+.2f}%"
        )

        print(
            f"{position:<5}"
            f"{len(position_df):>7,}"
            f"{previous_mae:>10.3f}"
            f"{last_three_mae:>10.3f}"
            f"{season_mae:>10.3f}"
            f"{ml_mae:>10.3f}"
            f"{best_text:>15}"
            f"{improvement_text:>13}"
        )

    # ----------------------------------------------
    # Position conclusions
    # ----------------------------------------------

    print(
        f"\n=== {label} POSITION VERDICTS ==="
    )

    for (
        position,
        metrics,
    ) in position_results.items():

        improvement_pct = (
            metrics[
                "improvement_pct"
            ]
        )

        if improvement_pct > 1.0:

            verdict = "PASS"

        elif improvement_pct >= -1.0:

            verdict = "TIE"

        else:

            verdict = "FAIL"

        print(
            f"{position}: "
            f"{verdict} "
            f"({improvement_pct:+.2f}% "
            f"vs best simple baseline)"
        )

    return position_results


# ==================================================
# Example predictions
# ==================================================

def print_prediction_examples(
    df,
    predictions,
    count=20,
):
    """
    Inspect recent 2026 predictions.
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
# Main
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
    # Overall baselines
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
    # Stage 1:
    # Train 2021-2024
    # Validate on 2025
    # ==================================================

    print(
        "\n=== TRAIN MODEL: 2021-2024 ==="
    )

    validation_model = build_model()

    model_features = (
        NUMERIC_FEATURES
        + CATEGORICAL_FEATURES
    )

    validation_model.fit(
        train_df[
            model_features
        ],
        train_df[
            TARGET
        ],
    )

    validation_predictions = (
        validation_model.predict(
            validation_df[
                model_features
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

    validation_position_results = (
        compare_by_position(
            validation_df,
            validation_predictions,
            "2025 VALIDATION",
        )
    )

    # ==================================================
    # Stage 2:
    # Train 2021-2025
    # Test on unseen 2026
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
            model_features
        ],
        final_training_df[
            TARGET
        ],
    )

    test_predictions = (
        final_model.predict(
            test_df[
                model_features
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

    test_position_results = (
        compare_by_position(
            test_df,
            test_predictions,
            "2026 TEST",
        )
    )

    # ==================================================
    # Overall comparison against BEST simple baseline
    # ==================================================

    print(
        "\n=== OVERALL MODEL VS BEST BASELINE ==="
    )

    overall_baselines = {
        name:
            metrics[
                "mae"
            ]
        for (
            name,
            metrics,
        ) in test_baselines.items()
    }

    best_baseline_name = min(
        overall_baselines,
        key=overall_baselines.get,
    )

    best_baseline_mae = (
        overall_baselines[
            best_baseline_name
        ]
    )

    ml_mae = (
        test_ml_metrics[
            "mae"
        ]
    )

    improvement = (
        best_baseline_mae
        - ml_mae
    )

    improvement_pct = (
        (
            improvement
            / best_baseline_mae
        )
        * 100.0
        if best_baseline_mae > 0
        else 0.0
    )

    print(
        f"Best baseline:        "
        f"{best_baseline_name}"
    )

    print(
        f"Best baseline MAE:    "
        f"{best_baseline_mae:.3f}"
    )

    print(
        f"ML model MAE:         "
        f"{ml_mae:.3f}"
    )

    print(
        f"MAE improvement:      "
        f"{improvement:+.3f}"
    )

    print(
        f"Percent improvement:  "
        f"{improvement_pct:+.2f}%"
    )

    if ml_mae < best_baseline_mae:

        print(
            "\nPASS: ML beats the strongest "
            "overall simple baseline."
        )

    else:

        print(
            "\nFAIL: ML does not beat the strongest "
            "overall simple baseline."
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

        "validation_position_metrics":
            validation_position_results,

        "test_position_metrics":
            test_position_results,
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