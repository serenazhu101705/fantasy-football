def normalize_status(value):
    """
    Normalize ESPN / FantasyPros injury designations
    into a consistent vocabulary.
    """

    if value is None:
        return ""

    status = str(
        value
    ).strip().upper()

    aliases = {
        "Q": "QUESTIONABLE",
        "QUESTIONABLE": "QUESTIONABLE",

        "D": "DOUBTFUL",
        "DOUBTFUL": "DOUBTFUL",

        "O": "OUT",
        "OUT": "OUT",

        "IR": "INJURY_RESERVE",
        "INJURY RESERVE": "INJURY_RESERVE",
        "INJURY_RESERVE": "INJURY_RESERVE",

        "ACTIVE": "ACTIVE",
        "NORMAL": "ACTIVE",
        "HEALTHY": "ACTIVE",

        "": "",
    }

    return aliases.get(
        status,
        status,
    )


def normalize_practice(value):
    """
    Normalize practice-report values.

    Expected outputs:

        DNP
        LIMITED
        FULL
        None
    """

    if value is None:
        return None

    text = str(
        value
    ).strip().upper()

    if (
        text == ""
        or text == "--"
        or text == "NONE"
    ):
        return None

    if text in (
        "DNP",
        "DID NOT PARTICIPATE",
        "DID NOT PRACTICE",
    ):
        return "DNP"

    if text in (
        "LIMIT",
        "LIMITED",
        "LP",
    ):
        return "LIMITED"

    if text in (
        "FULL",
        "FP",
    ):
        return "FULL"

    return text


def get_latest_submitted_practice(
    injury_report,
):
    """
    Return the latest practice report that was
    actually submitted.

    Crucially:

        practice_3 = None
        practice_3_submitted = False

    means Friday's report is not available yet.

    It does NOT mean the player did not practice.
    """

    if not injury_report:
        return {
            "day": None,
            "status": None,
        }

    practices = [
        (
            1,
            injury_report.get(
                "practice_1"
            ),
            injury_report.get(
                "practice_1_submitted",
                False,
            ),
        ),
        (
            2,
            injury_report.get(
                "practice_2"
            ),
            injury_report.get(
                "practice_2_submitted",
                False,
            ),
        ),
        (
            3,
            injury_report.get(
                "practice_3"
            ),
            injury_report.get(
                "practice_3_submitted",
                False,
            ),
        ),
    ]

    latest = {
        "day": None,
        "status": None,
    }

    for (
        day,
        value,
        submitted,
    ) in practices:

        if not submitted:
            continue

        latest = {
            "day": day,
            "status":
                normalize_practice(
                    value
                ),
        }

    return latest


def get_practice_history(
    injury_report,
):
    """
    Return submitted practice statuses in order.

    Example:

        ["LIMITED", "LIMITED"]

    If Friday's report is still pending, it is not
    represented as a DNP.
    """

    if not injury_report:
        return []

    history = []

    for day in (
        1,
        2,
        3,
    ):

        submitted = injury_report.get(
            f"practice_{day}_submitted",
            False,
        )

        if not submitted:
            continue

        status = normalize_practice(
            injury_report.get(
                f"practice_{day}"
            )
        )

        if status is not None:
            history.append(
                status
            )

    return history


def format_practice_summary(
    injury_report,
):
    """
    Human-readable practice progression.

    Example:

        LIMITED -> LIMITED -> pending
    """

    if not injury_report:
        return "No practice report"

    parts = []

    for day in (
        1,
        2,
        3,
    ):

        submitted = injury_report.get(
            f"practice_{day}_submitted",
            False,
        )

        if not submitted:

            parts.append(
                "pending"
            )

            continue

        practice = normalize_practice(
            injury_report.get(
                f"practice_{day}"
            )
        )

        if practice is None:

            parts.append(
                "--"
            )

        else:

            parts.append(
                practice
            )

    return " -> ".join(
        parts
    )


def get_probability_multiplier(
    probability,
):
    """
    Convert FantasyPros probability of playing into
    a MODEST risk adjustment.

    We deliberately do not do:

        projection * probability

    because forward-looking projections may already
    incorporate injury information.
    """

    if probability is None:
        return (
            1.0,
            "LOW",
        )

    try:
        probability = float(
            probability
        )

    except (
        TypeError,
        ValueError,
    ):
        return (
            1.0,
            "LOW",
        )

    if probability > 1.0:
        probability /= 100.0

    probability = max(
        0.0,
        min(
            1.0,
            probability,
        ),
    )

    if probability >= 0.95:
        return (
            1.0,
            "LOW",
        )

    if probability >= 0.85:
        return (
            0.97,
            "MODERATE",
        )

    if probability >= 0.70:
        return (
            0.93,
            "MODERATE",
        )

    if probability >= 0.50:
        return (
            0.88,
            "HIGH",
        )

    return (
        0.80,
        "HIGH",
    )


