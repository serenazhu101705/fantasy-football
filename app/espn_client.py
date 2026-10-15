import os

from dotenv import load_dotenv
from espn_api.football import League


load_dotenv()


# ==================================================
# League
# ==================================================

def get_league():
    return League(
        league_id=int(
            os.getenv(
                "ESPN_LEAGUE_ID"
            )
        ),
        year=2026,
        espn_s2=os.getenv(
            "ESPN_S2"
        ),
        swid=os.getenv(
            "ESPN_SWID"
        ),
    )


# ==================================================
# My team
# ==================================================

def get_my_team(
    league,
):
    team_id = int(
        os.getenv(
            "ESPN_TEAM_ID"
        )
    )

    return next(
        team
        for team in league.teams
        if team.team_id == team_id
    )


# ==================================================
# ESPN weekly projection map
# ==================================================

def get_week_projection_map(
    league,
    week,
):
    """
    Build a map of ESPN player ID -> weekly ESPN data.

    We use box scores because the BoxPlayer objects
    contain week-specific projected fantasy points.
    """

    projection_map = {}

    try:

        box_scores = league.box_scores(
            week=week
        )

    except Exception as e:

        print(
            "Could not get ESPN weekly "
            f"box scores: {e}"
        )

        return projection_map

    for matchup in box_scores:

        # ----------------------------------------------
        # Gather both teams' weekly player objects
        # ----------------------------------------------

        lineups = []

        home_lineup = getattr(
            matchup,
            "home_lineup",
            None,
        )

        away_lineup = getattr(
            matchup,
            "away_lineup",
            None,
        )

        if home_lineup:
            lineups.extend(
                home_lineup
            )

        if away_lineup:
            lineups.extend(
                away_lineup
            )

        # ----------------------------------------------
        # Save projection information by ESPN player ID
        # ----------------------------------------------

        for player in lineups:

            player_id = getattr(
                player,
                "playerId",
                None,
            )

            if player_id is None:
                continue

            projected_points = getattr(
                player,
                "projected_points",
                None,
            )

            on_bye_week = bool(
                getattr(
                    player,
                    "on_bye_week",
                    False,
                )
            )

            projection_map[
                player_id
            ] = {
                "projected_points":
                    projected_points,

                "on_bye_week":
                    on_bye_week,
            }

    return projection_map


# ==================================================
# My roster
# ==================================================

def get_roster(
    team,
    league=None,
    week=None,
):
    """
    Return the user's current ESPN roster.

    If league and week are provided, also attach
    ESPN's week-specific projected fantasy points.

    The league/week arguments are optional so this
    function is still compatible with older callers.
    """

    projection_map = {}

    if (
        league is not None
        and week is not None
    ):

        projection_map = (
            get_week_projection_map(
                league=league,
                week=week,
            )
        )

    players = []

    for player in team.roster:

        espn_id = (
            player.playerId
        )

        weekly_data = (
            projection_map.get(
                espn_id,
                {},
            )
        )

        players.append(
            {
                "espn_id":
                    espn_id,

                "name":
                    player.name,

                "position":
                    player.position,

                "nfl_team":
                    player.proTeam,

                "lineup_slot":
                    player.lineupSlot,

                "injury_status":
                    player.injuryStatus,

                # ESPN's weekly projection.
                #
                # Mainly used as a fallback for
                # K and D/ST when FantasyPros data
                # is unavailable.
                "espn_projected_points":
                    weekly_data.get(
                        "projected_points"
                    ),

                # ESPN's own bye indication.
                "espn_on_bye":
                    weekly_data.get(
                        "on_bye_week",
                        False,
                    ),

                "rostered":
                    True,
            }
        )

    return players


# ==================================================
# Free agents
# ==================================================

def get_free_agents(
    league,
    size=100,
    week=None,
):
    """
    Return ESPN free agents.

    We request the current fantasy week so ESPN can
    provide week-specific projected fantasy points.
    """

    if week is None:

        week = getattr(
            league,
            "current_week",
            None,
        )

    # --------------------------------------------------
    # Get free agents
    # --------------------------------------------------

    try:

        if week is not None:

            espn_players = (
                league.free_agents(
                    week=week,
                    size=size,
                )
            )

        else:

            espn_players = (
                league.free_agents(
                    size=size
                )
            )

    except TypeError:

        # Some espn_api versions may not accept the
        # explicit week argument.
        espn_players = (
            league.free_agents(
                size=size
            )
        )

    players = []

    for player in espn_players:

        players.append(
            {
                "espn_id":
                    player.playerId,

                "name":
                    player.name,

                "position":
                    player.position,

                "nfl_team":
                    player.proTeam,

                "percent_owned":
                    getattr(
                        player,
                        "percent_owned",
                        None,
                    ),

                "injury_status":
                    getattr(
                        player,
                        "injuryStatus",
                        None,
                    ),

                # ESPN week-specific projection.
                "espn_projected_points":
                    getattr(
                        player,
                        "projected_points",
                        None,
                    ),

                # ESPN bye flag if available.
                "espn_on_bye":
                    bool(
                        getattr(
                            player,
                            "on_bye_week",
                            False,
                        )
                    ),

                "rostered":
                    False,
            }
        )

    return players