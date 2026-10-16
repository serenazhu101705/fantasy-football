from pathlib import Path

import joblib
import pandas as pd
import polars as pl

from sklearn.ensemble import (
    HistGradientBoostingRegressor,
)

from sklearn.metrics import (
    mean_absolute_error,
    mean_squared_error,
)


# ==================================================
# Paths
# ==================================================

DATASET_PATH = Path(
    "data/training/player_weeks.parquet"
)

UNIVERSAL_MODEL_PATH = Path(
    "data/models/weekly_ppr_model.joblib"
)

POSITION_MODEL_PATH = Path(
    "data/models/position_ppr_models.joblib"
)


# ==================================================
# Configuration
# ==================================================

POSITIONS = (
    "QB",
    "RB",
    "WR",
    "TE",
)


FEATURES = [
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


TARGET = "target_ppr"


# ==================================================
# Loading
# ==================================================

def load_dataset():

    if not DATASET_PATH.exists():

        raise FileNotFoundError(
            f"Missing dataset: "
            f"{DATASET_PATH}"
        )

    raw = pl.read_parquet(
        DATASET_PATH
    )

    return pd.DataFrame(
        raw.to_dicts()
    )


def load_universal_model():

    if not UNIVERSAL_MODEL_PATH.exists():

        raise FileNotFoundError(
            "Run train_weekly_model.py first."
        )

    package = joblib.load(
        UNIVERSAL_MODEL_PATH
    )

    return package[
        "model"
    ]


# ==================================================
# Model
# ==================================================

def build_position_model():
    """
    Same underlying model as our universal version.

    This keeps the comparison fair:
    the main difference is whether QB/RB/WR/TE
    learn separate relationships.
    """

    return HistGradientBoostingRegressor(
        learning_rate=0.05,
        max_iter=300,
        max_leaf_nodes=31,
        min_samples_leaf=30,
        l2_regularization=1.0,
        random_state=42,
    )


# ==================================================
# Metrics
# ==================================================

def metrics(
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


# ==================================================
# Train separate models
# ==================================================

def train_models(
    train_df,
):

    models = {}

    for position in POSITIONS:

        position_df = train_df[
            train_df[
                "position"
            ] == position
        ].copy()

        print(
            f"Training {position} model "
            f"on {len(position_df):,} rows..."
        )

        model = (
            build_position_model()
        )

        model.fit(
            position_df[
                FEATURES
            ],
            position_df[
                TARGET
            ],
        )

        models[
            position
        ] = model

    return models


# ==================================================
# Position-model predictions
# ==================================================

def predict_position_models(
    models,
    df,
):

    predictions = pd.Series(
        index=df.index,
        dtype=float,
    )

    for position in POSITIONS:

        mask = (
            df[
                "position"
            ]
            == position
        )

        position_df = df[
            mask
        ]

        if len(
            position_df
        ) == 0:
            continue

        predictions.loc[
            mask
        ] = models[
            position
        ].predict(
            position_df[
                FEATURES
            ]
        )

    return predictions


# ==================================================
# Universal predictions
# ==================================================

def predict_universal(
    model,
    df,
):

    universal_features = (
        FEATURES
        + [
            "position",
        ]
    )

    return model.predict(
        df[
            universal_features
        ]
    )


# ==================================================
# Compare
# ==================================================

def compare_models(
    df,
    universal_predictions,
    position_predictions,
    label,
):

    result = df.copy()

    result[
        "universal_prediction"
    ] = universal_predictions

    result[
        "position_prediction"
    ] = position_predictions

    print(
        f"\n=== {label}: "
        f"UNIVERSAL VS POSITION MODELS ==="
    )

    print(
        f"{'POS':<5}"
        f"{'N':>7}"
        f"{'UNIVERSAL':>13}"
        f"{'POSITION':>13}"
        f"{'CHANGE':>12}"
        f"{'WINNER':>12}"
    )

    results = {}

    for position in POSITIONS:

        position_df = result[
            result[
                "position"
            ] == position
        ]

        if len(
            position_df
        ) == 0:
            continue

        actual = position_df[
            TARGET
        ]

        universal = metrics(
            actual,
            position_df[
                "universal_prediction"
            ],
        )

        separate = metrics(
            actual,
            position_df[
                "position_prediction"
            ],
        )

        improvement = (
            universal[
                "mae"
            ]
            - separate[
                "mae"
            ]
        )

        improvement_pct = (
            (
                improvement
                / universal[
                    "mae"
                ]
            )
            * 100.0
            if universal[
                "mae"
            ] > 0
            else 0.0
        )

        if improvement_pct > 1:

            winner = "POSITION"

        elif improvement_pct < -1:

            winner = "UNIVERSAL"

        else:

            winner = "TIE"

        results[
            position
        ] = {
            "universal_mae":
                universal[
                    "mae"
                ],

            "position_mae":
                separate[
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
            f"{universal['mae']:>13.3f}"
            f"{separate['mae']:>13.3f}"
            f"{improvement_pct:>+11.2f}%"
            f"{winner:>12}"
        )

    # --------------------------------------------------
    # Overall
    # --------------------------------------------------

    universal_overall = metrics(
        result[
            TARGET
        ],
        result[
            "universal_prediction"
        ],
    )

    position_overall = metrics(
        result[
            TARGET
        ],
        result[
            "position_prediction"
        ],
    )

    overall_improvement = (
        universal_overall[
            "mae"
        ]
        - position_overall[
            "mae"
        ]
    )

    overall_pct = (
        overall_improvement
        / universal_overall[
            "mae"
        ]
        * 100.0
    )

    print(
        "\nOVERALL"
    )

    print(
        f"Universal MAE: "
        f"{universal_overall['mae']:.3f}"
    )

    print(
        f"Position MAE:  "
        f"{position_overall['mae']:.3f}"
    )

    print(
        f"Change:        "
        f"{overall_pct:+.2f}%"
    )

    return {
        "positions":
            results,

        "universal_overall":
            universal_overall,

        "position_overall":
            position_overall,

        "overall_improvement_pct":
            overall_pct,
    }


# ==================================================
# Main
# ==================================================

def main():

    df = load_dataset()

    universal_model = (
        load_universal_model()
    )

    # ==================================================
    # Experiment 1
    #
    # Train 2021-2024
    # Test 2025
    # ==================================================

    train_2025 = df[
        df[
            "season"
        ] <= 2024
    ].copy()

    validation_2025 = df[
        df[
            "season"
        ] == 2025
    ].copy()

    print(
        "\n=== TRAIN POSITION MODELS: "
        "2021-2024 ==="
    )

    models_2025 = train_models(
        train_2025
    )

    position_predictions_2025 = (
        predict_position_models(
            models_2025,
            validation_2025,
        )
    )

    # --------------------------------------------------
    # Need a universal model trained only through 2024
    # for a fair 2025 comparison.
    # --------------------------------------------------

    from train_weekly_model import (
        build_model,
    )

    universal_2025 = (
        build_model()
    )

    universal_features = (
        FEATURES
        + [
            "position",
        ]
    )

    universal_2025.fit(
        train_2025[
            universal_features
        ],
        train_2025[
            TARGET
        ],
    )

    universal_predictions_2025 = (
        predict_universal(
            universal_2025,
            validation_2025,
        )
    )

    validation_results = compare_models(
        validation_2025,
        universal_predictions_2025,
        position_predictions_2025,
        "2025 VALIDATION",
    )

    # ==================================================
    # Experiment 2
    #
    # Train 2021-2025
    # Test 2026
    # ==================================================

    train_2026 = df[
        df[
            "season"
        ] <= 2025
    ].copy()

    test_2026 = df[
        df[
            "season"
        ] == 2026
    ].copy()

    print(
        "\n=== TRAIN POSITION MODELS: "
        "2021-2025 ==="
    )

    models_2026 = train_models(
        train_2026
    )

    position_predictions_2026 = (
        predict_position_models(
            models_2026,
            test_2026,
        )
    )

    universal_predictions_2026 = (
        predict_universal(
            universal_model,
            test_2026,
        )
    )

    test_results = compare_models(
        test_2026,
        universal_predictions_2026,
        position_predictions_2026,
        "2026 TEST",
    )

    # ==================================================
    # Decision
    # ==================================================

    print(
        "\n=== ARCHITECTURE VERDICT ==="
    )

    validation_gain = (
        validation_results[
            "overall_improvement_pct"
        ]
    )

    test_gain = (
        test_results[
            "overall_improvement_pct"
        ]
    )

    if (
        validation_gain > 1
        and test_gain > 1
    ):

        print(
            "Separate position models win "
            "in both 2025 and 2026."
        )

        print(
            "Recommendation: use separate "
            "QB/RB/WR/TE models."
        )

    elif (
        validation_gain < -1
        and test_gain < -1
    ):

        print(
            "Universal model wins "
            "in both seasons."
        )

        print(
            "Recommendation: keep one "
            "universal model."
        )

    else:

        print(
            "Results are mixed across seasons."
        )

        print(
            "Recommendation: keep the universal "
            "model for now and gather more evidence."
        )

    # ==================================================
    # Save latest position models
    # ==================================================

    POSITION_MODEL_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    package = {
        "models":
            models_2026,

        "features":
            FEATURES,

        "training_seasons":
            [
                2021,
                2022,
                2023,
                2024,
                2025,
            ],

        "validation_results":
            validation_results,

        "test_results":
            test_results,
    }

    joblib.dump(
        package,
        POSITION_MODEL_PATH,
    )

    print(
        f"\nSaved candidate position models to: "
        f"{POSITION_MODEL_PATH}"
    )


if __name__ == "__main__":
    main()