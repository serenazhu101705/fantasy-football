"""v7.26: independent FastAPI-front-door routing for NFL league statistics.

No network, Claude, ESPNs sessions, fantasy credentials, or paid calls.
"""
from __future__ import annotations
import sys
from pathlib import Path
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from api import EngineHost, SessionState, create_app
from nfl_chat_guard import maybe_answer_nfl_leaderboard
from nfl_leaderboards import parse_leaderboard_request


def available(question, *, season):
    assert season == 2026
    return {'status': 'available', 'source': 'nflverse weekly player statistics',
            'attempted_sources': ['nflverse'], 'season': 2026, 'stat': 'rushing_yards',
            'stat_label': 'Rushing yards', 'unit': 'yds', 'through_week': 7,
            'leaders': [{'rank': 1, 'name': 'Mocked RB', 'team': 'MIN', 'value': 888., 'games': 6}]}


class FrontDoorTests(unittest.TestCase):
    def test_exact_user_question_matches_rushing(self):
        for q in ('who leads the league in rushing yards',
                  'who leeeads the league in rushing yards',
                  'who leads NFL rushing yards right now?'):
            with self.subTest(question=q):
                self.assertEqual(parse_leaderboard_request(q)['metric'], 'rushing_yards')
                answer = maybe_answer_nfl_leaderboard(q, lookup=available)
                self.assertIn('Mocked RB', answer['answer'])
                self.assertEqual(answer['api_calls'], 0)
                self.assertEqual(answer['sources_attempted'], ['nflverse'])

    def test_non_leaderboard_leaves_existing_coach_unchanged(self):
        self.assertIsNone(maybe_answer_nfl_leaderboard('Should I start Gibbs or Chase Brown?', lookup=available))
        self.assertIsNone(maybe_answer_nfl_leaderboard('What is my fantasy league scoring?', lookup=available))

    def test_news_never_called_when_stats_are_unavailable(self):
        def missing(question, *, season):
            return {'status': 'unavailable', 'season': season, 'stat': 'rushing_yards',
                    'reason': 'No verified data', 'attempted_sources': ['nflverse', 'ESPN'],
                    'source_errors': {'nflverse': 'missing feed', 'ESPN': 'empty response'}}
        resp = maybe_answer_nfl_leaderboard('Who leads rushing yards?', lookup=missing)
        self.assertEqual(resp['api_calls'], 0)
        self.assertIn('nflverse, ESPN', resp['answer'])
        self.assertIn('empty response', resp['answer'])
        self.assertNotIn('Google News', resp['answer'])

    def test_fastapi_host_guard_intercepts_before_claude_and_resets_stale_context(self):
        calls=[]
        engine=types.SimpleNamespace(
            CHAT_HISTORY=[], DECISION_CONTEXT={'players':['Tre Tucker','Xavier Worthy'],
                'acquisition':True, 'waiver_drop_target':'Tre Tucker'}, SEASON=2026,
            ask_claude=lambda *args, **kwargs: calls.append('Claude'),
        )
        state=SessionState(history=[],decision=dict(engine.DECISION_CONTEXT))
        host=EngineHost(lambda: engine)
        with patch('nfl_chat_guard.verified_leaderboard', available):
            reply=host.chat(engine,state,'who leads the league in rushing yards')
        self.assertIn('Mocked RB',reply['answer'])
        self.assertEqual(reply['api_calls'],0)
        self.assertEqual(calls,[])
        self.assertEqual(state.decision['players'],[])
        self.assertEqual(len(state.history),2)
        self.assertIsNot(state.history,engine.CHAT_HISTORY)
        self.assertEqual(engine.DECISION_CONTEXT['players'],['Tre Tucker','Xavier Worthy'])

    def test_other_chat_uses_original_engine(self):
        engine=types.SimpleNamespace(
            CHAT_HISTORY=[], DECISION_CONTEXT={}, SEASON=2026,
            ask_claude=lambda *a,**kw: {'answer':'coach result','api_calls':1},
        )
        host=EngineHost(lambda:engine)
        resp=host.chat(engine,SessionState(), 'How is my matchup looking?')
        self.assertEqual(resp['answer'],'coach result')

    def test_health_advertises_active_guard(self):
        from fastapi.testclient import TestClient
        app=create_app(engine_loader=lambda:None)
        resp=TestClient(app).get('/api/health')
        self.assertEqual(resp.status_code,200)
        self.assertEqual(resp.json()['nfl_leaderboard_guard'],'v7.26')

    def test_verified_leaders_endpoint_uses_espn_fallback_aware_lookup(self):
        from fastapi.testclient import TestClient
        app=create_app(engine_loader=lambda:None)
        with patch('nfl_source_lookup.verified_leaderboard', available):
            r=TestClient(app).get('/api/nfl/verified-leaders?stat=rushing_yards&limit=5')
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['leaders'][0]['name'],'Mocked RB')

if __name__=='__main__': unittest.main()
