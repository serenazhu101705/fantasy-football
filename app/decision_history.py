import json
from datetime import datetime, timezone
from pathlib import Path


DECISIONS_DIR = Path("data/decisions")
DECISIONS_DIR.mkdir(parents=True, exist_ok=True)


def player_snapshot(player):
    """
    Store the information the model knew when the
    decision was made.
    """

    return {
        "espn_id": player.get("espn_id"),
        "fantasypros_id": player.get(
            "fantasypros_id"
        ),

        "name": player.get("name"),
        "position": player.get("position"),
        "nfl_team": player.get("nfl_team"),

        "injury_status": player.get(
            "injury_status"
        ),
        "lineup_slot": player.get(
            "lineup_slot"
        ),

        # ----------------------------------------------
        # Weekly value
        # ----------------------------------------------

        "projection_ppr": player.get("fp_ppr"),  # legacy compatibility
        "projection_points": (
            player.get("fp_points")
            if player.get("fp_points") is not None
            else player.get("espn_projected_points")
        ),

        "weekly_ecr": player.get(
            "ecr"
        ),

        "weekly_pos_rank": player.get(
            "pos_rank"
        ),

        "weekly_score": player.get(
            "weekly_score"
        ),

        "rank_min": player.get(
            "rank_min"
        ),

        "rank_max": player.get(
            "rank_max"
        ),

        "rank_std": player.get(
            "rank_std"
        ),

        "opponent": player.get(
            "opponent"
        ),

        # ----------------------------------------------
        # Rest-of-season value
        # ----------------------------------------------

        "ros_ecr": player.get(
            "ros_ecr"
        ),

        "ros_pos_rank": player.get(
            "ros_pos_rank"
        ),

        "ros_score": player.get(
            "ros_score"
        ),

        "ros_min": player.get(
            "ros_min"
        ),

        "ros_max": player.get(
            "ros_max"
        ),

        "ros_std": player.get(
            "ros_std"
        ),

        # ----------------------------------------------
        # Recent NFL performance / opportunity
        # ----------------------------------------------

        "recent_usage": player.get(
            "recent_usage"
        ),

        "weekly_data_complete": player.get(
            "weekly_data_complete"
        ),

        "weekly_missing_data": player.get(
            "weekly_missing_data"
        ),

        "ros_data_complete": player.get(
            "ros_data_complete"
        ),

        "ros_missing_data": player.get(
            "ros_missing_data"
        ),

        "availability": player.get(
            "availability"
        ),
    }

def save_weekly_snapshot(
    season,
    week,
    roster,
    optimized,
    comparison=None,
    league_settings=None,
):
    """
    Freeze the information the model knew when the
    recommendation was made.
    """

    lineup = optimized.get("lineup") or {}

    starting_ids = {
        player["espn_id"]
        for player in lineup.values()
        if player is not None
    }

    lineup_snapshot = {}

    for slot, player in lineup.items():
        if player is None:
            lineup_snapshot[slot] = None
        else:
            lineup_snapshot[slot] = player_snapshot(player)

    bench_snapshot = [
        player_snapshot(player)
        for player in roster
        if player["espn_id"] not in starting_ids
    ]

    data = {
        "season": season,
        "week": week,

        "saved_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),

        "decision_type": "weekly_lineup",
        "league_scoring": {
            "scoring_label": (league_settings or {}).get("scoring_label", "UNKNOWN"),
            "reception_points": (league_settings or {}).get("reception_points"),
            "nflverse_points_scope": (league_settings or {}).get("nflverse_points_scope"),
        },

        "projected_total": optimized.get(
            "projected_total"
        ),

        "lineup": lineup_snapshot,

        "bench": bench_snapshot,

        # Save the entire roster so we can later ask:
        # "Was there a better option on the bench?"
        "roster": [
            player_snapshot(player)
            for player in roster
        ],

        # Optional test comparison
        "comparison": comparison,

        # Filled in after the games happen
        "actual_results": None,

        # Filled in by our evaluator later
        "evaluation": None,
    }

    path = DECISIONS_DIR / (
        f"{season}_week{week}.json"
    )

    with open(path, "w") as file:
        json.dump(
            data,
            file,
            indent=2,
        )

    return path


def load_weekly_snapshot(season, week):
    path = DECISIONS_DIR / (
        f"{season}_week{week}.json"
    )

    if not path.exists():
        return None

    with open(path, "r") as file:
        return json.load(file)