def get_practice_multiplier(
    injury_report,
):
    """
    Convert practice trajectory into a modest
    workload/risk adjustment.

    Later-week practice matters more than early-week
    practice.

    NIR = non-injury-related. We generally should not
    punish a player for an NIR rest day.
    """

    if not injury_report:
        return (
            1.0,
            "LOW",
        )

    practice_injury_type = str(
        injury_report.get(
            "practice_injury_type"
        )
        or ""
    ).strip().upper()

    # --------------------------------------------------
    # Non-injury-related rest should usually not lower
    # fantasy expectations.
    # --------------------------------------------------

    if practice_injury_type in (
        "NIR",
        "NOT INJURY RELATED",
        "NOT INJURY-RELATED",
    ):

        return (
            1.0,
            "LOW",
        )

    latest = (
        get_latest_submitted_practice(
            injury_report
        )
    )

    day = latest[
        "day"
    ]

    status = latest[
        "status"
    ]

    history = get_practice_history(
        injury_report
    )

    if (
        day is None
        or status is None
    ):

        return (
            1.0,
            "LOW",
        )

    # --------------------------------------------------
    # Friday / final practice report
    # --------------------------------------------------

    if day == 3:

        if status == "FULL":

            return (
                0.99,
                "LOW",
            )

        if status == "LIMITED":

            return (
                0.92,
                "MODERATE",
            )

        if status == "DNP":

            return (
                0.75,
                "HIGH",
            )

    # --------------------------------------------------
    # Thursday report, Friday still pending
    # --------------------------------------------------

    if day == 2:

        if status == "FULL":

            return (
                1.0,
                "LOW",
            )

        if status == "LIMITED":

            # LIMITED -> LIMITED is more concerning
            # than DNP -> LIMITED.
            if (
                len(history) >= 2
                and history[-2]
                == "LIMITED"
            ):

                return (
                    0.94,
                    "MODERATE",
                )

            return (
                0.96,
                "MODERATE",
            )

        if status == "DNP":

            # Consecutive DNPs are more concerning.
            if (
                len(history) >= 2
                and history[-2]
                == "DNP"
            ):

                return (
                    0.84,
                    "HIGH",
                )

            return (
                0.88,
                "HIGH",
            )

    # --------------------------------------------------
    # Wednesday only
    #
    # Early-week absences are less predictive.
    # --------------------------------------------------

    if day == 1:

        if status == "FULL":

            return (
                1.0,
                "LOW",
            )

        if status == "LIMITED":

            return (
                0.98,
                "LOW",
            )

        if status == "DNP":

            return (
                0.94,
                "MODERATE",
            )

    return (
        1.0,
        "LOW",
    )


def get_status_multiplier(
    status,
):
    """
    Game-status adjustment.

    Questionable alone receives only a small penalty.
    The practice/probability signals determine whether
    the concern is actually meaningful.
    """

    if status in (
        "",
        "ACTIVE",
    ):

        return (
            1.0,
            "LOW",
        )

    if status == "QUESTIONABLE":

        return (
            0.97,
            "MODERATE",
        )

    if status == "DOUBTFUL":

        return (
            0.20,
            "HIGH",
        )

    if status in (
        "OUT",
        "INJURY_RESERVE",
    ):

        return (
            0.0,
            "OUT",
        )

    return (
        1.0,
        "LOW",
    )


def combine_risk_levels(
    *levels,
):
    """
    Return the highest risk level among the supplied
    signals.
    """

    order = {
        "LOW": 0,
        "MODERATE": 1,
        "HIGH": 2,
        "OUT": 3,
    }

    valid = [
        level
        for level in levels
        if level in order
    ]

    if not valid:
        return "LOW"

    return max(
        valid,
        key=lambda level:
            order[
                level
            ],
    )


