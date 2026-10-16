# ==================================================
# Decision engine
#
# IMPORTANT ARCHITECTURE:
#
# This file does NOT calculate a new fantasy score.
#
# player_value.py is the single source of truth for:
#
#   weekly_score
#   ros_score
#   availability
#
# This module interprets those scores and returns a
# structured decision that the LLM can explain.
# ==================================================


def _safe_float(
    value,
    default=None,
):
    """
    Safely convert a value to float.
    """

    if value is None:
        return default

    try:
        return float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return default


# ==================================================
# Usage helpers
# ==================================================

def _get_usage_value(
    player,
):
    """
    Return a human-readable recent opportunity metric.

    This is ONLY explanatory.

    It does not independently determine the winner,
    because opportunity is already incorporated into
    weekly_score inside player_value.py.
    """

    usage = (
        player.get(
            "recent_usage"
        )
        or {}
    )

    position = player.get(
        "position"
    )

    if position == "RB":

        carries = _safe_float(
            usage.get(
                "carries_per_game"
            ),
            0.0,
        )

        targets = _safe_float(
            usage.get(
                "targets_per_game"
            ),
            0.0,
        )

        return (
            carries
            + targets
        )

    if position in (
        "WR",
        "TE",
    ):

        return _safe_float(
            usage.get(
                "targets_per_game"
            ),
            0.0,
        )

    return None


# ==================================================
# Player snapshot
# ==================================================

def build_player_snapshot(
    player,
):
    """
    Build the structured evidence used to explain a
    start/sit decision.

    This function does NOT create a new player value.
    """

    availability = (
        player.get(
            "availability"
        )
        or {}
    )

    usage = (
        player.get(
            "recent_usage"
        )
        or {}
    )

    leaguelogs = (
        player
        .get(
            "signals",
            {}
        )
        .get(
            "leaguelogs",
            {}
        )
    )

    return {
        "name":
            player.get(
                "name"
            ),

        "position":
            player.get(
                "position"
            ),

        "team":
            player.get(
                "nfl_team"
            ),

        "opponent":
            player.get(
                "opponent"
            ),

        # ------------------------------------------
        # Central decision values
        # ------------------------------------------

        "weekly_score":
            player.get(
                "weekly_score"
            ),

        "ros_score":
            player.get(
                "ros_score"
            ),

        # ------------------------------------------
        # Forward-looking weekly information
        # ------------------------------------------

        "projection":
            player.get(
                "fp_ppr"
            ),

        "projection_source":
            player.get(
                "projection_source"
            ),

        "weekly_ecr":
            player.get(
                "ecr"
            ),

        "weekly_pos_rank":
            player.get(
                "pos_rank"
            ),

        # ------------------------------------------
        # Recent production / usage
        # ------------------------------------------

        "recent_ppr":
            usage.get(
                "ppr_points_per_game"
            ),

        "recent_opportunity":
            _get_usage_value(
                player
            ),

        "carries_per_game":
            usage.get(
                "carries_per_game"
            ),

        "targets_per_game":
            usage.get(
                "targets_per_game"
            ),

        # ------------------------------------------
        # Availability / injury
        # ------------------------------------------

        "can_play_this_week":
            availability.get(
                "can_play_this_week",
                True,
            ),

        "injury_status":
            availability.get(
                "injury_status"
            ),

        "risk_level":
            availability.get(
                "risk_level"
            ),

        "play_probability":
            availability.get(
                "play_probability"
            ),

        "workload_multiplier":
            availability.get(
                "workload_multiplier"
            ),

        "weekly_multiplier":
            availability.get(
                "weekly_multiplier"
            ),

        "practice_summary":
            availability.get(
                "practice_summary"
            ),

        "availability_reason":
            availability.get(
                "reason"
            ),

        # ------------------------------------------
        # Data quality
        # ------------------------------------------

        "weekly_data_complete":
            player.get(
                "weekly_data_complete"
            ),

        "weekly_missing_data":
            player.get(
                "weekly_missing_data",
                [],
            ),

        # ------------------------------------------
        # Long-term information
        # ------------------------------------------

        "ros_ecr":
            player.get(
                "ros_ecr"
            ),

        "ros_pos_rank":
            player.get(
                "ros_pos_rank"
            ),

        "leaguelogs_ros_rank":
            leaguelogs.get(
                "ros_pos_rank"
            ),
    }


# ==================================================
# Decision-strength helper
# ==================================================

