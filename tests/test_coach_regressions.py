"""Offline regression suite: no Anthropic calls, ESPN credentials, or network needed.

The real llm.py imports live clients at module import time. To test its router
without activating those clients, extract the exact production function bodies
via AST; do not reimplement the functions under test.
"""

from __future__ import annotations

import ast
import contextlib
from difflib import SequenceMatcher, get_close_matches
import io
import json
import os
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import Mock

APP_DIR = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP_DIR))


def load_functions(filename, names, injected=None):
    source = (APP_DIR / filename).read_text(encoding='utf-8')
    parsed = ast.parse(source, filename=filename)
    selected = [node for node in parsed.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    if {node.name for node in selected} != set(names):
        raise AssertionError(f'Missing functions in {filename}: {set(names) - {n.name for n in selected}}')
    scope = {
        're': re, 'os': os, 'json': json, 'SequenceMatcher': SequenceMatcher,
        'get_close_matches': get_close_matches,
    }
    if injected:
        scope.update(injected)
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), filename, 'exec'), scope)
    return scope


ROUTER_FUNCTIONS = [
    'normalize_name', 'json_text', '_non_none', '_debug_enabled', '_trace', '_evidence_sources',
    '_has_ros_cue', '_asks_league_scoring', '_is_roster_review',
    '_looks_like_waiver_acquisition', '_mentions_open_spot',
    '_is_waiver_replacement_request', '_waiver_drop_name', '_intent_for_message',
    '_extract_requested_position', '_extract_requested_add_count',
    '_all_resolvable_names', '_extract_player_names', '_reset_decision_context',
    '_explicit_horizon', '_merge_corrected_player', '_is_correction_turn',
    '_derive_turn_context', '_persist_turn_context', '_needs_chat_history',
    '_prefetch_evidence', 'select_tools_for_message', 'ask_claude',
]


