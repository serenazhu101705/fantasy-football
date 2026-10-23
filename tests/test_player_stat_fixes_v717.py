"""Regression coverage for v7.17 verified season stats and player summaries."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from espn_player_stats import parse_season_stats, parse_gamelog, public_player_data
from season_nfl_stats import current_season_player_stats, summarize_weekly_rows
from player_analysis import summarize_player, clear_analysis_cache
from matchup_live import player_profile


class SeasonIntegrityTests(unittest.TestCase):
    def test_no_career_stats_even_if_season_was_requested(self):
        career = {"categories": [{"name": "receiving", "labels": ["GP", "REC", "YDS", "TD"],
                                  "totals": ["108", "533", "8130", "56"]}]}
        self.assertEqual(parse_season_stats(career, season=2026), [])
        # Even a mislabeled year isn't a license to show 108 career games.
        self.assertEqual(parse_season_stats({**career, "season": 2026}, season=2026), [])

    def test_rejects_public_espn_unverified_career_totals(self):
        def fake_fetch(url):
            if "stats?" in url:
                return {"categories": [{"name": "receiving", "labels": ["GP", "REC", "YDS"],
                                        "totals": ["108", "533", "8130"]}]}
            return {"events": {}, "seasonTypes": []}
        actual = public_player_data(4567104, 2026, fetcher=fake_fetch)
        self.assertEqual(actual["season_stats"], [])
        self.assertEqual(actual["stats_source"], "unavailable")

    def test_accepts_genuine_season_and_clean_stat_labels(self):
        rows = parse_season_stats({"season": 2026, "categories": [
            {"name": "receiving", "labels": ["GP", "REC", "YDS", "TD"],
             "totals": ["5", "24", "304", "2"]}]}, season=2026)
        self.assertIn(("Receiving yards", 304), [(r["label"], r["value"]) for r in rows])
        self.assertFalse(any("receiving receiving" in r["label"].lower() for r in rows))

    def test_nflverse_current_season_only_and_correct_aggregation(self):
        sample = [
            {"player_display_name": "AJ Brown", "position": "WR", "season": 2025,
             "week": 7, "receptions": 100, "receiving_yards": 1500},
            {"player_display_name": "A.J. Brown", "position": "WR", "season": 2026,
             "week": 1, "receptions": 3, "receiving_yards": 74, "targets": 5},
            {"player_display_name": "AJ Brown", "position": "WR", "season": 2026,
             "week": 2, "receptions": 4, "receiving_yards": 50, "targets": 8},
            {"player_display_name": "AJ Brown", "position": "WR", "season": 2026,
             "week": 7, "receptions": 10, "receiving_yards": 300},  # not completed
        ]
        actual = current_season_player_stats("A.J. Brown", "WR", "NE", 2026, 7, rows=sample)
        named = {r["key"]: r["value"] for r in actual["season_stats"]}
        self.assertEqual(named["receptions"], 7)
        self.assertEqual(named["receiving_yards"], 124)
        self.assertEqual(actual["weeks_covered"], [1, 2])
        self.assertTrue(actual["verified_season"])

    def test_no_stats_returns_missing_not_career(self):
        actual = summarize_weekly_rows([{"season": 2025, "week": 4, "receptions": 17}], 2026, 7, "WR")
        self.assertEqual(actual["season_stats"], [])
        self.assertFalse(actual["verified_season"])

    def test_gamelog_career_history_not_returned_as_2026(self):
        history = {"events": {"1": {"gameDate": "2022-09-01", "gameResult": "W"},
                              "2": {"gameDate": "2026-09-08", "gameResult": "W"}},
                   "seasonTypes": [{"displayName": "Regular Season", "categories": [
                       {"labels": ["REC"], "events": [{"eventId": "1", "stats": ["3"]},
                                                      {"eventId": "2", "stats": ["4"]}]}]}]}
        self.assertEqual([g["event_id"] for g in parse_gamelog(history, season=2026)], ["2"])

    def test_profile_label_matches_verified_stats_not_sparse_espn_week(self):
        p = NS(playerId=42, name="A.J. Brown", proTeam="NE", position="WR", injuryStatus="ACTIVE",
               stats={6: {"points": 7.8, "breakdown": {"receivingReceptions": 3}}},
               avg_points=10, total_points=44)
        league = NS(year=2026, current_week=7, teams=[NS(roster=[p])])
        verified = {"season_stats": [{"key": "receptions", "label": "Receptions", "value": 27}],
                    "stats_source": "nflverse 2026 regular season (completed weeks)",
                    "weeks_covered": [1, 2, 3, 4, 5, 6], "verified_season": True}
        with patch("league_intelligence._get_box_scores", return_value=[]), \
             patch("matchup_live.player_public_bio", return_value={}), \
             patch("espn_player_stats.public_player_data", return_value={"season_stats": [], "recent_games": [], "stats_source": "unavailable"}), \
             patch("season_nfl_stats.current_season_player_stats", return_value=verified):
            result = player_profile(league, 42, 7)
        self.assertEqual(result["stat_coverage_weeks"], 6)
        self.assertEqual(result["season_stats_as_of_week"], 6)
        self.assertEqual(result["available_weeks_stat_totals"][0]["value"], 27)


class SummaryOutputTests(unittest.TestCase):
    def setUp(self):
        clear_analysis_cache()

    def test_sonnet_55_does_not_spend_budget_on_upfront_thinking(self):
        class Messages:
            def create(self, **params):
                self.params = params
                return NS(content=[NS(type="text", text="Season: 7 receptions. Last game: 3 catches. Week 7: projected 15.9.")],
                          usage=NS(input_tokens=85, output_tokens=60), stop_reason="end_turn")
        messages = Messages()
        result = summarize_player({"name": "A.J. Brown", "season_stats_as_of_week": 6},
                                  NS(messages=messages), "claude-sonnet-5-5")
        self.assertTrue(result["analysis"])
        self.assertEqual(messages.params["thinking"], {"type": "between_tools"})
        self.assertGreaterEqual(messages.params["max_tokens"], 800)

    def test_no_visible_text_preserves_clean_stop_reason(self):
        class Messages:
            def create(self, **params):
                return NS(content=[NS(type="thinking", thinking="not shown")], stop_reason="max_tokens")
        with self.assertRaisesRegex(ValueError, "stop_reason=max_tokens"):
            summarize_player({"name": "No text"}, NS(messages=Messages()), "claude-sonnet-5-5")


if __name__ == "__main__":
    unittest.main()
