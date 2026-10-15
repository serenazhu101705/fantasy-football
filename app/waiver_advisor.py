from copy import deepcopy

from lineup_optimizer import optimize_lineup


# ==================================================
# Lineup / roster construction constants
# ==================================================

REQUIRED_LINEUP_SLOTS = (
    "QB",
    "RB1",
    "RB2",
    "WR1",
    "WR2",
    "TE",
    "FLEX",
    "D/ST",
    "K",
)


# Minimum bench depth we want to maintain.
#
# These are NOT the minimum number required to start
# a lineup. They are roster-construction guardrails.
POSITION_MINIMUMS = {
    "QB": 1,
    "RB": 3,
    "WR": 3,
    "TE": 1,
}


# Going above these numbers is not illegal, but it can
# indicate inefficient roster construction.
POSITION_COMFORTABLE_MAX = {
    "QB": 2,
    "RB": 5,
    "WR": 5,
    "TE": 2,
}


# Penalty for each player above the comfortable max.
#
# QB gets a larger penalty because carrying too many QBs
# in a 1-QB league is usually especially inefficient.
EXCESS_POSITION_PENALTY = {
    "QB": 8,
    "RB": 2,
    "WR": 2,
    "TE": 4,
}


# ==================================================
# Roster balance helpers
# ==================================================

def get_position_counts(
    roster,
):
    """
    Count offensive players by fantasy position.
    """

    counts = {
        "QB": 0,
        "RB": 0,
        "WR": 0,
        "TE": 0,
    }

    for player in roster:

        position = player.get(
            "position"
        )

        if position in counts:
            counts[position] += 1

    return counts


def roster_balance_penalty(
    roster,
):
    """
    Calculate a simple roster-construction penalty.

    Lower is better.

    Large penalty:
        A position becomes dangerously thin.

    Small penalty:
        The roster has excessive depth at one position.

    This is separate from player value.
    """

    counts = get_position_counts(
        roster
    )

    penalty = 0.0
    risks = []

    # --------------------------------------------------
    # Too thin
    # --------------------------------------------------

    for (
        position,
        minimum,
    ) in POSITION_MINIMUMS.items():

        count = counts.get(
            position,
            0,
        )

        if count < minimum:

            shortage = (
                minimum
                - count
            )

            # Being too thin is a meaningful roster risk.
            thin_penalty = (
                25.0
                * shortage
            )

            penalty += (
                thin_penalty
            )

            risks.append(
                f"{position} depth falls to {count}"
            )

    # --------------------------------------------------
    # Excessive depth
    # --------------------------------------------------

    for (
        position,
        maximum,
    ) in POSITION_COMFORTABLE_MAX.items():

        count = counts.get(
            position,
            0,
        )

        if count > maximum:

            excess = (
                count
                - maximum
            )

            penalty += (
                excess
                * EXCESS_POSITION_PENALTY[
                    position
                ]
            )

    return {
        "penalty":
            penalty,

        "risks":
            risks,

        "counts":
            counts,
    }


def evaluate_roster_balance(
    before_roster,
    after_roster,
):
    """
    Compare roster construction before and after
    a transaction.

    Positive balance_delta:
        roster construction improved

    Negative balance_delta:
        roster construction became worse
    """

    before = roster_balance_penalty(
        before_roster
    )

    after = roster_balance_penalty(
        after_roster
    )

    balance_delta = (
        before["penalty"]
        - after["penalty"]
    )

    return {
        "before_counts":
            before["counts"],

        "after_counts":
            after["counts"],

        "before_penalty":
            before["penalty"],

        "after_penalty":
            after["penalty"],

        "balance_delta":
            balance_delta,

        "depth_risk":
            len(
                after["risks"]
            ) > 0,

        "depth_risks":
            after["risks"],
    }


# ==================================================
# Lineup legality
# ==================================================

def has_complete_lineup(
    optimized,
):
    """
    Return True only if the optimizer filled every
    required starting lineup slot.
    """

    lineup = (
        optimized.get(
            "lineup"
        )
        or {}
    )

    return all(
        lineup.get(
            slot
        ) is not None

        for slot
        in REQUIRED_LINEUP_SLOTS
    )


# ==================================================
# LeagueLogs helpers
# ==================================================

def _leaguelogs(
    player,
):
    """
    Return LeagueLogs signal data attached during
    enrichment.
    """

    return (
        player
        .get(
            "signals",
            {},
        )
        .get(
            "leaguelogs",
            {},
        )
    )


