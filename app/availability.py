def get_availability(player, current_week=None):
    """
    Convert injury information into separate weekly and
    rest-of-season availability multipliers.

    Important:
    Being on IR does NOT automatically mean a player's
    rest-of-season value is zero.

    Later, we will replace/augment this with real injury
    news and expected-return information.
    """
    is_on_bye = player.get(
        "is_on_bye",
        False,
    )

    status = (
        player.get("injury_status")
        or "ACTIVE"
    ).upper()

    # These fields are optional for now.
    # Later an injury/news system can populate them.
    season_ending = player.get(
        "season_ending",
        False,
    )

    expected_return_week = player.get(
        "expected_return_week"
    )

    # --------------------------------------------------
    # Season-ending injury
    # --------------------------------------------------

    if season_ending:
        return {
            "can_play_this_week": False,
            "weekly_multiplier": 0.0,
            "ros_multiplier": 0.0,
            "reason": "Season-ending injury",
        }
    
    # --------------------------------------------------
    # Bye week
    # --------------------------------------------------

    if is_on_bye:
        return {
            "can_play_this_week": False,
            "weekly_multiplier": 0.0,
            "ros_multiplier": 1.0,
            "reason": "Bye week",
        }
    
    # --------------------------------------------------
    # Active / normal
    # --------------------------------------------------

    if status in (
        "ACTIVE",
        "NORMAL",
        "HEALTHY",
    ):
        return {
            "can_play_this_week": True,
            "weekly_multiplier": 1.0,
            "ros_multiplier": 1.0,
            "reason": "Active",
        }

    # --------------------------------------------------
    # Questionable
    #
    # Still startable, but there is some short-term risk.
    # Long-term value is essentially unchanged.
    # --------------------------------------------------

    if status == "QUESTIONABLE":
        return {
            "can_play_this_week": True,
            "weekly_multiplier": 0.90,
            "ros_multiplier": 1.0,
            "reason": "Questionable",
        }

    # --------------------------------------------------
    # Doubtful
    # --------------------------------------------------

    if status == "DOUBTFUL":
        return {
            "can_play_this_week": False,
            "weekly_multiplier": 0.20,
            "ros_multiplier": 0.95,
            "reason": "Doubtful",
        }

    # --------------------------------------------------
    # Out
    #
    # Out this week does NOT mean worthless ROS.
    # --------------------------------------------------

    if status == "OUT":
        return {
            "can_play_this_week": False,
            "weekly_multiplier": 0.0,
            "ros_multiplier": 0.95,
            "reason": "Out this week",
        }

    # --------------------------------------------------
    # Injured reserve
    # --------------------------------------------------

    if status == "INJURY_RESERVE":

        # If we eventually know the return week, use it.
        if (
            current_week is not None
            and expected_return_week is not None
        ):

            weeks_away = max(
                expected_return_week
                - current_week,
                0,
            )

            # Closer return = more ROS value retained.
            if weeks_away <= 1:
                ros_multiplier = 0.95

            elif weeks_away <= 3:
                ros_multiplier = 0.85

            elif weeks_away <= 5:
                ros_multiplier = 0.70

            else:
                ros_multiplier = 0.50

        else:
            # We know they're on IR, but not when they
            # return. Do NOT make ROS value zero.
            ros_multiplier = 0.75

        return {
            "can_play_this_week": False,
            "weekly_multiplier": 0.0,
            "ros_multiplier": ros_multiplier,
            "reason": "Injured reserve",
        }

    # --------------------------------------------------
    # Unknown status
    #
    # Don't punish a player because ESPN used a status
    # string we haven't handled yet.
    # --------------------------------------------------

    return {
        "can_play_this_week": True,
        "weekly_multiplier": 1.0,
        "ros_multiplier": 1.0,
        "reason": f"Unknown status: {status}",
    }