class RouterRegressionTests(unittest.TestCase):
    def setUp(self):
        self.players = [
            {'name': 'Tre Tucker', 'position': 'WR', 'rostered': False},
            {'name': 'Xavier Worthy', 'position': 'WR', 'rostered': False},
            {'name': 'Malik Washington', 'position': 'WR', 'rostered': False},
            {'name': 'Germie Bernard', 'position': 'WR', 'rostered': False},
            {'name': 'Other Washington', 'position': 'WR', 'rostered': True},
        ]
        self.names = {p['name'].lower(): p for p in self.players}
        self.scope = load_functions('llm.py', ROUTER_FUNCTIONS, {
            'DECISION_CONTEXT': {
                'players': [], 'horizon': None, 'acquisition': False,
                'position': None, 'open_spot': False, 'requested_add_count': 1,
                'waiver_drop_target': None,
            },
            'APP_CONTEXT': {'current_week': 7, 'waiver_candidates': self.players, 'roster': []},
            'get_all_known_players': lambda: self.players,
            '_league': lambda: types.SimpleNamespace(player_map={}),
            '_find_loaded_player': lambda name: self.names.get(name.lower()),
            'find_player': lambda name: self.names.get(name.lower()),
            '_comparison_player': lambda player, include_matchup=True: {'name': player['name'], 'position': player['position']},
            '_current_week': lambda: 7,
            '_compact_league_context': lambda: {'scoring': 'PPR', 'reception_points': 1.0, 'team_count': 10},
            'TOOL_NAMES': [],
            'TOOLS': [{'name': name} for name in (
                'compare_players_weekly', 'compare_players_ros', 'get_player_details',
                'get_waiver_recommendations', 'get_roster', 'get_optimal_lineup',
                'get_shadow_prediction', 'get_system_status')],
            'tool_compare_players_weekly': lambda a, b: {'success': True, 'player_a': {'name': a}, 'player_b': {'name': b}},
            'tool_compare_players_ros': lambda a, b: {'success': True, 'player_a': {'name': a}, 'player_b': {'name': b}},
            '_prefetch_acquisition_compare': lambda ctx: {'intent': 'acquisition_compare', 'players': ctx['players'], 'horizon': ctx.get('horizon')},
            'tool_get_waiver_recommendations': lambda position=None, limit=5: {
                'requested_position': position or 'ALL', 'matching_candidates_loaded': 5,
                'candidate_pool': [{'name': 'Malik Washington', 'position': 'WR'}, {'name': 'Germie Bernard', 'position': 'WR'}],
                'transaction_model_result': 'NO_THRESHOLD_MOVE', 'moves': []},
            '_compact_roster_need': lambda position=None: {'roster_size': 0, 'position_counts': {}},
            '_compact_waiver_candidate': lambda p, include_detail=False: {'name': p['name'], 'position': p['position']},
            '_waiver_candidate_sort_key': lambda p: 1,
            'tool_get_optimal_lineup': lambda: {'lineup': []},
            '_response_token_budget': lambda _: 1600,
            '_effort_for_message': lambda _: 'medium',
            'MAX_TOOL_ROUNDS': 4,
            'MAX_HISTORY_MESSAGES': 2,
            'CHAT_HISTORY': [],
            'serialize_content_blocks': lambda blocks: blocks,
            'extract_text': lambda response: ''.join(b.text for b in response.content if b.type == 'text'),
            '_update_turn_context_from_tool': lambda *args: None,
            'CLAUDE_RETRY_MAX_TOKENS': 4200,
            'choice_instruction': lambda prefetched: '',
            'extract_choice': lambda text, prefetched: (text, None),
            'create_record': lambda **kwargs: {'evaluation_scope': 'not_weekly_scored'},
            'save_record': lambda record: None,
            'SEASON': 2026,
        })

    def call(self, name, *args, **kwargs):
        return self.scope[name](*args, **kwargs)

    def test_two_player_comparison_prefetches(self):
        ctx = self.call('_derive_turn_context', 'tucker or worthy')
        self.assertEqual(ctx['players'], ['Tre Tucker', 'Xavier Worthy'])
        self.assertEqual(ctx['effective_intent'], 'acquisition_compare')
        self.assertIsNotNone(self.call('_prefetch_evidence', 'tucker or worthy', ctx))

    def test_two_player_comparison_prefetch_without_waiver(self):
        self.players[0]['rostered'] = True
        self.players[1]['rostered'] = True
        ctx = self.call('_derive_turn_context', 'tucker or worthy')
        self.assertEqual(ctx['effective_intent'], 'weekly')
        self.assertEqual(self.call('_prefetch_evidence', 'tucker or worthy', ctx)['intent'], 'weekly_compare')

    def test_rest_of_season_typos(self):
        self.assertEqual(self.call('_intent_for_message', 'rest tof the season'), 'ros')
        self.assertEqual(self.call('_intent_for_message', 'reset of the season'), 'ros')

    def test_long_term_follow_up_keeps_players(self):
        ctx = self.call('_derive_turn_context', 'tucker or worthy')
        self.call('_persist_turn_context', ctx)
        next_ctx = self.call('_derive_turn_context', 'i am thinking long term')
        self.assertEqual(next_ctx['players'], ['Tre Tucker', 'Xavier Worthy'])
        self.assertEqual(next_ctx['horizon'], 'ros')
        self.assertEqual(next_ctx['effective_intent'], 'ros_acquisition_compare')

    def test_washington_follow_up_uses_local_alias(self):
        ctx = self.call('_derive_turn_context', 'worthy or malik washington')
        self.assertEqual(ctx['players'], ['Xavier Worthy', 'Malik Washington'])
        self.call('_persist_turn_context', ctx)
        later = self.call('_derive_turn_context', 'worthy or washington for ROS')
        self.assertEqual(later['players'], ['Xavier Worthy', 'Malik Washington'])
        self.assertEqual(later['horizon'], 'ros')

    def test_conservative_barnard_typo(self):
        names = self.call('_extract_player_names', 'barnard or tucker')
        self.assertEqual(names, ['Tre Tucker', 'Germie Bernard'] if names[0] == 'Tre Tucker' else ['Germie Bernard', 'Tre Tucker'])
        self.assertNotIn('Other Washington', names)

    def test_roster_does_not_match_ros(self):
        self.assertFalse(self.call('_has_ros_cue', 'my roster'))
        self.assertEqual(self.call('_intent_for_message', 'best move given my roster'), 'roster_review')
        self.assertEqual(self.call('_prefetch_evidence', 'best move given my roster')['intent'], 'roster_review')

    def test_lineup_is_lineup_intent(self):
        self.assertEqual(self.call('_intent_for_message', 'starting lineup'), 'lineup')
        self.assertEqual(self.call('_prefetch_evidence', 'starting lineup')['intent'], 'lineup')

    def test_empty_spot_recognized(self):
        self.assertEqual(self.call('_intent_for_message', 'I have an empty bench spot'), 'waiver')
        ctx = self.call('_derive_turn_context', 'I have an empty bench spot')
        self.assertTrue(ctx['open_spot'])
        self.assertTrue(ctx['acquisition'])

    def test_open_slot_standalone_add_does_not_drop(self):
        scope = load_functions('waiver_advisor.py', ['evaluate_open_slot_add'], {
            'deepcopy': lambda p: dict(p),
            'optimize_lineup': lambda roster: {
                'lineup': {'QB': roster[0]} if roster else {},
                'lineup_score': float(len(roster) * 8),
                'projected_total': float(len(roster) * 10),
            },
            'has_complete_lineup': lambda result: bool(result['lineup']),
            'evaluate_roster_balance': lambda before, after: {'balance_delta': len(after) - len(before)},
        })
        result = scope['evaluate_open_slot_add'](
            [{'name': 'Starter QB', 'position': 'QB'}],
            {'name': 'Malik Washington', 'position': 'WR', 'ros_score': 40},
        )
        self.assertIsNone(result['drop'])
        self.assertFalse(result['needs_drop'])
        self.assertEqual(result['projected_points_delta'], 10)

    def test_open_spot_prefetch_uses_add_only_advice(self):
        advisory = types.ModuleType('waiver_advisor')
        advisory.evaluate_open_slot_add = lambda roster, add: {
            'add': add['name'], 'drop': None, 'needs_drop': False,
        }
        with unittest.mock.patch.dict(sys.modules, {'waiver_advisor': advisory}):
            ctx = self.call('_derive_turn_context', 'best WR waiver pickup, I have an empty bench spot')
            result = self.call('_prefetch_evidence', 'best WR waiver pickup, I have an empty bench spot', ctx)
        self.assertTrue(result['user_reports_open_spot'])
        self.assertTrue(result['waivers']['open_spot_adds'])
        self.assertIsNone(result['waivers']['open_spot_adds'][0]['drop'])
        self.assertNotIn('moves', result['waivers'])

    def test_best_wr_waiver_requests_wr(self):
        self.assertEqual(self.call('_intent_for_message', 'best WR waiver pickup'), 'waiver')
        ctx = self.call('_derive_turn_context', 'best WR waiver pickup')
        result = self.call('_prefetch_evidence', 'best WR waiver pickup', ctx)
        self.assertEqual(result['waivers']['requested_position'], 'WR')
        self.assertEqual(result['waivers']['candidate_pool'][0]['position'], 'WR')

    def test_take_two_wrs(self):
        ctx = self.call('_derive_turn_context', 'take 2 WRs')
        result = self.call('_prefetch_evidence', 'take 2 WRs', ctx)
        self.assertEqual(result['requested_add_count'], 2)
        self.assertEqual(result['waivers']['requested_position'], 'WR')
        self.assertGreaterEqual(len(result['waivers']['candidate_pool']), 2)

    def test_scoring_question_from_espn_context(self):
        ctx = self.call('_derive_turn_context', 'what scoring format is my league?')
        result = self.call('_prefetch_evidence', 'what scoring format is my league?', ctx)
        self.assertEqual(result['league_context']['scoring'], 'PPR')
        self.assertEqual(result['intent'], 'league_info')

    def test_lloyd_swap_stays_anchored_across_jacobs_context_and_corrections(self):
        lloyd = {'name': 'MarShawn Lloyd', 'position': 'RB', 'rostered': True, 'lineup_slot': 'BE'}
        jacobs = {'name': 'Josh Jacobs', 'position': 'RB', 'rostered': True}
        wilson = {'name': 'Emanuel Wilson', 'position': 'RB', 'rostered': False}
        for player in (lloyd, jacobs, wilson):
            self.players.append(player)
            self.names[player['name'].lower()] = player
        self.scope['APP_CONTEXT']['roster'] = [lloyd]
        self.scope['APP_CONTEXT']['waiver_candidates'] = [wilson]
        self.scope['tool_get_waiver_recommendations'] = lambda position=None, limit=5: {
            'requested_position': position, 'matching_candidates_loaded': 1,
            'candidate_pool': [{'name': 'Emanuel Wilson', 'position': 'RB'}],
            'transaction_model_result': 'NO_THRESHOLD_MOVE', 'moves': [],
        }
        self.scope['_compact_roster_need'] = lambda position=None: {'focus_position': position, 'roster_size': 1}
        advisory = types.ModuleType('waiver_advisor')
        advisory.evaluate_transaction = lambda roster, add, drop: {
            'valid': True, 'weekly_lineup_delta': 1.5,
            'projected_points_delta': 1.0, 'ros_delta': 2.0,
        }
        utterances = [
            'should i drop marshawn lloyd and pick up a waiver rb',
            'can you get all the data you need for lloyd? josh jacobs is coming back and he is only projected 3 points this week',
            'no i am trying to decide if i should drop lloyd for a waiver rb or hold lloyd',
            'jacobs is not available',
            'are you stupid i have lloyd already, i am asking if i should drop lloyd for a current waiver rb',
        ]
        with unittest.mock.patch.dict(sys.modules, {'waiver_advisor': advisory}):
            for utterance in utterances:
                ctx = self.call('_derive_turn_context', utterance)
                self.assertEqual(ctx['effective_intent'], 'waiver_swap', utterance)
                self.assertEqual(ctx['waiver_drop_target'], 'MarShawn Lloyd', utterance)
                self.assertEqual(ctx['players'], ['MarShawn Lloyd'], utterance)
                evidence = self.call('_prefetch_evidence', utterance, ctx)
                self.assertEqual(evidence['intent'], 'waiver_swap', utterance)
                self.assertEqual(evidence['drop_roster_verification'], 'confirmed_owned')
                self.assertEqual(evidence['waivers']['candidate_pool'][0]['name'], 'Emanuel Wilson')
                self.assertEqual(evidence['waivers']['drop_specific_evaluations'][0]['drop'], 'MarShawn Lloyd')
                self.assertNotIn('Josh Jacobs', str(evidence['waivers']))
                self.call('_persist_turn_context', ctx)

    def test_lloyd_swap_preserves_user_ownership_claim_if_espn_omits_him(self):
        lloyd = {'name': 'MarShawn Lloyd', 'position': 'RB', 'rostered': False}
        self.players.append(lloyd)
        self.names[lloyd['name'].lower()] = lloyd
        self.scope['APP_CONTEXT']['roster'] = []
        self.scope['tool_get_waiver_recommendations'] = lambda position=None, limit=5: {
            'candidate_pool': [{'name': 'Emanuel Wilson', 'position': 'RB'}],
            'matching_candidates_loaded': 1, 'transaction_model_result': 'NO_THRESHOLD_MOVE',
        }
        ctx = self.call('_derive_turn_context', 'should i drop marshawn lloyd for a waiver RB')
        evidence = self.call('_prefetch_evidence', 'should i drop marshawn lloyd for a waiver RB', ctx)
        self.assertEqual(evidence['drop_roster_verification'], 'not_in_loaded_roster_user_says_owned')
        self.assertEqual(evidence['drop_target'], 'MarShawn Lloyd')
        self.assertEqual(evidence['waivers']['candidate_pool'][0]['name'], 'Emanuel Wilson')

    def test_explicit_new_player_comparison_exits_waiver_swap(self):
        lloyd = {'name': 'MarShawn Lloyd', 'position': 'RB', 'rostered': True}
        self.players.append(lloyd)
        self.names[lloyd['name'].lower()] = lloyd
        first = self.call('_derive_turn_context', 'drop marshawn lloyd for a waiver rb')
        self.call('_persist_turn_context', first)
        next_turn = self.call('_derive_turn_context', 'tucker or worthy for ros')
        self.assertNotEqual(next_turn['effective_intent'], 'waiver_swap')
        self.assertIsNone(next_turn['waiver_drop_target'])

    def test_explicit_new_waiver_category_exits_lloyd_swap(self):
        lloyd = {'name': 'MarShawn Lloyd', 'position': 'RB', 'rostered': True}
        self.players.append(lloyd)
        self.names[lloyd['name'].lower()] = lloyd
        first = self.call('_derive_turn_context', 'drop marshawn lloyd for a waiver rb')
        self.call('_persist_turn_context', first)
        new_task = self.call('_derive_turn_context', 'best WR waiver pickup')
        self.assertEqual(new_task['effective_intent'], 'waiver')
        self.assertIsNone(new_task['waiver_drop_target'])
        self.assertEqual(new_task['position'], 'WR')

    def test_waiver_swap_prefetch_uses_one_mock_claude_call(self):
        lloyd = {'name': 'MarShawn Lloyd', 'position': 'RB', 'rostered': True}
        wilson = {'name': 'Emanuel Wilson', 'position': 'RB', 'rostered': False}
        self.players.extend([lloyd, wilson])
        self.names[lloyd['name'].lower()] = lloyd
        self.names[wilson['name'].lower()] = wilson
        self.scope['APP_CONTEXT']['waiver_candidates'] = [wilson]
        self.scope['tool_get_waiver_recommendations'] = lambda position=None, limit=5: {
            'candidate_pool': [{'name': 'Emanuel Wilson', 'position': 'RB'}],
            'matching_candidates_loaded': 1, 'transaction_model_result': 'NO_THRESHOLD_MOVE',
        }
        mock_response = types.SimpleNamespace(
            stop_reason='end_turn',
            usage=types.SimpleNamespace(input_tokens=250, output_tokens=60),
            content=[types.SimpleNamespace(type='text', text='Compare hold Lloyd vs Wilson.')],
        )
        calls = Mock(return_value=mock_response)
        self.scope['create_claude_message'] = calls
        with contextlib.redirect_stdout(io.StringIO()):
            response = self.call('ask_claude', 'should i drop marshawn lloyd for a waiver RB')
        self.assertIn('Wilson', response)
        calls.assert_called_once()
        self.assertEqual(calls.call_args.args[1], [])
        self.assertIn('Emanuel Wilson', str(calls.call_args))
        self.assertIn('drop_target', str(calls.call_args))

    def test_one_fake_claude_call_on_comparison(self):
        fake_response = types.SimpleNamespace(
            stop_reason='end_turn',
            usage=types.SimpleNamespace(input_tokens=200, output_tokens=50),
            content=[types.SimpleNamespace(type='text', text='Start Tre Tucker.')],
        )
        calls = Mock(return_value=fake_response)
        self.scope['create_claude_message'] = calls
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.call('ask_claude', 'tucker or worthy')
        self.assertEqual(result, 'Start Tre Tucker.')
        calls.assert_called_once()
        self.assertEqual(calls.call_args.args[1], [])  # no tool-selection round
        self.assertEqual(self.scope['DECISION_CONTEXT']['players'], ['Tre Tucker', 'Xavier Worthy'])

    def test_api_returns_usage_without_changing_cli_answer(self):
        fake_response = types.SimpleNamespace(
            stop_reason='end_turn',
            usage=types.SimpleNamespace(input_tokens=2431, output_tokens=212),
            content=[types.SimpleNamespace(type='text', text='Start Tre Tucker.')],
        )
        calls = Mock(return_value=fake_response)
        self.scope['create_claude_message'] = calls
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.call('ask_claude', 'tucker or worthy', return_details=True)
        self.assertEqual(result['answer'], 'Start Tre Tucker.')
        self.assertEqual(result['input_tokens'], 2431)
        self.assertEqual(result['output_tokens'], 212)
        self.assertEqual(result['api_calls'], 1)
        self.assertTrue(result['intent'])
        calls.assert_called_once()

    def test_journal_capture_strips_receipt_with_one_claude_call(self):
        import recommendation_journal as journal
        self.players[0]['rostered'] = True
        self.players[1]['rostered'] = True
        stored = []
        self.scope.update({
            'choice_instruction': journal.choice_instruction,
            'extract_choice': journal.extract_choice,
            'create_record': journal.create_record,
            'save_record': lambda rec: stored.append(rec),
        })
        fake_response = types.SimpleNamespace(
            stop_reason='end_turn',
            usage=types.SimpleNamespace(input_tokens=2200, output_tokens=120),
            content=[types.SimpleNamespace(type='text', text='Start Tre Tucker.\nFC_CHOICE_JSON: {"choice": "Tre Tucker"}')],
        )
        calls = Mock(return_value=fake_response)
        self.scope['create_claude_message'] = calls
        with contextlib.redirect_stdout(io.StringIO()):
            response = self.call('ask_claude', 'tucker or worthy')
        self.assertEqual(response, 'Start Tre Tucker.')
        calls.assert_called_once()
        self.assertIn('FC_CHOICE_JSON', calls.call_args.args[0][-1]['content'])
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]['claude_choice'], 'Tre Tucker')
        self.assertEqual(stored[0]['evaluation_scope'], 'weekly_head_to_head')
        self.assertEqual(stored[0]['usage']['input_tokens'], 2200)

    def test_trace_is_off_by_default(self):
        with unittest.mock.patch.dict(os.environ, {'FANTASY_DEBUG': '0'}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.call('_trace', 'ROUTER', intent='weekly')
            self.assertEqual(output.getvalue(), '')
        with unittest.mock.patch.dict(os.environ, {'FANTASY_DEBUG': '1'}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.call('_trace', 'ROUTER', intent='weekly')
            self.assertIn('intent="weekly"', output.getvalue())


class ScoringRegressionTests(unittest.TestCase):
    def test_espn_formats(self):
        from league_settings import get_league_scoring_context
        cases = [(0.0, 'STANDARD', 'STD'), (0.5, 'HALF_PPR', 'HALF'), (1.0, 'PPR', 'PPR')]
        for reception_points, expected, fantasypros in cases:
            with self.subTest(reception_points=reception_points):
                league = types.SimpleNamespace(settings=types.SimpleNamespace(
                    scoring_format=[{'abbr': 'REC', 'label': 'Receptions', 'points': reception_points}],
                    team_count=10))
                context = get_league_scoring_context(league)
                self.assertEqual(context['reception_points'], reception_points)
                self.assertEqual(context['scoring_label'], expected)
                self.assertEqual(context['fantasypros_scoring'], fantasypros)

    def test_unknown_scoring_is_marked_unknown(self):
        from league_settings import get_league_scoring_context
        league = types.SimpleNamespace(settings=types.SimpleNamespace(scoring_format=[]), teams=[])
        result = get_league_scoring_context(league)
        self.assertEqual(result['scoring_label'], 'UNKNOWN')
        self.assertIsNone(result['reception_points'])
        self.assertTrue(result['fantasypros_scoring_approximate'])

    def test_optimizer_excludes_hard_ineligible_players(self):
        scope = load_functions('lineup_optimizer.py', ['is_available'])
        self.assertFalse(scope['is_available']({'availability': {'can_play_this_week': False}}))
        self.assertTrue(scope['is_available']({'availability': {'can_play_this_week': True}}))

    def test_optimizer_display_uses_league_projection(self):
        scope = load_functions('lineup_optimizer.py', ['projected_points'])
        player = {'fp_points': 8.5, 'fp_ppr': 12.0}
        self.assertEqual(scope['projected_points'](player), 8.5)
        self.assertEqual(scope['projected_points']({'fp_ppr': 12.0}), 12.0)

    def test_reception_adjusted_actuals(self):
        scope = load_functions('decision_evaluator.py', ['_league_points_from_nflverse', '_snapshot_scoring'])
        row = {'fantasy_points': 10, 'fantasy_points_ppr': 17, 'receptions': 7}
        for rp, expected in [(0.0, 10), (0.5, 13.5), (1.0, 17)]:
            self.assertEqual(scope['_league_points_from_nflverse'](row, rp), expected)
        self.assertEqual(scope['_snapshot_scoring']({'league_scoring': {'scoring_label': 'HALF_PPR', 'reception_points': 0.5}})['reception_points'], 0.5)
        self.assertEqual(scope['_snapshot_scoring']({})['scoring_label'], 'LEGACY_PPR')
        with self.assertRaises(RuntimeError):
            scope['_snapshot_scoring']({'league_scoring': {'scoring_label': 'UNKNOWN', 'reception_points': None}})


if __name__ == '__main__':
    unittest.main()