def _market_value(
    player,
):
    """
    Return LeagueLogs market value when available.
    """

    value = (
        _leaguelogs(
            player
        )
        .get(
            "market_value"
        )
    )

    return (
        float(
            value
        )
        if value is not None
        else None
    )


def _market_trend(
    player,
):
    """
    Return LeagueLogs 7-day and 30-day movement.
    """

    data = _leaguelogs(
        player
    )

    return {
        "move_7d":
            data.get(
                "market_move_7d"
            ),

        "move_30d":
            data.get(
                "market_move_30d"
            ),
    }


# ==================================================
# Player identity / transaction simulation
# ==================================================

def _same_player(
    a,
    b,
):
    """
    Prefer ESPN IDs when available.

    Fall back to name + position.
    """

    a_id = a.get(
        "espn_id"
    )

    b_id = b.get(
        "espn_id"
    )

    if (
        a_id is not None
        and b_id is not None
    ):
        return (
            a_id
            == b_id
        )

    return (
        a.get(
            "name"
        )
        == b.get(
            "name"
        )
        and
        a.get(
            "position"
        )
        == b.get(
            "position"
        )
    )


def simulate_transaction(
    roster,
    add_player,
    drop_player,
):
    """
    Return a new roster after:

        DROP drop_player
        ADD  add_player

    Does not modify the original roster.
    """

    new_roster = [
        deepcopy(
            player
        )

        for player
        in roster

        if not _same_player(
            player,
            drop_player,
        )
    ]

    new_roster.append(
        deepcopy(
            add_player
        )
    )

    return new_roster


# ==================================================
# Recommendation classification
# ==================================================

def classify_transaction(
    result,
):
    """
    Convert transaction metrics into a recommendation.

    Categories:

        STRONG_ADD
        ADD
        STREAMER
        STASH
        HOLD
        AVOID
        INCOMPLETE_DATA
    """

    weekly_delta = (
        result.get(
            "weekly_lineup_delta"
        )
        or 0.0
    )

    projected_delta = (
        result.get(
            "projected_points_delta"
        )
        or 0.0
    )

    ros_delta = result.get(
        "ros_delta"
    )

    market_delta = result.get(
        "market_value_delta"
    )

    is_stash = result.get(
        "is_stash",
        False,
    )

    weekly_complete = result.get(
        "candidate_weekly_complete"
    )

    ros_complete = result.get(
        "candidate_ros_complete"
    )

    depth_risk = result.get(
        "depth_risk",
        False,
    )

    # --------------------------------------------------
    # Incomplete data
    # --------------------------------------------------

    if (
        weekly_complete is False
        and ros_complete is False
    ):
        return {
            "category":
                "INCOMPLETE_DATA",

            "reason":
                (
                    "Both weekly and rest-of-season "
                    "data are incomplete, so there is "
                    "not enough evidence to recommend "
                    "the move."
                ),
        }

    # --------------------------------------------------
    # Injured / unavailable stash
    # --------------------------------------------------

    if is_stash:

        strong_ros_gain = (
            ros_delta is not None
            and ros_delta >= 15
        )

        strong_market_gain = (
            market_delta is not None
            and market_delta >= 750
        )

        if depth_risk:

            return {
                "category":
                    "HOLD",

                "reason":
                    (
                        "The stash has upside, but the "
                        "move would create too much "
                        "roster-depth risk."
                    ),
            }

        if (
            strong_ros_gain
            or strong_market_gain
        ):
            return {
                "category":
                    "STASH",

                "reason":
                    (
                        "Provides little immediate value "
                        "but offers enough long-term "
                        "upside to justify a bench spot."
                    ),
            }

        return {
            "category":
                "HOLD",

            "reason":
                (
                    "The stash does not provide enough "
                    "long-term improvement to justify "
                    "the roster move."
                ),
        }

    # --------------------------------------------------
    # Strong immediate upgrade
    # --------------------------------------------------

    weekly_upgrade = (
        weekly_delta >= 5
    )

    projection_upgrade = (
        projected_delta >= 2
    )

    ros_not_worse = (
        ros_delta is None
        or ros_delta >= 0
    )

    market_not_worse = (
        market_delta is None
        or market_delta >= 0
    )

    if (
        weekly_upgrade
        and projection_upgrade
        and ros_not_worse
        and market_not_worse
        and not depth_risk
    ):
        return {
            "category":
                "STRONG_ADD",

            "reason":
                (
                    "Meaningfully improves the current "
                    "starting lineup without sacrificing "
                    "long-term roster value."
                ),
        }

    # --------------------------------------------------
    # Weekly streamer
    # --------------------------------------------------

    long_term_downgrade = (
        (
            ros_delta is not None
            and ros_delta < 0
        )
        or
        (
            market_delta is not None
            and market_delta < 0
        )
    )

    if (
        weekly_delta >= 5
        and long_term_downgrade
    ):
        return {
            "category":
                "STREAMER",

            "reason":
                (
                    "Improves this week's lineup, but "
                    "sacrifices some longer-term value."
                ),
        }

    # --------------------------------------------------
    # Long-term add
    #
    # IMPORTANT:
    # A ROS upgrade alone is no longer enough.
    #
    # We allow only a very small current-week sacrifice.
    # --------------------------------------------------

    strong_ros_upgrade = (
        ros_delta is not None
        and ros_delta >= 10
    )

    strong_market_upgrade = (
        market_delta is not None
        and market_delta >= 500
    )

    current_week_not_hurt_much = (
        weekly_delta >= -1.0
        and projected_delta >= -1.0
    )

    if (
        (
            strong_ros_upgrade
            or strong_market_upgrade
        )
        and current_week_not_hurt_much
        and not depth_risk
    ):
        return {
            "category":
                "ADD",

            "reason":
                (
                    "Improves long-term roster value "
                    "without materially weakening the "
                    "current week's lineup."
                ),
        }

    # --------------------------------------------------
    # Clearly bad transaction
    # --------------------------------------------------

    ros_bad = (
        ros_delta is not None
        and ros_delta < 0
    )

    market_bad = (
        market_delta is not None
        and market_delta < 0
    )

    if (
        weekly_delta <= 0
        and ros_bad
        and market_bad
    ):
        return {
            "category":
                "AVOID",

            "reason":
                (
                    "Does not improve the current lineup "
                    "and reduces long-term roster value."
                ),
        }

    # --------------------------------------------------
    # Default
    # --------------------------------------------------

    return {
        "category":
            "HOLD",

        "reason":
            (
                "The upgrade is not large enough to "
                "justify making the roster move."
            ),
    }

