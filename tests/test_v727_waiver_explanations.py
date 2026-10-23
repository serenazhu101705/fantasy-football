"""Offline verification for team-specific waiver reasoning and honesty."""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from waiver_explanations import explain_candidate, personalized_waivers, browser_explanations
from browsing import waiver_browser


class WaiverImpactTests(unittest.TestCase):
    def setUp(self):
        self.roster = [
            {'espn_id': 1, 'name': 'Roster RB', 'position': 'RB', 'espn_projected_points': 9},
            {'espn_id': 2, 'name': 'Other RB', 'position': 'RB', 'espn_projected_points': 14},
            {'espn_id': 3, 'name': 'Bench RB', 'position': 'RB', 'espn_projected_points': 8},
            {'espn_id': 4, 'name': 'Top WR', 'position': 'WR', 'espn_projected_points': 17},
        ]
        self.add = {'espn_id': 10, 'name': 'New RB', 'position': 'RB', 'nfl_team': 'MIN',
                    'espn_projected_points': 13.5, 'fp_points': 14.3,
                    'fantasypros_ros_rank': 21, 'waiver_prescreen_score': 81}
        self.extra = {'espn_id': 11, 'name': 'Depth WR', 'position': 'WR', 'espn_projected_points': 7,
                      'waiver_prescreen_score': 65}
        self.transaction = {
            'add': self.add, 'drop': self.roster[2], 'recommendation': 'ADD',
            'recommendation_reason': 'Improves long-term roster value without hurting this week.',
            'projected_points_delta': 1.5, 'ros_delta': 6.2,
            'before_lineup': {'projection_complete': True},
            'after_lineup': {'projection_complete': True},
        }
        self.context = {'roster': self.roster, 'waiver_candidates':[self.add, self.extra],
                        'waiver_transactions':[self.transaction], 'current_week': 7}

    def test_ranks_actual_evaluator_move_first_and_names_drop(self):
        data = personalized_waivers(self.context, limit=2)
        self.assertEqual([r['player']['name'] for r in data['recommendations']], ['New RB','Depth WR'])
        top = data['recommendations'][0]
        self.assertTrue(top['actionable'])
        self.assertEqual(top['suggested_drop']['name'], 'Bench RB')
        self.assertIn('+1.5 points', top['this_week'])
        self.assertIn('3 RBs', top['fit'])
        self.assertIn('5.5 points above', top['roster_comparison'])
        self.assertIn('not a guaranteed lineup', top['roster_comparison'])
        self.assertEqual(top['projection_sources'], {'ESPN':13.5, 'FantasyPros':14.3})
        self.assertEqual(data['actionable_count'],1)

    def test_watchlist_never_invents_drop_or_upgrade(self):
        watch = personalized_waivers(self.context)['recommendations'][1]
        self.assertFalse(watch['actionable'])
        self.assertIsNone(watch['suggested_drop'])
        self.assertIn('No add/drop cleared', watch['decision'])
        self.assertNotIn('full projected lineup', watch['this_week'])

    def test_missing_projection_coverage_does_not_claim_point_gain(self):
        t = dict(self.transaction, before_lineup={'projection_complete': False})
        result = explain_candidate(self.context, self.add, t)
        self.assertNotIn('+1.5 points',result['this_week'])
        self.assertIn('isn\'t verified',result['this_week'])

    def test_injury_bye_and_no_projection_are_explicit(self):
        candidate = {'name':'Bye RB', 'position':'RB', 'on_bye_week':True}
        impact = explain_candidate(self.context, candidate)
        self.assertIn('on bye', impact['this_week'])
        self.assertIsNone(impact['suggested_drop'])
        self.assertIn('unconfirmed', impact['rest_of_season'])
        injured = {'name': 'Injury RB', 'position':'RB', 'injury_status':'QUESTIONABLE'}
        self.assertIn('QUESTIONABLE',explain_candidate(self.context,injured)['this_week'])

    def test_own_player_is_never_recommended_as_free_agent(self):
        self.context['waiver_candidates'].append({**self.roster[0], 'waiver_prescreen_score':100})
        names = [r['player']['name'] for r in personalized_waivers(self.context)['recommendations']]
        self.assertNotIn('Roster RB', names)

    def test_stale_drop_never_surfaces_as_actionable(self):
        self.context['waiver_transactions'][0]['drop'] = {'name': 'No longer owned', 'position': 'WR', 'espn_id':999}
        data = personalized_waivers(self.context, limit=1)
        self.assertFalse(data['recommendations'][0]['actionable'])

    def test_position_filter_applies_to_recommended_and_watchlist(self):
        data = personalized_waivers(self.context,position='WR')
        self.assertEqual([r['player']['name'] for r in data['recommendations']],['Depth WR'])
        with self.assertRaises(ValueError):
            personalized_waivers(self.context,position='BAD')

    def test_each_browsed_free_agent_has_matching_explanation(self):
        self.context['_browser_free_agents'] = [self.add,self.extra]
        data = waiver_browser(self.context, page=1, page_size=10)
        self.assertEqual(len(data['candidate_pool']),len(data['candidate_explanations']))
        self.assertEqual([p['name'] for p in data['candidate_pool']],
                         [r['player']['name'] for r in data['candidate_explanations']])
        self.assertTrue(data['candidate_explanations'][0]['actionable'])
        self.assertFalse(data['candidate_explanations'][1]['actionable'])
        self.assertEqual(data['total'],2)

    def test_pagination_does_not_lose_explanations(self):
        self.context['_browser_free_agents'] = [self.add,self.extra]
        one = waiver_browser(self.context, page=1, page_size=1)
        two = waiver_browser(self.context, page=2, page_size=1)
        self.assertEqual(len(one['candidate_explanations']),1)
        self.assertEqual(len(two['candidate_explanations']),1)
        self.assertNotEqual(one['candidate_explanations'][0]['player']['name'],two['candidate_explanations'][0]['player']['name'])

    def test_read_only_with_no_claude_dependency(self):
        before = [dict(p) for p in self.context['waiver_candidates']]
        personalized_waivers(self.context)
        browser_explanations(self.context,[self.add,self.extra])
        self.assertEqual(before, self.context['waiver_candidates'])

if __name__ == '__main__':
    unittest.main()
