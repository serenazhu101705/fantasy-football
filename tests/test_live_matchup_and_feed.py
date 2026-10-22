"""v7.14 features: no real ESPN, RSS, FantasyPros or Claude requests."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from matchup_live import head_to_head, nfl_game_statuses, enrich_live_players, player_public_bio, player_profile
from news_feed import is_fantasy_relevant, roster_news, news_feed
from lineup_explanations import explain_lineup

class MatchupTests(unittest.TestCase):
    def test_slot_alignment_including_two_rbs_and_flex(self):
        def p(slot,name,projection,id):
            return {'name':name,'slot':slot,'starter':True,'projected_points':projection,'espn_id':id}
        mine={'roster':[p('QB','Joe Burrow',22,1),p('RB','Gibbs',24,2),p('RB','Chase Brown',15,3),p('RB/WR/TE','Warren',16,4),p('BE','Bench',45,5)]}
        theirs={'roster':[p('RB','RB 1',19,6),p('QB','QB 2',18,7),p('RB','RB 2',17,8),p('RB/WR/TE','Flex 2',10,9)]}
        pairs=head_to_head(mine,theirs)
        self.assertEqual([p['slot'] for p in pairs],['QB','RB1','RB2','RB/WR/TE'])
        self.assertEqual([p['projected_advantage'] for p in pairs],['me','me','opponent','me'])
        self.assertEqual(pairs[1]['projection_margin'],5)
        self.assertFalse(any(p.get('my_player',{}).get('name')=='Bench' for p in pairs))
    def test_missing_projection_is_unknown_not_zero(self):
        mine={'roster':[{'slot':'QB','starter':True,'projected_points':None}]}
        them={'roster':[{'slot':'QB','starter':True,'projected_points':13}]}
        self.assertEqual(head_to_head(mine,them)[0]['projected_advantage'],'unknown')
    def test_live_nfl_statuses_and_opponents(self):
        source={'events':[{'date':'2026-10-22T20:00Z','competitions':[{'competitors':[{'team':{'abbreviation':'CIN'}},{'team':{'abbreviation':'BAL'}}]}],
                           'status':{'type':{'state':'in','shortDetail':'Q2 6:21'}}}]}
        league=NS(year=2026)
        status=nfl_game_statuses(league,7,fetcher=lambda year,week:source)
        self.assertEqual(status['games']['CIN']['state'],'LIVE')
        mine={'roster':[{'name':'Burrow','nfl_team':'CIN','on_bye':False}]};other={'roster':[]}
        enrich_live_players(mine,other,status)
        self.assertEqual(mine['roster'][0]['game_status'],'LIVE')
        self.assertEqual(mine['roster'][0]['opponent'],'BAL')
    def test_player_bio_safe_public_espn_metadata(self):
        data={'athlete':{'age':29,'displayHeight':'6\'4"','college':{'name':'LSU'},'experience':{'years':6}}}
        profile=player_public_bio(42,fetcher=lambda pid:data)
        self.assertEqual(profile['college'],'LSU')
        self.assertEqual(profile['years_pro'],6)
        self.assertEqual(player_public_bio(-1,fetcher=lambda pid:None),{})
    def test_profile_does_not_invent_stats(self):
        player=NS(playerId=22,name='RB X',stats={6:{'points':14,'breakdown':{'rushingYards':70,'receivingReceptions':4}}},
                  position='RB',proTeam='DET',injuryStatus='ACTIVE',avg_points=11,total_points=66,points=0,projected_points=12)
        league=NS(current_week=7, teams=[NS(roster=[player])])
        with patch('matchup_live.player_public_bio',return_value={}),patch('league_intelligence._get_box_scores',return_value=[]):
            profile=player_profile(league,22,7)
        self.assertEqual(profile['last_completed_game']['week'],6)
        self.assertEqual(profile['available_weeks_stat_totals'][0]['key'],'rushingYards')
        self.assertEqual(profile['stat_coverage_weeks'],1)

class NewsTests(unittest.TestCase):
    def test_watt_tv_contract_filtered(self):
        self.assertFalse(is_fantasy_relevant('JJ Watt signs CBS broadcast contract'))
        self.assertTrue(is_fantasy_relevant('Joe Burrow questionable after limited practice'))
        self.assertTrue(is_fantasy_relevant('Week 7 fantasy RB waiver pickups'))
    def test_news_feed_excludes_business(self):
        xml=b'''<rss><channel><item><title>Watt signs CBS TV contract</title><link>https://espn.com/a</link></item>
        <item><title>Week 7 Fantasy Football Waiver Adds</title><link>https://espn.com/b</link></item></channel></rss>'''
        feed=news_feed(fetcher=lambda url:xml,refresh=True)
        self.assertEqual(len(feed['articles']),1)
        self.assertEqual(feed['filtered_nonfantasy'],1)
        self.assertEqual(feed['articles'][0]['url'],'https://espn.com/b')
    def test_roster_news_verifiable_and_missing_marked(self):
        rss=b'''<rss><channel><item><title>A.J. Brown injured at practice</title>
        <link>https://espn.com/brown</link><description>WR questionable</description></item></channel></rss>'''
        roster=[{'name':'A.J. Brown','espn_id':88,'position':'WR','injury_status':'QUESTIONABLE'},
                {'name':'Rare Unknown','espn_id':100,'position':'TE','injury_status':'ACTIVE'}]
        calls=[]
        def athlete(url):
            calls.append(url)
            return json.dumps({'articles':[]}).encode()
        result=roster_news(roster,feed_fetcher=lambda url:rss,athlete_fetcher=athlete)
        self.assertEqual(result['total_players'],2)
        self.assertEqual(result['matched_players'],1)
        self.assertEqual(result['players'][0]['articles'][0]['title'],'A.J. Brown injured at practice')
        self.assertEqual(result['players'][1]['status'],'not_found')
        self.assertEqual(len(calls),1)
    def test_espn_athlete_fallback_is_attributed(self):
        roster=[{'name':'Jahmyr Gibbs','espn_id':42,'position':'RB'}]
        xml=b'<rss><channel></channel></rss>'
        athlete=json.dumps({'articles':[{'headline':'Jahmyr Gibbs injury update from practice',
            'links':{'web':{'href':'https://www.espn.com/story'}},
            'published':'2026-10-22T15:00:00Z'}]}).encode()
        # This date is in the present conversation's 2026 season, but the
        # test must not depend on simulated wall clock. Omit date for longevity.
        athlete=athlete.replace(b'"published": "2026-10-22T15:00:00Z"',b'"published": null')
        result=roster_news(roster,feed_fetcher=lambda _:xml,athlete_fetcher=lambda url:athlete)
        self.assertEqual(result['matched_players'],1)
        self.assertEqual(result['players'][0]['articles'][0]['source'],'ESPN Player News')

class LineupWhyTests(unittest.TestCase):
    def test_position_specific_alternative_with_available_projections(self):
        lineup={'lineup':{'WR1':{'name':'A.J. Brown','position':'WR','espn_id':1,'espn_projection':16,'fantasypros_projection':15},
                          'FLEX':{'name':'Jaylen Warren','position':'RB','espn_id':2,'espn_projection':17,'fantasypros_projection':16}},
                'projected_total':32,'projection_coverage':'2/2'}
        roster=[{'name':'A.J. Brown','position':'WR','espn_id':1},
                {'name':'Jaylen Warren','position':'RB','espn_id':2},
                {'name':'Germie Bernard','position':'WR','espn_id':3,'espn_projection':12},
                {'name':'Blake Corum','position':'RB','espn_id':4,'espn_projection':10}]
        result=explain_lineup(lineup,roster)
        self.assertEqual(result['slot_explanations']['WR1']['best_eligible_nonstarter']['name'],'Germie Bernard')
        self.assertEqual(result['slot_explanations']['FLEX']['best_eligible_nonstarter']['name'],'Germie Bernard')
        self.assertEqual(result['slot_explanations']['WR1']['projection_advantage'],3.5)
        self.assertFalse(result['slot_explanations']['WR1']['weekly_ecr_available'])
        self.assertIn('not the same',result['rankings_note'])

if __name__=='__main__':unittest.main()

class ClaudeSummaryTests(unittest.TestCase):
    def test_opt_in_summary_uses_one_model_call_and_caches(self):
        from player_analysis import summarize_player, clear_analysis_cache
        clear_analysis_cache()
        class Messages:
            calls=0
            def create(self,**kwargs):
                self.calls+=1
                self.kwargs=kwargs
                return NS(content=[NS(type='text',text='Season: 12.4 PPG. Last game: 9.0. This week: 11.5.')],usage=NS(input_tokens=100,output_tokens=30))
        messages=Messages();client=NS(messages=messages)
        profile={'name':'Starter X','week':7,'season_fantasy_ppg':12.4,'current_week':{'projected_points':11.5},'last_completed_game':{'week':6,'fantasy_points':9}}
        first=summarize_player(profile,client,'fake-sonnet')
        second=summarize_player(profile,client,'fake-sonnet')
        self.assertEqual(messages.calls,1)
        self.assertFalse(first['cached']);self.assertTrue(second['cached'])
        self.assertEqual(second['input_tokens'],0)
        self.assertIn('never make up',messages.kwargs['messages'][0]['content'])
