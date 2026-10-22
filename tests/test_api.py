"""Offline FastAPI contract and session isolation tests; no ESPN or Claude calls."""
import sys
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from api import create_app


class FakeEngine:
    APP_VERSION = 'v7.8'

    def __init__(self):
        self.APP_CONTEXT = None
        self.DECISION_CONTEXT = {'players': [], 'horizon': None}
        self.CHAT_HISTORY = []
        self.EXTERNAL_PLAYER_CACHE = {'test': 1}
        self.calls = 0

    def load_app_context(self):
        self.APP_CONTEXT = {'current_week': 7}

    def _reset_decision_context(self):
        self.DECISION_CONTEXT = {'players': [], 'horizon': None}

    def tool_get_roster(self):
        return {'league': 'Test league', 'week': 7,
                'league_settings': {'scoring': 'PPR'},
                'players': [{'name': 'Player A', 'position': 'RB'}]}

    def tool_get_optimal_lineup(self):
        return {'week': 7, 'lineup': {'RB': {'name': 'Player A'}}, 'projected_total': 10}

    def tool_get_waiver_recommendations(self, position=None, limit=5):
        if position and position not in {'RB', 'WR'}:
            return {'success': False, 'error': 'Unsupported position'}
        return {'success': True, 'requested_position': position or 'ALL', 'limit': limit,
                'candidate_pool': [{'name': 'Player B'}]}

    def ask_claude(self, user_message, *, return_details=False):
        self.calls += 1
        self.CHAT_HISTORY.append({'role': 'user', 'content': user_message})
        self.CHAT_HISTORY.append({'role': 'assistant', 'content': 'Okay.'})
        self.DECISION_CONTEXT['players'] = [user_message]
        result = {'answer': 'Okay.', 'api_calls': 1, 'input_tokens': 2200,
                  'output_tokens': 90, 'prefetched': True, 'intent': 'weekly'}
        return result if return_details else result['answer']


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.client = TestClient(create_app(lambda: self.engine))

    def new_session(self):
        reply = self.client.post('/api/sessions')
        self.assertEqual(reply.status_code, 201)
        return reply.json()['session_id']

    def test_health_does_not_start_expensive_sources(self):
        self.assertFalse(self.client.get('/api/health').json()['ready'])
        self.assertIsNone(self.engine.APP_CONTEXT)
        league = self.client.get('/api/league').json()
        self.assertEqual(league['roster_size'], 1)
        self.assertTrue(self.client.get('/api/health').json()['ready'])

    def test_reads_do_not_call_claude(self):
        self.assertEqual(len(self.client.get('/api/roster').json()['players']), 1)
        self.assertEqual(self.client.get('/api/lineup').json()['week'], 7)
        self.assertEqual(self.client.get('/api/waivers', params={'position':'RB'}).json()['requested_position'], 'RB')
        self.assertEqual(self.engine.calls, 0)

    def test_sessions_cannot_share_followup_state(self):
        a, b = self.new_session(), self.new_session()
        self.client.post('/api/chat', json={'message': 'tucker or worthy'}, headers={'X-Fantasy-Session': a})
        self.client.post('/api/chat', json={'message': 'gibbs or brown'}, headers={'X-Fantasy-Session': b})
        host = self.client.app.state.coach_host
        self.assertEqual(host.sessions[a].decision['players'], ['tucker or worthy'])
        self.assertEqual(host.sessions[b].decision['players'], ['gibbs or brown'])
        self.assertEqual(len(host.sessions[a].history), 2)
        self.assertEqual(len(host.sessions[b].history), 2)
        self.assertEqual(self.engine.calls, 2)
        self.assertEqual(self.engine.CHAT_HISTORY, [])

    def test_session_required_and_message_validated(self):
        self.assertEqual(self.client.post('/api/chat', json={'message': 'hello'}).status_code, 404)
        s = self.new_session()
        self.assertEqual(self.client.post('/api/chat', json={'message': '  '},
                         headers={'X-Fantasy-Session':s}).status_code, 422)
        self.assertEqual(self.client.post('/api/chat', json={'message': 'x'*2001},
                         headers={'X-Fantasy-Session':s}).status_code, 422)
        self.assertEqual(self.engine.calls, 0)

    def test_refresh_clears_sessions_only_after_success(self):
        s = self.new_session()
        self.assertTrue(self.client.post('/api/refresh').json()['sessions_reset'])
        self.assertEqual(self.client.post('/api/chat', json={'message': 'hello'},
                         headers={'X-Fantasy-Session':s}).status_code, 404)

    def test_waivers_validation(self):
        self.assertEqual(self.client.get('/api/waivers', params={'position':'XYZ'}).status_code, 422)
        self.assertEqual(self.client.get('/api/waivers', params={'limit': 100}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
