from availability import get_availability


def _safe_float(
    value,
    default=None,
):
    try:
        if value is None:
            return default

        return float(value)

    except (
        TypeError,
        ValueError,
    ):
        return default


def _rank_score(rank):
    """
    Convert positional rank into a 0-1 score.

    Lower rank is better.
    """

    rank = _safe_float(rank)

    if (
        rank is None
        or rank <= 0
    ):
        return None

    return 1.0 / (
        1.0
        + 0.1
        * (rank - 1)
    )


def _normalize_points(points):
    """
    Convert fantasy points into a rough 0-1 value.
    """

    points = _safe_float(points)

    if points is None:
        return None

    return min(
        max(
            points / 30.0,
            0.0,
        ),
        1.0,
    )


def _normalize_opportunity(
    player,
):
    """
    Estimate recent opportunity.

    RB:
        carries + targets

    WR / TE:
        targets

    QB:
        not modeled yet
    """

    usage = (
        player.get("recent_usage")
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

        return min(
            (
                carries
                + targets
            )
            / 25.0,
            1.0,
        )

    if position in (
        "WR",
        "TE",
    ):

        targets = _safe_float(
            usage.get(
                "targets_per_game"
            ),
            0.0,
        )

        return min(
            targets / 10.0,
            1.0,
        )

    # QB opportunity model will come later.
    return None

def _get_leaguelogs_signal(
    player,
):
    """
    Return the LeagueLogs signal dictionary attached
    during player enrichment.
    """

    return (
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

def get_weekly_data_quality(
    player,
    availability,
):
    """
    Determine whether we have enough information to
    trust the weekly score.

    Also return exactly which inputs are missing.
    """

    position = player.get(
        "position"
    )

    missing = []

    # --------------------------------------------------
    # K / DST
    # --------------------------------------------------

    if position in (
        "K",
        "D/ST",
    ):

        if player.get(
            "fp_ppr"
        ) is None:

            missing.append(
                "weekly_projection"
            )

        return {
            "complete": (
                len(missing) == 0
            ),
            "missing": missing,
        }

    # --------------------------------------------------
    # Player definitely cannot play this week
    #
    # If availability tells us the player has a 0%
    # chance of contributing this week, the weekly
    # decision itself is sufficiently known even if
    # FantasyPros does not publish a projection.
    # --------------------------------------------------

    if (
        availability.get(
            "weekly_multiplier"
        )
        == 0.0
    ):

        return {
            "complete": True,
            "missing": [],
        }

    # --------------------------------------------------
    # Offensive players
    # --------------------------------------------------

    if player.get(
        "fp_ppr"
    ) is None:

        missing.append(
            "weekly_projection"
        )

    if player.get(
        "ecr"
    ) is None:

        missing.append(
            "weekly_ecr"
        )

    usage = (
        player.get(
            "recent_usage"
        )
        or {}
    )

    if usage.get(
        "ppr_points_per_game"
    ) is None:

        missing.append(
            "recent_ppr"
        )

    # Opportunity is currently part of the RB/WR/TE
    # model, but not the QB model.
    if position == "RB":

        if (
            usage.get(
                "carries_per_game"
            ) is None
            or usage.get(
                "targets_per_game"
            ) is None
        ):

            missing.append(
                "recent_opportunity"
            )

    elif position in (
        "WR",
        "TE",
    ):

        if usage.get(
            "targets_per_game"
        ) is None:

            missing.append(
                "recent_opportunity"
            )

    return {
        "complete": (
            len(missing) == 0
        ),
        "missing": missing,
    }


def get_ros_data_quality(
    player,
    availability,
):
    """
    Determine whether we have enough information to
    trust the rest-of-season score.
    """

    position = player.get(
        "position"
    )

    # We do not currently calculate ROS values for
    # K or D/ST.
    if position not in (
        "QB",
        "RB",
        "WR",
        "TE",
    ):

        return {
            "complete": None,
            "missing": [],
        }

    # If we eventually know a player's injury is
    # season-ending, a ROS value of zero is reliable.
    if (
        availability.get(
            "ros_multiplier"
        )
        == 0.0
    ):

        return {
            "complete": True,
            "missing": [],
        }

    missing = []

    # FantasyPros ROS ECR is the most important
    # long-term input.
    if player.get(
        "ros_ecr"
    ) is None:

        missing.append(
            "ros_ecr"
        )
    
    leaguelogs = (
        _get_leaguelogs_signal(
            player
        )
    )

    if leaguelogs.get(
        "ros_pos_rank"
    ) is None:

        missing.append(
            "leaguelogs_ros"
        )


    usage = (
        player.get(
            "recent_usage"
        )
        or {}
    )

    if usage.get(
        "ppr_points_per_game"
    ) is None:

        missing.append(
            "recent_ppr"
        )

    if position == "RB":

        if (
            usage.get(
                "carries_per_game"
            ) is None
            or usage.get(
                "targets_per_game"
            ) is None
        ):

            missing.append(
                "recent_opportunity"
            )

    elif position in (
        "WR",
        "TE",
    ):

        if usage.get(
            "targets_per_game"
        ) is None:

            missing.append(
                "recent_opportunity"
            )

    return {
        "complete": (
            len(missing) == 0
        ),
        "missing": missing,
    }


def calculate_base_weekly_score(
    player,
):
    """
    Calculate weekly score before injury availability.

    Weights:

        50% weekly projection
        20% weekly ECR
        20% recent PPR
        10% recent opportunity

    Missing inputs contribute zero.

    We do NOT redistribute missing weight.
    """

    position = player.get(
        "position"
    )

    # ----------------------------------------------
    # K / DST
    # ----------------------------------------------

    if position in (
        "K",
        "D/ST",
    ):

        return _safe_float(
            player.get(
                "fp_ppr"
            )
        )

    # ----------------------------------------------
    # Offensive player
    # ----------------------------------------------

    projection = _normalize_points(
        player.get(
            "fp_ppr"
        )
    )

    ecr = _rank_score(
        player.get(
            "ecr"
        )
    )

    usage = (
        player.get(
            "recent_usage"
        )
        or {}
    )

    recent = _normalize_points(
        usage.get(
            "ppr_points_per_game"
        )
    )

    opportunity = (
        _normalize_opportunity(
            player
        )
    )

    score = 0.0

    if projection is not None:
        score += (
            projection
            * 0.50
        )

    if ecr is not None:
        score += (
            ecr
            * 0.20
        )

    if recent is not None:
        score += (
            recent
            * 0.20
        )

    if opportunity is not None:
        score += (
            opportunity
            * 0.10
        )

    return score * 100.0


def calculate_base_ros_score(
    player,
):
    """
    Calculate rest-of-season value before injury
    availability.

    Current ensemble:

        50% FantasyPros ROS ECR
        20% LeagueLogs ROS positional rank
        20% recent PPR production
        10% recent opportunity

    Missing inputs contribute zero.

    We deliberately DO NOT redistribute missing weight.
    """

    position = player.get(
        "position"
    )

    if position not in (
        "QB",
        "RB",
        "WR",
        "TE",
    ):
        return None

    # --------------------------------------------------
    # FantasyPros ROS
    # --------------------------------------------------

    fantasypros_ros = _rank_score(
        player.get(
            "ros_ecr"
        )
    )

    # --------------------------------------------------
    # LeagueLogs ROS
    # --------------------------------------------------

    leaguelogs = (
        _get_leaguelogs_signal(
            player
        )
    )

    leaguelogs_ros = _rank_score(
        leaguelogs.get(
            "ros_pos_rank"
        )
    )

    # --------------------------------------------------
    # Recent actual production
    # --------------------------------------------------

    usage = (
        player.get(
            "recent_usage"
        )
        or {}
    )

    recent = _normalize_points(
        usage.get(
            "ppr_points_per_game"
        )
    )

    # --------------------------------------------------
    # Recent opportunity
    # --------------------------------------------------

    opportunity = (
        _normalize_opportunity(
            player
        )
    )

    # --------------------------------------------------
    # Ensemble
    # --------------------------------------------------

    score = 0.0

    if fantasypros_ros is not None:
        score += (
            fantasypros_ros
            * 0.50
        )

    if leaguelogs_ros is not None:
        score += (
            leaguelogs_ros
            * 0.20
        )

    if recent is not None:
        score += (
            recent
            * 0.20
        )

    if opportunity is not None:
        score += (
            opportunity
            * 0.10
        )

    return score * 100.0

def calculate_weekly_score(
    player,
    current_week=None,
):
    """
    Apply weekly availability to base weekly value.
    """

    base_score = (
        calculate_base_weekly_score(
            player
        )
    )

    if base_score is None:
        return None

    availability = (
        get_availability(
            player,
            current_week=current_week,
        )
    )

    final_score = (
        base_score
        * availability[
            "weekly_multiplier"
        ]
    )

    return round(
        final_score,
        1,
    )


def calculate_ros_score(
    player,
    current_week=None,
):
    """
    Apply long-term availability to ROS value.
    """

    base_score = (
        calculate_base_ros_score(
            player
        )
    )

    if base_score is None:
        return None

    availability = (
        get_availability(
            player,
            current_week=current_week,
        )
    )

    final_score = (
        base_score
        * availability[
            "ros_multiplier"
        ]
    )

    return round(
        final_score,
        1,
    )


def add_player_values(
    players,
    current_week=None,
):
    """
    Add:

        weekly_score
        ros_score

        weekly_data_complete
        ros_data_complete

        weekly_missing_data
        ros_missing_data

        availability

    to every player.
    """

    for player in players:

        availability = (
            get_availability(
                player,
                current_week=current_week,
            )
        )

        player[
            "availability"
        ] = availability

        player[
            "weekly_score"
        ] = calculate_weekly_score(
            player,
            current_week=current_week,
        )

        player[
            "ros_score"
        ] = calculate_ros_score(
            player,
            current_week=current_week,
        )

        weekly_quality = (
            get_weekly_data_quality(
                player,
                availability,
            )
        )

        ros_quality = (
            get_ros_data_quality(
                player,
                availability,
            )
        )

        player[
            "weekly_data_complete"
        ] = weekly_quality[
            "complete"
        ]

        player[
            "weekly_missing_data"
        ] = weekly_quality[
            "missing"
        ]

        player[
            "ros_data_complete"
        ] = ros_quality[
            "complete"
        ]

        player[
            "ros_missing_data"
        ] = ros_quality[
            "missing"
        ]

    return players