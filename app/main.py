from espn_client import (
    get_league,
    get_my_team,
    get_roster,
    get_free_agents
)

from player_enrichment import (
    enrich_players,
    add_specific_rankings
)

from lineup_optimizer import (
    optimize_lineup,
)

from decision_history import (
    save_weekly_snapshot,
)

from decision_evaluator import (
    evaluate_pending_decisions,
)

from week_status import (
    get_week_status,
)


SEASON = 2026


def print_roster(roster):
    """
    Print the enriched roster.
    """

    print(
        "\n=== MY ROSTER ==="
    )

    print(
        f"{'PLAYER':25} "
        f"{'POS':5} "
        f"{'PROJ':>7} "
        f"{'WEEK':>7} "
        f"{'ROS':>7} "
        f"{'WK VAL':>7} "
        f"{'ROS VAL':>8} "
        f"{'L3 PPR':>8} "
        f"{'OPP':>9} "
        f"{'STATUS':>18}"
    )

    for player in roster:

        # Weekly projection
        projection = (
            player.get(
                "fp_ppr"
            )
        )

        proj_text = (
            f"{projection:.2f}"
            if projection is not None
            else "N/A"
        )

        # Weekly ECR
        weekly_rank = (
            str(
                player["pos_rank"]
            )
            if player.get(
                "pos_rank"
            ) is not None
            else "N/A"
        )

        # ROS ECR
        ros_rank = (
            str(
                player[
                    "ros_pos_rank"
                ]
            )
            if player.get(
                "ros_pos_rank"
            ) is not None
            else "N/A"
        )

        # Weekly score
        weekly_score = (
            player.get(
                "weekly_score"
            )
        )

        weekly_value = (
            f"{weekly_score:.1f}"
            if weekly_score is not None
            else "N/A"
        )

        # ROS score
        ros_score = (
            player.get(
                "ros_score"
            )
        )

        ros_value = (
            f"{ros_score:.1f}"
            if ros_score is not None
            else "N/A"
        )

        # Recent PPR
        usage = player.get(
            "recent_usage"
        )

        if (
            usage is not None
            and usage.get(
                "ppr_points_per_game"
            ) is not None
        ):

            recent_ppr = (
                f"{usage['ppr_points_per_game']:.2f}"
            )

        else:

            recent_ppr = "N/A"

        opponent = (
            player.get(
                "opponent"
            )
            or "N/A"
        )

        status = (
            player.get(
                "injury_status"
            )
            or "N/A"
        )

        print(
            f"{player['name']:25} "
            f"{player['position']:5} "
            f"{proj_text:>7} "
            f"{weekly_rank:>7} "
            f"{ros_rank:>7} "
            f"{weekly_value:>7} "
            f"{ros_value:>8} "
            f"{recent_ppr:>8} "
            f"{opponent:>9} "
            f"{status:>18}"
        )


def print_unmapped_players(roster):
    """
    Show ESPN players that could not be mapped to
    FantasyPros.
    """

    print(
        "\n=== UNMAPPED PLAYERS ==="
    )

    unmapped = [
        player
        for player in roster
        if player.get(
            "fantasypros_id"
        ) is None
    ]

    if not unmapped:

        print("None")

        return

    for player in unmapped:

        print(
            player["name"],
            player["espn_id"],
            player["position"],
        )


def print_optimal_lineup(
    optimized,
):
    """
    Print the weekly lineup recommendation.
    """

    print(
        "\n=== OPTIMAL LINEUP ==="
    )

    lineup = optimized.get(
        "lineup"
    )

    if lineup is None:

        print(
            "Could not generate "
            "a legal lineup."
        )

        return

    for slot, player in (
        lineup.items()
    ):

        if player is None:

            print(
                f"{slot:6} EMPTY"
            )

            continue

        projection = player.get(
            "fp_ppr"
        )

        weekly_score = player.get(
            "weekly_score"
        )

        ros_score = player.get(
            "ros_score"
        )

        proj_text = (
            f"{projection:.2f}"
            if projection is not None
            else "N/A"
        )

        weekly_text = (
            f"{weekly_score:.1f}"
            if weekly_score is not None
            else "N/A"
        )

        ros_text = (
            f"{ros_score:.1f}"
            if ros_score is not None
            else "N/A"
        )

        print(
            f"{slot:6} "
            f"{player['name']:25} "
            f"{player['position']:5} "
            f"PROJ {proj_text:>6} "
            f"WK VAL {weekly_text:>6} "
            f"ROS VAL {ros_text:>6}"
        )

    print(
        f"\nProjected fantasy points: "
        f"{optimized['projected_total']:.2f}"
    )

    print(
        f"Weekly lineup value: "
        f"{optimized['lineup_score']:.2f}"
    )

