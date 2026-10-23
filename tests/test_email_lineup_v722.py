"""v7.22 controls and free-agent profile, offline only (no ESPN writes/SMTP)."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from api import create_app
import email_subscriptions as emails
from lineup_actions import calculate_preview, create_preview, submit_preview, automatic_injury_bye_lineup, PREVIEWS


def roster():
    members = [('QB','Joe Burrow','QB',1,'CIN'), ('RB','Gibbs','RB',2,'DET'),
               ('RB','Brown','RB',3,'CIN'), ('WR','AJ Brown','WR',4,'NE'),
               ('WR','Metcalf','WR',5,'PIT'), ('TE','Loveland','TE',6,'CHI'),
               ('RB/WR/TE','Warren','RB',7,'PIT'), ('D/ST','Steelers','D/ST',8,'PIT'),
               ('K','Reichard','K',9,'MIN'), ('BE','Corum','RB',10,'LAR'),
               ('BE','Worthy','WR',11,'KC')]
    return [NS(lineupSlot=s,name=n,position=pos,playerId=pid,proTeam=team,injuryStatus='ACTIVE',on_bye_week=False)
            for s,n,pos,pid,team in members]


def optimal(team):
    slots=['QB','RB1','RB2','WR1','WR2','TE','FLEX','D/ST','K']
    chosen=team.roster[:9]
    return {'lineup':{key:{'espn_id':p.playerId,'position':p.position,'name':p.name} for key,p in zip(slots,chosen)},
            'projected_total':140.0}


def game_status(team):
    kickoff=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
    return {'games':{p.proTeam:{'state':'SCHEDULED','kickoff':kickoff} for p in team.roster}}

class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.team=NS(roster=roster(),team_id=7)
        self.league=NS(teams=[self.team],current_week=7,year=2026)
        self.optimal=optimal(self.team)
        PREVIEWS.clear()

    def test_standard_lineup_with_no_changes_is_read_only(self):
        preview=calculate_preview(self.league,self.team,self.optimal,game_status=game_status(self.team))
        self.assertEqual(preview['changes'],[])
        self.assertEqual(preview['change_count'],0)

    def test_preview_single_swap_and_write_disabled(self):
        self.optimal['lineup']['RB2']={'espn_id':10,'position':'RB','name':'Corum'}
        preview=create_preview(self.league,self.team,self.optimal,game_status=game_status(self.team))
        self.assertEqual({p['name'] for p in preview['changes']},{'Brown','Corum'})
        with patch.dict(os.environ, {'ESPN_WRITES_ENABLED':'0'}):
            with self.assertRaisesRegex(ValueError,'disabled'):
                submit_preview(self.league,self.team,self.optimal,preview['confirmation_token'],
                               game_status=game_status(self.team),sender=lambda *_:self.fail('write'))

    def test_write_enabled_requires_one_use_preview_and_checks_payload(self):
        self.optimal['lineup']['RB2']={'espn_id':10,'position':'RB','name':'Corum'}
        with patch.dict(os.environ,{'ESPN_WRITES_ENABLED':'1','ESPN_S2':'fake','ESPN_SWID':'{TEST}', 'ESPN_LEAGUE_ID':'1234'}):
            preview=create_preview(self.league,self.team,self.optimal,game_status=game_status(self.team))
            calls=[]
            def sender(url,payload,s2,swid):
                calls.append((url,payload,s2,swid))
                return NS(status_code=200)
            result=submit_preview(self.league,self.team,self.optimal,preview['confirmation_token'],
                                  game_status=game_status(self.team),sender=sender)
            self.assertFalse(result['verified']) # readback is required before claiming success
            self.assertEqual(len(calls),1)
            self.assertEqual(len(calls[0][1]['items']),2)
            self.assertIn('/transactions/',calls[0][0])
            with self.assertRaisesRegex(ValueError,'already used'):
                submit_preview(self.league,self.team,self.optimal,preview['confirmation_token'],
                               game_status=game_status(self.team),sender=sender)

    def test_reject_started_player_and_stale_roster(self):
        self.optimal['lineup']['RB2']={'espn_id':10,'position':'RB','name':'Corum'}
        games=game_status(self.team)
        games['games']['CIN']['state']='LIVE'
        with self.assertRaisesRegex(ValueError,'already live'):
            create_preview(self.league,self.team,self.optimal,game_status=games)
        games=game_status(self.team)
        preview=create_preview(self.league,self.team,self.optimal,game_status=games)
        self.team.roster[-1].lineupSlot='IR'
        with patch.dict(os.environ,{'ESPN_WRITES_ENABLED':'1'}):
            with self.assertRaisesRegex(ValueError,'roster changed'):
                submit_preview(self.league,self.team,self.optimal,preview['confirmation_token'],game_status=games)

    def test_auto_swap_only_out_player_not_healthy_starters(self):
        self.team.roster[2].injuryStatus='OUT'
        self.optimal['lineup']['RB2']={'espn_id':10,'position':'RB','name':'Corum'}
        automatic=automatic_injury_bye_lineup(self.team,self.optimal)
        self.assertEqual(automatic['lineup']['RB2']['espn_id'],10)
        self.assertEqual(automatic['lineup']['QB']['espn_id'],1)
        self.assertEqual(automatic['lineup']['WR1']['espn_id'],4)
        self.assertEqual(len(calculate_preview(self.league,self.team,automatic,game_status=game_status(self.team))['changes']),2)

class EmailTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old=emails.DB_PATH
        emails.DB_PATH=Path(self.temp.name)/'subscriptions.sqlite3'
        self.transport=[]

    def tearDown(self):
        emails.DB_PATH=self.old
        self.temp.cleanup()

    def test_requires_confirmation_and_opt_in_and_dedupes(self):
        def fake_send(to,subject,body):self.transport.append((to,subject,body))
        sub=emails.subscribe(' Person@Example.COM ',{'thursday_lineup':True},transport=fake_send)
        self.assertEqual(sub['status'],'pending_confirmation')
        self.assertEqual(emails.confirmed_subscribers(),[])
        link=self.transport[-1][2].split('opening:\n')[1].split('\n')[0]
        token=link.split('token=')[1]
        self.assertTrue(emails.confirm(token))
        self.assertFalse(emails.confirm(token))
        user=emails.confirmed_subscribers()[0]
        self.assertEqual(user['email'],'person@example.com')
        self.assertTrue(emails.send_digest(user,'thursday_lineup','2026-7','Optimal lineup','Lineup...',transport=fake_send))
        self.assertFalse(emails.send_digest(user,'thursday_lineup','2026-7','Optimal lineup','Lineup...',transport=fake_send))
        self.assertFalse(emails.send_digest(user,'injury_news','2026-7','News','News...',transport=fake_send))
        self.assertEqual(len(self.transport),2)
        unsub=self.transport[-1][2].split('unsubscribe?token=')[1].split('\n')[0]
        self.assertTrue(emails.unsubscribe(unsub))
        self.assertEqual(emails.confirmed_subscribers(),[])

    def test_reject_invalid_email(self):
        with self.assertRaises(ValueError): emails.subscribe('invalid',{'bye_alerts':True},transport=lambda *_:None)

class WaiverProfileTests(unittest.TestCase):
    def test_waiver_player_profile_gets_actual_id_from_cached_pool(self):
        class E:
            APP_CONTEXT={'league':NS(year=2026,current_week=7,teams=[]),
                         'team':NS(team_id=7), 'current_week':7,
                         '_browser_free_agents':[{'espn_id':3915999,'name':'Sam Darnold','nfl_team':'SEA',
                                                  'position':'QB','injury_status':'ACTIVE', 'espn_projected_points':18.5}]}
            DECISION_CONTEXT={}
        api=TestClient(create_app(lambda:E()))
        import matchup_live
        with patch('league_intelligence._get_box_scores',return_value=[]),\
             patch('matchup_live.player_public_bio',return_value={}),\
             patch('espn_player_stats.public_player_data',return_value={'season_stats':[],'recent_games':[],'stats_source':'unavailable'}),\
             patch('season_nfl_stats.current_season_player_stats',return_value={'season_stats':[], 'recent_games':[], 'weeks_covered':[], 'stats_source':'unavailable'}),\
             patch('matchup_live.nfl_game_statuses',return_value={'games':{}}):
            response=api.get('/api/players/3915999?week=7')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['name'],'Sam Darnold')
        self.assertEqual(response.json()['current_week']['projected_points'],18.5)
        self.assertIn('free-agent',response.json()['source'])

if __name__=='__main__': unittest.main()
