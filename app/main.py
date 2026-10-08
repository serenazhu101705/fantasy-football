from collections import defaultdict

from espn_client import (
    get_league,
    get_my_team,
    get_roster,
)

from fantasypros_client import (
    get_projections_for_players,
    get_consensus_rankings,
    get_rankings_for_player,
)

from player_ids import get_espn_to_fantasypros_map
from nfl_data import get_recent_usage
from decision_engine import compare_players
from lineup_optimizer import optimize_lineup
from decision_history import save_weekly_snapshot
from decision_evaluator import evaluate_pending_decisions
from week_status import get_week_status

SEASON = 2026


def main():
    # --------------------------------------------------
    # Automatically evaluate completed past weeks
    # --------------------------------------------------

    evaluate_pending_decisions()

    # --------------------------------------------------
    # Load ESPN league + roster
    # --------------------------------------------------

    league = get_league()
    my_team = get_my_team(league)
    roster = get_roster(my_team)

    # --------------------------------------------------
    # Map ESPN IDs -> FantasyPros IDs
    # --------------------------------------------------

    id_map = get_espn_to_fantasypros_map()

    for player in roster:
        player["fantasypros_id"] = id_map.get(
            str(player["espn_id"])
        )

    # --------------------------------------------------
    # Group mapped players by position
    # --------------------------------------------------

    players_by_position = defaultdict(list)

    for player in roster:
        fp_id = player["fantasypros_id"]

        if fp_id is None:
            continue

        position = player["position"]

        # FantasyPros uses DST instead of D/ST
        if position == "D/ST":
            position = "DST"

        players_by_position[position].append(fp_id)

    # --------------------------------------------------
    # Get FantasyPros projections
    # --------------------------------------------------

    projections = {}

    for position, fp_ids in players_by_position.items():
        try:
            result = get_projections_for_players(
                season=SEASON,
                week=league.current_week,
                position=position,
                player_ids=fp_ids,
            )

            for player in result:
                projections[player["fpid"]] = player["stats"]

        except Exception as e:
            print(
                f"Could not get projections for "
                f"{position}: {e}"
            )

    # --------------------------------------------------
    # Get FantasyPros weekly ECR
    # --------------------------------------------------

    ecr_data = {}

    for position in players_by_position.keys():
        try:
            result = get_consensus_rankings(
                season=SEASON,
                week=league.current_week,
                position=position,
                scoring="PPR",
            )

            for player in result.get("players", []):
                ecr_data[player["player_id"]] = {
                    "rank_ecr": player.get("rank_ecr"),
                    "rank_min": player.get("rank_min"),
                    "rank_max": player.get("rank_max"),
                    "rank_std": player.get("rank_std"),
                    "pos_rank": player.get("pos_rank"),
                    "start_sit_grade": player.get(
                        "start_sit_grade"
                    ),
                    "opponent": player.get(
                        "player_opponent"
                    ),
                    "experts": player.get("experts", {}),
                }

        except Exception as e:
            print(
                f"Could not get ECR for "
                f"{position}: {e}"
            )

    # --------------------------------------------------
    # Fill missing weekly ECR using player-specific data
    # --------------------------------------------------

    for player in roster:
        fp_id = player["fantasypros_id"]

        if fp_id is None:
            continue

        # Already got weekly ECR
        if fp_id in ecr_data:
            continue

        try:
            result = get_rankings_for_player(
                season=SEASON,
                week=league.current_week,
                player_id=fp_id,
            )

            for ranked_player in result.get("players", []):
                if ranked_player.get("id") != fp_id:
                    continue

                position = ranked_player.get("position_id")
                rank = ranked_player.get("rank", {})

                ecr = rank.get("ECR", {})
                ecr_min = rank.get("ECR_MIN", {})
                ecr_max = rank.get("ECR_MAX", {})
                ecr_std = rank.get("ECR_STD", {})

                # Current-week PPR rankings only
                weekly_ecr = ecr.get("PPR", {})
                weekly_min = ecr_min.get("PPR", {})
                weekly_max = ecr_max.get("PPR", {})
                weekly_std = ecr_std.get("PPR", {})

                rank_ecr = weekly_ecr.get(position)

                # No current-week rank available
                if rank_ecr is None:
                    continue

                ecr_data[fp_id] = {
                    "rank_ecr": rank_ecr,
                    "rank_min": weekly_min.get(position),
                    "rank_max": weekly_max.get(position),
                    "rank_std": weekly_std.get(position),
                    "pos_rank": f"{position}{rank_ecr}",
                    "start_sit_grade": None,
                    "opponent": None,
                    "experts": {},
                }

        except Exception as e:
            print(
                f"Could not get individual ranking "
                f"for {player['name']}: {e}"
            )

    # --------------------------------------------------
    # Get FantasyPros rest-of-season ECR
    # --------------------------------------------------

    ros_data = {}

    for player in roster:
        fp_id = player["fantasypros_id"]

        if fp_id is None:
            continue

        # ROS-PPR is mainly meaningful for QB/RB/WR/TE
        if player["position"] not in (
            "QB",
            "RB",
            "WR",
            "TE",
        ):
            continue

        try:
            result = get_rankings_for_player(
                season=SEASON,
                week=league.current_week,
                player_id=fp_id,
            )

            for ranked_player in result.get(
                "players", []
            ):
                if ranked_player.get("id") != fp_id:
                    continue

                position = ranked_player.get(
                    "position_id"
                )

                rank = ranked_player.get(
                    "rank", {}
                )

                ecr = rank.get("ECR", {})
                ecr_min = rank.get("ECR_MIN", {})
                ecr_max = rank.get("ECR_MAX", {})
                ecr_std = rank.get("ECR_STD", {})

                ros_ecr = (
                    ecr
                    .get("ROS-PPR", {})
                    .get(position)
                )

                ros_min = (
                    ecr_min
                    .get("ROS-PPR", {})
                    .get(position)
                )

                ros_max = (
                    ecr_max
                    .get("ROS-PPR", {})
                    .get(position)
                )

                ros_std = (
                    ecr_std
                    .get("ROS-PPR", {})
                    .get(position)
                )

                if ros_ecr is None:
                    continue

                ros_data[fp_id] = {
                    "ros_ecr": ros_ecr,
                    "ros_min": ros_min,
                    "ros_max": ros_max,
                    "ros_std": ros_std,
                    "ros_pos_rank": (
                        f"{position}{ros_ecr}"
                    ),
                }

        except Exception as e:
            print(
                f"Could not get ROS ranking for "
                f"{player['name']}: {e}"
            )

    # --------------------------------------------------
    # Add projections to roster
    # --------------------------------------------------

    for player in roster:
        fp_id = player["fantasypros_id"]
        stats = projections.get(fp_id)

        if stats:
            player["fp_std"] = stats.get("points")
            player["fp_half"] = stats.get("points_half")
            player["fp_ppr"] = stats.get("points_ppr")
        else:
            player["fp_std"] = None
            player["fp_half"] = None
            player["fp_ppr"] = None

    # --------------------------------------------------
    # Add ECR data to roster
    # --------------------------------------------------

    for player in roster:
        fp_id = player["fantasypros_id"]
        ranking = ecr_data.get(fp_id)

        if ranking:
            player["ecr"] = ranking["rank_ecr"]
            player["rank_min"] = ranking["rank_min"]
            player["rank_max"] = ranking["rank_max"]
            player["rank_std"] = ranking["rank_std"]
            player["pos_rank"] = ranking["pos_rank"]
            player["start_sit_grade"] = (
                ranking["start_sit_grade"]
            )
            player["opponent"] = ranking["opponent"]
            player["expert_rankings"] = (
                ranking["experts"]
            )

        else:
            player["ecr"] = None
            player["rank_min"] = None
            player["rank_max"] = None
            player["rank_std"] = None
            player["pos_rank"] = None
            player["start_sit_grade"] = None
            player["opponent"] = None
            player["expert_rankings"] = {}

    # --------------------------------------------------
    # Add ROS rankings to roster
    # --------------------------------------------------

    for player in roster:
        fp_id = player["fantasypros_id"]

        ros = ros_data.get(fp_id)

        if ros:
            player["ros_ecr"] = ros["ros_ecr"]
            player["ros_pos_rank"] = (
                ros["ros_pos_rank"]
            )
            player["ros_min"] = ros["ros_min"]
            player["ros_max"] = ros["ros_max"]
            player["ros_std"] = ros["ros_std"]

        else:
            player["ros_ecr"] = None
            player["ros_pos_rank"] = None
            player["ros_min"] = None
            player["ros_max"] = None
            player["ros_std"] = None
    # --------------------------------------------------
    # Add recent NFL usage/stats
    # --------------------------------------------------

    for player in roster:

        # We are not currently calculating recent usage
        # metrics for kickers or defenses.
        if player["position"] in ("K", "D/ST"):
            player["recent_usage"] = None
            continue

        try:
            player["recent_usage"] = get_recent_usage(
                player_name=player["name"],
                current_week=league.current_week,
                last_n=3,
            )

        except Exception as e:
            print(
                f"Could not get recent stats for "
                f"{player['name']}: {e}"
            )

            player["recent_usage"] = None

    # --------------------------------------------------
    # Print roster
    # --------------------------------------------------

    print(f"\nLeague: {league.settings.name}")
    print(f"Week: {league.current_week}")

    print("\n=== MY ROSTER ===")

    print(
        f"{'PLAYER':25} "
        f"{'POS':5} "
        f"{'PROJ':>7} "
        f"{'ECR':>7} "
        f"{'L3 PPR':>8} "
        f"{'OPP':>9} "
        f"{'STATUS':>18}"
    )

    for player in roster:

        # Weekly FantasyPros projection
        proj = (
            f"{player['fp_ppr']:.2f}"
            if player.get("fp_ppr") is not None
            else "N/A"
        )

        # Weekly positional ECR
        ecr = (
            str(player["pos_rank"])
            if player.get("pos_rank") is not None
            else "N/A"
        )

        # Recent actual fantasy production
        usage = player.get("recent_usage")

        recent_ppr = (
            f"{usage['ppr_points_per_game']:.2f}"
            if usage
            else "N/A"
        )

        opponent = player.get("opponent") or "N/A"

        status = (
            player.get("injury_status")
            or "N/A"
        )

        print(
            f"{player['name']:25} "
            f"{player['position']:5} "
            f"{proj:>7} "
            f"{ecr:>7} "
            f"{recent_ppr:>8} "
            f"{opponent:>9} "
            f"{status:>18}"
        )

    # --------------------------------------------------
    # Print unmapped FantasyPros players
    # --------------------------------------------------

    print("\n=== UNMAPPED PLAYERS ===")

    unmapped = [
        player
        for player in roster
        if player["fantasypros_id"] is None
    ]

    if not unmapped:
        print("None")

    else:
        for player in unmapped:
            print(
                player["name"],
                player["espn_id"],
                player["position"],
            )

    # --------------------------------------------------
    # Test player comparison
    # --------------------------------------------------

    gibbs = next(
        player
        for player in roster
        if player["name"] == "Jahmyr Gibbs"
    )

    brown = next(
        player
        for player in roster
        if player["name"] == "Chase Brown"
    )

    result = compare_players(
        gibbs,
        brown,
    )

    print("\n=== PLAYER COMPARISON ===")
    print(result)

    # --------------------------------------------------
    # Optimize starting lineup
    # --------------------------------------------------

    optimized = optimize_lineup(roster)

    print("\n=== OPTIMAL LINEUP ===")

    if optimized["lineup"] is None:
        print("Could not generate a legal lineup.")

    else:
        for slot, player in optimized["lineup"].items():

            if player is None:
                print(f"{slot:6} EMPTY")
                continue

            proj = (
                f"{player['fp_ppr']:.2f}"
                if player.get("fp_ppr") is not None
                else "N/A"
            )

            print(
                f"{slot:6} "
                f"{player['name']:25} "
                f"{player['position']:5} "
                f"{proj:>7}"
            )

        print(
            f"\nProjected total: "
            f"{optimized['projected_total']:.2f}"
        )
    
    # --------------------------------------------------
    # Automatically manage weekly decision snapshot
    # --------------------------------------------------

    week_status = get_week_status(
        SEASON,
        league.current_week,
    )

    if week_status == "NOT_STARTED":

        path = save_weekly_snapshot(
            season=SEASON,
            week=league.current_week,
            roster=roster,
            optimized=optimized,
            comparison=result,
        )

        print(
            f"\n[AUTO-SAVE] "
            f"Week {league.current_week} "
            f"has not started."
        )

        print(
            f"[AUTO-SAVE] Decision snapshot "
            f"saved/refreshed: {path}"
        )

    elif week_status == "IN_PROGRESS":

        print(
            f"\n[AUTO-SAVE] "
            f"Week {league.current_week} "
            f"is in progress."
        )

        print(
            "[AUTO-SAVE] Existing pregame "
            "snapshot left unchanged."
        )

    elif week_status == "COMPLETE":

        print(
            f"\n[AUTO-SAVE] "
            f"Week {league.current_week} "
            f"is complete."
        )

        print(
            "[AUTO-SAVE] No new pregame "
            "snapshot saved."
        )

    else:

        print(
            f"\n[AUTO-SAVE] Could not determine "
            f"Week {league.current_week} status."
        )


if __name__ == "__main__":
    main()