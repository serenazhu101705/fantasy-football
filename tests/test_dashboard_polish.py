"""Regression tests for v7.13. No external HTTP, ESPN credentials or Claude calls."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from league_intelligence import schedule, espn_win_probabilities, clear_cache, nfl_opponents
from news_feed import news_feed, _parse_rss
from api import create_app
from fastapi.testclient import TestClient


class DashboardPolishTests(unittest.TestCase):
    def setUp(self):
        clear_cache()

    def test_schedule_one_game_per_week_and_sorted(self):
        one = NS(team_id=1, team_name='Serena', schedule=[], scores=[101,98], outcomes=['W','L'])
        two = NS(team_id=2, team_name='Sora', schedule=[], scores=[99,108], outcomes=['L','W'])
        three = NS(team_id=3, team_name='Other', schedule=[], scores=[75, 84], outcomes=['W', 'L'])
        one.schedule=[two,two]
        two.schedule=[one,one]
        three.schedule=[three,three]
        league=NS(teams=[two,three,one])
        result = schedule(league)
        self.assertEqual(len(result['schedule']), 4)
        self.assertEqual([m['week'] for m in result['schedule']], [1,1,2,2])
        self.assertEqual(len([m for m in result['schedule'] if not m['bye'] and m['week']==1]), 1)
        self.assertEqual(result['schedule'][0]['score'], 101)
        self.assertEqual(result['schedule'][0]['opponent_score'], 99)
        filtered=schedule(league, 2)
        self.assertEqual(len(filtered['schedule']),2)
        self.assertTrue(all(m['team_id'] == 2 for m in filtered['schedule']))

    def test_espn_win_probability_exact_field_and_caching(self):
        calls=[]
        def fetch(**kwargs):
            calls.append(kwargs)
            return {'schedule': [{'home': {'teamId': 1, 'winProbability': .735},
                                  'away': {'teamId':2, 'winProbability': .265}}]}
        league=NS(current_week=7,currentMatchupPeriod=7, settings=NS(matchup_periods={7:[7]}),
                  espn_request=NS(league_get=fetch))
        first=espn_win_probabilities(league,7)
        second=espn_win_probabilities(league,7)
        self.assertEqual(first['teams'][1],73.5)
        self.assertEqual(first['teams'][2],26.5)
        self.assertEqual(len(calls),1)
        self.assertEqual(json.loads(calls[0]['headers']['x-fantasy-filter'])['schedule']['filterMatchupPeriodIds']['value'],[7])
        self.assertEqual(first, second)

    def test_missing_espn_probability_never_becomes_zero(self):
        league=NS(current_week=7,currentMatchupPeriod=7,settings=NS(matchup_periods={7:[7]}),
                  espn_request=NS(league_get=lambda **kw:{'schedule':[{'home':{'teamId':1},'away':{'teamId':2}}]}))
        result=espn_win_probabilities(league)
        self.assertEqual(result['status'],'unavailable')
        self.assertNotIn(1,result['teams'])

    def test_future_week_no_api_call(self):
        def fail(**kw):
            raise AssertionError('should not fetch')
        league=NS(current_week=7,espn_request=NS(league_get=fail))
        self.assertEqual(espn_win_probabilities(league,8)['status'],'unavailable')

    def test_rss_multi_provider_and_no_full_article(self):
        xml=b'''<?xml version="1.0"?><rss><channel><item><title>Week 7 sleepers</title>
          <link>https://news.example.org/nfl/7</link><description>&lt;p&gt;Injury and lineup &lt;b&gt;notes&lt;/b&gt;&lt;/p&gt;</description>
          <pubDate>Thu, 22 Oct 2026 15:00:00 GMT</pubDate></item></channel></rss>'''
        rows=_parse_rss(xml,'ESPN')
        self.assertEqual(rows[0]['summary'],'Injury and lineup notes')
        self.assertEqual(rows[0]['source'],'ESPN')
        self.assertEqual(rows[0]['published_at'][:10],'2026-10-22')
        result=news_feed(fetcher=lambda _:xml,refresh=True)
        self.assertEqual(result['status'],'available')
        self.assertEqual(len(result['articles']),1)
        self.assertEqual(result['articles'][0]['title'],'Week 7 sleepers')

    def test_malformed_news_source_partial_failure(self):
        def fetch(url):
            if 'espn' in url:
                raise ConnectionError('offline')
            return b'<rss><channel><item><title>Roster changes</title><link>https://rotowire.com/story</link></item></channel></rss>'
        result=news_feed(fetcher=fetch, refresh=True)
        self.assertEqual(result['unavailable_sources'], ['ESPN'])
        self.assertEqual(result['articles'][0]['source'],'RotoWire')

    def test_api_news_requires_no_engine(self):
        with patch('news_feed.news_feed',return_value={'status':'available','articles':[]}) as fn:
            client=TestClient(create_app(lambda: (_ for _ in ()).throw(RuntimeError('engine accessed'))))
            self.assertEqual(client.get('/api/news').status_code,200)
            fn.assert_called_once_with(limit=24)

if __name__=='__main__':
    unittest.main()
