from collections import defaultdict

from fantasypros_client import (
    get_projections_for_players,
    get_consensus_rankings,
    get_rankings_for_player,
)
from leaguelogs_client import (
    attach_leaguelogs_signals,
)

from player_ids import get_espn_to_fantasypros_map
from nfl_data import get_recent_usage
from player_value import add_player_values


def normalize_fp_id(value):
    """
    Normalize FantasyPros IDs so ints, floats, and
    numeric strings all compare the same way.
    """

    if value is None:
        return None

    try:
        return int(float(value))

    except (
        TypeError,
        ValueError,
    ):
        return str(value).strip()
    
def enrich_players(
    players,
    season,
    week,
    include_player_specific=True,
):
    """
    Take raw ESPN players and add:

        fantasypros_id

        weekly projections

        weekly ECR

        rest-of-season ECR

        recent NFL usage

        weekly_score

        ros_score

    The players list is updated in place and returned.
    """

    # --------------------------------------------------
    # Map ESPN IDs -> FantasyPros IDs
    # --------------------------------------------------

    id_map = get_espn_to_fantasypros_map()

    for player in players:
        player["fantasypros_id"] = normalize_fp_id(
            id_map.get(
                str(player["espn_id"])
            )
        )

    # --------------------------------------------------
    # Group mapped players by position
    # --------------------------------------------------

    players_by_position = defaultdict(list)

    for player in players:

        fp_id = player.get(
            "fantasypros_id"
        )

        if fp_id is None:
            continue

        position = player["position"]

        # FantasyPros uses DST instead of D/ST
        if position == "D/ST":
            position = "DST"

        players_by_position[
            position
        ].append(fp_id)

    # --------------------------------------------------
    # Weekly FantasyPros projections
    # --------------------------------------------------

    projections = {}

    for position, fp_ids in (
        players_by_position.items()
    ):

        try:
            result = (
                get_projections_for_players(
                    season=season,
                    week=week,
                    position=position,
                    player_ids=fp_ids,
                )
            )

            for result_player in result:

                fp_id = normalize_fp_id(
                    result_player.get("fpid")
                )

                if fp_id is not None:
                    projections[
                        fp_id
                    ] = result_player["stats"]

        except Exception as e:
            print(
                f"Could not get projections for "
                f"{position}: {e}"
            )

    # --------------------------------------------------
    # Weekly FantasyPros ECR
    # --------------------------------------------------

    ecr_data = {}

    for position in (
        players_by_position.keys()
    ):

        try:
            result = (
                get_consensus_rankings(
                    season=season,
                    week=week,
                    position=position,
                    scoring="PPR",
                )
            )

            for result_player in (
                result.get("players", [])
            ):

                fp_id = normalize_fp_id(
                    result_player.get(
                        "player_id"
                    )
                )

                if fp_id is None:
                    continue

                ecr_data[fp_id] = {
                    "rank_ecr":
                        result_player.get(
                            "rank_ecr"
                        ),

                    "rank_min":
                        result_player.get(
                            "rank_min"
                        ),

                    "rank_max":
                        result_player.get(
                            "rank_max"
                        ),

                    "rank_std":
                        result_player.get(
                            "rank_std"
                        ),

                    "pos_rank":
                        result_player.get(
                            "pos_rank"
                        ),

                    "start_sit_grade":
                        result_player.get(
                            "start_sit_grade"
                        ),

                    "opponent":
                        result_player.get(
                            "player_opponent"
                        ),

                    "experts":
                        result_player.get(
                            "experts",
                            {},
                        ),
                }

        except Exception as e:
            print(
                f"Could not get ECR for "
                f"{position}: {e}"
            )

    # --------------------------------------------------
    # Player-specific rankings
    #
    # Used for:
    #   1. Weekly ECR fallback
    #   2. Rest-of-season rankings
    #
    # This can be disabled for large free-agent pools
    # so we do not make dozens of API requests.
    # --------------------------------------------------

    ros_data = {}

    players_for_specific_rankings = (
        players
        if include_player_specific
        else []
    )

    for player in players_for_specific_rankings:

        fp_id = player.get(
            "fantasypros_id"
        )

        position = player[
            "position"
        ]

        if fp_id is None:
            continue

        # For now ROS is only used for
        # offensive skill positions.
        if position not in (
            "QB",
            "RB",
            "WR",
            "TE",
        ):
            continue

        try:
            result = (
                get_rankings_for_player(
                    season=season,
                    week=week,
                    player_id=fp_id,
                )
            )

            for ranked_player in (
                result.get("players", [])
            ):

                ranked_id = normalize_fp_id(
                    ranked_player.get("id")
                )

                if ranked_id != fp_id:
                    continue

                fp_position = (
                    ranked_player.get(
                        "position_id"
                    )
                )

                rank = ranked_player.get(
                    "rank",
                    {},
                )

                ecr = rank.get(
                    "ECR",
                    {},
                )

                ecr_min = rank.get(
                    "ECR_MIN",
                    {},
                )

                ecr_max = rank.get(
                    "ECR_MAX",
                    {},
                )

                ecr_std = rank.get(
                    "ECR_STD",
                    {},
                )

                # --------------------------------------
                # Weekly ECR fallback
                # --------------------------------------

                if fp_id not in ecr_data:

                    weekly_ecr = (
                        ecr
                        .get("PPR", {})
                        .get(fp_position)
                    )

                    weekly_min = (
                        ecr_min
                        .get("PPR", {})
                        .get(fp_position)
                    )

                    weekly_max = (
                        ecr_max
                        .get("PPR", {})
                        .get(fp_position)
                    )

                    weekly_std = (
                        ecr_std
                        .get("PPR", {})
                        .get(fp_position)
                    )

                    if weekly_ecr is not None:

                        ecr_data[
                            fp_id
                        ] = {
                            "rank_ecr":
                                weekly_ecr,

                            "rank_min":
                                weekly_min,

                            "rank_max":
                                weekly_max,

                            "rank_std":
                                weekly_std,

                            "pos_rank":
                                (
                                    f"{fp_position}"
                                    f"{weekly_ecr}"
                                ),

                            "start_sit_grade":
                                None,

                            "opponent":
                                None,

                            "experts":
                                {},
                        }

                # --------------------------------------
                # Rest-of-season ECR
                # --------------------------------------

                ros_ecr = (
                    ecr
                    .get(
                        "ROS-PPR",
                        {},
                    )
                    .get(fp_position)
                )

                ros_min = (
                    ecr_min
                    .get(
                        "ROS-PPR",
                        {},
                    )
                    .get(fp_position)
                )

                ros_max = (
                    ecr_max
                    .get(
                        "ROS-PPR",
                        {},
                    )
                    .get(fp_position)
                )

                ros_std = (
                    ecr_std
                    .get(
                        "ROS-PPR",
                        {},
                    )
                    .get(fp_position)
                )

                if ros_ecr is not None:

                    ros_data[
                        fp_id
                    ] = {
                        "ros_ecr":
                            ros_ecr,

                        "ros_min":
                            ros_min,

                        "ros_max":
                            ros_max,

                        "ros_std":
                            ros_std,

                        "ros_pos_rank":
                            (
                                f"{fp_position}"
                                f"{ros_ecr}"
                            ),
                    }

        except Exception as e:
            print(
                f"Could not get player "
                f"rankings for "
                f"{player['name']}: {e}"
            )

    # --------------------------------------------------
    # Attach weekly projections
    # --------------------------------------------------

    for player in players:

        fp_id = player.get(
            "fantasypros_id"
        )

        normalized_fp_id = normalize_fp_id(
            fp_id
        )

        stats = projections.get(
            normalized_fp_id
        )

        if stats:

            player["fp_std"] = (
                stats.get("points")
            )

            player["fp_half"] = (
                stats.get(
                    "points_half"
                )
            )

            player["fp_ppr"] = (
                stats.get(
                    "points_ppr"
                )
            )

        else:

            player["fp_std"] = None
            player["fp_half"] = None
            player["fp_ppr"] = None

    # --------------------------------------------------
    # Attach weekly ECR
    # --------------------------------------------------

    for player in players:

        fp_id = player.get(
            "fantasypros_id"
        )

        ranking = ecr_data.get(
            normalize_fp_id(fp_id)
        )

        if ranking:

            player["ecr"] = (
                ranking["rank_ecr"]
            )

            player["rank_min"] = (
                ranking["rank_min"]
            )

            player["rank_max"] = (
                ranking["rank_max"]
            )

            player["rank_std"] = (
                ranking["rank_std"]
            )

            player["pos_rank"] = (
                ranking["pos_rank"]
            )

            player[
                "start_sit_grade"
            ] = ranking[
                "start_sit_grade"
            ]

            player["opponent"] = (
                ranking["opponent"]
            )

            player[
                "expert_rankings"
            ] = ranking["experts"]

        else:

            player["ecr"] = None
            player["rank_min"] = None
            player["rank_max"] = None
            player["rank_std"] = None
            player["pos_rank"] = None

            player[
                "start_sit_grade"
            ] = None

            player["opponent"] = None

            player[
                "expert_rankings"
            ] = {}

    # --------------------------------------------------
    # Attach rest-of-season rankings
    # --------------------------------------------------

    for player in players:

        fp_id = player.get(
            "fantasypros_id"
        )

        ros = ros_data.get(
            normalize_fp_id(fp_id)
        )

        if ros:

            player["ros_ecr"] = (
                ros["ros_ecr"]
            )

            player[
                "ros_pos_rank"
            ] = ros[
                "ros_pos_rank"
            ]

            player["ros_min"] = (
                ros["ros_min"]
            )

            player["ros_max"] = (
                ros["ros_max"]
            )

            player["ros_std"] = (
                ros["ros_std"]
            )

        else:

            player["ros_ecr"] = None

            player[
                "ros_pos_rank"
            ] = None

            player["ros_min"] = None
            player["ros_max"] = None
            player["ros_std"] = None

    # --------------------------------------------------
    # Attach recent NFL usage
    # --------------------------------------------------

    for player in players:

        if player["position"] in (
            "K",
            "D/ST",
        ):

            player[
                "recent_usage"
            ] = None

            continue

        try:

            player[
                "recent_usage"
            ] = get_recent_usage(
                player_name=(
                    player["name"]
                ),
                current_week=week,
                last_n=3,
            )

        except Exception as e:

            print(
                f"Could not get recent "
                f"stats for "
                f"{player['name']}: {e}"
            )

            player[
                "recent_usage"
            ] = None

    # --------------------------------------------------
    # Attach independent LeagueLogs market + ROS signals
    # --------------------------------------------------

    try:
        attach_leaguelogs_signals(
            players
        )

    except Exception as e:
        print(
            f"Could not get LeagueLogs data: {e}"
        )

        # Keep the rest of the pipeline working even if
        # LeagueLogs is temporarily unavailable.
        for player in players:
            player.setdefault(
                "signals",
                {},
            )

            player["signals"][
                "leaguelogs"
            ] = {
                "matched": False,
            }

    
    # --------------------------------------------------
    # Calculate weekly + ROS value scores
    # --------------------------------------------------
    
    add_player_values(
        players,
        current_week=week
    )

    return players

