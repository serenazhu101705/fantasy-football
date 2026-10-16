from leaguelogs_client import (
    attach_leaguelogs_signals,
)


players = [
    {
        "name": "Jahmyr Gibbs",
        "position": "RB",
    },
    {
        "name": "Chase Brown",
        "position": "RB",
    },
    {
        "name": "A.J. Brown",
        "position": "WR",
    },
    {
        "name": "Davante Adams",
        "position": "WR",
    },
    {
        "name": "Joe Burrow",
        "position": "QB",
    },
    {
        "name": "Jaylen Warren",
        "position": "RB",
    },
    {
        "name": "DK Metcalf",
        "position": "WR",
    },
    {
        "name": "Colston Loveland",
        "position": "TE",
    },
    {
        "name": "Jacory Croskey-Merritt",
        "position": "RB",
    },
    {
        "name": "Malik Washington",
        "position": "WR",
    },
]


attach_leaguelogs_signals(
    players
)


print(
    "\n=== LEAGUELOGS TEST ==="
)

print(
    f"{'PLAYER':25} "
    f"{'POS':5} "
    f"{'MKT #':>6} "
    f"{'POS #':>6} "
    f"{'VALUE':>7} "
    f"{'7D':>5} "
    f"{'30D':>5} "
    f"{'ROS':>7} "
    f"{'BEST':>6} "
    f"{'WORST':>6}"
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
        f"{str(data.get('ros_worst')):>6}"
    )