"""Offline replay of ESPN objects. No real league, Claude, or external HTTP."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from league_intelligence import (matchup_strategy, standings, faab, schedule,
    team_roster, matchups, transactions, pending_claims, clear_cache)
from api import create_app


def player(name, pos, slot, proj, *, points=0, status="ACTIVE", played=0):
    return NS(playerId=abs(hash(name)) % 100000, name=name, position=pos,
        proTeam="TST", slot_position=slot, lineupSlot=slot, projected_points=proj,
        points=points, injuryStatus=status, on_bye_week=False, game_played=played)


class FakeESPN:
    def __init__(self):
        self.calls = 0
        self.spend = 7
        self.settings = NS(faab=True, acquisition_budget=100, minimum_bid=1)
        self.current_week = 7
        self.teams = [
            NS(team_id=1, team_name="Mine", wins=4, losses=2, ties=0, standing=3,
               points_for=700, points_against=630, acquisition_budget_spent=20,
               waiver_rank=2, roster=[player("Starter A", "WR", "WR", 10),
                                     player("Bench B", "WR", "BE", 14)],
               schedule=[], scores=[70, 80], outcomes=["W", "L"]),
            NS(team_id=2, team_name="Opponent", wins=5, losses=1, ties=0,
               standing=1, points_for=720, points_against=610, acquisition_budget_spent=33,
               waiver_rank=1, roster=[player("Other Player", "WR", "WR", 18)],
               schedule=[], scores=[84, 76], outcomes=["W", "W"]),
        ]
        self.teams[0].schedule = [self.teams[1]] * 8
        self.teams[1].schedule = [self.teams[0]] * 8
        self.projections = NS(
            home_team=self.teams[0], away_team=self.teams[1],
            home_projected=105, away_projected=117,
            home_score=0, away_score=0,
            home_lineup=[player("Starter A", "WR", "WR", 10),
                         player("Bench B", "WR", "BE", 14),
                         player("Starter C", "RB", "RB/WR/TE", 8)],
            away_lineup=[player("Other Player", "WR", "WR", 18)],
            is_playoff=False)

    def box_scores(self, week):
        self.calls += 1
        return [self.projections]

    def recent_activity(self, size):
        return [NS(date=1761110000000, actions=[(self.teams[0], "WAIVER ADDED", player("FA", "RB", "BE", 5), 5)])]


class FakeEngine:
    APP_VERSION = "v7.12"
    def __init__(self):
        self.APP_CONTEXT = None
        self.DECISION_CONTEXT = {}
        self.CHAT_HISTORY = []
        self.EXTERNAL_PLAYER_CACHE = {}
        self.calls = 0
        self.league = FakeESPN()

    def load_app_context(self):
        self.APP_CONTEXT = {"league": self.league, "team": self.league.teams[0], "current_week":7}

    def tool_get_roster(self):
        return {"league": "Fake", "week": 7, "players": []}

    def tool_get_optimal_lineup(self):
        return {"lineup": {}}

    def tool_get_waiver_recommendations(self, **kwargs):
        return {"candidate_pool": []}


class LeagueIntelTests(unittest.TestCase):
    def setUp(self):
        clear_cache()
        self.league = FakeESPN()

    def test_projection_math_and_lineup_gain(self):
        s = matchup_strategy(self.league, 1, 7)
        self.assertEqual(s["status"], "available")
        self.assertEqual(s["projected_margin"], -12)
        self.assertEqual(s["projected_favorite"], "opponent")
        self.assertEqual(s["target_to_exceed_opponent_projection"], 117.01)
        self.assertEqual(s["extra_points_above_my_projection_to_take_lead"], 12.01)
        self.assertIn(("Bench B", "Starter A", 4.0),
            {(m["start"], m["sit"], m["projected_gain"]) for m in s["possible_lineup_upgrades"]})
        self.assertNotIn("win_probability", s)

    def test_projected_favorite_and_tie(self):
        self.league.projections.home_projected = 130
        self.assertEqual(matchup_strategy(self.league, 1)["projected_margin"], 13)
        self.assertEqual(matchup_strategy(self.league, 1)["extra_points_above_my_projection_to_take_lead"], 0)
        clear_cache()
        self.league.projections.home_projected = 117
        self.assertEqual(matchup_strategy(self.league, 1)["projected_favorite"], "tied")
        self.assertEqual(matchup_strategy(self.league, 1)["extra_points_above_my_projection_to_take_lead"], 0.01)

    def test_no_projection_does_not_treat_missing_as_zero(self):
        self.league.projections.away_projected = None
        result = matchup_strategy(self.league, 1)
        self.assertIsNone(result["projected_margin"])
        self.assertIsNone(result["extra_points_above_my_projection_to_take_lead"])

    def test_bye_and_unavailable(self):
        self.league.projections.away_team = None
        self.assertEqual(matchup_strategy(self.league, 1)["status"], "bye")
        self.assertEqual(matchup_strategy(self.league, 4)["status"], "not_found")

    def test_no_misleading_locked_or_out_swaps(self):
        self.league.projections.home_lineup[1].injuryStatus = "OUT"
        self.assertEqual(matchup_strategy(self.league, 1)["possible_lineup_upgrades"], [])
        clear_cache()
        self.league.projections.home_lineup[1].injuryStatus = "ACTIVE"
        self.league.projections.home_lineup[0].game_played = 100
        self.assertNotIn("Starter A", [m["sit"] for m in
                         matchup_strategy(self.league, 1)["possible_lineup_upgrades"]])

    def test_future_week_not_silently_replaced_with_current_week(self):
        data = matchups(self.league, 8)
        self.assertEqual(data["status"], "future_projections_unavailable")
        self.assertEqual(self.league.calls, 0)
        self.assertEqual(matchup_strategy(self.league, 1, 8)["status"], "future_projections_unavailable")
        self.assertEqual(team_roster(self.league, 1, 8)["projection_status"], "unavailable")

    def test_cached_esnp_boxscores(self):
        matchup_strategy(self.league, 1, 7)
        matchups(self.league, 7)
        self.assertEqual(self.league.calls, 1)
        clear_cache()
        matchup_strategy(self.league, 1, 7)
        self.assertEqual(self.league.calls, 2)

    def test_standings_schedule_rosters(self):
        self.assertEqual(standings(self.league)["teams"][0]["name"], "Opponent")
        self.assertEqual(schedule(self.league, 1)["schedule"][6]["opponent"], "Opponent")
        self.assertEqual(team_roster(self.league, 2)["roster"][0]["name"], "Other Player")
        self.assertEqual(team_roster(self.league, 2)["roster"][0]["projected_points"], 18)
        with self.assertRaises(ValueError):
            team_roster(self.league, 123)

    def test_faab_and_pending_are_honestly_labeled(self):
        data = faab(self.league)
        self.assertEqual(data["teams"][0]["remaining"], 80)
        self.assertEqual(data["teams"][1]["remaining"], 67)
        self.assertEqual(pending_claims()["status"], "not_supported")
        self.assertIsNone(pending_claims()["claims"])
        self.league.settings.faab = False
        self.assertIsNone(faab(self.league)["teams"][0]["remaining"])

    def test_transactions_are_completed_only(self):
        data = transactions(self.league)
        self.assertEqual(data["status"], "completed_only")
        self.assertEqual(data["transactions"][0]["actions"][0]["bid"], 5)


class LeagueApiTests(unittest.TestCase):
    def setUp(self):
        clear_cache()
        self.engine = FakeEngine()
        self.client = TestClient(create_app(lambda: self.engine))

    def test_read_endpoints_without_claude(self):
        self.assertEqual(self.client.get('/api/league/standings').status_code, 200)
        self.assertEqual(self.client.get('/api/league/faab').json()["teams"][0]["remaining"], 80)
        self.assertEqual(len(self.client.get('/api/league/schedule').json()["schedule"]), 8)
        self.assertEqual(self.client.get('/api/league/teams/2/roster').json()["name"], "Opponent")
        self.assertEqual(self.client.get('/api/league/matchups').json()["week"], 7)
        self.assertEqual(self.client.get('/api/strategy/week').json()["projected_margin"], -12)
        self.assertEqual(self.client.get('/api/league/transactions').json()["status"], 'completed_only')
        self.assertEqual(self.client.get('/api/league/pending-claims').json()["status"], 'not_supported')
        self.assertEqual(self.engine.calls, 0)

    def test_endpoint_validation(self):
        self.assertEqual(self.client.get('/api/league/teams/100/roster').status_code, 422)
        self.assertEqual(self.client.get('/api/league/matchups?week=40').status_code, 422)
        self.assertEqual(self.client.get('/api/league/schedule?team_id=100').status_code, 422)


if __name__ == '__main__':
    unittest.main()
