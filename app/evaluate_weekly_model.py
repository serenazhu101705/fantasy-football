from pathlib import Path

import joblib
import pandas as pd
import polars as pl

from sklearn.metrics import mean_absolute_error

from train_weekly_model import (
    build_model,
    NUMERIC_FEATURES,
    CATEGORICAL_FEATURES,
    TARGET,
)


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
# Configuration
# ==================================================

POSITIONS = (
    "QB",
    "RB",
    "WR",
    "TE",
)

MODEL_FEATURES = (
    NUMERIC_FEATURES
    + CATEGORICAL_FEATURES
)


# Number of players we consider "fantasy relevant"
# at each position in a given week.
#
# We evaluate rankings among this pool rather than
# letting thousands of obvious bench players make
# ranking accuracy look artificially easy.
POOL_SIZE = {
    "QB": 24,
    "RB": 48,
    "WR": 60,
    "TE": 30,
}


# Approximate starter-tier size used for top-N overlap.
STARTER_TIER_SIZE = {
    "QB": 12,
    "RB": 24,
    "WR": 24,
    "TE": 12,
}


# Ignore pairs where actual scoring was nearly tied.
#
# If one player scores 10.1 and another scores 10.5,
# calling one prediction "wrong" is not especially
# meaningful for fantasy decision quality.
MIN_ACTUAL_POINT_DIFFERENCE = 2.0


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


def load_final_model():

    if not MODEL_PATH.exists():

        raise FileNotFoundError(
            f"Missing model: "
            f"{MODEL_PATH}\n"
            f"Run train_weekly_model.py first."
        )

    package = joblib.load(
        MODEL_PATH
    )

    return package[
        "model"
    ]


# ==================================================
# Fair model predictions
# ==================================================

def get_2025_predictions(
    df,
):
    """
    For 2025 evaluation we MUST train only on
    2021-2024.

    We cannot use the saved final model because that
    model was trained on 2025 and would leak future
    information into the evaluation.
    """

    training_df = df[
        df[
            "season"
        ] <= 2024
    ].copy()

    validation_df = df[
        df[
            "season"
        ] == 2025
    ].copy()

    model = build_model()

    model.fit(
        training_df[
            MODEL_FEATURES
        ],
        training_df[
            TARGET
        ],
    )

    predictions = model.predict(
        validation_df[
            MODEL_FEATURES
        ]
    )

    return (
        validation_df,
        predictions,
    )


def get_2026_predictions(
    df,
):
    """
    The saved final model was trained on 2021-2025,
    so it is safe to evaluate on 2026.
    """

    test_df = df[
        df[
            "season"
        ] == 2026
    ].copy()

    model = load_final_model()

    predictions = model.predict(
        test_df[
            MODEL_FEATURES
        ]
    )

    return (
        test_df,
        predictions,
    )


# ==================================================
# Add prediction columns
# ==================================================

def add_predictions(
    df,
    ml_predictions,
):

    result = df.copy()

    result[
        "ml_prediction"
    ] = ml_predictions

    result[
        "season_prediction"
    ] = result[
        "season_ppr_avg"
    ].fillna(
        0.0
    )

    result[
        "l3_prediction"
    ] = result[
        "l3_ppr_avg"
    ].fillna(
        0.0
    )

    return result


# ==================================================
# Fantasy-relevant weekly pool
# ==================================================

def get_weekly_pool(
    week_df,
    position,
):
    """
    Restrict evaluation to fantasy-relevant players.

    Pool selection uses ONLY pregame information.

    We use the better of:
        season average
        last-3 average

    solely to identify players who were reasonably
    relevant before the games occurred.

    The actual target score is NEVER used to decide
    who enters the pool.
    """

    position_df = week_df[
        week_df[
            "position"
        ] == position
    ].copy()

    if len(
        position_df
    ) == 0:

        return position_df

    position_df[
        "pregame_relevance"
    ] = position_df[
        [
            "season_ppr_avg",
            "l3_ppr_avg",
        ]
    ].max(
        axis=1
    )

    position_df = (
        position_df
        .sort_values(
            "pregame_relevance",
            ascending=False,
        )
        .head(
            POOL_SIZE[
                position
            ]
        )
    )

    return position_df


# ==================================================
# Pairwise start/sit accuracy
# ==================================================

