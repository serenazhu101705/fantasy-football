from itertools import combinations


def lineup_value(player):
    """
    Value used to choose the best weekly lineup.

    This is our combined weekly decision score from
    player_value.py, NOT projected fantasy points.
    """

    if player is None:
        return 0.0

    value = player.get("weekly_score")

    if value is None:
        return 0.0

    return float(value)


def projected_points(player):
    """
    FantasyPros projected points in the ESPN league's reception format.

    We do NOT use this to choose the lineup anymore.
    We keep it so we can report the expected fantasy
    point total of the recommended lineup.
    """

    if player is None:
        return 0.0

    value = player.get("fp_points")
    if value is None:
        value = player.get("fp_ppr")

    if value is None:
        return 0.0

    return float(value)


def is_available(player):
    """
    Determine whether a player can reasonably be
    considered for the starting lineup.

    Questionable players are still allowed because
    they may play.

    OUT and IR players are excluded.
    """

    availability = (
        player.get("availability")
        or {}
    )

    return availability.get(
        "can_play_this_week",
        True,
    )


def best_player(players):
    """
    Return the player with the highest weekly value.
    """

    if not players:
        return None

    return max(
        players,
        key=lineup_value,
    )


def optimize_lineup(roster):
    """
    Find the highest-value legal weekly lineup.

    Current league assumptions:

        1 QB
        2 RB
        2 WR
        1 TE
        1 FLEX (RB/WR/TE)
        1 D/ST
        1 K

    Players are selected using weekly_score.

    We separately calculate FantasyPros projected
    fantasy points for display.
    """

    # --------------------------------------------------
    # Remove unavailable players
    # --------------------------------------------------

    available = [
        player
        for player in roster
        if is_available(player)
    ]

    # --------------------------------------------------
    # Group by position
    # --------------------------------------------------

    qbs = [
        player
        for player in available
        if player["position"] == "QB"
    ]

    rbs = [
        player
        for player in available
        if player["position"] == "RB"
    ]

    wrs = [
        player
        for player in available
        if player["position"] == "WR"
    ]

    tes = [
        player
        for player in available
        if player["position"] == "TE"
    ]

    dsts = [
        player
        for player in available
        if player["position"] == "D/ST"
    ]

    kickers = [
        player
        for player in available
        if player["position"] == "K"
    ]

    # --------------------------------------------------
    # Make sure a legal lineup is possible
    # --------------------------------------------------

    if (
        len(qbs) < 1
        or len(rbs) < 2
        or len(wrs) < 2
        or len(tes) < 1
        or len(dsts) < 1
        or len(kickers) < 1
    ):
        return {
            "lineup": None,
            "lineup_score": 0.0,
            "projected_total": 0.0,
        }

    # --------------------------------------------------
    # QB / DST / K do not interact with FLEX, so we can
    # choose their best options immediately.
    # --------------------------------------------------

    qb = best_player(qbs)
    dst = best_player(dsts)
    kicker = best_player(kickers)

    # --------------------------------------------------
    # Search every legal RB / WR / TE / FLEX combination
    # --------------------------------------------------

    best_lineup = None
    best_total = -1.0

    for rb_combo in combinations(rbs, 2):

        for wr_combo in combinations(wrs, 2):

            for te in tes:

                used_ids = {
                    player["espn_id"]
                    for player in (
                        list(rb_combo)
                        + list(wr_combo)
                        + [te]
                    )
                }

                # FLEX can be any remaining
                # RB / WR / TE.
                flex_candidates = [
                    player
                    for player in (
                        rbs
                        + wrs
                        + tes
                    )
                    if player["espn_id"]
                    not in used_ids
                ]

                flex = best_player(
                    flex_candidates
                )

                if flex is None:
                    continue

                lineup = {
                    "QB": qb,
                    "RB1": rb_combo[0],
                    "RB2": rb_combo[1],
                    "WR1": wr_combo[0],
                    "WR2": wr_combo[1],
                    "TE": te,
                    "FLEX": flex,
                    "D/ST": dst,
                    "K": kicker,
                }

                # This determines which lineup wins.
                total_value = sum(
                    lineup_value(player)
                    for player
                    in lineup.values()
                )

                if total_value > best_total:
                    best_total = total_value
                    best_lineup = lineup

    # --------------------------------------------------
    # Calculate actual projected fantasy points
    # separately from our decision score.
    # --------------------------------------------------

    if best_lineup is None:
        return {
            "lineup": None,
            "lineup_score": 0.0,
            "projected_total": 0.0,
        }

    projected_total = sum(
        projected_points(player)
        for player
        in best_lineup.values()
    )

    return {
        "lineup": best_lineup,
        "lineup_score": round(
            best_total,
            2,
        ),
        "projected_total": round(
            projected_total,
            2,
        ),
    }