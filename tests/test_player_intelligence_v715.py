"""Offline tests for player-stats completeness, news targeting, and recap gating."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from espn_player_stats import parse_season_stats, parse_gamelog, public_player_data
from matchup_live import _clean_breakdown, player_profile
from news_feed import roster_news, _google_news_url
from weekly_recap import recap_eligibility, recap_status, generate_weekly_recap


class SeasonStatTests(unittest.TestCase):
    def test_public_espn_stats_are_full_season_not_one_roster_week(self):
        fixture = {'categories': [{'name':'rushing', 'labels':['CAR','YDS','TD'], 'totals':['66','290','2']},
                                  {'name':'receiving','labels':['REC','YDS','TD'], 'totals':['22','188','1']}],
                   'glossary': [{'abbreviation':'YDS','displayName':'Yards'}]}
        stats = parse_season_stats(fixture)
        self.assertIn(('Rushing yards', 290), [(s['label'], s['value']) for s in stats])
        self.assertIn(('Receiving yards', 188), [(s['label'], s['value']) for s in stats])
        self.assertEqual(len(stats),6)

    def test_numeric_espn_scoring_counters_not_shown_raw(self):
        rows=_clean_breakdown({'rushingAttempts':1,'rushingYards':5,'27':1,'47':14,'48':7,'49':3,
                               '50':2,'51':1,'210':1,'213':3})
        self.assertTrue(rows)
        self.assertFalse(any(r['label'].isdigit() for r in rows))
        self.assertNotIn('47',[r['key'] for r in rows])
        self.assertIn('Rushing yards', [r['label'] for r in rows])
        self.assertIn('Receiving first downs',[r['label'] for r in rows])

    def test_profile_prefers_public_season_totals_to_sparse_week(self):
        athlete=NS(playerId=42,name='Example Player',proTeam='CIN',position='RB',injuryStatus='ACTIVE',
                   total_points=35.0,avg_points=11.0,stats={6:{'points':10,'breakdown':{'rushingYards':5}}})
        league=NS(year=2026,teams=[NS(roster=[athlete])],current_week=7)
        fake={'season_stats':[{'key':'rushing:CAR','label':'Carries','value':80,'category':'rushing'}],
              'recent_games':[], 'stats_source':'ESPN NFL public season totals'}
        with patch('league_intelligence._get_box_scores',return_value=[]),\
             patch('matchup_live.player_public_bio',return_value={}),\
             patch('espn_player_stats.public_player_data',return_value=fake):
            profile=player_profile(league,42,7)
        self.assertTrue(profile['season_stats_complete'])
        self.assertEqual(profile['available_weeks_stat_totals'][0]['value'],80)
        self.assertEqual(profile['stat_coverage_weeks'],1)
        self.assertEqual(profile['season_stats_source'],'ESPN NFL public season totals')

    def test_public_gamelog_excludes_games_without_result(self):
        fixture={'labels':['ATT','YDS'],'events':{'1':{'gameDate':'2026-10-19','gameResult':'W','opponent':{'abbreviation':'NO'}},
                                                  '2':{'gameDate':'2026-10-26','opponent':{'abbreviation':'KC'}}},
                 'seasonTypes':[{'displayName':'2026 Regular Season','categories':[{'events':[
                      {'eventId':'1','stats':['19','114']},{'eventId':'2','stats':['5','20']}]}]}]}
        rows=parse_gamelog(fixture)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['opponent'],'NO')
        self.assertEqual(rows[0]['fantasy_points'],None)

    def test_public_player_source_failure_not_fake(self):
        def fail(url):
            raise ConnectionError('offline')
        data=public_player_data(42,2026,fetcher=fail)
        self.assertEqual(data['season_stats'],[])
        self.assertEqual(data['stats_source'],'unavailable')


class TargetedNewsTests(unittest.TestCase):
    def test_individual_google_rss_returns_relevant_verified_headline(self):
        feed=b'''<rss><channel><item><title>Joe Burrow fantasy football start advice for Week 7</title>
            <link>https://football.example.com/burrow-start</link></item><item>
            <title>Broadcaster Joe Burrow signs CBS TV contract</title>
            <link>https://football.example.com/unrelated</link></item></channel></rss>'''
        seen=[]
        def search(url):
            seen.append(url)
            return feed
        out=roster_news([{'name':'Joe Burrow','espn_id':42,'position':'QB'}],
                feed_fetcher=lambda _:b'<rss><channel/></rss>',
                athlete_fetcher=lambda _:b'{"articles":[]}',search_fetcher=search)
        self.assertEqual(out['matched_players'],1)
        self.assertEqual(len(out['players'][0]['articles']),1)
        self.assertIn('Joe+Burrow',seen[0])
        self.assertIn('news.google.com/search',out['players'][0]['coverage_url'])

    def test_no_real_article_still_gives_search_link_not_fake_news(self):
        out=roster_news([{'name':'Unknown Player','position':'RB','espn_id':123}],
                  feed_fetcher=lambda _:b'<rss><channel/></rss>',
                  athlete_fetcher=lambda _:b'{"articles":[]}',
                  search_fetcher=lambda _:b'<rss><channel/></rss>')
        row=out['players'][0]
        self.assertEqual(row['status'],'not_found')
        self.assertEqual(row['articles'],[])
        self.assertTrue(row['coverage_url'].startswith('https://news.google.com/search?'))


class WeeklyRecapTests(unittest.TestCase):
    @staticmethod
    def nfl_games(state='FINAL'):
        return {'status':'available','games':{str(i):{'state':state} for i in range(24)}}

    def test_refuses_partial_or_in_play_nfl_week(self):
        self.assertEqual(recap_eligibility({'status':'unavailable','games':{}})[0],'game_status_unavailable')
        self.assertEqual(recap_eligibility({'status':'available','games':{'A':{'state':'FINAL'}}})[0],'schedule_incomplete')
        statuses=self.nfl_games()
        statuses['games']['0']['state']='LIVE'
        self.assertEqual(recap_eligibility(statuses)[0],'games_in_progress')
        self.assertEqual(recap_eligibility(self.nfl_games())[0],'ready')

    def test_recap_requires_final_fantasy_score(self):
        league=NS(year=2026,current_week=7)
        with tempfile.TemporaryDirectory() as directory:
            result=recap_status(league,1,7,game_info=self.nfl_games(),
                strategy={'status':'available','my_team':{'points_scored':None},'opponent':{'points_scored':120}},
                recap_dir=Path(directory))
            self.assertEqual(result['status'],'fantasy_scores_pending')

    def test_one_paid_recap_then_cached_for_same_week(self):
        league=NS(year=2026,current_week=7)
        matchup={'week':7,'status':'available',
                 'my_team':{'name':'Serena','points_scored':122,'roster':[{'name':'Joe Burrow','slot':'QB','starter':True,'actual_points':20,'projected_points':19}]},
                 'opponent':{'name':'Rival','points_scored':110,'roster':[{'name':'Rival RB','slot':'RB','starter':True,'actual_points':15,'projected_points':17}]}}
        class Messages:
            calls=0
            def create(self,**kwargs):
                self.calls+=1
                return NS(content=[NS(type='text',text='Serena won 122 to 110. Burrow led. Maintain depth next week.')],
                          usage=NS(input_tokens=290,output_tokens=60))
        messages=Messages();client=NS(messages=messages)
        with tempfile.TemporaryDirectory() as directory:
            dest=Path(directory)
            fake_status=lambda *args,**kwargs:{'status':'ready','week':7,'season':2026,'note':'ready'}
            one=generate_weekly_recap(league,1,client,'test-model',7,
                   status_fn=fake_status,strategy_fn=lambda *args:matchup,recap_dir=dest)
            two=generate_weekly_recap(league,1,client,'test-model',7,
                   status_fn=fake_status,strategy_fn=lambda *args:matchup,recap_dir=dest)
            self.assertEqual(messages.calls,1)
            self.assertFalse(one['cached'])
            self.assertTrue(two['cached'])
            self.assertEqual(two['input_tokens'],0)
            self.assertNotIn('fake-model',json.dumps(two))


if __name__=='__main__':
    unittest.main()

class ApiEndpointTests(unittest.TestCase):
    def _client(self, messages):
        from api import create_app
        from fastapi.testclient import TestClient
        engine=NS(DECISION_CONTEXT={},APP_CONTEXT={"league":NS(year=2026,current_week=7),"team":NS(team_id=1)},
                  client=NS(messages=messages),MODEL='fake-model')
        return TestClient(create_app(lambda:engine))

    def test_player_analyze_works_with_ordinary_anthropic_client(self):
        class Messages:
            calls=0
            def create(self,**kwargs):
                self.calls+=1
                return NS(content=[NS(type='text',text='Last game and Week 7 summary grounded in ESPN facts.')],
                          usage=NS(input_tokens=100,output_tokens=25))
        messages=Messages();api=self._client(messages)
        with patch('matchup_live.player_profile',return_value={'name':'Player A','week':7,'current_week':{'projected_points':14}}):
            r=api.post('/api/players/99/analysis?week=7')
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['analysis'][:9],'Last game')
        self.assertEqual(messages.calls,1)

    def test_model_rejection_has_actionable_secret_safe_error(self):
        class Messages:
            def create(self,**kwargs):
                error=RuntimeError('private prompt and secret here')
                error.status_code=404
                raise error
        api=self._client(Messages())
        with patch('matchup_live.player_profile',return_value={'name':'Player B','week':7}):
            r=api.post('/api/players/101/analysis')
        self.assertEqual(r.status_code,502)
        self.assertIn('ANTHROPIC_MODEL',r.json()['detail'])
        self.assertNotIn('secret',r.json()['detail'])
