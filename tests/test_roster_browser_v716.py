"""Pagination/ownership guarantees. All fixtures offline; no ESPN or Claude calls."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from browsing import waiver_browser, browse_pool
from news_feed import news_feed_page
from api import create_app
from test_api import FakeEngine


class RosterBrowserTests(unittest.TestCase):
    def setUp(self):
        self.players=[{'espn_id':i,'name':f'Player {i}', 'position':'RB' if i%2 else 'WR',
                       'nfl_team':'CIN', 'espn_projection':float(i), 'waiver_prescreen_score':i}
                      for i in range(1, 36)]
        self.context={'free_agents':self.players, 'roster':[{'name':'Player 35','espn_id':35}]}

    def test_waiver_pages_are_distinct_from_5_player_advisor(self):
        a=waiver_browser(self.context,page=1,page_size=10)
        b=waiver_browser(self.context,page=2,page_size=10)
        self.assertEqual(a['total'],34)
        self.assertEqual(a['total_pages'],4)
        self.assertEqual(len(a['candidate_pool']),10)
        self.assertEqual(len(b['candidate_pool']),10)
        self.assertFalse(set(p['espn_id'] for p in a['candidate_pool']) &
                         set(p['espn_id'] for p in b['candidate_pool']))
        self.assertNotIn('Player 35',[p['name'] for p in a['candidate_pool']])

    def test_waiver_position_filters_and_last_page(self):
        a=waiver_browser(self.context,position='RB',page=2,page_size=10)
        self.assertEqual(a['total'],17)
        self.assertEqual(len(a['candidate_pool']),7)
        self.assertTrue(all(p['position']=='RB' for p in a['candidate_pool']))
        self.assertTrue(all(p['espn_id'] is not None for p in a['candidate_pool']))
        self.assertEqual(waiver_browser(self.context,position='QB')['total'],0)

    def test_waiver_fallback_is_clearly_labeled(self):
        result=waiver_browser({'waiver_candidates':self.players[:5]},page=1)
        self.assertEqual(result['scope'],'enriched_waiver_shortlist_only')

    def test_waiver_filter_validation(self):
        with self.assertRaises(ValueError): waiver_browser(self.context,position='FAKE')
        with self.assertRaises(ValueError): waiver_browser(self.context,page=0)

    def test_news_pagination_after_source_filtering(self):
        def rss(_):
            return (b'<rss><channel>' + b''.join(
                f'<item><title>Fantasy week 7 player {i} projections</title>'
                f'<link>https://example.com/{i}</link></item>'.encode()
                for i in range(14)) + b'</channel></rss>')
        one=news_feed_page(page=1,page_size=10,source='ESPN',fetcher=rss)
        two=news_feed_page(page=2,page_size=10,source='ESPN',fetcher=rss)
        self.assertEqual(one['total'],14)
        self.assertEqual(len(one['articles']),10)
        self.assertEqual(len(two['articles']),4)
        self.assertEqual(one['total_pages'],2)
        self.assertFalse({p['url'] for p in one['articles']} & {p['url'] for p in two['articles']})
        other=news_feed_page(page=1,page_size=10,source='RotoWire',fetcher=rss)
        self.assertEqual(other['total'],0)  # Duplicate URLs retain the first publisher attribution
        with self.assertRaises(ValueError): news_feed_page(source='SUSPICIOUS',fetcher=rss)

    def test_one_espn_only_browse_load_extends_beyond_preloaded_100(self):
        initial=self.players[:12]
        league=object()
        context={'league':league,'current_week':7,'free_agents':initial,'roster':[]}
        calls=[]
        def fetch(_league,*,size,week):
            calls.append((size,week))
            return self.players
        pool,scope=browse_pool(context,fetcher=fetch)
        self.assertEqual(len(pool),35)
        self.assertEqual(scope,'on_demand_espn_pool')
        # The browser should use the cached expanded pool on subsequent pages.
        a=waiver_browser(context,page=1)
        b=waiver_browser(context,page=3)
        self.assertEqual(a['total'],35)
        self.assertEqual(b['page'],3)
        self.assertEqual(calls,[(400,7)])

    def test_espn_browse_failure_falls_back_without_changing_advisor(self):
        context={'league':object(),'current_week':7,'free_agents':self.players[:12],'roster':[]}
        def fail(*args,**kwargs): raise ConnectionError('No ESPN')
        pool,scope=browse_pool(context,fetcher=fail)
        self.assertEqual(len(pool),12)
        self.assertEqual(scope,'espn_browse_fallback_to_loaded_pool')
        self.assertEqual(waiver_browser(context)['total'],12)

    def test_browser_api_endpoints_do_not_call_claude(self):
        engine=FakeEngine()
        engine.APP_CONTEXT={'current_week':7,'free_agents':self.players,
                            'roster':[{'espn_id':35,'name':'Player 35'}]}
        client=TestClient(create_app(lambda:engine))
        resp=client.get('/api/waivers/browse?page=2&page_size=10&position=RB')
        self.assertEqual(resp.status_code,200)
        self.assertEqual(resp.json()['page'],2)
        self.assertEqual(resp.json()['total'],17)
        self.assertEqual(engine.calls,0)
        self.assertEqual(client.get('/api/waivers/browse?page=0').status_code,422)

if __name__ == '__main__': unittest.main()