def pairwise_accuracy(
    df,
    prediction_column,
):
    """
    Ask:

        If choosing between two fantasy-relevant
        players at the same position that week,
        did the prediction rank the player who
        actually scored more points higher?

    We skip actual near-ties.
    """

    correct = 0
    total = 0

    grouped = df.groupby(
        [
            "season",
            "week",
            "position",
        ]
    )

    for (
        _,
        group,
    ) in grouped:

        rows = list(
            group.itertuples()
        )

        for i in range(
            len(rows)
        ):

            for j in range(
                i + 1,
                len(rows),
            ):

                player_a = rows[
                    i
                ]

                player_b = rows[
                    j
                ]

                actual_a = getattr(
                    player_a,
                    TARGET,
                )

                actual_b = getattr(
                    player_b,
                    TARGET,
                )

                actual_difference = (
                    actual_a
                    - actual_b
                )

                if (
                    abs(
                        actual_difference
                    )
                    < MIN_ACTUAL_POINT_DIFFERENCE
                ):

                    continue

                prediction_a = getattr(
                    player_a,
                    prediction_column,
                )

                prediction_b = getattr(
                    player_b,
                    prediction_column,
                )

                predicted_difference = (
                    prediction_a
                    - prediction_b
                )

                # Predicted exact ties are treated as
                # half-correct rather than arbitrarily
                # correct or incorrect.
                if predicted_difference == 0:

                    correct += 0.5

                elif (
                    actual_difference
                    * predicted_difference
                    > 0
                ):

                    correct += 1

                total += 1

    accuracy = (
        correct
        / total
        if total > 0
        else 0.0
    )

    return {
        "correct": correct,
        "total": total,
        "accuracy": accuracy,
    }


# ==================================================
# Starter-tier hit rate
# ==================================================

def starter_tier_hit_rate(
    df,
    prediction_column,
):
    """
    For every position/week:

        actual top N players
        vs
        predicted top N players

    Then calculate overlap.

    Example QB:

        actual top 12 scorers
        vs
        predicted top 12

    This is another ranking metric that is much more
    fantasy-relevant than raw point MAE.
    """

    hits = 0
    total = 0

    grouped = df.groupby(
        [
            "season",
            "week",
            "position",
        ]
    )

    for (
        (
            _,
            _,
            position,
        ),
        group,
    ) in grouped:

        if position not in STARTER_TIER_SIZE:
            continue

        starter_count = min(
            STARTER_TIER_SIZE[
                position
            ],
            len(
                group
            ),
        )

        if starter_count == 0:
            continue

        actual_top = (
            group
            .nlargest(
                starter_count,
                TARGET,
            )
            .index
        )

        predicted_top = (
            group
            .nlargest(
                starter_count,
                prediction_column,
            )
            .index
        )

        overlap = len(
            set(
                actual_top
            )
            & set(
                predicted_top
            )
        )

        hits += overlap
        total += starter_count

    return (
        hits
        / total
        if total > 0
        else 0.0
    )


# ==================================================
# Build evaluation pool
# ==================================================

def build_evaluation_pool(
    df,
):
    """
    Build a common weekly player pool used by ALL
    prediction methods.

    This avoids giving one method an easier set of
    players than another.
    """

    pools = []

    for (
        season,
        week,
    ), week_df in df.groupby(
        [
            "season",
            "week",
        ]
    ):

        for position in POSITIONS:

            pool = get_weekly_pool(
                week_df,
                position,
            )

            if len(
                pool
            ) > 0:

                pools.append(
                    pool
                )

    if not pools:

        return pd.DataFrame()

    return pd.concat(
        pools,
        ignore_index=True,
    )


# ==================================================
# Position evaluation
# ==================================================

def evaluate_position(
    pool,
    position,
):
    """
    Compare ranking accuracy for one position.
    """

    position_df = pool[
        pool[
            "position"
        ] == position
    ].copy()

    methods = {
        "ML":
            "ml_prediction",

        "SEASON":
            "season_prediction",

        "L3":
            "l3_prediction",
    }

    results = {}

    for (
        name,
        column,
    ) in methods.items():

        pairwise = pairwise_accuracy(
            position_df,
            column,
        )

        starter_hit = (
            starter_tier_hit_rate(
                position_df,
                column,
            )
        )

        results[
            name
        ] = {
            "pairwise_accuracy":
                pairwise[
                    "accuracy"
                ],

            "pair_count":
                pairwise[
                    "total"
                ],

            "starter_hit_rate":
                starter_hit,
        }

    return results


