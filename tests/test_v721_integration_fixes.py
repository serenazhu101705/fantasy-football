"""Screenshots-driven contracts: real ESPN weekly scores, lineup, ranked team news."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from api import create_app
from matchup_live import _completed_espn_fantasy_scores, player_profile
from lineup_explanations import explain_lineup
from news_feed import _deduplicate_and_rank, roster_news

class ScoresContractTests(unittest.TestCase):
    def test_completed_espn_week_scores_by_exact_id(self):
        seen=[]
        def get_week(league,week):
            seen.append(week)
            return [NS(home_lineup=[NS(playerId=99,points=float(week)+0.75,projected_points=10.0,
                                      breakdown={'receivingYards': 65}),
                                    NS(playerId=11,points=88)], away_lineup=[])]
        result=_completed_espn_fantasy_scores(NS(),99,7,get_scores=get_week,max_games=5)
        self.assertEqual([g['week'] for g in result],[6,5,4,3,2])
        self.assertEqual(result[0]['fantasy_points'],6.75)
        self.assertEqual(result[0]['fantasy_points_source'],'ESPN Fantasy league box score')
        self.assertEqual(seen,[6,5,4,3,2])
        # A result for any *other* ESPN ID is not allowed to leak into this player.
        self.assertEqual(_completed_espn_fantasy_scores(NS(),44,7,get_scores=get_week),[])

    def test_profile_five_scores_when_roster_contains_just_one_stat_week(self):
        player=NS(playerId=101,name='A.J. Brown',proTeam='NE',position='WR',injuryStatus='ACTIVE',
                  total_points=45.0,avg_points=9.0,stats={6:{'points':9.5,'projected_points':15.0,
                                            'breakdown': {'receivingYards':55}}})
        league=NS(year=2026,current_week=7,teams=[NS(roster=[player])])
        def boxes(league,week):
            if week==7: return []
            if week in range(2,7):
                return [NS(home_lineup=[NS(playerId=101,points=week+3.5,
                                          projected_points=15.0,breakdown={'receivingYards':55})],away_lineup=[])]
            return []
        with patch('league_intelligence._get_box_scores',side_effect=boxes), \
             patch('matchup_live.player_public_bio',return_value={}), \
             patch('espn_player_stats.public_player_data',return_value={'season_stats': [],'recent_games': [
                {'game_date':'2026-10-11','fantasy_points':None,'stats':[]}], 'stats_source':'unavailable'}), \
             patch('season_nfl_stats.current_season_player_stats',return_value={'season_stats':[], 'recent_games':[], 'weeks_covered':[], 'stats_source':'unavailable'}):
            response=player_profile(league,101,7)
        self.assertEqual([g['week'] for g in response['recent_games']],[6,5,4,3,2])
        self.assertTrue(all(g['fantasy_points'] is not None for g in response['recent_games']))
        self.assertEqual(response['recent_games'][0]['fantasy_points'],9.5)
        self.assertEqual(response['recent_games'][1]['fantasy_points'],8.5)
        self.assertNotEqual(response['recent_games'][1]['fantasy_points_source'],'ESPN NFL gamelog')

    def test_current_week_only_enters_recent_games_if_nfl_game_is_final(self):
        player=NS(playerId=101,name='A.J. Brown',proTeam='NE',position='WR',
                  total_points=48,avg_points=10,injuryStatus='ACTIVE',
                  stats={7:{'points':7.8,'projected_points':15.9,'breakdown':{'receivingYards':68}}})
        matchup=NS(home_lineup=[NS(playerId=101,name='A.J. Brown',proTeam='NE',position='WR',
                      points=7.8,projected_points=15.9,breakdown={'receivingYards':68})],away_lineup=[])
        league=NS(year=2026,current_week=7,teams=[NS(roster=[player])])
        common=[patch('league_intelligence._get_box_scores',side_effect=lambda l,w:[matchup] if w==7 else []),
                patch('matchup_live.player_public_bio',return_value={}),
                patch('espn_player_stats.public_player_data',return_value={'season_stats':[], 'recent_games':[], 'stats_source':'unavailable'}),
                patch('season_nfl_stats.current_season_player_stats',return_value={'season_stats':[], 'recent_games':[], 'weeks_covered':[], 'stats_source':'unavailable'})]
        from contextlib import ExitStack
        for status,expect in [('LIVE',False),('FINAL',True)]:
            with ExitStack() as ctx:
                for cm in common: ctx.enter_context(cm)
                ctx.enter_context(patch('matchup_live.nfl_game_statuses',return_value={'games':{'NE':{'state':status,'opponent':'CHI'}}}))
                profile=player_profile(league,101,7)
            self.assertEqual(bool(profile['recent_games']),expect)
            if expect:
                self.assertEqual(profile['recent_games'][0]['fantasy_points'],7.8)
                self.assertEqual(profile['recent_games'][0]['opponent'],'CHI')

class LineupContractTests(unittest.TestCase):
    def test_no_selected_starter_is_an_alternative_even_with_conflicting_team_label(self):
        lineup={'lineup':{
          'QB':{'name':'Joe Burrow','position':'QB','espn_id':1,'team':'CIN','espn_projection':20},
          'RB1':{'name':'Jahmyr Gibbs','position':'RB','espn_id':2,'team':'DET','espn_projection':25.8},
          'RB2':{'name':'Chase Brown','position':'RB','espn_id':3,'team':'CIN','espn_projection':15},
          'WR1':{'name':'A.J. Brown','position':'WR','espn_id':4,'espn_projection':15.8}}}
        roster=[{'name':'Joe Burrow','position':'QB','team':'Cincinnati Bengals','espn_projection':20},
                {'name':'Jahmyr Gibbs','position':'RB','team':'Detroit Lions','espn_projection':25.8},
                {'name':'Chase Brown','position':'RB','team':'Cincinnati Bengals','espn_projection':15},
                {'name':'A.J. Brown','position':'WR','espn_projection':15.8},
                {'name':'Blake Corum','position':'RB','espn_projection':10},
                {'name':'Germie Bernard','position':'WR','espn_projection':13.4}]
        result=explain_lineup(lineup,roster)['slot_explanations']
        self.assertIsNone(result['QB']['best_eligible_nonstarter'])
        self.assertEqual(result['RB2']['best_eligible_nonstarter']['name'],'Blake Corum')
        self.assertEqual(result['WR1']['best_eligible_nonstarter']['name'],'Germie Bernard')
        for slot, row in result.items():
            if row['best_eligible_nonstarter']:
                self.assertNotEqual(row['player'],row['best_eligible_nonstarter']['name'],slot)

    def test_actual_api_lineup_explanation_never_returns_self(self):
        class Engine:
            APP_CONTEXT={'league':NS(year=2026),'roster':[
               {'name':'Joe Burrow','position':'QB','espn_id':1},
               {'name':'Jahmyr Gibbs','position':'RB','espn_id':2},
               {'name':'Chase Brown','position':'RB','espn_id':3},
               {'name':'Blake Corum','position':'RB','espn_id':4}], 'current_week':7}
            DECISION_CONTEXT={}
            def tool_get_roster(self):
                return {'players':[{'name': 'Joe Burrow','position':'QB','team':'Cincinnati Bengals','espn_projection':20},
                                   {'name':'Jahmyr Gibbs','position':'RB','team':'Detroit Lions','espn_projection':26},
                                   {'name':'Chase Brown','position':'RB','team':'CIN','espn_projection':15},
                                   {'name':'Blake Corum','position':'RB','team':'LAR','espn_projection':10}]}
            def tool_get_optimal_lineup(self):
                return {'lineup':{'QB':{'name':'Joe Burrow','position':'QB','team':'CIN','espn_projection':20},
                                  'RB1':{'name':'Jahmyr Gibbs','position':'RB','team':'DET','espn_projection':26},
                                  'RB2':{'name':'Chase Brown','position':'RB','team':'CIN','espn_projection':15}},
                        'projected_total':61,'projection_coverage':'3/3'}
        with patch('league_intelligence.nfl_opponents',return_value={}):
            response=TestClient(create_app(lambda:Engine())).get('/api/lineup')
        self.assertEqual(response.status_code,200,response.text)
        explanation=response.json()['explanation']['slot_explanations']
        self.assertIsNone(explanation['QB']['best_eligible_nonstarter'])
        self.assertEqual(explanation['RB2']['best_eligible_nonstarter']['name'],'Blake Corum')

class UnifiedNewsTests(unittest.TestCase):
    def test_recent_relevant_story_outranks_older_provider_first_result(self):
        articles=[{'title':'A.J. Brown leads fantasy trade rankings Week 7','summary':'',
                   'url':'https://old.example.com/aj', 'source':'FTN · Google News','published_at':'2026-10-21T14:00:00Z'},
                  {'title':'A.J. Brown injury update ahead of Week 7','summary':'',
                   'url':'https://latest.example.com/aj','source':'Yahoo Sports · Google News','published_at':'2026-10-23T14:00:00Z'},
                  {'title':'A.J. Brown injury update ahead of Week 7 | Yahoo Sports','summary':'',
                   'url':'https://duplicate.example.com/aj','source':'ESPN Player News','published_at':'2026-10-23T13:00:00Z'}]
        rows=_deduplicate_and_rank(articles,'A.J. Brown','ACTIVE',now=datetime(2026,10,23,18,0,tzinfo=timezone.utc))
        self.assertEqual(rows[0]['url'],'https://latest.example.com/aj')
        self.assertEqual(len(rows),2)

    def test_news_combines_three_sources_and_returns_more_than_two(self):
        now=datetime.now(timezone.utc)
        date=now.strftime('%a, %d %b %Y %H:%M:%S GMT')
        feed=(f'<rss><channel><item><title>A.J. Brown fantasy trade outlook</title><link>https://feed.example.com/story</link><pubDate>{date}</pubDate></item></channel></rss>').encode()
        google=(f'<rss><channel><item><title>A.J. Brown injury practice update</title><link>https://google.example.com/story</link><pubDate>{date}</pubDate></item></channel></rss>').encode()
        import json
        espn=json.dumps({'articles':[{'headline':'A.J. Brown fantasy points outlook week 7',
          'links':{'web':{'href':'https://espn.example.com/story'}}}]}).encode()
        result=roster_news([{'name':'A.J. Brown','position':'WR','espn_id':77}],
             feed_fetcher=lambda _:feed, athlete_fetcher=lambda _:espn,search_fetcher=lambda _:google)
        self.assertEqual(len(result['players'][0]['articles']),3)
        self.assertIn('refreshed_at',result)
        self.assertTrue(result['players'][0]['articles'][0]['published_at'])

if __name__=='__main__': unittest.main()
