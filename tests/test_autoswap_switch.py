"""Offline tests for the GitHub-backed Auto Swap switch. No live ESPN writes."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import auto_swap_control as ctrl


class AutoSwapSwitchTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "FANTASY_AUTOSWAP_GITHUB_REPO": "example/fantasy-football",
            "FANTASY_AUTOSWAP_GITHUB_TOKEN": "not-a-real-token",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_missing_config_cannot_enable(self):
        with patch.dict(os.environ, {"FANTASY_AUTOSWAP_GITHUB_TOKEN": ""}):
            self.assertFalse(ctrl.website_status()["configurable"])
            with self.assertRaises(ctrl.AutoSwapControlError):
                ctrl.website_set(True)

    def test_reads_actual_remote_state(self):
        with patch.object(ctrl, "_send", return_value=(200, {"value": "1"})):
            self.assertTrue(ctrl.website_status()["enabled"])
        with patch.object(ctrl, "_send", return_value=(200, {"value": "0"})):
            self.assertFalse(ctrl.website_status()["enabled"])

    def test_write_confirms_readback(self):
        calls = []
        def fake(method, repo, token, body=None):
            calls.append((method, body))
            if method == "PATCH": return 204, {}
            if method == "GET": return 200, {"value": "1"}
            raise AssertionError(method)
        with patch.object(ctrl, "_send", side_effect=fake):
            self.assertTrue(ctrl.website_set(True)["enabled"])
        self.assertEqual(calls, [("PATCH", {"name": ctrl.VARIABLE, "value": "1"}), ("GET", None)])

    def test_creates_missing_variable(self):
        with patch.object(ctrl, "_send", side_effect=[(404, {}), (201, {}), (200, {"value": "0"})]) as mock:
            self.assertFalse(ctrl.website_set(False)["enabled"])
            self.assertEqual(mock.call_args_list[1].args[0], "POST")

    def test_failed_write_does_not_claim_success(self):
        with patch.object(ctrl, "_send", return_value=(403, {})):
            with self.assertRaises(ctrl.AutoSwapControlError):
                ctrl.website_set(True)
        with patch.object(ctrl, "_send", side_effect=[(204, {}), (200, {"value": "0"})]):
            with self.assertRaises(ctrl.AutoSwapControlError):
                ctrl.website_set(True)

    def test_worker_denies_without_explicit_opt_in(self):
        with patch.dict(os.environ, {"FANTASY_PERSONAL_EMAIL_MODE":"1",
                                  "FANTASY_REPOSITORY_PRIVATE":"true",
                                  "FANTASY_AUTO_SWAP_ENABLED":"0",
                                  "ESPN_WRITES_ENABLED":"1",
                                  "ESPN_AUTO_SWAPS_ENABLED":"1",
                                  "GITHUB_REPOSITORY":"example/fantasy-football",
                                  "GITHUB_TOKEN":"test", "FANTASY_OWNER_EMAIL":"owner@example.com"}):
            with patch.object(ctrl, "_send") as request:
                self.assertFalse(ctrl.worker_is_enabled())
                request.assert_not_called()

    def test_worker_rechecks_remote_flag_each_time(self):
        with patch.dict(os.environ, {"FANTASY_PERSONAL_EMAIL_MODE":"1",
                                  "FANTASY_REPOSITORY_PRIVATE":"true",
                                  "FANTASY_AUTO_SWAP_ENABLED":"1",
                                  "ESPN_WRITES_ENABLED":"1",
                                  "ESPN_AUTO_SWAPS_ENABLED":"1",
                                  "GITHUB_REPOSITORY":"example/fantasy-football",
                                  "GITHUB_TOKEN":"test", "FANTASY_OWNER_EMAIL":"owner@example.com"}):
            with patch.object(ctrl, "_send", side_effect=[(200, {"value": "1"}), (200, {"value": "0"})]):
                self.assertTrue(ctrl.worker_is_enabled())
                self.assertFalse(ctrl.worker_is_enabled())
            with patch.object(ctrl, "_send", side_effect=ctrl.AutoSwapControlError("offline")):
                self.assertFalse(ctrl.worker_is_enabled())
            with patch.dict(os.environ, {"FANTASY_REPOSITORY_PRIVATE": "false"}):
                self.assertFalse(ctrl.worker_is_enabled())

    def test_bootstrap_sets_and_clears_owner_preference(self):
        # Keep the actual confirmed SQLite preference in sync, not just the UI.
        with tempfile.TemporaryDirectory() as temp:
            dbfile = str(Path(temp) / "subscriptions.db")
            with patch.dict(os.environ, {"FANTASY_SUBSCRIPTIONS_DB": dbfile,
                                      "FANTASY_PERSONAL_EMAIL_MODE": "1",
                                      "FANTASY_REPOSITORY_PRIVATE": "true",
                                      "FANTASY_OWNER_EMAIL": "owner@example.com",
                                      "FANTASY_AUTO_SWAP_ENABLED": "1",
                                      "ESPN_WRITES_ENABLED": "1",
                                      "ESPN_AUTO_SWAPS_ENABLED": "1"}):
                for name in ("email_subscriptions", "github_email_bootstrap"):
                    sys.modules.pop(name, None)
                from github_email_bootstrap import ensure_owner_subscription
                from email_subscriptions import _db
                self.assertEqual(ensure_owner_subscription(), "initialized")
                with _db() as db:
                    self.assertEqual(db.execute("SELECT auto_swap FROM subscribers").fetchone()[0], 1)
                with patch.dict(os.environ, {"FANTASY_AUTO_SWAP_ENABLED": "0",
                                          "ESPN_WRITES_ENABLED": "0",
                                          "ESPN_AUTO_SWAPS_ENABLED": "0"}):
                    self.assertEqual(ensure_owner_subscription(), "existing")
                    with _db() as db:
                        self.assertEqual(db.execute("SELECT auto_swap FROM subscribers").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
