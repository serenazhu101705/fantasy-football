from espn_client import (
    get_league,
    get_my_team,
    get_roster,
    get_free_agents,
)

from player_enrichment import (
    enrich_players,
    add_specific_rankings,
)

from lineup_optimizer import (
    optimize_lineup,
)
from waiver_advisor import (
    evaluate_all_transactions,
    rank_transactions,
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

from injury_monitor import (
    attach_injury_signals,
)
from weekly_ml_update import (
    maybe_run_weekly_ml_update,
)
from league_settings import (
    get_league_scoring_context,
    reception_points_or_default,
)

SEASON = 2026


# ==================================================
# Roster display
# ==================================================

def print_roster(
    roster,
):
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
        f"{'L3 PTS':>8} "
        f"{'OPP':>9} "
        f"{'STATUS':>18}"
    )

    for player in roster:

        # ------------------------------------------
        # Weekly projection
        # ------------------------------------------

        projection = (
            player.get("fp_points")
            if player.get("fp_points") is not None
            else player.get("fp_ppr")
        )

        proj_text = (
            f"{projection:.2f}"
            if projection is not None
            else "N/A"
        )

        # ------------------------------------------
        # Weekly ECR
        # ------------------------------------------

        weekly_rank = (
            str(
                player[
                    "pos_rank"
                ]
            )
            if player.get(
                "pos_rank"
            ) is not None
            else "N/A"
        )

        # ------------------------------------------
        # ROS ECR
        # ------------------------------------------

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

        # ------------------------------------------
        # Weekly value
        # ------------------------------------------

        weekly_score = player.get(
            "weekly_score"
        )

        weekly_value = (
            f"{weekly_score:.1f}"
            if weekly_score is not None
            else "N/A"
        )

        # ------------------------------------------
        # ROS value
        # ------------------------------------------

        ros_score = player.get(
            "ros_score"
        )

        ros_value = (
            f"{ros_score:.1f}"
            if ros_score is not None
            else "N/A"
        )

        # ------------------------------------------
        # Recent PPR production
        # ------------------------------------------

        usage = player.get(
            "recent_usage"
        )

        if (
            usage is not None
            and (
                usage.get("fantasy_points_per_game") is not None
                or usage.get("ppr_points_per_game") is not None
            )
        ):

            recent_value = (
                usage.get("fantasy_points_per_game")
                if usage.get("fantasy_points_per_game") is not None
                else usage.get("ppr_points_per_game")
            )
            recent_ppr = f"{recent_value:.2f}"

        else:

            recent_ppr = "N/A"

        # ------------------------------------------
        # Opponent
        # ------------------------------------------

        opponent = (
            player.get(
                "opponent"
            )
            or "N/A"
        )

        # ------------------------------------------
        # Injury status
        # ------------------------------------------

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


# ==================================================
# Unmapped FantasyPros players
# ==================================================

def print_unmapped_players(
    roster,
):
    """
    Show ESPN players that could not be mapped to
    FantasyPros.

    Note:
    A player can still have an ESPN projection even if
    they are unmapped to FantasyPros.
    """

    print(
        "\n=== UNMAPPED FANTASYPROS PLAYERS ==="
    )

    unmapped = [
        player
        for player in roster
        if player.get(
            "fantasypros_id"
        ) is None
    ]

    if not unmapped:

        print(
            "None"
        )

        return

    for player in unmapped:

        print(
            player["name"],
            player["espn_id"],
            player["position"],
        )


# ==================================================
# Optimal lineup display
# ==================================================

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

    for (
        slot,
        player,
    ) in lineup.items():

        if player is None:

            print(
                f"{slot:6} EMPTY"
            )

            continue

        projection = (
            player.get("fp_points")
            if player.get("fp_points") is not None
            else player.get("fp_ppr")
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


# ==================================================
# Waiver prescreen
# ==================================================

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
    # ----------------------------------------------

    weekly_score = (
        player.get(
            "weekly_score"
        )
        or 0.0
    )

    weekly_component = min(
        max(
            weekly_score
            / 100.0,
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
            {},
        )
        .get(
            "leaguelogs",
            {},
        )
    )

    # ----------------------------------------------
    # Market value
    # ----------------------------------------------

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
                market_value
                / 10000.0,
                0.0,
            ),
            1.0,
        )

    # ----------------------------------------------
    # LeagueLogs ROS positional rank
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
                    ros_pos_rank
                    - 1
                )
            )
        )

    # ----------------------------------------------
    # Final prescreen score
    # ----------------------------------------------

    score = (
        weekly_component
        * 0.50

        + market_component
        * 0.30

        + ros_component
        * 0.20
    )

    return round(
        score
        * 100.0,
        2,
    )


