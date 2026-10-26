import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from weekly_analysis_cache import get_report, save_report

class WeeklyReportTests(unittest.TestCase):
    def test_opt_in_persistence_and_week_rollover(self):
        with tempfile.TemporaryDirectory() as root:
            old = os.environ.get('FANTASY_ANALYSIS_CACHE_DB')
            os.environ['FANTASY_ANALYSIS_CACHE_DB'] = str(Path(root) / 'store.sqlite3')
            try:
                self.assertIsNone(get_report(2026, 7, 123, 'league'))
                save_report(2026, 7, 123, {'analysis':'A weekly scouting report','source':'Claude'}, 'league')
                item = get_report(2026, 7, 123, 'league')
                self.assertTrue(item['cached'])
                self.assertEqual(item['input_tokens'],0)
                self.assertIsNone(get_report(2026, 8, 123, 'league'))
                self.assertIsNone(get_report(2026, 7, 123, 'league'))
            finally:
                if old is None: os.environ.pop('FANTASY_ANALYSIS_CACHE_DB',None)
                else: os.environ['FANTASY_ANALYSIS_CACHE_DB'] = old
    def test_players_separate(self):
        with tempfile.TemporaryDirectory() as root:
            old = os.environ.get('FANTASY_ANALYSIS_CACHE_DB')
            os.environ['FANTASY_ANALYSIS_CACHE_DB'] = str(Path(root) / 'store.sqlite3')
            try:
                save_report(2026, 7, 123, {'analysis':'one'})
                self.assertIsNone(get_report(2026, 7, 456))
            finally:
                if old is None: os.environ.pop('FANTASY_ANALYSIS_CACHE_DB',None)
                else: os.environ['FANTASY_ANALYSIS_CACHE_DB'] = old

if __name__ == '__main__': unittest.main()
