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

from train_weekly_model import (
    NUMERIC_FEATURES as BASE_NUMERIC_FEATURES,
    CATEGORICAL_FEATURES,
    TARGET,
    build_model as build_base_model,
)


# ==================================================
# Paths
# ==================================================

DATASET_PATH = Path(
    "data/training/player_weeks_with_ecr.parquet"
)

MODEL_PATH = Path(
    "data/models/weekly_ppr_model_ecr.joblib"
)


# ==================================================
# ECR features
# ==================================================

ECR_FEATURES = [
    "fp_weekly_ecr",
    "fp_weekly_ecr_score",
    "fp_weekly_sd",
    "fp_weekly_best",
    "fp_weekly_worst",
    "fp_weekly_range",
]


ECR_NUMERIC_FEATURES = (
    BASE_NUMERIC_FEATURES
    + ECR_FEATURES
)

ECR_MODEL_FEATURES = (
    ECR_NUMERIC_FEATURES
    + CATEGORICAL_FEATURES
)

BASE_MODEL_FEATURES = (
    BASE_NUMERIC_FEATURES
    + CATEGORICAL_FEATURES
)


# ==================================================
# Load dataset
# ==================================================

def load_dataset():
    """
    Load player-week data merged with leakage-safe
    historical FantasyPros weekly ECR.
    """

    if not DATASET_PATH.exists():

        raise FileNotFoundError(
            f"Missing dataset: "
            f"{DATASET_PATH}\n"
            f"Run merge_ecr_training_data.py first."
        )

    raw = pl.read_parquet(
        DATASET_PATH
    )

    df = pd.DataFrame(
        raw.to_dicts()
    )

    print(
        f"Loaded {len(df):,} player-weeks."
    )

    return df


# ==================================================
# Metrics
# ==================================================

def calculate_metrics(
    actual,
    predicted,
):

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

    result = calculate_metrics(
        actual,
        predicted,
    )

    print(
        f"{name:25} "
        f"MAE {result['mae']:6.3f}   "
        f"RMSE {result['rmse']:6.3f}"
    )

    return result


# ==================================================
# ECR-enhanced model
# ==================================================