def get_waiver_prescreen_score(
    player,
):
    """
    Cheap first-pass waiver score used only to decide
    which 5 players at each position deserve deeper
    FantasyPros analysis.

    This is NOT the final waiver recommendation score.

    Components:

        50% current weekly value
        30% LeagueLogs market value
        20% LeagueLogs ROS positional rank
    """

    # ----------------------------------------------
    # Weekly value
    # Already on roughly a 0-100 scale.
    # ----------------------------------------------

    weekly_score = (
        player.get(
            "weekly_score"
        )
        or 0.0
    )

    weekly_component = min(
        max(
            weekly_score / 100.0,
            0.0,
        ),
        1.0,
    )

    # ----------------------------------------------
    # LeagueLogs
    # ----------------------------------------------

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

    # Market value is already 0 -> 10,000.
    market_value = (
        leaguelogs.get(
            "market_value"
        )
    )

    if market_value is None:
        market_component = 0.0

    else:
        market_component = min(
            max(
                market_value / 10000.0,
                0.0,
            ),
            1.0,
        )

    # ----------------------------------------------
    # LeagueLogs ROS positional rank
    #
    # Same rank normalization concept we use in
    # player_value.py.
    # ----------------------------------------------

    ros_pos_rank = (
        leaguelogs.get(
            "ros_pos_rank"
        )
    )

    if (
        ros_pos_rank is None
        or ros_pos_rank <= 0
    ):
        ros_component = 0.0

    else:
        ros_component = (
            1.0
            / (
                1.0
                + 0.1
                * (
                    ros_pos_rank - 1
                )
            )
        )

    # ----------------------------------------------
    # Final prescreen
    # ----------------------------------------------

    score = (
        weekly_component * 0.50
        + market_component * 0.30
        + ros_component * 0.20
    )

    return round(
        score * 100.0,
        2,
    )

def get_top_waiver_candidates(
    free_agents,
    per_position=5,
):
    """
    Select a balanced waiver shortlist.

    We do NOT simply pick the highest weekly scores.

    The shortlist considers:

        - immediate weekly usefulness
        - LeagueLogs market value
        - LeagueLogs rest-of-season rank

    This helps us catch both weekly streamers and
    valuable long-term pickups.
    """

    candidates = []

    for position in (
        "QB",
        "RB",
        "WR",
        "TE",
    ):

        position_players = [
            player
            for player in free_agents
            if player["position"]
            == position
        ]

        for player in position_players:

            player[
                "waiver_prescreen_score"
            ] = (
                get_waiver_prescreen_score(
                    player
                )
            )

        position_players.sort(
            key=lambda player: (
                player.get(
                    "waiver_prescreen_score",
                    0.0,
                )
            ),
            reverse=True,
        )

        candidates.extend(
            position_players[
                :per_position
            ]
        )

    return candidates


def print_waiver_candidates(
    players,
):
    print(
        "\n=== TOP WAIVER CANDIDATES ==="
    )

    print(
        f"{'PLAYER':25} "
        f"{'POS':5} "
        f"{'PROJ':>7} "
        f"{'WEEK':>7} "
        f"{'ROS':>7} "
        f"{'WK VAL':>7} "
        f"{'WK DATA':>8} "
        f"{'ROS VAL':>8} "
        f"{'ROS DATA':>9} "
        f"{'L3 PPR':>8}"
    )

    position_order = {
        "QB": 0,
        "RB": 1,
        "WR": 2,
        "TE": 3,
    }

    players = sorted(
        players,
        key=lambda player: (
            position_order.get(
                player["position"],
                99,
            ),
            -(
                player.get(
                    "weekly_score"
                )
                or 0
            ),
        ),
    )

    for player in players:

        projection = player.get(
            "fp_ppr"
        )

        projection_text = (
            f"{projection:.2f}"
            if projection is not None
            else "N/A"
        )

        weekly_rank = (
            str(player["pos_rank"])
            if player.get("pos_rank")
            is not None
            else "N/A"
        )

        ros_rank = (
            str(
                player["ros_pos_rank"]
            )
            if player.get(
                "ros_pos_rank"
            ) is not None
            else "N/A"
        )

        weekly_score = (
            player.get(
                "weekly_score"
            )
        )

        weekly_text = (
            f"{weekly_score:.1f}"
            if weekly_score is not None
            else "N/A"
        )

        ros_score = player.get(
            "ros_score"
        )

        ros_text = (
            f"{ros_score:.1f}"
            if ros_score is not None
            else "N/A"
        )

        weekly_complete = player.get(
            "weekly_data_complete"
        )

        ros_complete = player.get(
            "ros_data_complete"
        )


        if weekly_complete is True:
            weekly_data_text = "FULL"

        elif weekly_complete is False:
            weekly_data_text = "PARTIAL"

        else:
            weekly_data_text = "N/A"


        if ros_complete is True:
            ros_data_text = "FULL"

        elif ros_complete is False:
            ros_data_text = "PARTIAL"

        else:
            ros_data_text = "N/A"

        usage = player.get(
            "recent_usage"
        ) or {}

        recent_ppr = usage.get(
            "ppr_points_per_game"
        )

        recent_text = (
            f"{recent_ppr:.2f}"
            if recent_ppr is not None
            else "N/A"
        )

        print(
            f"{player['name']:25} "
            f"{player['position']:5} "
            f"{projection_text:>7} "
            f"{weekly_rank:>7} "
            f"{ros_rank:>7} "
            f"{weekly_text:>7} "
            f"{weekly_data_text:>8} "
            f"{ros_text:>8} "
            f"{ros_data_text:>9} "
            f"{recent_text:>8}"
        )

