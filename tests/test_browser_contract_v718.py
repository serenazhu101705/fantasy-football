"""v7.18 regression tests – browser/API contracts, no live providers or paid calls."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from api import create_app, _include_profile_ids
from news_feed import news_feed_page
from player_analysis import summarize_player, clear_analysis_cache


class FakeResponse:
    content = [SimpleNamespace(type="text", text="**Season:** Solid usage. **Last game:** 9.4 points. **This week:** 13.2 projected.")]
    usage = SimpleNamespace(input_tokens=150, output_tokens=45)
    stop_reason = "end_turn"


class FakeMessages:
    calls = 0
    params = None
    def create(self, **kwargs):
        self.calls += 1
        self.params = kwargs
        return FakeResponse()


class FakeEngine:
    APP_VERSION = 'v7.18'
    MODEL = 'claude-sonnet-5-5'
    def __init__(self):
        self.APP_CONTEXT = {
            'league': SimpleNamespace(year=2026),
            'team': SimpleNamespace(team_id=7),
            'roster': [
                {'name':'A.J. Brown','position':'WR','espn_id':4567104,'lineup_slot':'WR', 'nfl_team':'NE'},
                {'name':'Joe Burrow','position':'QB','espn_id':3915511,'lineup_slot':'QB', 'nfl_team':'CIN'},
            ],
            'current_week': 7,
        }
        self.DECISION_CONTEXT = {'players': []}
        self.CHAT_HISTORY = []
        self.client = SimpleNamespace(messages=FakeMessages())

    def tool_get_roster(self):
        # Exact bug: compact LLM roster intentionally omits ESPN IDs.
        return {'players': [
            {'name':'A.J. Brown','position':'WR','lineup_slot':'WR','team':'NE','espn_projection':15.9},
            {'name':'Joe Burrow','position':'QB','lineup_slot':'QB','team':'CIN','espn_projection':19.4},
        ], 'league':'Test league','week':7,'league_settings':{'scoring':'PPR'}}

    def tool_get_optimal_lineup(self):
        return {'week':7,'lineup':{
            'WR':{'name':'A.J. Brown','position':'WR','espn_projection':15.9},
            'QB':{'name':'Joe Burrow','position':'QB','espn_projection':19.4},
        },'projected_total':35.3}


class BrowserApiContractTests(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.api = TestClient(create_app(lambda:self.engine))
        clear_analysis_cache()

    def test_overview_and_my_roster_have_clickable_ids(self):
        response = self.api.get('/api/roster')
        self.assertEqual(response.status_code,200)
        players=response.json()['players']
        self.assertEqual([p['espn_id'] for p in players],[4567104,3915511])
        self.assertEqual(self.engine.client.messages.calls,0)

    def test_optimal_lineup_contains_ids_too(self):
        response = self.api.get('/api/lineup')
        self.assertEqual(response.status_code,200)
        lineup=response.json()['lineup']
        self.assertEqual(lineup['QB']['espn_id'],3915511)
        self.assertEqual(lineup['WR']['espn_id'],4567104)
        self.assertEqual(self.engine.client.messages.calls,0)

    def test_only_unambiguous_exact_matches_can_gain_an_id(self):
        pool=[{'name':'A.J. Brown','position':'WR','espn_id':123},
              {'name':'A.J. Brown','position':'WR','espn_id':456}]
        rows=[{'name':'A.J. Brown','position':'WR'}]
        _include_profile_ids(rows,pool)
        self.assertNotIn('espn_id',rows[0])
        rows=[{'name':'Joe Burrow','position':'QB'}]
        _include_profile_ids(rows,[{'name':'Joe Burrow','position':'QB','espn_id':321}])
        self.assertEqual(rows[0]['espn_id'],321)

    def test_news_browse_endpoint_import_and_pagination(self):
        payload={'status':'available','sources':['ESPN','RotoWire','FantasyPros Articles'],
                 'unavailable_sources':[], 'filtered_nonfantasy':0,
                 'articles':[{'source':'ESPN','title':f'Fantasy injury report week 7 #{i}',
                              'url':f'https://example.com/{i}','published_at':'2026-10-23T10:00:00+00:00',
                              'summary':'Injury report'} for i in range(25)]}
        with patch('news_feed._get_feed',return_value=payload):
            first=self.api.get('/api/news/browse?page=1&page_size=10&source=ALL')
            second=self.api.get('/api/news/browse?page=2&page_size=10&source=ESPN')
            third=self.api.get('/api/news/browse?page=3&page_size=10&source=ESPN')
        self.assertEqual([r.status_code for r in (first,second,third)],[200,200,200])
        self.assertEqual([len(r.json()['articles']) for r in (first,second,third)],[10,10,5])
        self.assertEqual(first.json()['total_pages'],3)
        self.assertNotEqual(first.json()['articles'][0]['url'],second.json()['articles'][0]['url'])
        self.assertEqual(self.engine.client.messages.calls,0)

    def test_click_player_opens_profile_without_claude(self):
        expected={'name':'A.J. Brown','position':'WR','nfl_team':'NE','week':7,
                  'season_fantasy_points':35.6,'season_fantasy_ppg':12.1,
                  'current_week':{'projected_points':15.9,'fantasy_points':None}}
        with patch('matchup_live.player_profile',return_value=expected) as profile:
            response=self.api.get('/api/players/4567104?week=7')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['name'],'A.J. Brown')
        profile.assert_called_once()
        self.assertEqual(self.engine.client.messages.calls,0)

    def test_optional_player_analysis_one_call_and_cached(self):
        expected={'name':'A.J. Brown','position':'WR','nfl_team':'NE','week':7,
                  'season_fantasy_points':35.6,'season_fantasy_ppg':12.1,
                  'current_week':{'projected_points':15.9,'fantasy_points':None}}
        with patch('matchup_live.player_profile',return_value=expected):
            one=self.api.post('/api/players/4567104/analysis?week=7')
            two=self.api.post('/api/players/4567104/analysis?week=7')
        self.assertEqual(one.status_code,200,one.text)
        self.assertIn('Season',one.json()['analysis'])
        self.assertEqual(two.status_code,200)
        self.assertTrue(two.json()['cached'])
        self.assertEqual(self.engine.client.messages.calls,1)
        self.assertEqual(self.engine.client.messages.params['thinking'], {'type':'between_tools'})

    def test_pagination_ui_styles_are_installed_together(self):
        web=Path(__file__).resolve().parents[1]/'web'/'src'
        app=(web/'App.tsx').read_text()
        styles=(web/'styles.css').read_text()
        self.assertIn('pagination-bar',app)
        self.assertIn('.pagination-bar{',styles)
        self.assertIn('aria-current',app)
        self.assertIn('onOpen={openPlayer}',app)


if __name__=='__main__':unittest.main()
