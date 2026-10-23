"""v7.19 regression: real weekly recap request mode, output and HTTP errors.

All Claude and ESPN calls are mocked; never spends credits.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from weekly_recap import RecapEmptyResponseError, generate_weekly_recap


LEAGUE = NS(year=2026, current_week=7)
MATCHUP = {
    'week': 7,
    'status': 'available',
    'my_team': {'name': 'Mine', 'points_scored': 142,
                'roster': [{'name': 'QB One', 'slot': 'QB', 'starter': True,
                            'actual_points': 26.0, 'projected_points': 19.0}]},
    'opponent': {'name': 'Opponent', 'points_scored': 129,
                 'roster': [{'name': 'RB Two', 'slot': 'RB', 'starter': True,
                             'actual_points': 18.0, 'projected_points': 17.0}]},
}


def ready(*args, **kwargs):
    return {'status': 'ready', 'season': 2026, 'week': 7}


def recap(client, path, model='claude-sonnet-5-5'):
    return generate_weekly_recap(
        LEAGUE, 1, client, model, 7, status_fn=ready,
        strategy_fn=lambda *args: MATCHUP, recap_dir=Path(path))


class MessageRecorder:
    def __init__(self, result):
        self.calls = []
        self.result = result

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def api_result(*blocks, stop='end_turn'):
    return NS(content=list(blocks), stop_reason=stop,
              usage=NS(input_tokens=345, output_tokens=300))


class WeeklyRecapFixTests(unittest.TestCase):
    def test_sonnet_55_short_recap_disables_upfront_thinking(self):
        messages = MessageRecorder(api_result(
            NS(type='thinking', thinking=''),
            NS(type='text', text='Mine won 142–129. QB One contributed 26 points.')))
        client = NS(messages=messages)
        with tempfile.TemporaryDirectory() as directory:
            first = recap(client, directory)
            second = recap(client, directory)
        self.assertEqual(first['analysis'], 'Mine won 142–129. QB One contributed 26 points.')
        self.assertEqual(first['source'], 'Claude analysis of finalized ESPN fantasy matchup')
        self.assertEqual(len(messages.calls), 1)
        self.assertTrue(second['cached'])
        self.assertEqual(second['input_tokens'], 0)
        config = messages.calls[0]
        self.assertEqual(config['thinking'], {'type': 'between_tools'})
        self.assertEqual(config['output_config'], {'effort': 'low'})
        self.assertGreaterEqual(config['max_tokens'], 1200)

    def test_empty_text_is_not_cached_and_no_automatic_paid_retry(self):
        messages = MessageRecorder(api_result(
            NS(type='thinking', thinking=''), stop='max_tokens'))
        client = NS(messages=messages)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RecapEmptyResponseError) as error:
                recap(client, directory)
            self.assertEqual(list(Path(directory).glob('recap_*.json')), [])
        self.assertEqual(error.exception.stop_reason, 'max_tokens')
        self.assertEqual(error.exception.block_types, ['thinking'])
        self.assertEqual(len(messages.calls), 1)

    def test_non_sonnet_model_does_not_receive_sonnet_only_thinking(self):
        messages = MessageRecorder(api_result(NS(type='text', text='Recap ready.')))
        with tempfile.TemporaryDirectory() as directory:
            recap(NS(messages=messages), directory, model='test-model')
        self.assertNotIn('thinking', messages.calls[0])
        self.assertNotIn('output_config', messages.calls[0])

    def test_empty_response_maps_to_provider_error_not_week_not_finished(self):
        from api import create_app
        from fastapi.testclient import TestClient
        engine = NS(DECISION_CONTEXT={}, APP_CONTEXT={'league': LEAGUE, 'team': NS(team_id=1)},
                    client=NS(messages=object()), MODEL='claude-sonnet-5-5')
        app = TestClient(create_app(lambda: engine))
        with patch('weekly_recap.generate_weekly_recap',
                   side_effect=RecapEmptyResponseError('max_tokens', ['thinking'])):
            response = app.post('/api/recaps/week?week=7')
        self.assertEqual(response.status_code, 502)
        self.assertIn('stop_reason=max_tokens', response.json()['detail'])
        self.assertNotIn('API_KEY', response.text)


if __name__ == '__main__':
    unittest.main()
