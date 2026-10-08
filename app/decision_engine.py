def _safe_number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _relative_score(a, b):
    """
    Convert two positive values into scores that sum to 1.

    Example:
        20 vs 10 -> 0.67 vs 0.33
    """

    a = max(_safe_number(a), 0)
    b = max(_safe_number(b), 0)

    total = a + b

    if total == 0:
        return 0.5, 0.5

    return a / total, b / total


def _ecr_score(a_rank, b_rank):
    """
    Lower ECR is better, so convert rank into inverse rank.

    RB1 > RB5
    """

    if a_rank is None and b_rank is None:
        return 0.5, 0.5

    if a_rank is None:
        return 0.0, 1.0

    if b_rank is None:
        return 1.0, 0.0

    a_value = 1 / float(a_rank)
    b_value = 1 / float(b_rank)

    return _relative_score(a_value, b_value)


def _get_usage_value(player):
    """
    Position-specific opportunity metric.
    """

    usage = player.get("recent_usage")

    if not usage:
        return 0.0

    position = player["position"]

    if position == "RB":
        # Both rushing and receiving opportunities matter
        return (
            _safe_number(usage.get("carries_per_game"))
            + _safe_number(usage.get("targets_per_game"))
        )

    if position in ("WR", "TE"):
        return _safe_number(
            usage.get("targets_per_game")
        )

    # We will build better QB-specific features later
    return 0.0


def compare_players(player_a, player_b):
    """
    Compare two players using:
        50% FantasyPros projection
        25% recent PPR production
        15% recent opportunity/usage
        10% ECR
    """

    # --------------------------------------------------
    # Weekly projection
    # --------------------------------------------------

    proj_a, proj_b = _relative_score(
        player_a.get("fp_ppr"),
        player_b.get("fp_ppr"),
    )

    # --------------------------------------------------
    # Recent PPR production
    # --------------------------------------------------

    usage_a = player_a.get("recent_usage") or {}
    usage_b = player_b.get("recent_usage") or {}

    recent_a, recent_b = _relative_score(
        usage_a.get("ppr_points_per_game"),
        usage_b.get("ppr_points_per_game"),
    )

    # --------------------------------------------------
    # Opportunity / usage
    # --------------------------------------------------

    opportunity_a, opportunity_b = _relative_score(
        _get_usage_value(player_a),
        _get_usage_value(player_b),
    )

    # --------------------------------------------------
    # Expert consensus
    # --------------------------------------------------

    ecr_a, ecr_b = _ecr_score(
        player_a.get("ecr"),
        player_b.get("ecr"),
    )

    # --------------------------------------------------
    # Weighted final score
    # --------------------------------------------------

    score_a = (
        0.50 * proj_a
        + 0.25 * recent_a
        + 0.15 * opportunity_a
        + 0.10 * ecr_a
    )

    score_b = (
        0.50 * proj_b
        + 0.25 * recent_b
        + 0.15 * opportunity_b
        + 0.10 * ecr_b
    )

    if score_a >= score_b:
        winner = player_a
        loser = player_b
    else:
        winner = player_b
        loser = player_a

    difference = abs(score_a - score_b)

    # Simple confidence estimate for now
    confidence = min(
        95,
        50 + difference * 100,
    )

    return {
        "recommended_player": winner["name"],
        "other_player": loser["name"],
        "confidence": round(confidence, 1),

        "scores": {
            player_a["name"]: round(score_a, 4),
            player_b["name"]: round(score_b, 4),
        },

        "comparison": {
            "projection": {
                player_a["name"]: player_a.get("fp_ppr"),
                player_b["name"]: player_b.get("fp_ppr"),
            },

            "recent_ppr": {
                player_a["name"]:
                    usage_a.get("ppr_points_per_game"),

                player_b["name"]:
                    usage_b.get("ppr_points_per_game"),
            },

            "opportunity": {
                player_a["name"]:
                    _get_usage_value(player_a),

                player_b["name"]:
                    _get_usage_value(player_b),
            },

            "ecr": {
                player_a["name"]:
                    player_a.get("pos_rank"),

                player_b["name"]:
                    player_b.get("pos_rank"),
            },
        },
    }