# ==================================================
# Evaluate one transaction
# ==================================================

def evaluate_transaction(
    roster,
    add_player,
    drop_player,
):
    """
    Evaluate one possible waiver transaction.

    We look at:

        1. Weekly lineup impact
        2. Projected-points lineup impact
        3. LeagueLogs market value change
        4. ROS score change for same-position moves
        5. Data quality
        6. Injury / stash context
        7. Positional roster balance

    No LLM is involved here.
    """

    current = optimize_lineup(
        roster
    )

    new_roster = simulate_transaction(
        roster,
        add_player,
        drop_player,
    )

    roster_balance = (
        evaluate_roster_balance(
            roster,
            new_roster,
        )
    )

    after = optimize_lineup(
        new_roster
    )

    # --------------------------------------------------
    # Reject illegal / incomplete starting lineups
    # --------------------------------------------------

    if not has_complete_lineup(
        after
    ):

        return {
            "add":
                add_player,

            "drop":
                drop_player,

            "new_roster":
                new_roster,

            "before_lineup":
                current,

            "after_lineup":
                after,

            "valid":
                False,

            "weekly_lineup_delta":
                None,

            "projected_points_delta":
                None,

            "market_value_delta":
                None,

            "same_position":
                (
                    add_player.get(
                        "position"
                    )
                    == drop_player.get(
                        "position"
                    )
                ),

            "ros_delta":
                None,

            "roster_balance_delta":
                roster_balance[
                    "balance_delta"
                ],

            "depth_risk":
                roster_balance[
                    "depth_risk"
                ],

            "depth_risks":
                roster_balance[
                    "depth_risks"
                ],

            "before_position_counts":
                roster_balance[
                    "before_counts"
                ],

            "after_position_counts":
                roster_balance[
                    "after_counts"
                ],

            "recommendation":
                "INVALID",

            "recommendation_reason":
                (
                    "This transaction would leave the "
                    "roster unable to field a complete "
                    "starting lineup."
                ),
        }

    # --------------------------------------------------
    # Weekly lineup impact
    # --------------------------------------------------

    current_weekly = (
        current.get(
            "lineup_score"
        )
        or 0.0
    )

    after_weekly = (
        after.get(
            "lineup_score"
        )
        or 0.0
    )

    weekly_lineup_delta = (
        after_weekly
        - current_weekly
    )

    # --------------------------------------------------
    # Projected fantasy-point impact
    # --------------------------------------------------

    current_projection = (
        current.get(
            "projected_total"
        )
        or 0.0
    )

    after_projection = (
        after.get(
            "projected_total"
        )
        or 0.0
    )

    projected_points_delta = (
        after_projection
        - current_projection
    )

    # --------------------------------------------------
    # LeagueLogs market value
    #
    # Useful across positions.
    # --------------------------------------------------

    add_market = _market_value(
        add_player
    )

    drop_market = _market_value(
        drop_player
    )

    market_value_delta = None

    if (
        add_market is not None
        and drop_market is not None
    ):

        market_value_delta = (
            add_market
            - drop_market
        )

    # --------------------------------------------------
    # ROS score
    #
    # Only directly compare same-position players
    # because our current ROS score is position-relative.
    # --------------------------------------------------

    same_position = (
        add_player.get(
            "position"
        )
        == drop_player.get(
            "position"
        )
    )

    ros_delta = None

    if same_position:

        add_ros = (
            add_player.get(
                "ros_score"
            )
        )

        drop_ros = (
            drop_player.get(
                "ros_score"
            )
        )

        if (
            add_ros is not None
            and drop_ros is not None
        ):

            ros_delta = (
                add_ros
                - drop_ros
            )

    # --------------------------------------------------
    # Data quality
    # --------------------------------------------------

    add_weekly_complete = (
        add_player.get(
            "weekly_data_complete"
        )
    )

    add_ros_complete = (
        add_player.get(
            "ros_data_complete"
        )
    )

    drop_weekly_complete = (
        drop_player.get(
            "weekly_data_complete"
        )
    )

    drop_ros_complete = (
        drop_player.get(
            "ros_data_complete"
        )
    )

    # --------------------------------------------------
    # Stash detection
    # --------------------------------------------------

    availability = (
        add_player.get(
            "availability"
        )
        or {}
    )

    add_can_play = (
        availability.get(
            "can_play_this_week",
            True,
        )
    )

    is_stash = (
        not add_can_play
        and (
            add_player.get(
                "ros_score"
            )
            or 0.0
        ) > 0
    )

    # --------------------------------------------------
    # LeagueLogs trend
    # --------------------------------------------------

    trend = _market_trend(
        add_player
    )

    # --------------------------------------------------
    # Build result
    # --------------------------------------------------

    result = {
        "add":
            add_player,

        "drop":
            drop_player,

        "valid":
            True,

        "new_roster":
            new_roster,

        "before_lineup":
            current,

        "after_lineup":
            after,

        "weekly_lineup_delta":
            round(
                weekly_lineup_delta,
                2,
            ),

        "projected_points_delta":
            round(
                projected_points_delta,
                2,
            ),

        "market_value_delta":
            (
                round(
                    market_value_delta,
                    1,
                )
                if market_value_delta
                is not None
                else None
            ),

        "same_position":
            same_position,

        "ros_delta":
            (
                round(
                    ros_delta,
                    1,
                )
                if ros_delta
                is not None
                else None
            ),

        "candidate_weekly_complete":
            add_weekly_complete,

        "candidate_ros_complete":
            add_ros_complete,

        "drop_weekly_complete":
            drop_weekly_complete,

        "drop_ros_complete":
            drop_ros_complete,

        "candidate_missing_weekly":
            add_player.get(
                "weekly_missing_data",
                [],
            ),

        "candidate_missing_ros":
            add_player.get(
                "ros_missing_data",
                [],
            ),

        "is_stash":
            is_stash,

        "market_move_7d":
            trend[
                "move_7d"
            ],

        "market_move_30d":
            trend[
                "move_30d"
            ],

        "roster_balance_delta":
            roster_balance[
                "balance_delta"
            ],

        "depth_risk":
            roster_balance[
                "depth_risk"
            ],

        "depth_risks":
            roster_balance[
                "depth_risks"
            ],

        "before_position_counts":
            roster_balance[
                "before_counts"
            ],

        "after_position_counts":
            roster_balance[
                "after_counts"
            ],
    }

    recommendation = (
        classify_transaction(
            result
        )
    )

    result[
        "recommendation"
    ] = recommendation[
        "category"
    ]

    result[
        "recommendation_reason"
    ] = recommendation[
        "reason"
    ]

    return result


