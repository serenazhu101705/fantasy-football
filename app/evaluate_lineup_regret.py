from itertools import combinations
from pathlib import Path
import re
import unicodedata

import joblib
import pandas as pd
import polars as pl

from espn_client import (
    get_league,
    get_my_team,
)

from train_weekly_model import (
    NUMERIC_FEATURES,
    CATEGORICAL_FEATURES,
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

SEASON = 2026

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

# --------------------------------------------------
# First blend experiment.
#
# Do NOT tune these weights on the four 2026 weeks.
# That would badly overfit such a tiny sample.
# --------------------------------------------------

ML_BLEND_WEIGHT = 0.50
ESPN_BLEND_WEIGHT = 0.50


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

    both become approximately the same key.
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
# Load ML data
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


def load_model():

    if not MODEL_PATH.exists():

        raise FileNotFoundError(
            f"Missing model: "
            f"{MODEL_PATH}"
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
# Weekly prediction lookup
# ==================================================

def build_week_prediction_lookup(
    dataset,
    model,
    week,
):
    """
    Build ML predictions for every available player
    during the requested 2026 week.

    The saved model was trained only on 2021-2025,
    so these 2026 predictions remain out-of-sample.
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

    if len(
        week_df
    ) == 0:

        return {}

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
# Match ESPN roster to ML data
# ==================================================

def build_matched_roster(
    espn_roster,
    prediction_lookup,
):
    """
    Match ESPN historical roster players to their
    pregame ML features.

    For this experiment, every prediction method uses
    the SAME matched player pool.

    That means:

        ML
        ESPN projection
        50/50 blend
        season average
        last-3 average
        hindsight

    are all choosing from exactly the same set of
    players.

    This makes the comparison fair.

    IR players are removed before matching because
    they were not valid lineup candidates.
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
        # IR players are not lineup candidates.
        #
        # Skip them before attempting the nflverse match so
        # they do not artificially increase our unmatched
        # count.
        # --------------------------------------------------

        if slot == "IR":
            continue

        actual_points = getattr(
            player,
            "points",
            0.0,
        )

        if actual_points is None:
            actual_points = 0.0

        espn_projection = getattr(
            player,
            "projected_points",
            None,
        )

        if espn_projection is None:
            espn_projection = 0.0

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
        # No ML feature row.
        #
        # For this particular experiment we exclude this
        # player from every method so everyone uses the
        # identical candidate pool.
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
                        float(
                            actual_points
                        ),

                    "espn_projection":
                        float(
                            espn_projection
                        ),
                }
            )

            continue

        ml_prediction = float(
            prediction[
                "ml_prediction"
            ]
        )

        espn_prediction = float(
            espn_projection
        )

        blend_prediction = (
            ML_BLEND_WEIGHT
            * ml_prediction
            + ESPN_BLEND_WEIGHT
            * espn_prediction
        )

        matched.append(
            {
                "key":
                    key,

                "name":
                    name,

                "position":
                    position,

                "slot":
                    slot,

                "actual_points":
                    float(
                        actual_points
                    ),

                "ml_prediction":
                    ml_prediction,

                "espn_prediction":
                    espn_prediction,

                "blend_prediction":
                    blend_prediction,

                "season_prediction":
                    float(
                        prediction[
                            "season_ppr_avg"
                        ]
                    ),

                "l3_prediction":
                    float(
                        prediction[
                            "l3_ppr_avg"
                        ]
                    ),
            }
        )

    return (
        matched,
        unmatched,
    )


# ==================================================
# Lineup optimizer
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

    score_key can be:

        ml_prediction
        espn_prediction
        blend_prediction
        season_prediction
        l3_prediction
        actual_points
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

    # Need enough players to construct a legal lineup.
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
                            player[
                                score_key
                            ]
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
    Calculate what the user's actual submitted
    offensive starters scored.

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

        points = getattr(
            player,
            "points",
            0.0,
        )

        if points is None:
            points = 0.0

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
                    float(
                        points
                    ),
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

def result_score(
    player,
    title,
):
    """
    Pick the appropriate displayed prediction based
    on the lineup label.
    """

    if "BLEND" in title:
        return player[
            "blend_prediction"
        ]

    if "ESPN" in title:
        return player[
            "espn_prediction"
        ]

    if "ML" in title:
        return player[
            "ml_prediction"
        ]

    if "SEASON" in title:
        return player[
            "season_prediction"
        ]

    if "LAST-3" in title:
        return player[
            "l3_prediction"
        ]

    return player[
        "actual_points"
    ]


def print_lineup(
    title,
    result,
):

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

        print(
            f"  {slot:4} "
            f"{player['name']:24} "
            f"pred="
            f"{result_score(player, title):6.2f} "
            f"actual="
            f"{player['actual_points']:6.2f}"
        )

    print(
        f"  Actual points: "
        f"{result['actual_score']:.2f}"
    )


