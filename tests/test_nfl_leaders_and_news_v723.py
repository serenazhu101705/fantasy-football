"""Regressions from the live 2026-10-23 My Team News and general-QB leader logs.

All tests are offline, with no ESPN credentials or paid API calls.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))

from nfl_leaderboards import (aggregate_leaders, format_leaderboard_answer,
                              get_nfl_leaderboard, parse_leaderboard_request)
from api import create_app


ROWS = [
    dict(season=2026, week=1, player_id='QB1', player_display_name='Quarterback One',
         recent_team='AAA', passing_yards=300, passing_tds=2, rushing_yards=20),
    dict(season=2026, week=2, player_id='QB1', player_display_name='Quarterback One',
         recent_team='AAA', passing_yards=350, passing_tds=3, rushing_yards=0),
    dict(season=2026, week=1, player_id='QB2', player_display_name='Quarterback Two',
         recent_team='BBB', passing_yards=450, passing_tds=1, rushing_yards=10),
    dict(season=2025, week=3, player_id='QB2', player_display_name='Quarterback Two',
         recent_team='BBB', passing_yards=1000, passing_tds=10, rushing_yards=0),
]


class FakeFrame:
    def __init__(self, rows):
        self.rows=rows
        self.columns=list(rows[0].keys())
    def select(self, columns):
        return FakeFrame([{key: r[key] for key in columns} for r in self.rows])
    def to_dicts(self):
        return self.rows


class LeaderboardTests(unittest.TestCase):
    def test_actual_user_phrase_and_close_variants(self):
        for q in ('who leads the league in passing yard',
                  'who leads the NFL in passing yards?',
                  'who has the most passing yards'):
            self.assertEqual(parse_leaderboard_request(q)['metric'], 'passing_yards')
        self.assertEqual(parse_leaderboard_request('top 10 rushing yards')['limit'], 10)
        self.assertIsNone(parse_leaderboard_request('who has the most fantasy league points'))
        self.assertIsNone(parse_leaderboard_request('tucker or worthy'))
        self.assertIsNone(parse_leaderboard_request('how many passing yards did Joe Burrow have last week?'))

    def test_leaderboard_aggregates_current_season_and_player_id(self):
        actual = aggregate_leaders(ROWS, 'passing_yards', season=2026)
        self.assertEqual(actual['leaders'][0]['name'], 'Quarterback One')
        self.assertEqual(actual['leaders'][0]['value'], 650)
        self.assertEqual(actual['leaders'][0]['games'], 2)
        self.assertEqual(actual['leaders'][1]['value'], 450)
        self.assertEqual(actual['through_week'], 2)
        self.assertIn('Quarterback One', format_leaderboard_answer(actual))

    def test_stat_lookup_calls_nflverse_not_anthropic_or_fantasypros(self):
        load = unittest.mock.Mock(return_value=FakeFrame(ROWS))
        with patch.dict(sys.modules, {'nfl_data': SimpleNamespace(get_weekly_player_stats=load)}):
            data = get_nfl_leaderboard('who leads in passing yards', season=2026)
        self.assertEqual(data['status'], 'available')
        self.assertEqual(load.call_count, 1)
        self.assertEqual(data['leaders'][0]['value'], 650)

    def test_other_season_never_substitutes_2026(self):
        self.assertEqual(get_nfl_leaderboard('who leads NFL passing yards in 2025',season=2026)['status'], 'unavailable')

    def test_missing_stat_column_is_not_mislabeled_or_invented(self):
        with patch.dict(sys.modules, {'nfl_data': SimpleNamespace(
            get_weekly_player_stats=lambda: FakeFrame([
                {'season':2026,'week':7,'player_display_name':'QB'}]))}):
            r=get_nfl_leaderboard('who leads NFL in passing yards',season=2026)
        self.assertEqual(r['status'],'unavailable')


class FakeEngine:
    APP_VERSION='v7.23'
    MODEL='claude-sonnet-5-5'
    def __init__(self):
        self.APP_CONTEXT={'league':SimpleNamespace(year=2026),
                          'team':SimpleNamespace(team_id=1),
                          'roster':[{'name':'Joe Burrow','position':'QB','espn_id':123,'lineup_slot':'QB'}],
                          'current_week':7}
        self.DECISION_CONTEXT={'players': []}
        self.CHAT_HISTORY=[]
    def tool_get_roster(self):
        return {'players':[{'name':'Joe Burrow','position':'QB','espn_id':123}]}


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(create_app(lambda: FakeEngine()))

    def test_my_team_news_refresh_contract_does_not_raise_typeerror(self):
        result={'status':'available','players':[{'name':'Joe Burrow','articles':[]}], 'total_players':1}
        with patch('news_feed.roster_news',return_value=result) as fn:
            response=self.client.get('/api/news/roster?refresh=true')
        self.assertEqual(response.status_code,200,response.text)
        fn.assert_called_once()
        self.assertTrue(fn.call_args.kwargs['refresh'])

    def test_outdated_news_module_returns_actionable_error_not_500(self):
        def old_roster_news(players):
            return None
        with patch('news_feed.roster_news',new=old_roster_news):
            response=self.client.get('/api/news/roster')
        self.assertEqual(response.status_code,503)
        self.assertIn('news_feed.py',response.json()['detail'])

    def test_leaderboard_api_is_read_only_and_validates_stat(self):
        payload={'status':'available','leaders':[{'rank':1,'name':'Test QB','value':1500}]}
        with patch('nfl_leaderboards.get_nfl_leaderboard',return_value=payload) as fn:
            response=self.client.get('/api/nfl/leaders?stat=passing_yards&limit=5')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['leaders'][0]['name'],'Test QB')
        self.assertEqual(fn.call_count,1)
        self.assertEqual(self.client.get('/api/nfl/leaders?stat=totally_unknown').status_code,422)


if __name__=='__main__':
    unittest.main()
