import json
from datetime import datetime, timezone
from pathlib import Path


DECISIONS_DIR = Path("data/decisions")
DECISIONS_DIR.mkdir(parents=True, exist_ok=True)


def player_snapshot(player):
    """
    Store only the information we care about for evaluating
    a decision later.
    """

    return {
        "espn_id": player.get("espn_id"),
        "fantasypros_id": player.get("fantasypros_id"),

        "name": player.get("name"),
        "position": player.get("position"),
        "nfl_team": player.get("nfl_team"),

        "injury_status": player.get("injury_status"),
        "lineup_slot": player.get("lineup_slot"),

        # Weekly FantasyPros data
        "projection_ppr": player.get("fp_ppr"),
        "ecr": player.get("ecr"),
        "pos_rank": player.get("pos_rank"),
        "rank_min": player.get("rank_min"),
        "rank_max": player.get("rank_max"),
        "rank_std": player.get("rank_std"),
        "opponent": player.get("opponent"),

        # Recent actual NFL usage
        "recent_usage": player.get("recent_usage"),
    }


def save_weekly_snapshot(
    season,
    week,
    roster,
    optimized,
    comparison=None,
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