# ==================================================
# Waiver shortlist
# ==================================================

def get_top_waiver_candidates(
    free_agents,
    per_position=5,
):
    """
    Select a balanced waiver shortlist.

    We do NOT simply pick the highest weekly scores.

    The shortlist considers:

        immediate weekly usefulness
        LeagueLogs market value
        LeagueLogs rest-of-season rank

    This helps catch both weekly streamers and valuable
    long-term pickups.
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
            if player.get(
                "position"
            ) == position
        ]

        for player in position_players:

            player[
                "waiver_prescreen_score"
            ] = get_waiver_prescreen_score(
                player
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


# ==================================================
# Waiver display
# ==================================================

def print_waiver_candidates(
    players,
):
    """
    Print the shortlisted waiver candidates.
    """

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
        f"{'L3 PTS':>8}"
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
                player.get(
                    "position"
                ),
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

        # ------------------------------------------
        # Projection
        # ------------------------------------------

        projection = (
            player.get("fp_points")
            if player.get("fp_points") is not None
            else player.get("fp_ppr")
        )

        projection_text = (
            f"{projection:.2f}"
            if projection is not None
            else "N/A"
        )

        # ------------------------------------------
        # Weekly rank
        # ------------------------------------------

        weekly_rank = (
            str(
                player[
                    "pos_rank"
                ]
            )
            if player.get(
                "pos_rank"
            ) is not None
            else "N/A"
        )

        # ------------------------------------------
        # ROS rank
        # ------------------------------------------

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

        # ------------------------------------------
        # Weekly value
        # ------------------------------------------

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

        # ------------------------------------------
        # ROS value
        # ------------------------------------------

        ros_score = player.get(
            "ros_score"
        )

        ros_text = (
            f"{ros_score:.1f}"
            if ros_score is not None
            else "N/A"
        )

        # ------------------------------------------
        # Data completeness
        # ------------------------------------------

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

        # ------------------------------------------
        # Recent production
        # ------------------------------------------

        usage = (
            player.get(
                "recent_usage"
            )
            or {}
        )

        recent_ppr = (
            usage.get("fantasy_points_per_game")
            if usage.get("fantasy_points_per_game") is not None
            else usage.get("ppr_points_per_game")
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

def print_waiver_advice(
    ranked_transactions,
    limit=10,
):
    """
    Print the best waiver add/drop recommendations.

    Only the best transaction for each potential add
    is displayed so the output does not contain many
    duplicate recommendations for the same player.
    """

    print(
        "\n=== WAIVER ADVISOR ==="
    )

    if not ranked_transactions:

        print(
            "No valid waiver transactions found."
        )

        return

    # --------------------------------------------------
    # Keep only the best transaction for each player
    # we might add.
    #
    # rank_transactions() already puts the strongest
    # version of each move first.
    # --------------------------------------------------

    best_by_add = []

    seen_adds = set()
    actionable_categories = {
        "STRONG_ADD",
        "ADD",
        "STASH",
        "STREAMER",
    }

    for result in ranked_transactions:
        if result.get(
            "recommendation"
        ) not in actionable_categories:
            continue

        add_player = result.get(
            "add",
            {},
        )

        add_key = (
            add_player.get(
                "espn_id"
            )
            or (
                add_player.get(
                    "name"
                ),
                add_player.get(
                    "position"
                ),
            )
        )

        if add_key in seen_adds:
            continue

        seen_adds.add(
            add_key
        )

        best_by_add.append(
            result
        )

        if len(
            best_by_add
        ) >= limit:
            break
        
    if not best_by_add:

        print(
            "No waiver moves currently clear "
            "the threshold for an add."
        )

        return

    # --------------------------------------------------
    # Print each recommendation
    # --------------------------------------------------

    for index, result in enumerate(
        best_by_add,
        start=1,
    ):

        add_player = result.get(
            "add",
            {},
        )

        drop_player = result.get(
            "drop",
            {},
        )

        category = result.get(
            "recommendation",
            "N/A",
        )

        reason = result.get(
            "recommendation_reason",
            "",
        )

        weekly_delta = result.get(
            "weekly_lineup_delta"
        )

        projected_delta = result.get(
            "projected_points_delta"
        )

        ros_delta = result.get(
            "ros_delta"
        )

        market_delta = result.get(
            "market_value_delta"
        )

        balance_delta = result.get(
            "roster_balance_delta"
        )

        depth_risk = result.get(
            "depth_risk",
            False,
        )

        depth_risks = (
            result.get(
                "depth_risks"
            )
            or []
        )

        # ----------------------------------------------
        # Formatting
        # ----------------------------------------------

        weekly_text = (
            f"{weekly_delta:+.2f}"
            if weekly_delta is not None
            else "N/A"
        )

        projected_text = (
            f"{projected_delta:+.2f}"
            if projected_delta is not None
            else "N/A"
        )

        ros_text = (
            f"{ros_delta:+.1f}"
            if ros_delta is not None
            else "N/A"
        )

        market_text = (
            f"{market_delta:+.0f}"
            if market_delta is not None
            else "N/A"
        )

        balance_text = (
            f"{balance_delta:+.1f}"
            if balance_delta is not None
            else "N/A"
        )

        # ----------------------------------------------
        # Recommendation header
        # ----------------------------------------------

        print(
            f"\n{index}. "
            f"[{category}] "
            f"ADD {add_player.get('name', 'N/A')} "
            f"({add_player.get('position', 'N/A')})"
        )

        print(
            f"   DROP "
            f"{drop_player.get('name', 'N/A')} "
            f"({drop_player.get('position', 'N/A')})"
        )

        # ----------------------------------------------
        # Metrics
        # ----------------------------------------------

        print(
            f"   Weekly lineup value: "
            f"{weekly_text}"
        )

        print(
            f"   Projected points:    "
            f"{projected_text}"
        )

        print(
            f"   ROS value:           "
            f"{ros_text}"
        )

        print(
            f"   Market value:        "
            f"{market_text}"
        )

        print(
            f"   Roster balance:      "
            f"{balance_text}"
        )

        # ----------------------------------------------
        # Depth warning
        # ----------------------------------------------

        if depth_risk:

            if depth_risks:

                print(
                    "   WARNING: "
                    + ", ".join(
                        depth_risks
                    )
                )

            else:

                print(
                    "   WARNING: "
                    "Creates a roster-depth risk."
                )

        # ----------------------------------------------
        # Explanation
        # ----------------------------------------------

        if reason:

            print(
                f"   Why: {reason}"
            )

    print(
        "\nNote: K and D/ST waiver moves are "
        "not evaluated by this model yet."
    )

# ==================================================
# Main
# ==================================================

def main():

    # ==================================================
    # Weekly ML maintenance
    #
    # Cheap cache check on every startup.
    # Full refresh/retrain only when a new completed
    # fantasy week exists.
    # ==================================================

    maybe_run_weekly_ml_update()
    # --------------------------------------------------
    # Evaluate completed historical weeks
    #
    # This is independent of the current ESPN roster.
    # --------------------------------------------------

    evaluate_pending_decisions()

    # --------------------------------------------------
    # Load ESPN league
    # --------------------------------------------------

    league = get_league()

    # IMPORTANT:
    # Establish current week before fetching anything
    # that needs a scoring period.
    current_week = (
        league.current_week
    )

    # --------------------------------------------------
    # Authoritative league scoring context from ESPN
    # --------------------------------------------------

    league_settings = get_league_scoring_context(league)
    reception_points = reception_points_or_default(league_settings)
    fantasypros_scoring = league_settings.get("fantasypros_scoring", "PPR")

    # --------------------------------------------------
    # Get my ESPN team
    # --------------------------------------------------

    my_team = get_my_team(
        league
    )

    # --------------------------------------------------
    # Get fresh ESPN roster
    #
    # Passing league + week lets espn_client attach
    # ESPN weekly projections as a fallback.
    # --------------------------------------------------

    roster = get_roster(
        my_team,
        league=league,
        week=current_week,
    )

    # --------------------------------------------------
    # Enrich roster
    # --------------------------------------------------

    roster = enrich_players(
        players=roster,
        season=SEASON,
        week=current_week,
        scoring=fantasypros_scoring,
        reception_points=reception_points,
    )

    # --------------------------------------------------
    # Get fresh ESPN free agents
    #
    # Passing the current week lets ESPN attach
    # week-specific projected points.
    # --------------------------------------------------

    free_agents = get_free_agents(
        league,
        size=100,
        week=current_week,
    )

    # --------------------------------------------------
    # First-pass free-agent enrichment
    #
    # Batch FantasyPros projections/ECR, nflverse stats,
    # LeagueLogs, bye status, etc.
    #
    # Do NOT make an individual FantasyPros rankings
    # request for all ~100 free agents.
    # --------------------------------------------------

    free_agents = enrich_players(
        players=free_agents,
        season=SEASON,
        week=current_week,
        include_player_specific=False,
        include_injuries=False,
        scoring=fantasypros_scoring,
        reception_points=reception_points,
    )

    # --------------------------------------------------
    # Select waiver shortlist
    #
    # Keep 5 per position.
    # --------------------------------------------------

    waiver_candidates = (
        get_top_waiver_candidates(
            free_agents,
            per_position=5,
        )
    )

    # --------------------------------------------------
    # Add detailed injury/practice information only to
    # the final waiver shortlist.
    #
    # This avoids making a large injury request for all
    # 100 free agents.
    # --------------------------------------------------

    try:

        waiver_candidates = (
            attach_injury_signals(
                players=waiver_candidates,
                season=SEASON,
                week=current_week,
                include_news=False,
                force_refresh=False,
            )
        )

    except Exception as e:

        print(
            "Could not get waiver injury data: "
            f"{e}"
        )

    # --------------------------------------------------
    # Fully enrich only shortlisted waiver candidates
    #
    # This adds player-specific FantasyPros weekly
    # fallback + ROS rankings.
    # --------------------------------------------------

    waiver_candidates = (
        add_specific_rankings(
            players=waiver_candidates,
            season=SEASON,
            week=current_week,
            scoring=fantasypros_scoring,
        )
    )

    # --------------------------------------------------
    # Evaluate actual waiver add/drop combinations
    # --------------------------------------------------

    waiver_transactions = (
        evaluate_all_transactions(
            roster=roster,
            waiver_candidates=waiver_candidates,
        )
    )

    ranked_waiver_transactions = (
        rank_transactions(
            waiver_transactions
        )
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

    print(
        "Scoring: "
        f"{league_settings.get('scoring_label')} "
        f"({league_settings.get('reception_points')} pts/reception)"
    )

    # --------------------------------------------------
    # Display roster
    # --------------------------------------------------

    print_roster(
        roster
    )

    # --------------------------------------------------
    # Display waiver shortlist
    # --------------------------------------------------

    print_waiver_candidates(
        waiver_candidates
    )

    print_waiver_advice(
        ranked_waiver_transactions,
        limit=10,
    )

    # --------------------------------------------------
    # Show players without FantasyPros mapping
    # --------------------------------------------------

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
            league_settings=league_settings,
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
    
    # ==================================================
    # Return structured application context
    #
    # main.py can still be run normally, but the
    # chatbot can now reuse all of the data it loaded.
    # ==================================================

    return {
        "league":
            league,

        "team":
            my_team,

        "current_week":
            current_week,

        "league_settings":
            league_settings,

        "roster":
            roster,

        "free_agents":
            free_agents,

        "waiver_candidates":
            waiver_candidates,

        "waiver_transactions":
            ranked_waiver_transactions,

        "optimized":
            optimized,
    }


# ==================================================
# Entry point
# ==================================================

if __name__ == "__main__":
    main()