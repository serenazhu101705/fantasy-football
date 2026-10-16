from itertools import combinations
from pathlib import Path
import math
import re
import unicodedata

import joblib
import pandas as pd
import polars as pl

from espn_client import (
    get_league,
    get_my_team,
)

from train_ecr_model import (
    ECR_MODEL_FEATURES,
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
# Configuration
# ==================================================

SEASON = 2026

POSITIONS = (
    "QB",
    "RB",
    "WR",
    "TE",
)

MODEL_FEATURES = list(
    ECR_MODEL_FEATURES
)


# --------------------------------------------------
# Projection / ECR-ML blend experiment
#
# ML weight:
#
#   0.00 = 100% ESPN projection
#   0.25 = 25% ECR-ML + 75% ESPN
#   0.50 = 50% ECR-ML + 50% ESPN
#   0.75 = 75% ECR-ML + 25% ESPN
#   1.00 = 100% ECR-ML
#
# IMPORTANT:
#
# We currently only have a handful of completed 2026
# weeks from this ESPN league.
#
# Therefore this is an EXPLORATORY lineup backtest.
# We should NOT automatically deploy whichever weight
# wins four or five weeks of data.
# --------------------------------------------------

BLEND_WEIGHTS = (
    0.00,
    0.25,
    0.50,
    0.75,
    1.00,
)


# ==================================================
# General helpers
# ==================================================

def safe_float(
    value,
    default=0.0,
):
    """
    Convert a value to float safely.

    Missing / NaN values become default.
    """

    if value is None:
        return float(
            default
        )

    try:

        value = float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):

        return float(
            default
        )

    if math.isnan(
        value
    ):

        return float(
            default
        )

    return value


def blend_column_name(
    ml_weight,
):
    """
    Convert a blend weight to the dataframe/player
    dictionary field used by the backtest.

    Example:

        0.25
        ->
        blend_25_prediction
    """

    percent = int(
        round(
            ml_weight
            * 100
        )
    )

    return (
        f"blend_"
        f"{percent}_"
        f"prediction"
    )


def blend_actual_column(
    ml_weight,
):

    percent = int(
        round(
            ml_weight
            * 100
        )
    )

    return (
        f"blend_"
        f"{percent}_"
        f"actual"
    )


def blend_regret_column(
    ml_weight,
):

    percent = int(
        round(
            ml_weight
            * 100
        )
    )

    return (
        f"blend_"
        f"{percent}_"
        f"regret"
    )


# ==================================================
# Name normalization
# ==================================================