def _decision_strength(
    winner_snapshot,
    loser_snapshot,
):
    """
    Describe how strongly the weekly_score favors one
    player.

    This is NOT a probability that the recommendation
    will be correct.
    """

    winner_score = _safe_float(
        winner_snapshot.get(
            "weekly_score"
        )
    )

    loser_score = _safe_float(
        loser_snapshot.get(
            "weekly_score"
        )
    )

    if (
        winner_score is None
        or loser_score is None
    ):
        return "LOW"

    # Hard availability difference is decisive.
    if (
        winner_snapshot.get(
            "can_play_this_week"
        )
        and not loser_snapshot.get(
            "can_play_this_week"
        )
    ):
        return "HIGH"

    denominator = max(
        abs(
            winner_score
        ),
        abs(
            loser_score
        ),
        1.0,
    )

    relative_margin = (
        abs(
            winner_score
            - loser_score
        )
        / denominator
    )

    # Missing data lowers confidence in close decisions.
    incomplete = (
        winner_snapshot.get(
            "weekly_data_complete"
        )
        is False
        or loser_snapshot.get(
            "weekly_data_complete"
        )
        is False
    )

    if relative_margin >= 0.20:

        if incomplete:
            return "MODERATE"

        return "HIGH"

    if relative_margin >= 0.08:

        if incomplete:
            return "LOW"

        return "MODERATE"

    return "LOW"


# ==================================================
# Main weekly comparison
# ==================================================

def compare_players(
    player_a,
    player_b,
):
    """
    Compare two players for THIS WEEK.

    The winner is determined using weekly_score from
    player_value.py.

    We do NOT independently combine projection, ECR,
    recent production, usage, or injury information
    here because those are already incorporated into
    weekly_score.

    Those component signals are returned only so the
    eventual LLM can explain WHY the decision was made.
    """

    snapshot_a = (
        build_player_snapshot(
            player_a
        )
    )

    snapshot_b = (
        build_player_snapshot(
            player_b
        )
    )

    name_a = (
        snapshot_a[
            "name"
        ]
    )

    name_b = (
        snapshot_b[
            "name"
        ]
    )

    can_play_a = (
        snapshot_a.get(
            "can_play_this_week",
            True,
        )
    )

    can_play_b = (
        snapshot_b.get(
            "can_play_this_week",
            True,
        )
    )

    score_a = _safe_float(
        snapshot_a.get(
            "weekly_score"
        )
    )

    score_b = _safe_float(
        snapshot_b.get(
            "weekly_score"
        )
    )

    # ==================================================
    # Neither player is available
    # ==================================================

    if (
        not can_play_a
        and not can_play_b
    ):

        return {
            "decision":
                "NEITHER_AVAILABLE",

            "recommended_player":
                None,

            "other_player":
                None,

            "decision_strength":
                "HIGH",

            # Backward-compatible field.
            # This is a heuristic strength indicator,
            # NOT a calibrated probability.
            "confidence":
                95.0,

            "confidence_is_probability":
                False,

            "score_margin":
                None,

            "reason":
                (
                    f"Neither {name_a} nor {name_b} "
                    f"is currently available to play."
                ),

            "players": {
                name_a:
                    snapshot_a,

                name_b:
                    snapshot_b,
            },
        }

    # ==================================================
    # Hard availability difference
    # ==================================================

    if (
        can_play_a
        and not can_play_b
    ):

        winner = snapshot_a
        loser = snapshot_b

    elif (
        can_play_b
        and not can_play_a
    ):

        winner = snapshot_b
        loser = snapshot_a

    # ==================================================
    # Both players available
    # ==================================================

    else:

        # ----------------------------------------------
        # No usable weekly scores
        # ----------------------------------------------

        if (
            score_a is None
            and score_b is None
        ):

            return {
                "decision":
                    "INSUFFICIENT_DATA",

                "recommended_player":
                    None,

                "other_player":
                    None,

                "decision_strength":
                    "LOW",

                "confidence":
                    50.0,

                "confidence_is_probability":
                    False,

                "score_margin":
                    None,

                "reason":
                    (
                        "Neither player has a usable "
                        "weekly decision score."
                    ),

                "players": {
                    name_a:
                        snapshot_a,

                    name_b:
                        snapshot_b,
                },
            }

        # ----------------------------------------------
        # Only one score is available
        # ----------------------------------------------

        if score_a is None:

            winner = snapshot_b
            loser = snapshot_a

        elif score_b is None:

            winner = snapshot_a
            loser = snapshot_b

        # ----------------------------------------------
        # Normal comparison
        # ----------------------------------------------

        elif score_a >= score_b:

            winner = snapshot_a
            loser = snapshot_b

        else:

            winner = snapshot_b
            loser = snapshot_a

    # ==================================================
    # Result
    # ==================================================

    winner_score = _safe_float(
        winner.get(
            "weekly_score"
        )
    )

    loser_score = _safe_float(
        loser.get(
            "weekly_score"
        )
    )

    if (
        winner_score is not None
        and loser_score is not None
    ):

        score_margin = (
            winner_score
            - loser_score
        )

    else:

        score_margin = None

    strength = (
        _decision_strength(
            winner,
            loser,
        )
    )

    # --------------------------------------------------
    # Backward-compatible numerical confidence.
    #
    # This is deliberately NOT called a probability.
    # --------------------------------------------------

    strength_confidence = {
        "LOW": 60.0,
        "MODERATE": 75.0,
        "HIGH": 90.0,
    }

    confidence = (
        strength_confidence[
            strength
        ]
    )

    # --------------------------------------------------
    # Concise deterministic reason
    # --------------------------------------------------

    if (
        winner.get(
            "can_play_this_week"
        )
        and not loser.get(
            "can_play_this_week"
        )
    ):

        reason = (
            f"{winner['name']} is available, while "
            f"{loser['name']} is currently unavailable."
        )

    elif score_margin is not None:

        reason = (
            f"{winner['name']} has the higher "
            f"weekly decision score "
            f"({winner_score:.1f} vs "
            f"{loser_score:.1f})."
        )

    else:

        reason = (
            f"{winner['name']} has the stronger "
            f"available weekly evaluation."
        )

    return {
        "decision":
            "START",

        "recommended_player":
            winner[
                "name"
            ],

        "other_player":
            loser[
                "name"
            ],

        "decision_strength":
            strength,

        "confidence":
            confidence,

        "confidence_is_probability":
            False,

        "score_margin":
            (
                round(
                    score_margin,
                    1,
                )
                if score_margin
                is not None
                else None
            ),

        "reason":
            reason,

        "players": {
            name_a:
                snapshot_a,

            name_b:
                snapshot_b,
        },
    }


