from itertools import combinations


def projected_points(player):
    """
    Use FantasyPros PPR projection as our lineup optimization
    value for now.
    """
    value = player.get("fp_ppr")

    if value is None:
        return 0.0

    return float(value)


def is_available(player):
    """
    Players on IR should not be considered startable.
    We can make this smarter later.
    """
    status = player.get("injury_status")

    return status != "INJURY_RESERVE"


def best_player(players):
    if not players:
        return None

    return max(
        players,
        key=projected_points,
    )


def optimize_lineup(roster):
    """
    Assumes:
        1 QB
        2 RB
        2 WR
        1 TE
        1 FLEX (RB/WR/TE)
        1 D/ST
        1 K
    """

    available = [
        player
        for player in roster
        if is_available(player)
    ]

    qbs = [
        p for p in available
        if p["position"] == "QB"
    ]

    rbs = [
        p for p in available
        if p["position"] == "RB"
    ]

    wrs = [
        p for p in available
        if p["position"] == "WR"
    ]

    tes = [
        p for p in available
        if p["position"] == "TE"
    ]

    dsts = [
        p for p in available
        if p["position"] == "D/ST"
    ]

    kickers = [
        p for p in available
        if p["position"] == "K"
    ]

    best_lineup = None
    best_total = -1

    # Try every legal combination of:
    # 2 RB, 2 WR, 1 TE
    for rb_combo in combinations(rbs, 2):
        for wr_combo in combinations(wrs, 2):
            for te in tes:

                used_ids = {
                    p["espn_id"]
                    for p in (
                        list(rb_combo)
                        + list(wr_combo)
                        + [te]
                    )
                }

                # FLEX can be any remaining RB/WR/TE
                flex_candidates = [
                    p
                    for p in (rbs + wrs + tes)
                    if p["espn_id"] not in used_ids
                ]

                flex = best_player(flex_candidates)

                if flex is None:
                    continue

                qb = best_player(qbs)
                dst = best_player(dsts)
                kicker = best_player(kickers)

                if qb is None or kicker is None:
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

                total = sum(
                    projected_points(player)
                    for player in lineup.values()
                    if player is not None
                )

                if total > best_total:
                    best_total = total
                    best_lineup = lineup

    return {
        "lineup": best_lineup,
        "projected_total": best_total,
    }