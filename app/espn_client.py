import os
from dotenv import load_dotenv
from espn_api.football import League

load_dotenv()


def get_league():
    return League(
        league_id=int(os.getenv("ESPN_LEAGUE_ID")),
        year=2026,
        espn_s2=os.getenv("ESPN_S2"),
        swid=os.getenv("ESPN_SWID"),
    )


def get_my_team(league):
    team_id = int(os.getenv("ESPN_TEAM_ID"))

    return next(
        team for team in league.teams
        if team.team_id == team_id
    )


def get_roster(team):
    players = []

    for player in team.roster:
        players.append({
            "espn_id": player.playerId,
            "name": player.name,
            "position": player.position,
            "nfl_team": player.proTeam,
            "lineup_slot": player.lineupSlot,
            "injury_status": player.injuryStatus,
            "rostered": True,
        })

    return players


def get_free_agents(league, size=100):
    players = []

    for player in league.free_agents(size=size):
        players.append({
            "espn_id": player.playerId,
            "name": player.name,
            "position": player.position,
            "nfl_team": player.proTeam,
            "percent_owned": player.percent_owned,
            "rostered": False,
        })

    return players