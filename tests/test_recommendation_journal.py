"""Offline recommendation audit tests (never contact NFL, ESPN, or Claude)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from recommendation_journal import choice_instruction, extract_choice, create_record, save_record
from evaluate_claude_recommendations import kickoff_utc, score_record


A = {'name': 'Tre Tucker', 'position': 'WR', 'espn_projection': 10.5, 'fantasypros_projection': 10.3, 'weekly_score_advisory': 15}
B = {'name': 'Xavier Worthy', 'position': 'WR', 'espn_projection': 12.4, 'fantasypros_projection': 9.7, 'weekly_score_advisory': 13}
WEEK = {'intent': 'weekly_compare', 'comparison': {'player_a': A, 'player_b': B}}
ROS = {'intent': 'ros_compare', 'comparison': {'player_a': A, 'player_b': B}}


def record(evidence=WEEK, choice='Tre Tucker', at='2026-10-22T00:00:00+00:00'):
    return create_record(
        season=2026, week=7, question='tucker or worthy', answer='Start Tucker.',
        prefetched=evidence, turn_context={'effective_intent': 'weekly'},
        league_scoring={'scoring': 'PPR', 'reception_points': 1.0},
        choice=choice, api_calls=1, input_tokens=2300, output_tokens=300,
        recorded_at=datetime.fromisoformat(at),
    )


class JournalTests(unittest.TestCase):
    def test_receipt_instruction_single_existing_call(self):
        text = choice_instruction(WEEK)
        self.assertIn('Tre Tucker', text)
        self.assertIn('Xavier Worthy', text)
        self.assertIn('FC_CHOICE_JSON:', text)
        self.assertEqual(choice_instruction({'intent': 'waiver_swap'}), '')

    def test_valid_choice_stripped_and_saved(self):
        answer = 'Start Tucker.\nFC_CHOICE_JSON: {"choice": "Tre Tucker"}'
        visible, choice = extract_choice(answer, WEEK)
        self.assertEqual(visible, 'Start Tucker.')
        self.assertEqual(choice, 'Tre Tucker')
        r = record(choice=choice)
        self.assertEqual(r['evaluation_scope'], 'weekly_head_to_head')
        self.assertEqual(r['choice_verification'], 'receipt')
        self.assertEqual(r['candidate_players'][1]['espn_projection'], 12.4)
        with tempfile.TemporaryDirectory() as d, patch.dict('os.environ', {'FANTASY_JOURNAL':'1'}):
            first = save_record(r, root=d)
            self.assertEqual(json.loads(first.read_text())['claude_choice'], 'Tre Tucker')
            second = save_record(record(), root=d)
            self.assertNotEqual(first, second)
            self.assertTrue(first.exists())

    def test_fabricated_choice_refused(self):
        for suffix in ('FC_CHOICE_JSON: {"choice": "Josh Jacobs"}',
                       'FC_CHOICE_JSON: not-json',
                       'FC_CHOICE_JSON: {"choice": null}'):
            _, choice = extract_choice('Lean Tucker.\n' + suffix, WEEK)
            self.assertIsNone(choice)

    def test_undecided_answer_not_guessed(self):
        visible, choice = extract_choice('Start Tre Tucker. No receipt provided.', WEEK)
        self.assertIsNone(choice)
        self.assertIn('Tucker', visible)

    def test_ros_not_weekly_scored(self):
        self.assertEqual(record(ROS)['evaluation_scope'], 'not_weekly_scored')

    def test_disabled_journal(self):
        with tempfile.TemporaryDirectory() as d, patch.dict('os.environ', {'FANTASY_JOURNAL':'0'}):
            self.assertIsNone(save_record(record(), root=d))
            self.assertFalse(list(Path(d).iterdir()))


class EvaluationTests(unittest.TestCase):
    first_kickoff = datetime.fromisoformat('2026-10-22T00:15:00+00:00')

    def test_scored_with_regret_and_frozen_baselines(self):
        result = score_record(record(), {'Tre Tucker': 10, 'Xavier Worthy': 15}, self.first_kickoff)
        self.assertEqual(result['status'], 'SCORED')
        self.assertEqual(result['claude_regret_points'], 5)
        self.assertFalse(result['hit'])
        self.assertEqual(result['baseline']['espn']['choice'], 'Xavier Worthy')
        self.assertEqual(result['baseline']['espn']['regret_points'], 0)
        self.assertEqual(result['baseline']['fantasypros']['regret_points'], 5)

    def test_postkickoff_decision_refused(self):
        r = record(at='2026-10-22T00:16:00+00:00')
        result = score_record(r, {'Tre Tucker': 10, 'Xavier Worthy': 15}, self.first_kickoff)
        self.assertEqual(result['status'], 'AFTER_WEEK_KICKOFF')

    def test_no_receipt_not_scored(self):
        self.assertEqual(score_record(record(choice=None), {'Tre Tucker': 10, 'Xavier Worthy': 15}, self.first_kickoff)['status'], 'NO_VERIFIED_CLAUDE_CHOICE')

    def test_missing_actual_not_zero(self):
        self.assertEqual(score_record(record(), {'Tre Tucker': 10}, self.first_kickoff)['status'], 'MISSING_ACTUAL_STATS')

    def test_ros_not_compared_to_weekly_outcomes(self):
        self.assertEqual(score_record(record(ROS), {'Tre Tucker': 10, 'Xavier Worthy': 15}, self.first_kickoff)['status'], 'NOT_WEEKLY_SCORABLE')

    def test_dst_kickoff_conversion(self):
        utc = kickoff_utc({'gameday':'2026-10-22', 'gametime':'20:15'})
        self.assertEqual(utc.isoformat(), '2026-10-23T00:15:00+00:00')


if __name__ == '__main__':
    unittest.main()
