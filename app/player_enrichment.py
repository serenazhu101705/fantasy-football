from collections import defaultdict
import re
import unicodedata

from fantasypros_client import (
    get_projections_for_players,
    get_position_projections,
    get_consensus_rankings,
    get_cached_rankings_for_player,
    get_cached_consensus_rankings,
)

from leaguelogs_client import (
    attach_leaguelogs_signals,
)

from nfl_data import (
    get_recent_usage,
    is_team_on_bye,
)

from player_ids import (
    get_espn_to_fantasypros_map,
)

from player_value import (
    add_player_values,
)

from injury_monitor import (
    attach_injury_signals,
)


# ==================================================
# FantasyPros ID helpers
# ==================================================

def normalize_fp_id(
    value,
):
    """
    Normalize FantasyPros IDs so ints, floats, and
    numeric strings all compare the same way.
    """

    if value is None:
        return None

    try:
        return int(
            float(
                value
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        return str(
            value
        ).strip()


# ==================================================
# Name / team matching helpers
# ==================================================

def normalize_player_name(
    value,
):
    """
    Normalize player names so ESPN and FantasyPros
    names can be compared reliably.
    """

    if value is None:
        return ""

    name = unicodedata.normalize(
        "NFKD",
        str(value),
    )

    name = (
        name
        .encode(
            "ascii",
            "ignore",
        )
        .decode()
    )

    name = name.lower()

    # Remove common suffixes.
    name = re.sub(
        r"\b(jr|sr|ii|iii|iv)\b\.?",
        "",
        name,
    )

    # Remove punctuation / normalize whitespace.
    name = re.sub(
        r"[^a-z0-9]+",
        " ",
        name,
    )

    return " ".join(
        name.split()
    )


def normalize_team(
    value,
):
    """
    Normalize common NFL abbreviation differences
    between data providers.
    """

    if value is None:
        return None

    team = str(
        value
    ).upper().strip()

    aliases = {
        # Rams
        "LA": "LAR",
        "LAR": "LAR",

        # Commanders
        "WAS": "WSH",
        "WSH": "WSH",

        # Jaguars
        "JAC": "JAX",
        "JAX": "JAX",

        # Raiders
        "OAK": "LV",
        "LVR": "LV",
        "LV": "LV",
    }

    return aliases.get(
        team,
        team,
    )


def _projection_name(
    projection,
):
    """
    FantasyPros has used slightly different field names
    across endpoints. Handle common variants.
    """

    return (
        projection.get(
            "name"
        )
        or projection.get(
            "player_name"
        )
        or projection.get(
            "short_name"
        )
    )


def _projection_team(
    projection,
):
    """
    Return normalized team abbreviation from a
    FantasyPros projection row.
    """

    return normalize_team(
        projection.get(
            "team_id"
        )
        or projection.get(
            "player_team_id"
        )
        or projection.get(
            "team"
        )
    )


def find_special_projection(
    player,
    projection_rows,
):
    """
    Match ESPN K/DST players to FantasyPros full-position
    projection rows.

    K:
        match by normalized player name

    D/ST:
        match by NFL team abbreviation
    """

    position = player.get(
        "position"
    )

    # --------------------------------------------------
    # Defense
    # --------------------------------------------------

    if position == "D/ST":

        espn_team = normalize_team(
            player.get(
                "nfl_team"
            )
        )

        if espn_team is None:
            return None

        for projection in projection_rows:

            fp_team = _projection_team(
                projection
            )

            if (
                fp_team is not None
                and fp_team == espn_team
            ):
                return projection

        return None

    # --------------------------------------------------
    # Kicker
    # --------------------------------------------------

    if position == "K":

        espn_name = normalize_player_name(
            player.get(
                "name"
            )
        )

        for projection in projection_rows:

            fp_name = normalize_player_name(
                _projection_name(
                    projection
                )
            )

            if (
                espn_name
                and espn_name == fp_name
            ):
                return projection

    return None


# ==================================================
# Main enrichment pipeline
# ==================================================
def add_cached_ranking_fallback(
    player,
    season,
    week,
):
    """
    Fill missing weekly and ROS FantasyPros rankings
    from an existing individual-player cache.

    This NEVER calls the FantasyPros API.
    """

    fp_id = normalize_fp_id(
        player.get(
            "fantasypros_id"
        )
    )

    if fp_id is None:
        return player

    result = get_cached_rankings_for_player(
        season=season,
        week=week,
        player_id=fp_id,
    )

    if not result:
        return player

    for ranked_player in (
        result.get(
            "players",
            [],
        )
    ):

        ranked_id = normalize_fp_id(
            ranked_player.get(
                "id"
            )
        )

        if ranked_id != fp_id:
            continue

        position = ranked_player.get(
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

        # ----------------------------------------------
        # Weekly fallback
        # ----------------------------------------------

        if player.get(
            "ecr"
        ) is None:

            weekly_ecr = (
                ecr
                .get(
                    "PPR",
                    {},
                )
                .get(
                    position
                )
            )

            if weekly_ecr is not None:

                player[
                    "ecr"
                ] = weekly_ecr

                player[
                    "pos_rank"
                ] = (
                    f"{position}"
                    f"{weekly_ecr}"
                )

                player[
                    "rank_min"
                ] = (
                    ecr_min
                    .get(
                        "PPR",
                        {},
                    )
                    .get(
                        position
                    )
                )

                player[
                    "rank_max"
                ] = (
                    ecr_max
                    .get(
                        "PPR",
                        {},
                    )
                    .get(
                        position
                    )
                )

                player[
                    "rank_std"
                ] = (
                    ecr_std
                    .get(
                        "PPR",
                        {},
                    )
                    .get(
                        position
                    )
                )

        # ----------------------------------------------
        # ROS fallback
        # ----------------------------------------------

        if player.get(
            "ros_ecr"
        ) is None:

            ros_ecr = (
                ecr
                .get(
                    "ROS-PPR",
                    {},
                )
                .get(
                    position
                )
            )

            if ros_ecr is not None:

                player[
                    "ros_ecr"
                ] = ros_ecr

                player[
                    "ros_pos_rank"
                ] = (
                    f"{position}"
                    f"{ros_ecr}"
                )

                player[
                    "ros_min"
                ] = (
                    ecr_min
                    .get(
                        "ROS-PPR",
                        {},
                    )
                    .get(
                        position
                    )
                )

                player[
                    "ros_max"
                ] = (
                    ecr_max
                    .get(
                        "ROS-PPR",
                        {},
                    )
                    .get(
                        position
                    )
                )

                player[
                    "ros_std"
                ] = (
                    ecr_std
                    .get(
                        "ROS-PPR",
                        {},
                    )
                    .get(
                        position
                    )
                )

        break

    return player

def enrich_players(
    players,
    season,
    week,
    include_player_specific=True,
    include_injuries=True,
):
    """
    Take raw ESPN players and attach:

        FantasyPros ID
        FantasyPros weekly projection
        ESPN weekly projection fallback for K/DST
        weekly ECR
        rest-of-season ECR
        recent NFL usage
        LeagueLogs signals
        bye status
        FantasyPros injury / practice signals
        weekly score
        ROS score

    The list is modified in place and returned.
    """

    # --------------------------------------------------
    # ESPN ID -> FantasyPros ID
    # --------------------------------------------------

    id_map = (
        get_espn_to_fantasypros_map()
    )

    for player in players:

        player[
            "fantasypros_id"
        ] = normalize_fp_id(
            id_map.get(
                str(
                    player.get(
                        "espn_id"
                    )
                )
            )
        )

    # --------------------------------------------------
    # Group mapped players by FantasyPros position
    # --------------------------------------------------

    players_by_position = defaultdict(
        list
    )

    for player in players:

        fp_id = player.get(
            "fantasypros_id"
        )

        if fp_id is None:
            continue

        position = player.get(
            "position"
        )

        # FantasyPros uses DST instead of D/ST.
        if position == "D/ST":
            position = "DST"

        players_by_position[
            position
        ].append(
            fp_id
        )

    # ==================================================
    # FantasyPros weekly projections
    # ==================================================

    projections = {}

    for (
        position,
        fp_ids,
    ) in players_by_position.items():

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
                    result_player.get(
                        "fpid"
                    )
                )

                if fp_id is None:
                    continue

                projections[
                    fp_id
                ] = (
                    result_player.get(
                        "stats",
                        {},
                    )
                )

        except Exception as e:

            print(
                f"Could not get projections for "
                f"{position}: {e}"
            )

    # ==================================================
    # Special K / DST FantasyPros pools
    #
    # Only try this when:
    #
    #   1. the player is not mapped to FantasyPros
    #   2. ESPN does NOT already give us a projection
    #
    # This prevents unnecessary API requests when ESPN
    # can already provide a usable K/DST fallback.
    # ==================================================

    special_projection_pools = {
        "K": [],
        "DST": [],
    }

    needs_kicker_pool = any(
        (
            player.get(
                "position"
            ) == "K"
            and player.get(
                "fantasypros_id"
            ) is None
            and player.get(
                "espn_projected_points"
            ) is None
        )
        for player in players
    )

    needs_dst_pool = any(
        (
            player.get(
                "position"
            ) == "D/ST"
            and player.get(
                "fantasypros_id"
            ) is None
            and player.get(
                "espn_projected_points"
            ) is None
        )
        for player in players
    )

    if needs_kicker_pool:

        try:

            special_projection_pools[
                "K"
            ] = get_position_projections(
                season=season,
                week=week,
                position="K",
            )

        except Exception as e:

            print(
                "Could not get full K "
                f"projections: {e}"
            )

    if needs_dst_pool:

        try:

            special_projection_pools[
                "DST"
            ] = get_position_projections(
                season=season,
                week=week,
                position="DST",
            )

        except Exception as e:

            print(
                "Could not get full DST "
                f"projections: {e}"
            )

    # ==================================================
    # Weekly FantasyPros ECR
    # ==================================================

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
                result.get(
                    "players",
                    [],
                )
            ):

                fp_id = normalize_fp_id(
                    result_player.get(
                        "player_id"
                    )
                )

                if fp_id is None:
                    continue

                ecr_data[
                    fp_id
                ] = {
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

   # ==================================================
    # Rest-of-season FantasyPros consensus rankings
    #
    # IMPORTANT:
    # Fetch one bulk ROS ranking list per position instead
    # of making one FantasyPros API request per player.
    #
    # include_player_specific=False is still used during
    # the large free-agent first pass so we avoid doing
    # unnecessary ROS work for ~100 players.
    # ==================================================

    ros_data = {}

    if include_player_specific:

        for position in (
            players_by_position.keys()
        ):

            # ROS values are currently only useful for
            # offensive fantasy positions.
            if position not in (
                "QB",
                "RB",
                "WR",
                "TE",
            ):
                continue

            try:

                result = (
                    get_consensus_rankings(
                        season=season,
                        week=week,
                        position=position,
                        scoring="PPR",
                        ranking_type="ROS",
                    )
                )

                for result_player in (
                    result.get(
                        "players",
                        [],
                    )
                ):

                    fp_id = normalize_fp_id(
                        result_player.get(
                            "player_id"
                        )
                    )

                    if fp_id is None:
                        continue

                    ros_data[
                        fp_id
                    ] = {
                        "ros_ecr":
                            result_player.get(
                                "rank_ecr"
                            ),

                        "ros_min":
                            result_player.get(
                                "rank_min"
                            ),

                        "ros_max":
                            result_player.get(
                                "rank_max"
                            ),

                        "ros_std":
                            result_player.get(
                                "rank_std"
                            ),

                        "ros_pos_rank":
                            result_player.get(
                                "pos_rank"
                            ),
                    }

            except Exception as e:

                print(
                    f"Could not get ROS ECR for "
                    f"{position}: {e}"
                )


    # ==================================================
    # Attach weekly projections
    #
    # IMPORTANT:
    # This is OUTSIDE the player-specific rankings loop.
    # ==================================================

    for player in players:

        fp_id = player.get(
            "fantasypros_id"
        )

        normalized_fp_id = (
            normalize_fp_id(
                fp_id
            )
        )

        position = player.get(
            "position"
        )

        stats = projections.get(
            normalized_fp_id
        )

        # --------------------------------------------------
        # Special FantasyPros fallback for unmapped K/DST
        # --------------------------------------------------

        matched_projection = None

        if (
            position == "K"
            and special_projection_pools[
                "K"
            ]
        ):

            matched_projection = (
                find_special_projection(
                    player,
                    special_projection_pools[
                        "K"
                    ],
                )
            )

        elif (
            position == "D/ST"
            and special_projection_pools[
                "DST"
            ]
        ):

            matched_projection = (
                find_special_projection(
                    player,
                    special_projection_pools[
                        "DST"
                    ],
                )
            )

        if matched_projection is not None:

            matched_fp_id = normalize_fp_id(
                matched_projection.get(
                    "fpid"
                )
                or matched_projection.get(
                    "player_id"
                )
            )

            if matched_fp_id is not None:

                player[
                    "fantasypros_id"
                ] = matched_fp_id

            matched_stats = (
                matched_projection.get(
                    "stats"
                )
            )

            if matched_stats:
                stats = matched_stats

        # --------------------------------------------------
        # FantasyPros projection available
        # --------------------------------------------------

        if stats:

            standard_points = stats.get(
                "points"
            )

            half_points = stats.get(
                "points_half"
            )

            ppr_points = stats.get(
                "points_ppr"
            )

            # ----------------------------------------------
            # K / DST
            #
            # Reception scoring doesn't matter here.
            # FantasyPros may only populate "points".
            # ----------------------------------------------

            if position in (
                "K",
                "D/ST",
            ):

                player[
                    "fp_std"
                ] = standard_points

                player[
                    "fp_half"
                ] = (
                    half_points
                    if half_points is not None
                    else standard_points
                )

                player[
                    "fp_ppr"
                ] = (
                    ppr_points
                    if ppr_points is not None
                    else standard_points
                )

            else:

                player[
                    "fp_std"
                ] = standard_points

                player[
                    "fp_half"
                ] = half_points

                player[
                    "fp_ppr"
                ] = ppr_points

            player[
                "projection_source"
            ] = "FantasyPros"

        # --------------------------------------------------
        # ESPN fallback for K / DST
        # --------------------------------------------------

        else:

            espn_projection = (
                player.get(
                    "espn_projected_points"
                )
            )

            if (
                position in (
                    "K",
                    "D/ST",
                )
                and espn_projection is not None
            ):

                player[
                    "fp_std"
                ] = espn_projection

                player[
                    "fp_half"
                ] = espn_projection

                player[
                    "fp_ppr"
                ] = espn_projection

                player[
                    "projection_source"
                ] = "ESPN"

            else:

                player[
                    "fp_std"
                ] = None

                player[
                    "fp_half"
                ] = None

                player[
                    "fp_ppr"
                ] = None

                player[
                    "projection_source"
                ] = None

    # ==================================================
    # Attach weekly ECR
    # ==================================================

    for player in players:

        fp_id = player.get(
            "fantasypros_id"
        )

        ranking = ecr_data.get(
            normalize_fp_id(
                fp_id
            )
        )

        if ranking:

            player[
                "ecr"
            ] = ranking[
                "rank_ecr"
            ]

            player[
                "rank_min"
            ] = ranking[
                "rank_min"
            ]

            player[
                "rank_max"
            ] = ranking[
                "rank_max"
            ]

            player[
                "rank_std"
            ] = ranking[
                "rank_std"
            ]

            player[
                "pos_rank"
            ] = ranking[
                "pos_rank"
            ]

            player[
                "start_sit_grade"
            ] = ranking[
                "start_sit_grade"
            ]

            player[
                "opponent"
            ] = ranking[
                "opponent"
            ]

            player[
                "expert_rankings"
            ] = ranking[
                "experts"
            ]

        else:

            player[
                "ecr"
            ] = None

            player[
                "rank_min"
            ] = None

            player[
                "rank_max"
            ] = None

            player[
                "rank_std"
            ] = None

            player[
                "pos_rank"
            ] = None

            player[
                "start_sit_grade"
            ] = None

            player[
                "opponent"
            ] = None

            player[
                "expert_rankings"
            ] = {}

    # ==================================================
    # Attach ROS FantasyPros rankings
    # ==================================================

    for player in players:

        fp_id = player.get(
            "fantasypros_id"
        )

        ros = ros_data.get(
            normalize_fp_id(
                fp_id
            )
        )

        if ros:

            player[
                "ros_ecr"
            ] = ros[
                "ros_ecr"
            ]

            player[
                "ros_pos_rank"
            ] = ros[
                "ros_pos_rank"
            ]

            player[
                "ros_min"
            ] = ros[
                "ros_min"
            ]

            player[
                "ros_max"
            ] = ros[
                "ros_max"
            ]

            player[
                "ros_std"
            ] = ros[
                "ros_std"
            ]

        else:

            player[
                "ros_ecr"
            ] = None

            player[
                "ros_pos_rank"
            ] = None

            player[
                "ros_min"
            ] = None

            player[
                "ros_max"
            ] = None

            player[
                "ros_std"
            ] = None
    # ==================================================
    # Cache-only ranking fallback
    #
    # For roster-sized enrichment, use any individual
    # ranking files we already have on disk to fill gaps.
    #
    # This makes ZERO FantasyPros API requests.
    # ==================================================

    if include_player_specific:

        for player in players:

            add_cached_ranking_fallback(
                player=player,
                season=season,
                week=week,
            )
    # ==================================================
    # Recent NFL usage
    # ==================================================

    for player in players:

        if player.get(
            "position"
        ) in (
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
                    player.get(
                        "name"
                    )
                ),
                current_week=week,
                last_n=3,
            )

        except Exception as e:

            print(
                f"Could not get recent "
                f"stats for "
                f"{player.get('name')}: "
                f"{e}"
            )

            player[
                "recent_usage"
            ] = None

    # ==================================================
    # LeagueLogs
    # ==================================================

    try:

        attach_leaguelogs_signals(
            players
        )

    except Exception as e:

        print(
            f"Could not get LeagueLogs data: "
            f"{e}"
        )

        for player in players:

            player.setdefault(
                "signals",
                {},
            )

            player[
                "signals"
            ][
                "leaguelogs"
            ] = {
                "matched":
                    False,
            }

    # ==================================================
    # Bye detection
    #
    # Use both nflverse schedule data and ESPN's
    # BoxPlayer bye flag.
    # ==================================================

    for player in players:

        schedule_bye = (
            is_team_on_bye(
                player.get(
                    "nfl_team"
                ),
                week,
            )
        )

        espn_bye = bool(
            player.get(
                "espn_on_bye",
                False,
            )
        )

        player[
            "is_on_bye"
        ] = (
            schedule_bye
            or espn_bye
        )

    # ==================================================
    # Injury / practice monitoring
    # ==================================================

    if include_injuries:

        try:

            players = attach_injury_signals(
                players=players,
                season=season,
                week=week,
                include_news=True,
                force_refresh=False,
            )

        except Exception as e:

            print(
                "Could not get injury / practice data: "
                f"{e}"
            )

            for player in players:

                player.setdefault(
                    "injury_report",
                    None,
                )

                player.setdefault(
                    "injury_news",
                    [],
                )

    # ==================================================
    # Calculate weekly + ROS scores
    # ==================================================
        
    add_player_values(
        players,
        current_week=week,
    )

    return players

# ==================================================
# Add player-specific rankings to waiver shortlist
# ==================================================

def add_specific_rankings(
    players,
    season,
    week,
):
    """
    Fully enrich shortlisted waiver candidates using
    cached FantasyPros ranking data only.

    This function makes ZERO FantasyPros API requests.

    Priority:
        1. Bulk cached ROS consensus rankings
        2. Existing individual-player ranking caches
    """

    # --------------------------------------------------
    # Group shortlisted players by position
    # --------------------------------------------------

    players_by_position = defaultdict(
        list
    )

    for player in players:

        position = player.get(
            "position"
        )

        fp_id = normalize_fp_id(
            player.get(
                "fantasypros_id"
            )
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

        players_by_position[
            position
        ].append(
            player
        )

    # --------------------------------------------------
    # Try cached bulk ROS rankings first.
    #
    # IMPORTANT:
    # This NEVER contacts FantasyPros.
    # --------------------------------------------------

    for (
        position,
        position_players,
    ) in players_by_position.items():

        result = (
            get_cached_consensus_rankings(
                season=season,
                week=week,
                position=position,
                scoring="PPR",
                ranking_type="ROS",
            )
        )

        if not result:
            continue

        ros_lookup = {}

        for ranked_player in (
            result.get(
                "players",
                [],
            )
        ):

            fp_id = normalize_fp_id(
                ranked_player.get(
                    "player_id"
                )
            )

            if fp_id is None:
                continue

            ros_lookup[
                fp_id
            ] = ranked_player

        for player in position_players:

            fp_id = normalize_fp_id(
                player.get(
                    "fantasypros_id"
                )
            )

            ranked_player = (
                ros_lookup.get(
                    fp_id
                )
            )

            if ranked_player is None:
                continue

            player[
                "ros_ecr"
            ] = ranked_player.get(
                "rank_ecr"
            )

            player[
                "ros_pos_rank"
            ] = ranked_player.get(
                "pos_rank"
            )

            player[
                "ros_min"
            ] = ranked_player.get(
                "rank_min"
            )

            player[
                "ros_max"
            ] = ranked_player.get(
                "rank_max"
            )

            player[
                "ros_std"
            ] = ranked_player.get(
                "rank_std"
            )

    # --------------------------------------------------
    # Individual-player CACHE-ONLY fallback.
    #
    # This restores weekly/ROS values from caches we
    # already created earlier without contacting the API.
    # --------------------------------------------------

    for player in players:

        add_cached_ranking_fallback(
            player=player,
            season=season,
            week=week,
        )

    # --------------------------------------------------
    # Rankings/injury data may have changed player value,
    # so calculate again.
    # --------------------------------------------------

    add_player_values(
        players,
        current_week=week,
    )

    return players