def normalize_name(
    value,
):
    """
    Normalize ESPN and nflverse player names so they
    can be matched reliably.

    Examples:

        "A.J. Brown"
        "AJ Brown"

    both become approximately:

        "aj brown"
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

    # Collapse repeated spaces.
    name = re.sub(
        r"\s+",
        " ",
        name,
    ).strip()

    return name


# ==================================================
# Load ECR-enhanced ML data
# ==================================================

def load_dataset():

    if not DATASET_PATH.exists():

        raise FileNotFoundError(
            f"Missing dataset: "
            f"{DATASET_PATH}\n"
            f"Run merge_ecr_training_data.py first."
        )

    raw = pl.read_parquet(
        DATASET_PATH
    )

    return pd.DataFrame(
        raw.to_dicts()
    )


def load_model():

    if not MODEL_PATH.exists():

        raise FileNotFoundError(
            f"Missing model: "
            f"{MODEL_PATH}\n"
            f"Run train_ecr_model.py first."
        )

    package = joblib.load(
        MODEL_PATH
    )

    return package[
        "model"
    ]


# ==================================================
# Historical ESPN roster
# ==================================================

def get_historical_roster(
    league,
    team_id,
    week,
):
    """
    Get our ESPN roster snapshot from the historical
    matchup for a completed week.
    """

    box_scores = league.box_scores(
        week=week
    )

    for matchup in box_scores:

        home_team = getattr(
            matchup,
            "home_team",
            None,
        )

        away_team = getattr(
            matchup,
            "away_team",
            None,
        )

        if (
            home_team is not None
            and home_team.team_id
            == team_id
        ):

            return getattr(
                matchup,
                "home_lineup",
                [],
            )

        if (
            away_team is not None
            and away_team.team_id
            == team_id
        ):

            return getattr(
                matchup,
                "away_lineup",
                [],
            )

    return []


# ==================================================
# Weekly ECR-ML prediction lookup
# ==================================================

def build_week_prediction_lookup(
    dataset,
    model,
    week,
):
    """
    Build ECR-enhanced ML predictions for every
    eligible historical player row in one 2026 week.

    The saved ECR model was trained on leakage-safe
    2021-2025 data, so 2026 remains out-of-sample.
    """

    week_df = dataset[
        (
            dataset[
                "season"
            ]
            == SEASON
        )
        &
        (
            dataset[
                "week"
            ]
            == week
        )
    ].copy()

    # --------------------------------------------------
    # The ECR model requires a valid historical weekly
    # FantasyPros ECR snapshot.
    # --------------------------------------------------

    week_df = week_df[
        (
            week_df[
                "has_weekly_ecr"
            ]
            == True
        )
        &
        (
            week_df[
                "fp_weekly_ecr"
            ].notna()
        )
    ].copy()

    if len(
        week_df
    ) == 0:

        return {}

    # --------------------------------------------------
    # Generate ECR-enhanced ML predictions.
    # --------------------------------------------------

    week_df[
        "ml_prediction"
    ] = model.predict(
        week_df[
            MODEL_FEATURES
        ]
    )

    week_df[
        "normalized_name"
    ] = week_df[
        "player_display_name"
    ].apply(
        normalize_name
    )

    lookup = {}

    for (
        _,
        row,
    ) in week_df.iterrows():

        key = (
            row[
                "normalized_name"
            ],
            row[
                "position"
            ],
        )

        lookup[
            key
        ] = row.to_dict()

    return lookup


# ==================================================
# Match ESPN roster to ECR-ML data
# ==================================================

def build_matched_roster(
    espn_roster,
    prediction_lookup,
):
    """
    Match ESPN historical roster players to their
    pregame ML/ECR features.

    Every method uses exactly the SAME matched player
    pool. This prevents a method from receiving an
    easier candidate set.

    We exclude players occupying the ESPN IR roster
    slot because they were not legal starting options.
    """

    matched = []

    unmatched = []

    for player in espn_roster:

        position = getattr(
            player,
            "position",
            None,
        )

        if position not in POSITIONS:
            continue

        name = getattr(
            player,
            "name",
            "UNKNOWN",
        )

        slot = getattr(
            player,
            "slot_position",
            "N/A",
        )

        # --------------------------------------------------
        # A player occupying fantasy IR cannot be selected
        # as a starter.
        # --------------------------------------------------

        if slot == "IR":
            continue

        actual_points = safe_float(
            getattr(
                player,
                "points",
                0.0,
            )
        )

        espn_projection = safe_float(
            getattr(
                player,
                "projected_points",
                0.0,
            )
        )

        key = (
            normalize_name(
                name
            ),
            position,
        )

        prediction = (
            prediction_lookup.get(
                key
            )
        )

        # --------------------------------------------------
        # No ECR/ML feature row.
        #
        # Exclude the player from EVERY prediction method
        # so comparisons use an identical candidate pool.
        # --------------------------------------------------

        if prediction is None:

            unmatched.append(
                {
                    "name":
                        name,

                    "position":
                        position,

                    "slot":
                        slot,

                    "actual_points":
                        actual_points,

                    "espn_projection":
                        espn_projection,
                }
            )

            continue

        ml_prediction = safe_float(
            prediction.get(
                "ml_prediction"
            )
        )

        season_prediction = safe_float(
            prediction.get(
                "season_ppr_avg"
            )
        )

        l3_prediction = safe_float(
            prediction.get(
                "l3_ppr_avg"
            )
        )

        matched_player = {
            "key":
                key,

            "name":
                name,

            "position":
                position,

            "slot":
                slot,

            "actual_points":
                actual_points,

            "ml_prediction":
                ml_prediction,

            "espn_prediction":
                espn_projection,

            "season_prediction":
                season_prediction,

            "l3_prediction":
                l3_prediction,
        }

        # --------------------------------------------------
        # Build every ESPN/ECR-ML blend.
        # --------------------------------------------------

        for ml_weight in BLEND_WEIGHTS:

            espn_weight = (
                1.0
                - ml_weight
            )

            column_name = (
                blend_column_name(
                    ml_weight
                )
            )

            matched_player[
                column_name
            ] = (
                ml_weight
                * ml_prediction
                + espn_weight
                * espn_projection
            )

        matched.append(
            matched_player
        )

    return (
        matched,
        unmatched,
    )


# ==================================================
# Historical offensive lineup optimizer
# ==================================================

def optimize_offensive_lineup(
    players,
    score_key,
):
    """
    Optimize:

        1 QB
        2 RB
        2 WR
        1 TE
        1 FLEX (RB/WR/TE)

    using score_key.

    The actual score of the selected lineup is stored
    separately so we can compare decision quality.
    """

    qbs = [
        player
        for player in players
        if player[
            "position"
        ] == "QB"
    ]

    rbs = [
        player
        for player in players
        if player[
            "position"
        ] == "RB"
    ]

    wrs = [
        player
        for player in players
        if player[
            "position"
        ] == "WR"
    ]

    tes = [
        player
        for player in players
        if player[
            "position"
        ] == "TE"
    ]

    # --------------------------------------------------
    # Need enough players for a legal lineup.
    # --------------------------------------------------

    if (
        len(qbs) < 1
        or len(rbs) < 2
        or len(wrs) < 2
        or len(tes) < 1
    ):

        return None

    best = None

    for qb in qbs:

        for rb_pair in combinations(
            rbs,
            2,
        ):

            for wr_pair in combinations(
                wrs,
                2,
            ):

                for te in tes:

                    used_keys = {
                        qb[
                            "key"
                        ],

                        rb_pair[
                            0
                        ][
                            "key"
                        ],

                        rb_pair[
                            1
                        ][
                            "key"
                        ],

                        wr_pair[
                            0
                        ][
                            "key"
                        ],

                        wr_pair[
                            1
                        ][
                            "key"
                        ],

                        te[
                            "key"
                        ],
                    }

                    flex_candidates = [
                        player
                        for player in players
                        if (
                            player[
                                "position"
                            ]
                            in (
                                "RB",
                                "WR",
                                "TE",
                            )
                            and player[
                                "key"
                            ]
                            not in used_keys
                        )
                    ]

                    for flex in flex_candidates:

                        lineup = {
                            "QB":
                                qb,

                            "RB1":
                                rb_pair[
                                    0
                                ],

                            "RB2":
                                rb_pair[
                                    1
                                ],

                            "WR1":
                                wr_pair[
                                    0
                                ],

                            "WR2":
                                wr_pair[
                                    1
                                ],

                            "TE":
                                te,

                            "FLEX":
                                flex,
                        }

                        selection_score = sum(
                            safe_float(
                                player.get(
                                    score_key
                                )
                            )
                            for player
                            in lineup.values()
                        )

                        actual_score = sum(
                            player[
                                "actual_points"
                            ]
                            for player
                            in lineup.values()
                        )

                        if (
                            best is None
                            or selection_score
                            > best[
                                "selection_score"
                            ]
                        ):

                            best = {
                                "lineup":
                                    lineup,

                                "selection_score":
                                    selection_score,

                                "actual_score":
                                    actual_score,
                            }

    return best


# ==================================================
# Actual submitted ESPN lineup
# ==================================================

def get_actual_espn_offense(
    espn_roster,
):
    """
    Calculate what the user's real submitted offensive
    starters scored.

    Excludes:

        K
        D/ST
        BE
        IR
    """

    starters = []

    for player in espn_roster:

        position = getattr(
            player,
            "position",
            None,
        )

        slot = getattr(
            player,
            "slot_position",
            None,
        )

        if position not in POSITIONS:
            continue

        if slot in (
            "BE",
            "IR",
        ):
            continue

        points = safe_float(
            getattr(
                player,
                "points",
                0.0,
            )
        )

        starters.append(
            {
                "name":
                    getattr(
                        player,
                        "name",
                        "UNKNOWN",
                    ),

                "position":
                    position,

                "slot":
                    slot,

                "actual_points":
                    points,
            }
        )

    total = sum(
        player[
            "actual_points"
        ]
        for player
        in starters
    )

    return (
        starters,
        total,
    )


# ==================================================
# Printing helpers
# ==================================================

def print_lineup(
    title,
    result,
    score_key,
):
    """
    Print one model-selected lineup.

    score_key explicitly tells us which prediction
    selected the lineup, avoiding fragile title parsing.
    """

    print(
        f"\n{title}"
    )

    if result is None:

        print(
            "Could not construct lineup."
        )

        return

    for (
        slot,
        player,
    ) in result[
        "lineup"
    ].items():

        predicted = safe_float(
            player.get(
                score_key
            )
        )

        print(
            f"  {slot:4} "
            f"{player['name']:24} "
            f"pred="
            f"{predicted:6.2f} "
            f"actual="
            f"{player['actual_points']:6.2f}"
        )

    print(
        f"  Actual points: "
        f"{result['actual_score']:.2f}"
    )


# ==================================================
# Evaluate one historical week
# ==================================================

def evaluate_week(
    league,
    team_id,
    dataset,
    model,
    week,
):

    espn_roster = (
        get_historical_roster(
            league=league,
            team_id=team_id,
            week=week,
        )
    )

    prediction_lookup = (
        build_week_prediction_lookup(
            dataset=dataset,
            model=model,
            week=week,
        )
    )

    (
        matched_roster,
        unmatched,
    ) = build_matched_roster(
        espn_roster=espn_roster,
        prediction_lookup=prediction_lookup,
    )

    (
        actual_starters,
        actual_submitted_score,
    ) = get_actual_espn_offense(
        espn_roster
    )

    print(
        "\n"
        + "=" * 76
    )

    print(
        f"WEEK {week}"
    )

    print(
        "=" * 76
    )

    offensive_roster_count = sum(
        1
        for player in espn_roster
        if (
            getattr(
                player,
                "position",
                None,
            )
            in POSITIONS
            and getattr(
                player,
                "slot_position",
                None,
            )
            != "IR"
        )
    )

    print(
        f"Eligible offensive ESPN roster: "
        f"{offensive_roster_count}"
    )

    print(
        f"Matched to ECR-ML features: "
        f"{len(matched_roster)}"
    )

    print(
        f"Unmatched: "
        f"{len(unmatched)}"
    )

    if unmatched:

        print(
            "\nUnmatched players:"
        )

        for player in unmatched:

            print(
                f"  "
                f"{player['name']:24} "
                f"{player['position']:3} "
                f"slot={player['slot']:8} "
                f"ESPN proj="
                f"{player['espn_projection']:.2f}"
            )

    # ==================================================
    # Optimize with pure methods
    # ==================================================

    ml_result = (
        optimize_offensive_lineup(
            matched_roster,
            "ml_prediction",
        )
    )

    espn_projection_result = (
        optimize_offensive_lineup(
            matched_roster,
            "espn_prediction",
        )
    )

    season_result = (
        optimize_offensive_lineup(
            matched_roster,
            "season_prediction",
        )
    )

    l3_result = (
        optimize_offensive_lineup(
            matched_roster,
            "l3_prediction",
        )
    )

    hindsight_result = (
        optimize_offensive_lineup(
            matched_roster,
            "actual_points",
        )
    )

    # ==================================================
    # Optimize every projection / ML blend
    # ==================================================

    blend_results = {}

    for ml_weight in BLEND_WEIGHTS:

        column_name = (
            blend_column_name(
                ml_weight
            )
        )

        blend_results[
            ml_weight
        ] = (
            optimize_offensive_lineup(
                matched_roster,
                column_name,
            )
        )

    # --------------------------------------------------
    # Make sure every method could build a lineup.
    # --------------------------------------------------

    required_results = [
        ml_result,
        espn_projection_result,
        season_result,
        l3_result,
        hindsight_result,
    ]

    required_results.extend(
        blend_results.values()
    )

    if any(
        result is None
        for result
        in required_results
    ):

        print(
            "\nSKIP: not enough matched players "
            "to construct every legal lineup."
        )

        return None

    # ==================================================
    # Actual submitted ESPN lineup
    # ==================================================

    print(
        "\nACTUAL SUBMITTED ESPN OFFENSIVE LINEUP"
    )

    for player in actual_starters:

        print(
            f"  "
            f"{player['slot']:8} "
            f"{player['name']:24} "
            f"{player['actual_points']:6.2f}"
        )

    print(
        f"  Actual submitted offense: "
        f"{actual_submitted_score:.2f}"
    )

    # ==================================================
    # Main prediction lineups
    # ==================================================

    print_lineup(
        "ESPN PROJECTION LINEUP",
        espn_projection_result,
        "espn_prediction",
    )

    print_lineup(
        "ECR-ENHANCED ML LINEUP",
        ml_result,
        "ml_prediction",
    )

    # 0% and 100% are already represented by the pure
    # ESPN and pure ML lineups above, so only print the
    # true hybrid blends here.

    for ml_weight in (
        0.25,
        0.50,
        0.75,
    ):

        print_lineup(
            (
                f"{ml_weight:.0%} ECR-ML + "
                f"{1.0 - ml_weight:.0%} ESPN "
                f"LINEUP"
            ),
            blend_results[
                ml_weight
            ],
            blend_column_name(
                ml_weight
            ),
        )

    print_lineup(
        "SEASON-AVERAGE LINEUP",
        season_result,
        "season_prediction",
    )

    print_lineup(
        "LAST-3 LINEUP",
        l3_result,
        "l3_prediction",
    )

    print_lineup(
        "HINDSIGHT-OPTIMAL LINEUP",
        hindsight_result,
        "actual_points",
    )

    # ==================================================
    # Regret
    # ==================================================

    hindsight_score = (
        hindsight_result[
            "actual_score"
        ]
    )

    submitted_regret = (
        hindsight_score
        - actual_submitted_score
    )

    ml_regret = (
        hindsight_score
        - ml_result[
            "actual_score"
        ]
    )

    espn_projection_regret = (
        hindsight_score
        - espn_projection_result[
            "actual_score"
        ]
    )

    season_regret = (
        hindsight_score
        - season_result[
            "actual_score"
        ]
    )

    l3_regret = (
        hindsight_score
        - l3_result[
            "actual_score"
        ]
    )

    blend_actual_scores = {}

    blend_regrets = {}

    for (
        ml_weight,
        result,
    ) in blend_results.items():

        actual_score = (
            result[
                "actual_score"
            ]
        )

        blend_actual_scores[
            ml_weight
        ] = actual_score

        blend_regrets[
            ml_weight
        ] = (
            hindsight_score
            - actual_score
        )

    print(
        "\nREGRET"
    )

    print(
        f"Your submitted:   "
        f"{submitted_regret:.2f}"
    )

    print(
        f"ESPN projection:  "
        f"{espn_projection_regret:.2f}"
    )

    print(
        f"ECR-ML:           "
        f"{ml_regret:.2f}"
    )

    for ml_weight in BLEND_WEIGHTS:

        print(
            f"{ml_weight:>4.0%} ML / "
            f"{1.0 - ml_weight:>4.0%} ESPN: "
            f"{blend_regrets[ml_weight]:.2f}"
        )

    print(
        f"Season average:   "
        f"{season_regret:.2f}"
    )

    print(
        f"Last-3 average:   "
        f"{l3_regret:.2f}"
    )

    # ==================================================
    # Return one week of results
    # ==================================================

    week_result = {
        "week":
            week,

        "matched":
            len(
                matched_roster
            ),

        "unmatched":
            len(
                unmatched
            ),

        # ----------------------------------------------
        # Actual points produced by each selected lineup
        # ----------------------------------------------

        "submitted_actual":
            actual_submitted_score,

        "ml_actual":
            ml_result[
                "actual_score"
            ],

        "espn_projection_actual":
            espn_projection_result[
                "actual_score"
            ],

        "season_actual":
            season_result[
                "actual_score"
            ],

        "l3_actual":
            l3_result[
                "actual_score"
            ],

        "hindsight_actual":
            hindsight_score,

        # ----------------------------------------------
        # Regret
        # ----------------------------------------------

        "submitted_regret":
            submitted_regret,

        "ml_regret":
            ml_regret,

        "espn_projection_regret":
            espn_projection_regret,

        "season_regret":
            season_regret,

        "l3_regret":
            l3_regret,
    }

    # ----------------------------------------------
    # Add dynamic blend results.
    # ----------------------------------------------

    for ml_weight in BLEND_WEIGHTS:

        week_result[
            blend_actual_column(
                ml_weight
            )
        ] = (
            blend_actual_scores[
                ml_weight
            ]
        )

        week_result[
            blend_regret_column(
                ml_weight
            )
        ] = (
            blend_regrets[
                ml_weight
            ]
        )

    return week_result


# ==================================================
# Head-to-head helper
# ==================================================

def print_head_to_head(
    result_df,
    left_name,
    left_column,
    right_name,
    right_column,
):
    """
    Compare the actual fantasy points generated by two
    lineup-selection methods week by week.
    """

    left_wins = (
        result_df[
            left_column
        ]
        > result_df[
            right_column
        ]
    ).sum()

    right_wins = (
        result_df[
            right_column
        ]
        > result_df[
            left_column
        ]
    ).sum()

    ties = (
        result_df[
            left_column
        ]
        == result_df[
            right_column
        ]
    ).sum()

    total_point_difference = (
        result_df[
            left_column
        ].sum()
        - result_df[
            right_column
        ].sum()
    )

    print(
        f"\n{left_name} VS {right_name}"
    )

    print(
        f"{left_name} wins: "
        f"{left_wins}"
    )

    print(
        f"{right_name} wins: "
        f"{right_wins}"
    )

    print(
        f"Ties: "
        f"{ties}"
    )

    print(
        f"Total {left_name} "
        f"point advantage: "
        f"{total_point_difference:+.2f}"
    )


# ==================================================
# Summary
# ==================================================

def print_summary(
    results,
):

    if not results:

        print(
            "\nNo valid weeks evaluated."
        )

        return

    result_df = pd.DataFrame(
        results
    )

    print(
        "\n"
        + "=" * 100
    )

    print(
        "ECR-ENHANCED LINEUP BACKTEST SUMMARY"
    )

    print(
        "=" * 100
    )

    print(
        f"Completed weeks evaluated: "
        f"{len(result_df)}"
    )

    # ==================================================
    # Week-by-week actual points
    # ==================================================

    print(
        "\nWEEK-BY-WEEK ACTUAL POINTS"
    )

    print(
        f"{'WK':<5}"
        f"{'REAL':>9}"
        f"{'ESPN':>9}"
        f"{'25ML':>9}"
        f"{'50ML':>9}"
        f"{'75ML':>9}"
        f"{'ML':>9}"
        f"{'OPT':>9}"
    )

    for (
        _,
        row,
    ) in result_df.iterrows():

        print(
            f"{int(row['week']):<5}"
            f"{row['submitted_actual']:>9.2f}"
            f"{row['espn_projection_actual']:>9.2f}"
            f"{row['blend_25_actual']:>9.2f}"
            f"{row['blend_50_actual']:>9.2f}"
            f"{row['blend_75_actual']:>9.2f}"
            f"{row['ml_actual']:>9.2f}"
            f"{row['hindsight_actual']:>9.2f}"
        )

    # ==================================================
    # Baseline summary
    # ==================================================

    print(
        "\nBASELINE AVERAGE ACTUAL POINTS"
    )

    print(
        f"Your real lineup:    "
        f"{result_df['submitted_actual'].mean():.2f}"
    )

    print(
        f"ESPN projection:     "
        f"{result_df['espn_projection_actual'].mean():.2f}"
    )

    print(
        f"ECR-enhanced ML:     "
        f"{result_df['ml_actual'].mean():.2f}"
    )

    print(
        f"Season average:      "
        f"{result_df['season_actual'].mean():.2f}"
    )

    print(
        f"Last-3 average:      "
        f"{result_df['l3_actual'].mean():.2f}"
    )

    print(
        f"Hindsight optimal:   "
        f"{result_df['hindsight_actual'].mean():.2f}"
    )

    # ==================================================
    # Blend comparison
    # ==================================================

    print(
        "\nBLEND WEIGHT COMPARISON"
    )

    print(
        f"{'ECR-ML':>10}"
        f"{'ESPN':>10}"
        f"{'AVG PTS':>12}"
        f"{'AVG REGRET':>14}"
        f"{'VS ESPN':>12}"
    )

    espn_average = (
        result_df[
            "espn_projection_actual"
        ].mean()
    )

    best_weight = None

    best_average = None

    for ml_weight in BLEND_WEIGHTS:

        actual_column = (
            blend_actual_column(
                ml_weight
            )
        )

        regret_column = (
            blend_regret_column(
                ml_weight
            )
        )

        average_points = (
            result_df[
                actual_column
            ].mean()
        )

        average_regret = (
            result_df[
                regret_column
            ].mean()
        )

        vs_espn = (
            average_points
            - espn_average
        )

        print(
            f"{ml_weight:>9.0%}"
            f"{1.0 - ml_weight:>10.0%}"
            f"{average_points:>12.2f}"
            f"{average_regret:>14.2f}"
            f"{vs_espn:>+12.2f}"
        )

        if (
            best_average is None
            or average_points
            > best_average
        ):

            best_average = (
                average_points
            )

            best_weight = (
                ml_weight
            )

    # ==================================================
    # Best observed blend
    # ==================================================

    print(
        "\nBEST OBSERVED BLEND"
    )

    print(
        f"{best_weight:.0%} ECR-ML + "
        f"{1.0 - best_weight:.0%} ESPN"
    )

    print(
        f"Average actual lineup points: "
        f"{best_average:.2f}"
    )

    print(
        f"Difference vs pure ESPN: "
        f"{best_average - espn_average:+.2f} "
        f"points/week"
    )

    print(
        "\nIMPORTANT:"
    )

    print(
        "This is an exploratory result from a small "
        "number of completed 2026 weeks."
    )

    print(
        "Do NOT automatically deploy the winning "
        "weight to the live system yet."
    )

    # ==================================================
    # Average regret
    # ==================================================

    print(
        "\nAVERAGE LINEUP REGRET"
    )

    print(
        f"Your real lineup:    "
        f"{result_df['submitted_regret'].mean():.2f}"
    )

    print(
        f"ESPN projection:     "
        f"{result_df['espn_projection_regret'].mean():.2f}"
    )

    print(
        f"ECR-enhanced ML:     "
        f"{result_df['ml_regret'].mean():.2f}"
    )

    print(
        f"Season average:      "
        f"{result_df['season_regret'].mean():.2f}"
    )

    print(
        f"Last-3 average:      "
        f"{result_df['l3_regret'].mean():.2f}"
    )

    # ==================================================
    # Head-to-head with ESPN
    # ==================================================

    print(
        "\nHEAD-TO-HEAD AGAINST ESPN"
    )

    for ml_weight in (
        0.25,
        0.50,
        0.75,
        1.00,
    ):

        label = (
            f"{ml_weight:.0%} ML"
        )

        print_head_to_head(
            result_df=result_df,
            left_name=label,
            left_column=(
                blend_actual_column(
                    ml_weight
                )
            ),
            right_name="ESPN",
            right_column=(
                "espn_projection_actual"
            ),
        )


# ==================================================
# Main
# ==================================================

def main():

    dataset = load_dataset()

    model = load_model()

    league = get_league()

    my_team = get_my_team(
        league
    )

    print(
        f"League: "
        f"{league.settings.name}"
    )

    print(
        f"Team: "
        f"{my_team.team_name}"
    )

    print(
        f"Current week: "
        f"{league.current_week}"
    )

    print(
        "Testing ECR-ML weights: "
        + ", ".join(
            f"{weight:.0%}"
            for weight
            in BLEND_WEIGHTS
        )
    )

    results = []

    # --------------------------------------------------
    # Week 1 is intentionally excluded because the ML
    # features require same-season history.
    #
    # league.current_week itself is excluded because it
    # may still be in progress.
    # --------------------------------------------------

    for week in range(
        2,
        league.current_week,
    ):

        result = evaluate_week(
            league=league,
            team_id=my_team.team_id,
            dataset=dataset,
            model=model,
            week=week,
        )

        if result is not None:

            results.append(
                result
            )

    print_summary(
        results
    )


if __name__ == "__main__":
    main()