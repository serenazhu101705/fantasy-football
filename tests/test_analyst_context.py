import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.analyst_context import analyst_context

class AnalystTests(unittest.TestCase):
    def test_bye_not_missing_or_zero(self):
        def loader(league, week):
            games = {'CIN': {'state': 'FINAL', 'opponent': 'BAL'}} if week == 7 else {'BAL': {'state': 'FINAL', 'opponent': 'CLE'}}
            return {'status': 'available', 'games': games}
        profile = {'week': 8, 'nfl_team': 'CIN', 'recent_games': [{'week': 7, 'fantasy_points': 28.88}]}
        result = analyst_context(profile, league=object(), status_loader=loader)
        assert next(x for x in result['last_five_weeks'] if x['week'] == 6)['classification'] == 'BYE'
        assert next(x for x in result['last_five_weeks'] if x['week'] == 7)['classification'] == 'PLAYED'

    def test_missing_score_not_bye_if_team_played(self):
        def loader(league, week):
            return {'status': 'available', 'games': {'CIN': {'state': 'FINAL'}}}
        result = analyst_context({'week': 7, 'nfl_team': 'CIN', 'recent_games': []}, league=object(), status_loader=loader)
        assert result['last_five_weeks'][-1]['classification'] == 'NO_PLAYER_SCORE_IN_COMPLETED_TEAM_GAME'

    def test_unavailable_schedule_not_bye(self):
        result = analyst_context({'week': 8, 'nfl_team': 'CIN', 'recent_games': []}, league=object(), status_loader=lambda l,w: {'status': 'unavailable', 'games': {}})
        assert all(x['classification'] == 'UNKNOWN' for x in result['last_five_weeks'])

if __name__ == '__main__':
    unittest.main()