# ==================================================
# Drop protection
# ==================================================

def get_protection_status(
    player,
):
    """
    Decide whether a player should be protected from
    ordinary waiver-drop recommendations.

    This is intentionally conservative.

    A protected player could still eventually be traded,
    but the waiver engine should not casually drop them.
    """

    position = player.get(
        "position"
    )

    # K/DST will get their own model later.
    if position in (
        "K",
        "D/ST",
    ):

        return {
            "protected":
                True,

            "reason":
                "Special position handled separately",
        }

    ll = _leaguelogs(
        player
    )

    market_rank = ll.get(
        "market_rank"
    )

    market_value = ll.get(
        "market_value"
    )

    ros_score = player.get(
        "ros_score"
    )

    availability = (
        player.get(
            "availability"
        )
        or {}
    )

    can_play = (
        availability.get(
            "can_play_this_week",
            True,
        )
    )

    # --------------------------------------------------
    # Elite / high-value asset
    # --------------------------------------------------

    if (
        market_rank is not None
        and market_rank <= 50
    ):

        return {
            "protected":
                True,

            "reason":
                (
                    f"Top-{market_rank} "
                    "LeagueLogs asset"
                ),
        }

    if (
        market_value is not None
        and market_value >= 1600
    ):

        return {
            "protected":
                True,

            "reason":
                "High market value",
        }

    # --------------------------------------------------
    # Strong ROS asset
    # --------------------------------------------------

    if (
        ros_score is not None
        and ros_score >= 60
    ):

        return {
            "protected":
                True,

            "reason":
                "Strong rest-of-season value",
        }

    # --------------------------------------------------
    # Valuable injured stash
    # --------------------------------------------------

    if not can_play:

        if (
            market_value is not None
            and market_value >= 1200
        ):

            return {
                "protected":
                    True,

                "reason":
                    "Valuable injured stash",
            }

    return {
        "protected":
            False,

        "reason":
            None,
    }


