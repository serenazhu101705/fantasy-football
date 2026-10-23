"""Regression for self-comparison and scoreless dated NFL game logs."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from lineup_explanations import explain_lineup
from season_nfl_stats import summarize_weekly_rows
from matchup_live import player_profile


class LineupAlternativesTests(unittest.TestCase):
    def test_browser_restored_ids_do_not_make_starters_their_own_alternative(self):
        def p(name, pos, score, pid=None, slot=None):
            return {"name": name, "position": pos, "team": "A",
                    "espn_projection": score, "fantasypros_projection": score,
                    **({"espn_id": pid} if pid else {}),
                    **({"lineup_slot": slot} if slot else {})}
        qb = p("Joe Burrow", "QB", 19.6, 101)
        rb1 = p("Jahmyr Gibbs", "RB", 25.8, 102)
        rb2 = p("Chase Brown", "RB", 15.1, 103)
        wr1 = p("A.J. Brown", "WR", 15.8, 104)
        wr2 = p("DK Metcalf", "WR", 14.0, 105)
        flex = p("Jaylen Warren", "RB", 17.0, 106)
        lineup = {"lineup": {"QB": qb, "RB1": rb1, "RB2": rb2,
                              "WR1": wr1, "WR2": wr2, "FLEX": flex}}
        # Compact tool_get_roster rows carry NO espn_id; formerly this caused
        # Burrow vs Burrow, Gibbs vs Gibbs, and Gibbs vs Chase Brown.
        roster = [dict({k: v for k, v in starter.items() if k != "espn_id"}, lineup_slot="BE")
                  for starter in lineup["lineup"].values()]
        roster += [p("Trevor Lawrence", "QB", 17.0, slot="BE"),
                   p("Blake Corum", "RB", 10.0, slot="BE"),
                   p("Germie Bernard", "WR", 13.2, slot="BE"),
                   p("Pat Freiermuth", "TE", 9.4, slot="BE")]
        result = explain_lineup(lineup, roster)["slot_explanations"]
        self.assertEqual(result["QB"]["best_eligible_nonstarter"]["name"], "Trevor Lawrence")
        self.assertEqual(result["RB1"]["best_eligible_nonstarter"]["name"], "Blake Corum")
        self.assertEqual(result["RB2"]["best_eligible_nonstarter"]["name"], "Blake Corum")
        self.assertAlmostEqual(result["RB2"]["projection_advantage"], 5.1)
        self.assertEqual(result["WR1"]["best_eligible_nonstarter"]["name"], "Germie Bernard")
        self.assertEqual(result["FLEX"]["best_eligible_nonstarter"]["name"], "Germie Bernard")
        self.assertNotIn("Joe Burrow leads Joe Burrow", result["QB"]["reason"])

    def test_bye_and_ir_are_not_used_as_alternatives(self):
        lineup = {"lineup": {"QB": {"name": "Joe Burrow", "position": "QB", "espn_id": 1,
                                     "espn_projection": 20}}}
        roster = [{"name": "Joe Burrow", "position": "QB"},
                  {"name": "Trevor Lawrence", "position": "QB", "espn_projection": 10,
                   "on_bye_week": True},
                  {"name": "Other QB", "position": "QB", "espn_projection": 12,
                   "can_play_this_week": False},
                  {"name": "IR QB", "position": "QB", "lineup_slot": "IR", "espn_projection": 14}]
        result = explain_lineup(lineup, roster)["slot_explanations"]["QB"]
        self.assertIsNone(result["best_eligible_nonstarter"])
        self.assertIn("No other eligible", result["reason"])


class RecentGamesTests(unittest.TestCase):
    def test_verified_weekly_stats_produce_fantasy_points_and_stats(self):
        rows = [{"season": 2026, "week": 4, "season_type": "REG", "fantasy_points": 11.5,
                 "fantasy_points_ppr": 16.5, "receptions": 5, "targets": 7, "receiving_yards": 85},
                {"season": 2026, "week": 5, "season_type": "REG", "fantasy_points": 7.0,
                 "fantasy_points_ppr": 10.0, "receptions": 3, "targets": 5, "receiving_yards": 70},
                {"season": 2025, "week": 6, "fantasy_points": 100}]
        report = summarize_weekly_rows(rows, 2026, 7, "WR", reception_points=1.0)
        self.assertEqual([x["week"] for x in report["recent_games"]], [5, 4])
        self.assertEqual(report["recent_games"][0]["fantasy_points"], 10.0)
        self.assertEqual(report["recent_games"][1]["fantasy_points"], 16.5)
        self.assertIn("Receptions", [x["label"] for x in report["recent_games"][0]["stats"]])

    def test_half_ppr_scoring_uses_half_point_per_catch(self):
        rows = [{"season": 2026, "week": 4, "fantasy_points_ppr": 16,
                 "receptions": 6, "receiving_yards": 100}]
        report = summarize_weekly_rows(rows, 2026, 7, "WR", reception_points=0.5)
        self.assertEqual(report["recent_games"][0]["fantasy_points"], 13.0)

    def test_zero_placeholder_standard_does_not_hide_real_ppr_score(self):
        rows = [{"season": 2026, "week": 4, "fantasy_points": 0,
                 "fantasy_points_ppr": 16.5, "receptions": 5, "receiving_yards": 85}]
        report = summarize_weekly_rows(rows, 2026, 7, "WR", reception_points=1.0)
        self.assertEqual(report["recent_games"][0]["fantasy_points"], 16.5)

    def test_profile_merges_verified_weeks_and_preserves_espn_points(self):
        p = NS(playerId=55, name="A.J. Brown", proTeam="NE", position="WR",
               injuryStatus="ACTIVE", total_points=48, avg_points=12,
               stats={6: {"points": 9.2, "projected_points": 14.0,
                          "breakdown": {"receivingReceptions": 3}}})
        league = NS(year=2026, current_week=7, settings=NS(scoring_format="PPR"),
                    teams=[NS(roster=[p])])
        verified = {"season_stats": [{"key": "receptions", "label": "Receptions", "value": 6}],
                    "weeks_covered": [5, 6], "stats_source": "nflverse 2026", "verified_season": True,
                    "recent_games": [{"week": 6, "fantasy_points": 8.9,
                                      "stats": [{"key": "receptions", "label": "Receptions", "value": 3}]},
                                     {"week": 5, "fantasy_points": 15.4,
                                      "stats": [{"key": "receptions", "label": "Receptions", "value": 3}]}]}
        with patch("league_intelligence._get_box_scores", return_value=[]), \
             patch("matchup_live.player_public_bio", return_value={}), \
             patch("espn_player_stats.public_player_data", return_value={"season_stats": [], "recent_games": [], "stats_source": "unavailable"}), \
             patch("season_nfl_stats.current_season_player_stats", return_value=verified):
            result = player_profile(league, 55, 7)
        self.assertEqual([g["week"] for g in result["recent_games"]], [6, 5])
        self.assertEqual(result["recent_games"][0]["fantasy_points"], 9.2)
        self.assertEqual(result["recent_games"][0]["fantasy_points_source"], "ESPN Fantasy league score")
        self.assertEqual(result["recent_games"][1]["fantasy_points"], 15.4)
        self.assertEqual(result["last_completed_game"]["week"], 6)
        self.assertEqual(result["recent_games"][0]["projected_points"], 14.0)


if __name__ == "__main__":
    unittest.main()