def build_injury_reason(
    status,
    injury_report,
    probability,
    practice_summary,
    risk_level,
):
    """
    Build a concise explanation that can later be
    surfaced by the chatbot.
    """

    parts = []

    injury_type = None

    if injury_report:

        injury_type = (
            injury_report.get(
                "injury_type"
            )
            or injury_report.get(
                "practice_injury_type"
            )
        )

    if status not in (
        "",
        "ACTIVE",
    ):

        if injury_type:

            parts.append(
                f"{status.title()} "
                f"with {str(injury_type).lower()} injury"
            )

        else:

            parts.append(
                status.title()
            )

    elif injury_type:

        injury_text = str(
            injury_type
        ).strip()

        if (
            injury_text
            and injury_text.upper()
            != "NIR"
        ):

            parts.append(
                f"{injury_text.lower()} injury"
            )

    if (
        practice_summary
        != "No practice report"
    ):

        parts.append(
            f"practice: "
            f"{practice_summary}"
        )

    if probability is not None:

        parts.append(
            f"play probability "
            f"{probability:.0%}"
        )

    if not parts:

        return "Active"

    parts.append(
        f"{risk_level.lower()} injury risk"
    )

    return "; ".join(
        parts
    )


def get_ir_ros_multiplier(
    current_week,
    expected_return_week,
):
    """
    Preserve long-term value for IR players based on
    estimated return date when available.
    """

    if (
        current_week is not None
        and expected_return_week is not None
    ):

        weeks_away = max(
            expected_return_week
            - current_week,
            0,
        )

        if weeks_away <= 1:
            return 0.95

        if weeks_away <= 3:
            return 0.85

        if weeks_away <= 5:
            return 0.70

        return 0.50

    return 0.75


