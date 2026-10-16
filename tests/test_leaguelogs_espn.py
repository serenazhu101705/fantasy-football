from espn_client import (
    get_league,
    get_my_team,
    get_roster,
    get_free_agents,
)

from leaguelogs_client import (
    attach_leaguelogs_signals,
)


def print_players(
    title,
    players,
):
    print(
        f"\n=== {title} ==="
    )

    print(
        f"{'PLAYER':25} "
        f"{'POS':5} "
        f"{'MKT #':>6} "
        f"{'POS #':>6} "
        f"{'VALUE':>7} "
        f"{'7D':>5} "
        f"{'30D':>5} "
        f"{'LL ROS':>7} "
        f"{'BEST':>6} "
        f"{'WORST':>6} "
        f"{'LL POS':>7}"
    )

    for player in players:

        data = (
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

        print(
            f"{player['name']:25} "
            f"{player['position']:5} "
            f"{str(data.get('market_rank')):>6} "
            f"{str(data.get('market_pos_rank')):>6} "
            f"{str(data.get('market_value')):>7} "
            f"{str(data.get('market_move_7d')):>5} "
            f"{str(data.get('market_move_30d')):>5} "
            f"{str(data.get('ros_consensus')):>7} "
            f"{str(data.get('ros_best')):>6} "
            f"{str(data.get('ros_worst')):>6} "
            f"{str(data.get('ros_pos_rank')):>7} "
        )


league = get_league()

my_team = get_my_team(
    league
)

roster = get_roster(
    my_team
)

free_agents = get_free_agents(
    league,
    size=100,
)


attach_leaguelogs_signals(
    roster
)

attach_leaguelogs_signals(
    free_agents
)


print_players(
    "MY ROSTER - LEAGUELOGS",
    roster,
)


# Only show free agents that LeagueLogs actually ranks.
ranked_free_agents = [
    player
    for player in free_agents
    if (
        player
        .get(
            "signals",
            {}
        )
        .get(
            "leaguelogs",
            {}
        )
        .get(
            "matched"
        )
    )
]


ranked_free_agents.sort(
    key=lambda player: (
        player
        .get(
            "signals",
            {}
        )
        .get(
            "leaguelogs",
            {}
        )
        .get(
            "market_rank"
        )
        or 9999
    )
)


print_players(
    "TOP FREE AGENTS - LEAGUELOGS",
    ranked_free_agents[:20],
)