def build_ecr_model():
    """
    Same model family and hyperparameters as the
    original model.

    The experiment is therefore testing whether ECR
    features add predictive information rather than
    whether a different algorithm happens to win.
    """

    preprocessor = ColumnTransformer(
        transformers=[
            (
                "numeric",
                "passthrough",
                ECR_NUMERIC_FEATURES,
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

    regressor = HistGradientBoostingRegressor(
        learning_rate=0.05,
        max_iter=300,
        max_leaf_nodes=31,
        min_samples_leaf=30,
        l2_regularization=1.0,
        random_state=42,
    )

    return Pipeline(
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


# ==================================================
# Fair evaluation data
# ==================================================

def get_ecr_rows(
    df,
):
    """
    Only rows with a leakage-safe weekly ECR can be
    used by the ECR model.

    We compare the original model on these SAME rows
    so the comparison is fair.
    """

    result = df[
        df[
            "has_weekly_ecr"
        ]
        == True
    ].copy()

    result = result[
        result[
            "fp_weekly_ecr"
        ].notna()
    ].copy()

    return result


# ==================================================
# Position comparison
# ==================================================

def compare_by_position(
    df,
    base_predictions,
    ecr_predictions,
    label,
):

    result = df.copy()

    result[
        "base_prediction"
    ] = base_predictions

    result[
        "ecr_prediction"
    ] = ecr_predictions

    print(
        f"\n=== {label} BY POSITION ==="
    )

    print(
        f"{'POS':<5}"
        f"{'N':>7}"
        f"{'BASE':>10}"
        f"{'ECR':>10}"
        f"{'CHANGE':>12}"
        f"{'WINNER':>12}"
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
            ]
            == position
        ]

        if len(
            position_df
        ) == 0:

            continue

        actual = position_df[
            TARGET
        ]

        base = calculate_metrics(
            actual,
            position_df[
                "base_prediction"
            ],
        )

        ecr = calculate_metrics(
            actual,
            position_df[
                "ecr_prediction"
            ],
        )

        improvement = (
            base[
                "mae"
            ]
            - ecr[
                "mae"
            ]
        )

        improvement_pct = (
            improvement
            / base[
                "mae"
            ]
            * 100.0
            if base[
                "mae"
            ] > 0
            else 0.0
        )

        if improvement_pct > 1.0:

            winner = "ECR"

        elif improvement_pct < -1.0:

            winner = "BASE"

        else:

            winner = "TIE"

        position_results[
            position
        ] = {
            "n":
                len(
                    position_df
                ),

            "base_mae":
                base[
                    "mae"
                ],

            "ecr_mae":
                ecr[
                    "mae"
                ],

            "improvement_pct":
                improvement_pct,

            "winner":
                winner,
        }

        print(
            f"{position:<5}"
            f"{len(position_df):>7,}"
            f"{base['mae']:>10.3f}"
            f"{ecr['mae']:>10.3f}"
            f"{improvement_pct:>+11.2f}%"
            f"{winner:>12}"
        )

    return position_results


# ==================================================
# One experiment
# ==================================================

def run_experiment(
    training_df,
    evaluation_df,
    label,
):
    """
    Train:

        original model
        ECR-enhanced model

    on historical data and evaluate them on exactly
    the same ECR-available future player-weeks.
    """

    print(
        f"\n"
        f"============================================================"
    )

    print(
        label
    )

    print(
        f"============================================================"
    )

    print(
        f"Training rows:   "
        f"{len(training_df):,}"
    )

    print(
        f"Evaluation rows: "
        f"{len(evaluation_df):,}"
    )

    # ==================================================
    # Original model
    # ==================================================

    print(
        "\nTraining original model..."
    )

    base_model = build_base_model()

    base_model.fit(
        training_df[
            BASE_MODEL_FEATURES
        ],
        training_df[
            TARGET
        ],
    )

    base_predictions = (
        base_model.predict(
            evaluation_df[
                BASE_MODEL_FEATURES
            ]
        )
    )

    # ==================================================
    # ECR model
    # ==================================================

    print(
        "Training ECR-enhanced model..."
    )

    ecr_model = build_ecr_model()

    ecr_model.fit(
        training_df[
            ECR_MODEL_FEATURES
        ],
        training_df[
            TARGET
        ],
    )

    ecr_predictions = (
        ecr_model.predict(
            evaluation_df[
                ECR_MODEL_FEATURES
            ]
        )
    )

    # ==================================================
    # Overall metrics
    # ==================================================

    actual = evaluation_df[
        TARGET
    ]

    print(
        f"\n=== {label} OVERALL ==="
    )

    base_metrics = print_metrics(
        "Original ML",
        actual,
        base_predictions,
    )

    ecr_metrics = print_metrics(
        "ECR-enhanced ML",
        actual,
        ecr_predictions,
    )

    improvement = (
        base_metrics[
            "mae"
        ]
        - ecr_metrics[
            "mae"
        ]
    )

    improvement_pct = (
        improvement
        / base_metrics[
            "mae"
        ]
        * 100.0
        if base_metrics[
            "mae"
        ] > 0
        else 0.0
    )

    print(
        f"\nMAE improvement: "
        f"{improvement:+.3f}"
    )

    print(
        f"Percent improvement: "
        f"{improvement_pct:+.2f}%"
    )

    if improvement_pct > 1.0:

        print(
            "PASS: ECR-enhanced model wins."
        )

    elif improvement_pct >= -1.0:

        print(
            "TIE: ECR does not materially change MAE."
        )

    else:

        print(
            "FAIL: Original model is better."
        )

    # ==================================================
    # Position-level comparison
    # ==================================================

    position_results = compare_by_position(
        evaluation_df,
        base_predictions,
        ecr_predictions,
        label,
    )

    return {
        "base_model":
            base_model,

        "ecr_model":
            ecr_model,

        "base_metrics":
            base_metrics,

        "ecr_metrics":
            ecr_metrics,

        "improvement_pct":
            improvement_pct,

        "position_results":
            position_results,
    }


# ==================================================
# Prediction examples
# ==================================================

def print_prediction_examples(
    df,
    base_predictions,
    ecr_predictions,
    count=25,
):

    result = df.copy()

    result[
        "base_prediction"
    ] = base_predictions

    result[
        "ecr_prediction"
    ] = ecr_predictions

    result[
        "base_error"
    ] = (
        result[
            "base_prediction"
        ]
        - result[
            TARGET
        ]
    ).abs()

    result[
        "ecr_error"
    ] = (
        result[
            "ecr_prediction"
        ]
        - result[
            TARGET
        ]
    ).abs()

    result[
        "ecr_gain"
    ] = (
        result[
            "base_error"
        ]
        - result[
            "ecr_error"
        ]
    )

    columns = [
        "player_display_name",
        "position",
        "season",
        "week",
        "fp_weekly_ecr",
        "base_prediction",
        "ecr_prediction",
        TARGET,
        "base_error",
        "ecr_error",
        "ecr_gain",
    ]

    print(
        "\n=== SAMPLE 2026 PREDICTIONS ==="
    )

    print(
        result[
            columns
        ]
        .tail(
            count
        )
        .to_string(
            index=False,
            formatters={
                "fp_weekly_ecr":
                    lambda x:
                        f"{x:.1f}",

                "base_prediction":
                    lambda x:
                        f"{x:.2f}",

                "ecr_prediction":
                    lambda x:
                        f"{x:.2f}",

                TARGET:
                    lambda x:
                        f"{x:.2f}",

                "base_error":
                    lambda x:
                        f"{x:.2f}",

                "ecr_error":
                    lambda x:
                        f"{x:.2f}",

                "ecr_gain":
                    lambda x:
                        f"{x:+.2f}",
            },
        )
    )


# ==================================================
# Main
# ==================================================

def main():

    df = load_dataset()

    ecr_df = get_ecr_rows(
        df
    )

    print(
        f"Leakage-safe ECR rows: "
        f"{len(ecr_df):,}"
    )

    # ==================================================
    # 2025 validation experiment
    #
    # Train 2021-2024
    # Test 2025
    # ==================================================

    train_2025 = ecr_df[
        ecr_df[
            "season"
        ]
        <= 2024
    ].copy()

    validation_2025 = ecr_df[
        ecr_df[
            "season"
        ]
        == 2025
    ].copy()

    validation_results = (
        run_experiment(
            training_df=train_2025,
            evaluation_df=validation_2025,
            label="2025 VALIDATION",
        )
    )

    # ==================================================
    # 2026 test experiment
    #
    # Train 2021-2025
    # Test 2026
    # ==================================================

    train_2026 = ecr_df[
        ecr_df[
            "season"
        ]
        <= 2025
    ].copy()

    test_2026 = ecr_df[
        ecr_df[
            "season"
        ]
        == 2026
    ].copy()

    test_results = (
        run_experiment(
            training_df=train_2026,
            evaluation_df=test_2026,
            label="2026 TEST",
        )
    )

    # ==================================================
    # Architecture decision
    # ==================================================

    validation_gain = (
        validation_results[
            "improvement_pct"
        ]
    )

    test_gain = (
        test_results[
            "improvement_pct"
        ]
    )

    print(
        "\n"
        + "=" * 60
    )

    print(
        "ECR MODEL VERDICT"
    )

    print(
        "=" * 60
    )

    print(
        f"2025 improvement: "
        f"{validation_gain:+.2f}%"
    )

    print(
        f"2026 improvement: "
        f"{test_gain:+.2f}%"
    )

    if (
        validation_gain > 1.0
        and test_gain > 1.0
    ):

        print(
            "\nECR improves the model in both "
            "validation and test data."
        )

        print(
            "Recommendation: keep the "
            "ECR-enhanced model."
        )

    elif (
        validation_gain < -1.0
        and test_gain < -1.0
    ):

        print(
            "\nECR hurts the model in both "
            "validation and test data."
        )

        print(
            "Recommendation: keep the "
            "original model."
        )

    else:

        print(
            "\nResults are mixed."
        )

        print(
            "Recommendation: do not replace the "
            "original model yet."
        )

    # ==================================================
    # Save FINAL ECR model
    #
    # Train on all leakage-safe 2021-2025 rows.
    # ==================================================

    final_training_df = ecr_df[
        ecr_df[
            "season"
        ]
        <= 2025
    ].copy()

    final_ecr_model = (
        build_ecr_model()
    )

    final_ecr_model.fit(
        final_training_df[
            ECR_MODEL_FEATURES
        ],
        final_training_df[
            TARGET
        ],
    )

    MODEL_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    package = {
        "model":
            final_ecr_model,

        "numeric_features":
            ECR_NUMERIC_FEATURES,

        "categorical_features":
            CATEGORICAL_FEATURES,

        "ecr_features":
            ECR_FEATURES,

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

        "validation_results":
            {
                "base_metrics":
                    validation_results[
                        "base_metrics"
                    ],

                "ecr_metrics":
                    validation_results[
                        "ecr_metrics"
                    ],

                "improvement_pct":
                    validation_gain,
            },

        "test_results":
            {
                "base_metrics":
                    test_results[
                        "base_metrics"
                    ],

                "ecr_metrics":
                    test_results[
                        "ecr_metrics"
                    ],

                "improvement_pct":
                    test_gain,
            },
    }

    joblib.dump(
        package,
        MODEL_PATH,
    )

    print(
        f"\nSaved ECR candidate model to: "
        f"{MODEL_PATH}"
    )


if __name__ == "__main__":
    main()