# ==================================================
# Rest-of-season comparison
# ==================================================

def compare_players_ros(
    player_a,
    player_b,
):
    """
    Compare two players for REST-OF-SEASON value.

    Useful later for:
        trades
        add/drop decisions
        stash decisions

    Uses ros_score from player_value.py rather than
    inventing another long-term scoring formula.
    """

    snapshot_a = (
        build_player_snapshot(
            player_a
        )
    )

    snapshot_b = (
        build_player_snapshot(
            player_b
        )
    )

    name_a = (
        snapshot_a[
            "name"
        ]
    )

    name_b = (
        snapshot_b[
            "name"
        ]
    )

    score_a = _safe_float(
        snapshot_a.get(
            "ros_score"
        )
    )

    score_b = _safe_float(
        snapshot_b.get(
            "ros_score"
        )
    )

    if (
        score_a is None
        and score_b is None
    ):

        return {
            "decision":
                "INSUFFICIENT_DATA",

            "recommended_player":
                None,

            "other_player":
                None,

            "reason":
                (
                    "Neither player has a usable "
                    "rest-of-season score."
                ),

            "players": {
                name_a:
                    snapshot_a,

                name_b:
                    snapshot_b,
            },
        }

    if score_a is None:

        winner = snapshot_b
        loser = snapshot_a

    elif score_b is None:

        winner = snapshot_a
        loser = snapshot_b

    elif score_a >= score_b:

        winner = snapshot_a
        loser = snapshot_b

    else:

        winner = snapshot_b
        loser = snapshot_a

    winner_score = _safe_float(
        winner.get(
            "ros_score"
        )
    )

    loser_score = _safe_float(
        loser.get(
            "ros_score"
        )
    )

    score_margin = None

    if (
        winner_score is not None
        and loser_score is not None
    ):

        score_margin = (
            winner_score
            - loser_score
        )

    return {
        "decision":
            "ROS",

        "recommended_player":
            winner[
                "name"
            ],

        "other_player":
            loser[
                "name"
            ],

        "score_margin":
            (
                round(
                    score_margin,
                    1,
                )
                if score_margin
                is not None
                else None
            ),

        "reason":
            (
                f"{winner['name']} has the higher "
                f"rest-of-season value "
                f"({winner_score:.1f} vs "
                f"{loser_score:.1f})."
                if (
                    winner_score is not None
                    and loser_score is not None
                )
                else (
                    f"{winner['name']} has the stronger "
                    f"available rest-of-season data."
                )
            ),

        "players": {
            name_a:
                snapshot_a,

            name_b:
                snapshot_b,
        },
    }