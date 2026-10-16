from waiver_advisor import (
    evaluate_transaction,
    evaluate_all_transactions,
    rank_transactions,
    get_drop_candidates,
)
from itertools import count


TEST_PLAYER_ID = count(1)

def make_player(
    name,
    position,
    weekly,
    ros,
    market,
    projection,
    can_play=True,
    weekly_complete=True,
    ros_complete=True,
):
    return {
        "name": name,
        "position": position,
        "espn_id": next(TEST_PLAYER_ID),

        "weekly_score":
            weekly,

        "ros_score":
            ros,

        "fp_ppr":
            projection,

        "weekly_data_complete":
            weekly_complete,

        "ros_data_complete":
            ros_complete,

        "weekly_missing_data":
            [],

        "ros_missing_data":
            [],

        "availability": {
            "can_play_this_week":
                can_play,
        },

        "injury_status":
            (
                "ACTIVE"
                if can_play
                else
                "INJURY_RESERVE"
            ),

        "signals": {
            "leaguelogs": {
                "market_value":
                    market,

                "market_move_7d":
                    10,

                "market_move_30d":
                    20,
            }
        },
        
    }


# --------------------------------------------------
# Fake legal roster
# --------------------------------------------------

roster = [

    make_player(
        "QB Starter",
        "QB",
        70,
        70,
        2000,
        20,
    ),

    make_player(
        "RB Star",
        "RB",
        85,
        90,
        5000,
        22,
    ),

    make_player(
        "RB Two",
        "RB",
        60,
        55,
        1800,
        15,
    ),

    make_player(
        "RB Bench",
        "RB",
        30,
        25,
        600,
        7,
    ),

    make_player(
        "WR One",
        "WR",
        70,
        70,
        2500,
        18,
    ),

    make_player(
        "WR Two",
        "WR",
        55,
        50,
        1600,
        14,
    ),

    make_player(
        "WR Bench",
        "WR",
        25,
        30,
        700,
        6,
    ),

    make_player(
        "TE Starter",
        "TE",
        45,
        45,
        1200,
        10,
    ),

    make_player(
        "D/ST",
        "D/ST",
        None,
        None,
        None,
        None,
    ),

    make_player(
        "Kicker",
        "K",
        8,
        None,
        None,
        8,
    ),
]



# --------------------------------------------------
# Case 1:
# Clear weekly + ROS upgrade
# --------------------------------------------------

good_add = make_player(
    "Good Waiver WR",
    "WR",
    60,
    60,
    1800,
    15,
)

result = evaluate_transaction(
    roster,
    good_add,
    roster[6],
)

print(
    "\n=== CASE 1: CLEAR UPGRADE ==="
)

print(
    "ADD:",
    result["add"]["name"],
)

print(
    "DROP:",
    result["drop"]["name"],
)

print(
    "Weekly lineup delta:",
    result[
        "weekly_lineup_delta"
    ],
)

print(
    "Projected points delta:",
    result[
        "projected_points_delta"
    ],
)

print(
    "ROS delta:",
    result[
        "ros_delta"
    ],
)

print(
    "Market value delta:",
    result[
        "market_value_delta"
    ],
)
print(
    "Recommendation:",
    result["recommendation"],
)

print(
    "Reason:",
    result["recommendation_reason"],
)


# --------------------------------------------------
# Case 2:
# Good streamer, bad long-term trade
# --------------------------------------------------

streamer = make_player(
    "One Week Streamer",
    "WR",
    75,
    20,
    500,
    19,
)

result = evaluate_transaction(
    roster,
    streamer,
    roster[5],
)

print(
    "\n=== CASE 2: SHORT-TERM ONLY ==="
)

print(
    "ADD:",
    result["add"]["name"],
)

print(
    "DROP:",
    result["drop"]["name"],
)

print(
    "Weekly lineup delta:",
    result[
        "weekly_lineup_delta"
    ],
)

print(
    "ROS delta:",
    result[
        "ros_delta"
    ],
)

print(
    "Market value delta:",
    result[
        "market_value_delta"
    ],
)
print(
    "Recommendation:",
    result["recommendation"],
)

print(
    "Reason:",
    result["recommendation_reason"],
)

# --------------------------------------------------
# Case 3:
# Injured long-term stash
# --------------------------------------------------

stash = make_player(
    "Injured Star",
    "WR",
    0,
    80,
    3000,
    0,
    can_play=False,
)

result = evaluate_transaction(
    roster,
    stash,
    roster[6],
)

print(
    "\n=== CASE 3: IR STASH ==="
)

print(
    "ADD:",
    result["add"]["name"],
)

print(
    "DROP:",
    result["drop"]["name"],
)

print(
    "Weekly lineup delta:",
    result[
        "weekly_lineup_delta"
    ],
)

print(
    "ROS delta:",
    result[
        "ros_delta"
    ],
)

print(
    "Market value delta:",
    result[
        "market_value_delta"
    ],
)

print(
    "Is stash:",
    result[
        "is_stash"
    ],
)
print(
    "Recommendation:",
    result["recommendation"],
)

print(
    "Reason:",
    result["recommendation_reason"],
)

print(
    "\n=== DROP CANDIDATES ==="
)

drop_candidates = (
    get_drop_candidates(
        roster
    )
)

for player in roster:

    print(
        f"{player['name']:20} "
        f"protected="
        f"{player.get('waiver_protected')} "
        f"reason="
        f"{player.get('protection_reason')}"
    )


print(
    "\n=== RANKED WAIVER MOVES ==="
)

waivers = [
    good_add,
    streamer,
    stash,
]

results = (
    evaluate_all_transactions(
        roster,
        waivers,
    )
)

ranked = (
    rank_transactions(
        results
    )
)

for i, result in enumerate(
    ranked[:10],
    start=1,
):

    print(
        f"\n{i}. "
        f"ADD {result['add']['name']} "
        f"/ DROP {result['drop']['name']}"
    )

    print(
        "   Recommendation:",
        result["recommendation"],
    )

    print(
        "   Weekly lineup delta:",
        result[
            "weekly_lineup_delta"
        ],
    )

    print(
        "   ROS delta:",
        result[
            "ros_delta"
        ],
    )

    print(
        "   Market delta:",
        result[
            "market_value_delta"
        ],
    )

    print(
        "   Reason:",
        result[
            "recommendation_reason"
        ],
    )
    print(
        "   Roster balance:",
        result["roster_balance_delta"],
    )

    print(
        "   Depth risk:",
        result["depth_risk"],
    )

    if result["depth_risks"]:
        print(
            "   Depth warnings:",
            ", ".join(
                result["depth_risks"]
            ),
        )