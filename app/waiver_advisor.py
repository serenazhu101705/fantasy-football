from copy import deepcopy

from lineup_optimizer import optimize_lineup


def _leaguelogs(player):
    return (
        player
        .get("signals", {})
        .get("leaguelogs", {})
    )


def _market_value(player):
    value = _leaguelogs(
        player
    ).get(
        "market_value"
    )

    return (
        float(value)
        if value is not None
        else None
    )


def _market_trend(player):
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
        return a_id == b_id

    return (
        a.get("name")
        == b.get("name")
        and
        a.get("position")
        == b.get("position")
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
        deepcopy(player)
        for player in roster
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


def evaluate_transaction(
    roster,
    add_player,
    drop_player,
):
    """
    Evaluate one possible waiver transaction.

    We currently look at:

        1. Weekly lineup impact
        2. Projected-points lineup impact
        3. LeagueLogs market value change
        4. ROS score change when comparing the
           same position
        5. Data quality
        6. Injury/stash context

    No LLM is involved here.
    """

    current = optimize_lineup(
        roster
    )

    new_roster = (
        simulate_transaction(
            roster,
            add_player,
            drop_player,
        )
    )

    after = optimize_lineup(
        new_roster
    )

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
    # This is useful across positions, unlike our
    # position-relative ROS score.
    # --------------------------------------------------

    add_market = (
        _market_value(
            add_player
        )
    )

    drop_market = (
        _market_value(
            drop_player
        )
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
    # Our ROS score
    #
    # Only directly compare this when players are the
    # same position because the score is currently
    # position-relative.
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

    result = {
        "add": add_player,
        "drop": drop_player,

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


def evaluate_all_transactions(
    roster,
    waiver_candidates,
):
    """
    Evaluate every ADD/DROP combination.

    Later we will add smarter filtering so obvious
    non-droppable stars do not need to be evaluated.
    """

    results = []

    for add_player in waiver_candidates:

        for drop_player in roster:

            # Do not compare K/DST against offensive
            # players yet. Their valuation model is
            # separate and unfinished.
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

            results.append(
                result
            )

    return results

def classify_transaction(
    result,
):
    """
    Turn transaction metrics into a human-readable
    recommendation category.

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
                    "Interesting candidate, but both "
                    "weekly and rest-of-season data "
                    "are incomplete."
                ),
        }

    # --------------------------------------------------
    # Injured stash
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

        if (
            strong_ros_gain
            or strong_market_gain
        ):
            return {
                "category":
                    "STASH",

                "reason":
                    (
                        "Provides little or no immediate "
                        "value, but offers meaningful "
                        "long-term upside."
                    ),
            }

        return {
            "category":
                "HOLD",

            "reason":
                (
                    "Injured stash does not provide "
                    "enough long-term improvement to "
                    "justify the roster move."
                ),
        }

    # --------------------------------------------------
    # Strong all-around upgrade
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
    ):
        return {
            "category":
                "STRONG_ADD",

            "reason":
                (
                    "Improves the starting lineup "
                    "without sacrificing long-term "
                    "roster value."
                ),
        }

    # --------------------------------------------------
    # Weekly streamer
    # --------------------------------------------------

    long_term_downgrade = (
        (
            ros_delta is not None
            and ros_delta < -10
        )
        or
        (
            market_delta is not None
            and market_delta < -500
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
                    "the transaction sacrifices too much "
                    "long-term value."
                ),
        }

    # --------------------------------------------------
    # Long-term add
    # --------------------------------------------------

    strong_ros_upgrade = (
        ros_delta is not None
        and ros_delta >= 10
    )

    strong_market_upgrade = (
        market_delta is not None
        and market_delta >= 500
    )

    if (
        strong_ros_upgrade
        or strong_market_upgrade
    ):
        return {
            "category":
                "ADD",

            "reason":
                (
                    "Does not dramatically improve this "
                    "week, but improves the roster's "
                    "long-term value."
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
                    "Does not improve the weekly lineup "
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

