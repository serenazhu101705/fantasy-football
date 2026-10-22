"""Projection-first optimal lineup regression tests; no network or Claude calls."""

import ast
from pathlib import Path
import sys
import unittest

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from lineup_optimizer import lineup_value, optimize_lineup, projected_points, projection_sources


def player(name, position, espn, fp, weekly, *, active=True):
    return {
        "espn_id": name,
        "name": name,
        "position": position,
        "espn_projected_points": espn,
        "fp_points": fp,
        "weekly_score": weekly,
        "availability": {"can_play_this_week": active},
    }


def roster(*, aj_espn=18, aj_fp=17, aj_active=True):
    return [
        player("Joe Burrow", "QB", 21, 20, 50),
        player("Jahmyr Gibbs", "RB", 17, 18, 50),
        player("Chase Brown", "RB", 15, 16, 50),
        player("Jaylen Warren", "RB", 13, 14, 50),
        player("A.J. Brown", "WR", aj_espn, aj_fp, 10, active=aj_active),
        player("DK Metcalf", "WR", 14, 13, 30),
        player("Germie Bernard", "WR", 7, 8, 90),
        player("Xavier Worthy", "WR", 10, 9, 35),
        player("Colston Loveland", "TE", 12, 11, 55),
        player("Steelers D/ST", "D/ST", 7, 8, 10),
        player("Will Reichard", "K", 9, 9, 10),
    ]


class ProjectionConsistencyTests(unittest.TestCase):
    def test_aj_brown_starts_despite_lower_weekly_advisory_score(self):
        result = optimize_lineup(roster())
        starters = {p["name"] for p in result["lineup"].values()}
        self.assertIn("A.J. Brown", starters)
        self.assertNotIn("Germie Bernard", starters)
        self.assertEqual(result["selection_method"], "projection_consensus_with_advisory_tiebreak")
        self.assertTrue(result["projection_complete"])

    def test_espn_projection_used_when_fantasypros_is_missing(self):
        result = optimize_lineup(roster(aj_fp=None))
        names = {p["name"] for p in result["lineup"].values()}
        self.assertIn("A.J. Brown", names)
        self.assertNotIn("Germie Bernard", names)
        self.assertAlmostEqual(projected_points(roster(aj_fp=None)[4]), 18.0)

    def test_missing_espn_is_not_scored_as_zero(self):
        self.assertEqual(projected_points(player("A", "WR", None, 12, 10)), 12)
        self.assertEqual(projected_points(player("B", "WR", 18, None, 10)), 18)
        self.assertEqual(projected_points(player("C", "WR", 18, 12, 10)), 15)
        self.assertEqual(projection_sources(player("D", "WR", None, None, 10)), {})

    def test_out_players_never_start(self):
        result = optimize_lineup(roster(aj_active=False))
        names = {p["name"] for p in result["lineup"].values()}
        self.assertNotIn("A.J. Brown", names)

    def test_advisory_tie_break_cannot_override_large_projection_gap(self):
        better = player("Higher", "WR", 17, 17, 0)
        worse = player("Lower", "WR", 7, 7, 100)
        self.assertGreater(lineup_value(better), lineup_value(worse))
        # Projections within a few tenths can legitimately be separated by
        # the existing advisory score.
        close_a = player("Close A", "WR", 12.0, 12.0, 10)
        close_b = player("Close B", "WR", 11.9, 11.9, 60)
        self.assertGreater(lineup_value(close_b), lineup_value(close_a))

    def test_projected_total_is_consensus_not_fantasypros_only(self):
        result = optimize_lineup(roster())
        expected = sum(projected_points(p) for p in result["lineup"].values())
        self.assertEqual(result["projected_total"], round(expected, 2))
        self.assertEqual(result["projection_coverage"], "9/9")
        self.assertEqual(result["lineup_score"], round(sum(p["weekly_score"] for p in result["lineup"].values()), 2))

    def test_missing_projection_is_disclosed(self):
        data = roster()
        data[0]["espn_projected_points"] = None
        data[0]["fp_points"] = None
        result = optimize_lineup(data)
        self.assertFalse(result["projection_complete"])
        self.assertEqual(result["projection_coverage"], "8/9")

    def test_llm_lineup_tool_exposes_same_projection_basis(self):
        # Extract the production function to avoid importing expensive live clients.
        source = (APP / "llm.py").read_text()
        tree = ast.parse(source)
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "tool_get_optimal_lineup"]
        self.assertEqual(len(nodes), 1)
        result = optimize_lineup(roster())
        scope = {
            "APP_CONTEXT": {"optimized": result, "current_week": 7},
            "_current_week": lambda: 7,
            "compact_player": lambda p, **kwargs: {"name": p["name"]},
            "_lineup_projected_points": projected_points,
            "_lineup_projection_sources": projection_sources,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), "llm.py", "exec"), scope)
        payload = scope["tool_get_optimal_lineup"]()
        self.assertEqual(payload["selection_method"], result["selection_method"])
        self.assertEqual(payload["projected_total"], result["projected_total"])
        self.assertEqual(payload["lineup"]["WR1"]["name"], "A.J. Brown")
        self.assertEqual(payload["lineup"]["WR1"]["lineup_projection"], 17.5)
        self.assertEqual(payload["lineup"]["WR1"]["lineup_projection_sources"], ["ESPN", "FantasyPros"])


if __name__ == "__main__":
    unittest.main()