# ==================================================
# Full evaluation
# ==================================================

def evaluate_dataset(
    df,
    label,
):

    pool = build_evaluation_pool(
        df
    )

    print(
        f"\n=== {label} START/SIT EVALUATION ==="
    )

    print(
        f"Fantasy-relevant player-weeks: "
        f"{len(pool):,}"
    )

    print(
        "\nPAIRWISE ACCURACY"
    )

    print(
        f"{'POS':<5}"
        f"{'PAIRS':>12}"
        f"{'ML':>10}"
        f"{'SEASON':>10}"
        f"{'L3':>10}"
    )

    position_results = {}

    for position in POSITIONS:

        results = evaluate_position(
            pool,
            position,
        )

        position_results[
            position
        ] = results

        pair_count = (
            results[
                "ML"
            ][
                "pair_count"
            ]
        )

        print(
            f"{position:<5}"
            f"{pair_count:>12,}"
            f"{results['ML']['pairwise_accuracy']:>9.2%}"
            f"{results['SEASON']['pairwise_accuracy']:>10.2%}"
            f"{results['L3']['pairwise_accuracy']:>10.2%}"
        )

    # --------------------------------------------------
    # Overall pairwise accuracy
    # --------------------------------------------------

    print(
        "\nOVERALL PAIRWISE ACCURACY"
    )

    for (
        display_name,
        column,
    ) in (
        (
            "ML",
            "ml_prediction",
        ),
        (
            "Season average",
            "season_prediction",
        ),
        (
            "Last-3 average",
            "l3_prediction",
        ),
    ):

        result = pairwise_accuracy(
            pool,
            column,
        )

        print(
            f"{display_name:20} "
            f"{result['accuracy']:.2%} "
            f"({result['total']:,} decisions)"
        )

    # --------------------------------------------------
    # Starter-tier hit rate
    # --------------------------------------------------

    print(
        "\nSTARTER-TIER HIT RATE"
    )

    print(
        f"{'POS':<5}"
        f"{'ML':>10}"
        f"{'SEASON':>10}"
        f"{'L3':>10}"
    )

    for position in POSITIONS:

        results = (
            position_results[
                position
            ]
        )

        print(
            f"{position:<5}"
            f"{results['ML']['starter_hit_rate']:>9.2%}"
            f"{results['SEASON']['starter_hit_rate']:>10.2%}"
            f"{results['L3']['starter_hit_rate']:>10.2%}"
        )

    print(
        "\nOVERALL STARTER-TIER HIT RATE"
    )

    for (
        display_name,
        column,
    ) in (
        (
            "ML",
            "ml_prediction",
        ),
        (
            "Season average",
            "season_prediction",
        ),
        (
            "Last-3 average",
            "l3_prediction",
        ),
    ):

        hit_rate = (
            starter_tier_hit_rate(
                pool,
                column,
            )
        )

        print(
            f"{display_name:20} "
            f"{hit_rate:.2%}"
        )

    # --------------------------------------------------
    # MAE on the SAME fantasy-relevant pool
    # --------------------------------------------------

    print(
        "\nFANTASY-RELEVANT POOL MAE"
    )

    actual = pool[
        TARGET
    ]

    for (
        display_name,
        column,
    ) in (
        (
            "ML",
            "ml_prediction",
        ),
        (
            "Season average",
            "season_prediction",
        ),
        (
            "Last-3 average",
            "l3_prediction",
        ),
    ):

        mae = mean_absolute_error(
            actual,
            pool[
                column
            ],
        )

        print(
            f"{display_name:20} "
            f"{mae:.3f}"
        )


# ==================================================
# Main
# ==================================================

def main():

    df = load_dataset()

    # ==================================================
    # 2025
    # ==================================================

    (
        validation_df,
        validation_predictions,
    ) = get_2025_predictions(
        df
    )

    validation_df = add_predictions(
        validation_df,
        validation_predictions,
    )

    evaluate_dataset(
        validation_df,
        "2025 VALIDATION",
    )

    # ==================================================
    # 2026
    # ==================================================

    (
        test_df,
        test_predictions,
    ) = get_2026_predictions(
        df
    )

    test_df = add_predictions(
        test_df,
        test_predictions,
    )

    evaluate_dataset(
        test_df,
        "2026 TEST",
    )


if __name__ == "__main__":
    main()