def add_specific_rankings(
    players,
    season,
    week,
):
    """
    Add player-specific FantasyPros ranking data to
    players that have already gone through the basic
    enrichment pipeline.

    This does NOT refetch projections or bulk ECR.
    """

    for player in players:

        fp_id = player.get(
            "fantasypros_id"
        )

        position = player.get(
            "position"
        )

        if fp_id is None:
            continue

        if position not in (
            "QB",
            "RB",
            "WR",
            "TE",
        ):
            continue

        try:
            result = get_rankings_for_player(
                season=season,
                week=week,
                player_id=fp_id,
            )

            for ranked_player in result.get(
                "players", []
            ):

                ranked_id = normalize_fp_id(
                    ranked_player.get("id")
                )

                normalized_fp_id = normalize_fp_id(
                    fp_id
                )

                if ranked_id != normalized_fp_id:
                    continue

                fp_position = ranked_player.get(
                    "position_id"
                )

                rank = ranked_player.get(
                    "rank",
                    {},
                )

                ecr = rank.get(
                    "ECR",
                    {},
                )

                ecr_min = rank.get(
                    "ECR_MIN",
                    {},
                )

                ecr_max = rank.get(
                    "ECR_MAX",
                    {},
                )

                ecr_std = rank.get(
                    "ECR_STD",
                    {},
                )

                # --------------------------------------
                # Weekly fallback
                # --------------------------------------

                if player.get("ecr") is None:

                    weekly_ecr = (
                        ecr
                        .get("PPR", {})
                        .get(fp_position)
                    )

                    if weekly_ecr is not None:

                        player["ecr"] = (
                            weekly_ecr
                        )

                        player["pos_rank"] = (
                            f"{fp_position}"
                            f"{weekly_ecr}"
                        )

                        player["rank_min"] = (
                            ecr_min
                            .get("PPR", {})
                            .get(fp_position)
                        )

                        player["rank_max"] = (
                            ecr_max
                            .get("PPR", {})
                            .get(fp_position)
                        )

                        player["rank_std"] = (
                            ecr_std
                            .get("PPR", {})
                            .get(fp_position)
                        )

                # --------------------------------------
                # ROS
                # --------------------------------------

                ros_ecr = (
                    ecr
                    .get("ROS-PPR", {})
                    .get(fp_position)
                )

                if ros_ecr is not None:

                    player["ros_ecr"] = (
                        ros_ecr
                    )

                    player[
                        "ros_pos_rank"
                    ] = (
                        f"{fp_position}"
                        f"{ros_ecr}"
                    )

                    player["ros_min"] = (
                        ecr_min
                        .get("ROS-PPR", {})
                        .get(fp_position)
                    )

                    player["ros_max"] = (
                        ecr_max
                        .get("ROS-PPR", {})
                        .get(fp_position)
                    )

                    player["ros_std"] = (
                        ecr_std
                        .get("ROS-PPR", {})
                        .get(fp_position)
                    )

        except Exception as e:

            print(
                f"Could not get specific rankings "
                f"for {player['name']}: {e}"
            )

    # Rankings changed, so recalculate values.
    add_player_values(
        players,
        current_week=week
    )

    return players