def get_availability(
    player,
    current_week=None,
):
    """
    Convert ESPN + FantasyPros injury/practice data
    into weekly and rest-of-season availability.

    Returned fields remain backward compatible with
    the old availability.py while adding:

        risk_level
        play_probability
        workload_multiplier
        practice_summary
        injury_status

    Important design decision:

    We do NOT multiply projection directly by
    probability_of_playing.

    FantasyPros / ESPN forward projections may already
    incorporate injury information, so doing that can
    double-count injury risk.

    Instead, game status, practice progression, and
    play probability each produce a modest risk
    adjustment. We use the most concerning adjustment
    instead of multiplying correlated penalties.
    """

    # ==================================================
    # Existing contextual fields
    # ==================================================

    is_on_bye = player.get(
        "is_on_bye",
        False,
    )

    season_ending = player.get(
        "season_ending",
        False,
    )

    expected_return_week = player.get(
        "expected_return_week"
    )

    injury_report = player.get(
        "injury_report"
    ) or {}

    # ==================================================
    # Determine injury status
    #
    # IMPORTANT:
    #
    # Separate HARD weekly eligibility from SOFT
    # injury-risk information.
    #
    # ESPN/FantasyPros OUT or IR means the player cannot
    # currently play, even if practice reports exist.
    #
    # For players who are eligible, FantasyPros status,
    # practice progression, and play probability determine
    # workload / injury risk.
    # ==================================================

    fp_status = normalize_status(
        injury_report.get(
            "status"
        )
    )

    if not fp_status:

        fp_status = normalize_status(
            injury_report.get(
                "status_short"
            )
        )

    espn_status = normalize_status(
        player.get(
            "injury_status"
        )
        or "ACTIVE"
    )

    # --------------------------------------------------
    # Hard weekly eligibility status
    # --------------------------------------------------

    hard_unavailable_statuses = {
        "OUT",
        "INJURY_RESERVE",
    }

    hard_status = None

    # ESPN is our direct current player-status source.
    if espn_status in hard_unavailable_statuses:

        hard_status = espn_status

    # FantasyPros can independently identify a player
    # as definitely unavailable.
    elif fp_status in hard_unavailable_statuses:

        hard_status = fp_status

    # --------------------------------------------------
    # Soft injury status
    #
    # FantasyPros is preferred here because its status is
    # accompanied by practice/probability information.
    # --------------------------------------------------

    soft_status = (
        fp_status
        or espn_status
        or "ACTIVE"
    )

    # The effective status used by the weekly eligibility
    # logic must respect hard unavailability.
    status = (
        hard_status
        or soft_status
    )
    # ==================================================
    # Probability
    # ==================================================

    probability = injury_report.get(
        "probability_of_playing"
    )

    if probability is not None:

        try:

            probability = float(
                probability
            )

            if probability > 1.0:
                probability /= 100.0

            probability = max(
                0.0,
                min(
                    1.0,
                    probability,
                ),
            )

        except (
            TypeError,
            ValueError,
        ):

            probability = None

    # ==================================================
    # Practice
    # ==================================================

    practice_summary = (
        format_practice_summary(
            injury_report
        )
    )

    # ==================================================
    # Season-ending injury
    # ==================================================

    if season_ending:

        return {
            "can_play_this_week": False,

            "weekly_multiplier": 0.0,

            "workload_multiplier": 0.0,

            "ros_multiplier": 0.0,

            "risk_level": "OUT",

            "play_probability": 0.0,

            "practice_summary":
                practice_summary,

            "injury_status":
                status,

            "reason":
                "Season-ending injury",
                
            "espn_injury_status":
                espn_status,

            "fantasypros_injury_status":
                fp_status,
        }

    # ==================================================
    # Bye
    # ==================================================

    if is_on_bye:

        return {
            "can_play_this_week": False,

            "weekly_multiplier": 0.0,

            "workload_multiplier": 0.0,

            "ros_multiplier": 1.0,

            "risk_level": "OUT",

            "play_probability":
                probability,

            "practice_summary":
                practice_summary,

            "injury_status":
                status,

            "reason":
                "Bye week",
        }

    # ==================================================
    # Injured reserve
    # ==================================================

    if status == "INJURY_RESERVE":

        ros_multiplier = (
            get_ir_ros_multiplier(
                current_week,
                expected_return_week,
            )
        )

        return {
            "can_play_this_week": False,

            "weekly_multiplier": 0.0,

            "workload_multiplier": 0.0,

            "ros_multiplier":
                ros_multiplier,

            "risk_level": "OUT",

            "play_probability":
                probability,

            "practice_summary":
                practice_summary,

            "injury_status":
                status,

            "reason":
                "Injured reserve",
        }

    # ==================================================
    # Out
    # ==================================================

    if status == "OUT":

        return {
            "can_play_this_week": False,

            "weekly_multiplier": 0.0,

            "workload_multiplier": 0.0,

            "ros_multiplier": 0.95,

            "risk_level": "OUT",

            "play_probability": 0.0,

            "practice_summary":
                practice_summary,

            "injury_status":
                status,

            "reason":
                build_injury_reason(
                    status=status,
                    injury_report=injury_report,
                    probability=probability,
                    practice_summary=practice_summary,
                    risk_level="OUT",
                ),
        }

    # ==================================================
    # Doubtful
    #
    # Technically possible to play, but we do not want
    # the lineup optimizer starting a doubtful player
    # while healthy options exist.
    # ==================================================

    if status == "DOUBTFUL":

        return {
            "can_play_this_week": False,

            "weekly_multiplier": 0.20,

            "workload_multiplier": 0.20,

            "ros_multiplier": 0.95,

            "risk_level": "HIGH",

            "play_probability":
                probability,

            "practice_summary":
                practice_summary,

            "injury_status":
                status,

            "reason":
                build_injury_reason(
                    status=status,
                    injury_report=injury_report,
                    probability=probability,
                    practice_summary=practice_summary,
                    risk_level="HIGH",
                ),
        }

    # ==================================================
    # Active / questionable / practice-report player
    # ==================================================

    (
        status_multiplier,
        status_risk,
    ) = get_status_multiplier(
        status
    )

    (
        practice_multiplier,
        practice_risk,
    ) = get_practice_multiplier(
        injury_report
    )

    (
        probability_multiplier,
        probability_risk,
    ) = get_probability_multiplier(
        probability
    )

    # --------------------------------------------------
    # IMPORTANT:
    #
    # These signals are strongly correlated.
    #
    # Do not:
    #
    #   status
    #   * practice
    #   * probability
    #
    # because we would likely triple-count the same
    # injury concern.
    #
    # Use the most conservative individual signal.
    # --------------------------------------------------

    workload_multiplier = min(
        status_multiplier,
        practice_multiplier,
        probability_multiplier,
    )

    risk_level = combine_risk_levels(
        status_risk,
        practice_risk,
        probability_risk,
    )

    # ==================================================
    # Rest-of-season adjustment
    #
    # Normal questionable/current practice issues
    # should barely affect ROS value.
    # ==================================================

    if risk_level == "HIGH":

        ros_multiplier = 0.98

    else:

        ros_multiplier = 1.0

    # ==================================================
    # Reason
    # ==================================================

    reason = build_injury_reason(
        status=status,
        injury_report=injury_report,
        probability=probability,
        practice_summary=practice_summary,
        risk_level=risk_level,
    )

    return {
        "can_play_this_week": True,

        "weekly_multiplier":
            workload_multiplier,

        "workload_multiplier":
            workload_multiplier,

        "ros_multiplier":
            ros_multiplier,

        "risk_level":
            risk_level,

        "play_probability":
            probability,

        "practice_summary":
            practice_summary,

        "injury_status":
            status,

        "reason":
            reason,
    }