def get_drop_candidates(
    roster,
):
    """
    Return roster players that can realistically be
    considered for a waiver drop.
    """

    candidates = []

    for player in roster:

        protection = (
            get_protection_status(
                player
            )
        )

        player[
            "waiver_protected"
        ] = protection[
            "protected"
        ]

        player[
            "protection_reason"
        ] = protection[
            "reason"
        ]

        if protection[
            "protected"
        ]:
            continue

        candidates.append(
            player
        )

    return candidates


# ==================================================
# Evaluate all possible realistic waiver moves
# ==================================================

def evaluate_all_transactions(
    roster,
    waiver_candidates,
):
    """
    Evaluate realistic waiver ADD/DROP combinations.

    Protected players are excluded from ordinary
    waiver-drop consideration.

    Invalid transactions are removed.
    """

    results = []

    drop_candidates = (
        get_drop_candidates(
            roster
        )
    )

    for add_player in waiver_candidates:

        for drop_player in drop_candidates:

            # K/DST will eventually have separate
            # streaming / replacement logic.
            if (
                add_player.get(
                    "position"
                )
                in (
                    "K",
                    "D/ST",
                )
                or
                drop_player.get(
                    "position"
                )
                in (
                    "K",
                    "D/ST",
                )
            ):
                continue

            result = (
                evaluate_transaction(
                    roster,
                    add_player,
                    drop_player,
                )
            )

            if result.get(
                "valid",
                True,
            ):

                results.append(
                    result
                )

    return results


# ==================================================
# Ranking
# ==================================================

def transaction_priority(
    result,
):
    """
    Assign a sortable priority score.

    Categories provide the main ordering.
    Individual metrics act as tie-breakers.
    """

    category_weights = {
        "STRONG_ADD": 100,
        "ADD": 75,
        "STASH": 65,
        "STREAMER": 50,
        "HOLD": 20,
        "INCOMPLETE_DATA": 10,
        "AVOID": 0,
        "INVALID": -1000,
    }

    score = category_weights.get(
        result.get(
            "recommendation"
        ),
        0,
    )

    weekly_delta = (
        result.get(
            "weekly_lineup_delta"
        )
        or 0.0
    )

    market_delta = (
        result.get(
            "market_value_delta"
        )
        or 0.0
    )

    ros_delta = (
        result.get(
            "ros_delta"
        )
        or 0.0
    )

    balance_delta = (
        result.get(
            "roster_balance_delta"
        )
        or 0.0
    )

    score += (
        weekly_delta
        * 0.5
    )

    score += (
        ros_delta
        * 0.15
    )

    score += (
        market_delta
        / 500.0
    )

    # Reward better roster construction and penalize
    # moves that make the position mix worse.
    score += (
        balance_delta
    )

    return score

def rank_transactions(
    results,
):
    """
    Return best waiver moves first.
    """

    return sorted(
        results,
        key=transaction_priority,
        reverse=True,
    )