def main():

    # --------------------------------------------------
    # Evaluate completed historical weeks
    # --------------------------------------------------

    evaluate_pending_decisions()

    # --------------------------------------------------
    # Load ESPN
    # --------------------------------------------------

    league = get_league()

    my_team = get_my_team(
        league
    )

    current_week = (
        league.current_week
    )

    roster = get_roster(
        my_team
    )

    # --------------------------------------------------
    # Enrich roster
    # --------------------------------------------------

    roster = enrich_players(
        players=roster,
        season=SEASON,
        week=current_week,
    )

    # --------------------------------------------------
    # Load ESPN free agents
    # --------------------------------------------------

    free_agents = get_free_agents(
        league,
        size=100,
    )

    # --------------------------------------------------
    # First-pass free-agent enrichment
    #
    # Batch projections/ECR and recent stats, but do NOT
    # make individual FantasyPros ranking requests yet.
    # --------------------------------------------------

    free_agents = enrich_players(
        players=free_agents,
        season=SEASON,
        week=current_week,
        include_player_specific=False,
    )

    # --------------------------------------------------
    # Select the most relevant waiver candidates
    # --------------------------------------------------

    waiver_candidates = (
        get_top_waiver_candidates(
            free_agents,
            per_position=5,
        )
    )

    # --------------------------------------------------
    # Fully enrich only those candidates
    #
    # Now we fetch ROS rankings and weekly fallbacks.
    # At most ~20 offensive players instead of 100.
    # --------------------------------------------------

    waiver_candidates = add_specific_rankings(
        players=waiver_candidates,
        season=SEASON,
        week=current_week,
    )

    # --------------------------------------------------
    # Basic league information
    # --------------------------------------------------

    print(
        f"\nLeague: "
        f"{league.settings.name}"
    )

    print(
        f"Week: "
        f"{current_week}"
    )

    # --------------------------------------------------
    # Display roster
    # --------------------------------------------------

    print_roster(
        roster
    )

    print_waiver_candidates(
        waiver_candidates
    )

    print_unmapped_players(
        roster
    )

    # --------------------------------------------------
    # Optimize lineup
    # --------------------------------------------------

    optimized = optimize_lineup(
        roster
    )

    print_optimal_lineup(
        optimized
    )

    # --------------------------------------------------
    # Weekly snapshot lifecycle
    # --------------------------------------------------

    week_status = get_week_status(
        SEASON,
        current_week,
    )

    if week_status == "NOT_STARTED":

        path = save_weekly_snapshot(
            season=SEASON,
            week=current_week,
            roster=roster,
            optimized=optimized,
            comparison=None,
        )

        print(
            f"\n[AUTO-SAVE] "
            f"Week {current_week} "
            f"has not started."
        )

        print(
            f"[AUTO-SAVE] "
            f"Decision snapshot "
            f"saved/refreshed: "
            f"{path}"
        )

    elif week_status == "IN_PROGRESS":

        print(
            f"\n[AUTO-SAVE] "
            f"Week {current_week} "
            f"is in progress."
        )

        print(
            "[AUTO-SAVE] Existing "
            "pregame snapshot "
            "left unchanged."
        )

    elif week_status == "COMPLETE":

        print(
            f"\n[AUTO-SAVE] "
            f"Week {current_week} "
            f"is complete."
        )

        print(
            "[AUTO-SAVE] No new "
            "pregame snapshot saved."
        )

    else:

        print(
            f"\n[AUTO-SAVE] "
            f"Could not determine "
            f"Week {current_week} "
            f"status."
        )


if __name__ == "__main__":
    main()