# ==================================================
# Evaluate one week
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
            league,
            team_id,
            week,
        )
    )

    prediction_lookup = (
        build_week_prediction_lookup(
            dataset,
            model,
            week,
        )
    )

    (
        matched_roster,
        unmatched,
    ) = build_matched_roster(
        espn_roster,
        prediction_lookup,
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
        f"Matched to ML features: "
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
    # Optimize using each prediction method
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

    blend_result = (
        optimize_offensive_lineup(
            matched_roster,
            "blend_prediction",
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

    if any(
        result is None
        for result in (
            ml_result,
            espn_projection_result,
            blend_result,
            season_result,
            l3_result,
            hindsight_result,
        )
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
    # Model / projection lineups
    # ==================================================

    print_lineup(
        "ML LINEUP",
        ml_result,
    )

    print_lineup(
        "ESPN PROJECTION LINEUP",
        espn_projection_result,
    )

    print_lineup(
        "50/50 BLEND LINEUP",
        blend_result,
    )

    print_lineup(
        "SEASON-AVERAGE LINEUP",
        season_result,
    )

    print_lineup(
        "LAST-3 LINEUP",
        l3_result,
    )

    print_lineup(
        "HINDSIGHT-OPTIMAL LINEUP",
        hindsight_result,
    )

    # ==================================================
    # Regret
    # ==================================================

    hindsight_score = (
        hindsight_result[
            "actual_score"
        ]
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

    blend_regret = (
        hindsight_score
        - blend_result[
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

    submitted_regret = (
        hindsight_score
        - actual_submitted_score
    )

    print(
        "\nREGRET"
    )

    print(
        f"Your submitted:   "
        f"{submitted_regret:.2f}"
    )

    print(
        f"ML:               "
        f"{ml_regret:.2f}"
    )

    print(
        f"ESPN projection:  "
        f"{espn_projection_regret:.2f}"
    )

    print(
        f"50/50 blend:      "
        f"{blend_regret:.2f}"
    )

    print(
        f"Season average:   "
        f"{season_regret:.2f}"
    )

    print(
        f"Last-3 average:   "
        f"{l3_regret:.2f}"
    )

    return {
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

        # ------------------------------------------
        # Actual points scored by each chosen lineup
        # ------------------------------------------

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

        "blend_actual":
            blend_result[
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

        # ------------------------------------------
        # Regret
        # ------------------------------------------

        "submitted_regret":
            submitted_regret,

        "ml_regret":
            ml_regret,

        "espn_projection_regret":
            espn_projection_regret,

        "blend_regret":
            blend_regret,

        "season_regret":
            season_regret,

        "l3_regret":
            l3_regret,
    }


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
    Compare the actual fantasy points produced by two
    lineup-selection methods week-by-week.
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
        f"Total {left_name} point advantage: "
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
        + "=" * 90
    )

    print(
        "LINEUP BACKTEST SUMMARY"
    )

    print(
        "=" * 90
    )

    # ==================================================
    # Week-by-week
    # ==================================================

    print(
        "\nWEEK-BY-WEEK ACTUAL POINTS"
    )

    print(
        f"{'WK':<5}"
        f"{'REAL':>10}"
        f"{'ML':>10}"
        f"{'ESPN':>10}"
        f"{'BLEND':>10}"
        f"{'SEASON':>10}"
        f"{'L3':>10}"
        f"{'OPTIMAL':>10}"
    )

    for (
        _,
        row,
    ) in result_df.iterrows():

        print(
            f"{int(row['week']):<5}"
            f"{row['submitted_actual']:>10.2f}"
            f"{row['ml_actual']:>10.2f}"
            f"{row['espn_projection_actual']:>10.2f}"
            f"{row['blend_actual']:>10.2f}"
            f"{row['season_actual']:>10.2f}"
            f"{row['l3_actual']:>10.2f}"
            f"{row['hindsight_actual']:>10.2f}"
        )

    # ==================================================
    # Average points
    # ==================================================

    print(
        "\nAVERAGE ACTUAL POINTS"
    )

    print(
        f"Your real lineup:    "
        f"{result_df['submitted_actual'].mean():.2f}"
    )

    print(
        f"ML lineup:           "
        f"{result_df['ml_actual'].mean():.2f}"
    )

    print(
        f"ESPN projection:     "
        f"{result_df['espn_projection_actual'].mean():.2f}"
    )

    print(
        f"50/50 ML + ESPN:     "
        f"{result_df['blend_actual'].mean():.2f}"
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
        f"ML:                  "
        f"{result_df['ml_regret'].mean():.2f}"
    )

    print(
        f"ESPN projection:     "
        f"{result_df['espn_projection_regret'].mean():.2f}"
    )

    print(
        f"50/50 blend:         "
        f"{result_df['blend_regret'].mean():.2f}"
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
    # Head-to-head comparisons
    # ==================================================

    print_head_to_head(
        result_df=result_df,
        left_name="ML",
        left_column="ml_actual",
        right_name="ESPN",
        right_column="espn_projection_actual",
    )

    print_head_to_head(
        result_df=result_df,
        left_name="BLEND",
        left_column="blend_actual",
        right_name="ESPN",
        right_column="espn_projection_actual",
    )

    print_head_to_head(
        result_df=result_df,
        left_name="BLEND",
        left_column="blend_actual",
        right_name="ML",
        right_column="ml_actual",
    )

    print_head_to_head(
        result_df=result_df,
        left_name="ML",
        left_column="ml_actual",
        right_name="SEASON",
        right_column="season_actual",
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
        f"Blend weights: "
        f"{ML_BLEND_WEIGHT:.0%} ML + "
        f"{ESPN_BLEND_WEIGHT:.0%} ESPN"
    )

    results = []

    # --------------------------------------------------
    # Week 1 is intentionally excluded because our
    # current ML model requires same-season history.
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