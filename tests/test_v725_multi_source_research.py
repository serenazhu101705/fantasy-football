"""Offline regression tests for v7.25. No network, ESPN cookies, or Claude credits."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))

from nfl_leaderboards import parse_leaderboard_request, format_leaderboard_answer
from nfl_source_lookup import (verified_leaderboard, parse_espn_leaders,
                               espn_leaderboard, search_nfl_sources, _fetch_bytes)
import test_coach_regressions as router_tests


class MultiSourceResearchTests(unittest.TestCase):
    def test_exact_typo_from_user_is_rushing_leaderboard(self):
        self.assertEqual(parse_leaderboard_request('who leeeads the league in rushing yards')['metric'],
                         'rushing_yards')

    def test_rushing_question_resolves_before_claude_and_resets_comparison(self):
        router = router_tests.RouterRegressionTests()
        router.setUp()
        router.scope['DECISION_CONTEXT']['players'] = ['Tre Tucker', 'Xavier Worthy']
        router.scope['create_claude_message'] = lambda *a, **kw: self.fail('Claude must not be called')
        result = router.call('ask_claude', 'who leeeads the league in rushing yards', return_details=True)
        self.assertEqual(result['api_calls'], 0)
        self.assertIn('Sample RB', result['answer'])
        self.assertEqual(router.scope['DECISION_CONTEXT']['players'], [])

    def test_espn_fallback_when_nflverse_missing(self):
        calls = []
        def old(question, *, season):
            calls.append('nflverse')
            return {'status': 'unavailable', 'reason': 'missing metric column'}
        def espn(stat, *, season, limit):
            calls.append('espn')
            self.assertEqual((stat, season, limit), ('rushing_yards', 2026, 5))
            return {'status': 'available', 'source': 'ESPN NFL statistical leaders', 'season': season,
                    'stat': stat, 'stat_label': 'Rushing yards', 'unit': 'yds', 'leaders': [
                        {'rank': 1, 'name': 'Sample Runner', 'team': 'MIN', 'value': 1134.0}]}
        result = verified_leaderboard('who leeeads the league in rushing yards',
                                     nflverse=old, espn=espn)
        self.assertEqual(calls, ['nflverse', 'espn'])
        self.assertIn('Sample Runner', format_leaderboard_answer(result))
        self.assertIn('ESPN', format_leaderboard_answer(result))

    def test_espn_leaders_match_specific_stat_not_first_category(self):
        payload = {'categories': [
            {'name':'passingYards', 'leaders': [{'athlete': {'displayName': 'QB One'}, 'value': 3400}]},
            {'displayName':'Rushing Yards', 'leaders': [{'athlete': {'displayName': 'Runner One'}, 'value': 1200}]},
        ]}
        result = parse_espn_leaders(payload, 'rushing_yards', season=2026)
        self.assertEqual(result['leaders'][0]['name'], 'Runner One')
        self.assertNotIn('QB One', str(result))
        self.assertEqual(parse_espn_leaders(payload, 'receiving_yards',season=2026)['status'], 'unavailable')

    def test_malformed_or_inconsistent_espn_values_not_invented(self):
        payload = {'categories':[{'name':'rushingYards', 'leaders':[
            {'athlete':{'displayName':'Runner'}, 'displayValue':'N/A'},
            {'athlete':{'displayName':'Another'}, 'displayValue':'1,235'},
        ]}]}
        result = parse_espn_leaders(payload,'rushing_yards',season=2026)
        self.assertEqual(len(result['leaders']),1)
        self.assertEqual(result['leaders'][0]['value'],1235.)

    def test_explicit_historical_season_does_not_fetch_current(self):
        def must_not_call(*args, **kwargs):
            self.fail('Must not substitute 2026 for historical season')
        result = verified_leaderboard('who leads passing yards in 2025',nflverse=must_not_call,espn=must_not_call)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['season'], 2025)

    def test_generic_question_queries_multiple_sources_and_attributes(self):
        urls=[]
        def fetch(url):
            urls.append(url)
            if 'espn.com/apis/site/v2' in url:
                return json.dumps({'articles':[{'headline':'Trade involving Minnesota running back',
                   'links':{'web':{'href':'https://www.espn.com/trade'}}, 'published':'2026-10-22'}]}).encode()
            if 'fantasypros.com' in url:
                return b'<rss><channel><item><title>Minnesota injury news</title><link>https://www.fantasypros.com/article</link></item></channel></rss>'
            return b'<rss><channel><item><title>Minnesota injury update</title><link>https://news.google.com/item</link></item></channel></rss>'
        result=search_nfl_sources('Any Minnesota injury updates?', fetcher=fetch)
        self.assertEqual(result['status'],'available')
        self.assertEqual(len(urls),3)
        self.assertIn('ESPN NFL News',result['sources_checked'])
        self.assertIn('FantasyPros Articles RSS',result['sources_checked'])
        self.assertIn('Google News RSS',result['sources_checked'])
        self.assertTrue(all(x['url'].startswith('https://') for x in result['articles']))

    def test_generic_question_does_not_hallucinate_when_all_sources_fail(self):
        def fail(url):
            raise TimeoutError('offline')
        result=search_nfl_sources('Any Minnesota injury updates?',fetcher=fail)
        self.assertEqual(result['status'],'unavailable')
        self.assertEqual(len(result['errors']),3)
        self.assertEqual(result['articles'],[])

    def test_disallow_arbitrary_url_fetch(self):
        with self.assertRaises(ValueError):
            _fetch_bytes('https://random-example.com/private-keys')

    def test_generic_query_prefetches_research_and_reset_old_player_context(self):
        router=router_tests.RouterRegressionTests()
        router.setUp()
        router.scope['DECISION_CONTEXT']['players']=['Tre Tucker','Xavier Worthy']
        context=router.call('_derive_turn_context','what are the latest NFL injury updates?')
        self.assertEqual(context['effective_intent'],'nfl_research')
        self.assertEqual(context['players'],[])
        result=router.call('_prefetch_evidence','what are the latest NFL injury updates?',context)
        self.assertEqual(result['intent'],'nfl_research')
        self.assertTrue(result['research']['articles'])

    def test_general_nfl_questions_choose_source_lookup_not_roster_only(self):
        router=router_tests.RouterRegressionTests()
        router.setUp()
        router.scope['TOOLS'].append({'name':'search_nfl_sources'})
        selected=router.call('select_tools_for_message','who was injured in the NFL this week?')
        self.assertEqual([tool['name'] for tool in selected], ['search_nfl_sources'])
        self.assertFalse(router.call('_is_general_nfl_question', 'what happened in my league?'))

    def test_fantasy_league_scoring_still_uses_espn(self):
        router=router_tests.RouterRegressionTests()
        router.setUp()
        self.assertEqual(router.call('_intent_for_message', 'what is my league scoring format?'), 'league_info')

if __name__ == '__main__':
    unittest.main()
