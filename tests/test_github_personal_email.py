"""Offline coverage for personal scheduled GitHub delivery; no external calls."""
from __future__ import annotations

from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
sys.path.insert(0, str(ROOT / '.github' / 'scripts'))
import email_subscriptions as mail
import github_email_bootstrap as bootstrap
import restore_email_state as state


class GithubEmailTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.sqlite3'
        self.patchers = [
            patch.object(mail, 'DB_PATH', self.path),
            patch.dict(os.environ, {
                'FANTASY_PERSONAL_EMAIL_MODE': '1',
                'FANTASY_REPOSITORY_PRIVATE': 'true',
                'FANTASY_OWNER_EMAIL': 'owner@example.com',
                'GITHUB_REPOSITORY': 'privateowner/fantasy-coach',
                'ESPN_WRITES_ENABLED': '0',
                'ESPN_AUTO_SWAPS_ENABLED': '0',
            }),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_bootstrap_is_confirmed_and_does_not_recreate(self):
        self.assertEqual(bootstrap.ensure_owner_subscription(), 'initialized')
        self.assertEqual(bootstrap.ensure_owner_subscription(), 'existing')
        rows = mail.confirmed_subscribers()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['thursday_lineup'])
        self.assertTrue(rows[0]['monday_recap'])
        self.assertTrue(rows[0]['injury_news'])
        self.assertTrue(rows[0]['bye_alerts'])
        self.assertFalse(rows[0]['auto_swap'])

    def test_same_event_sent_once_and_no_fake_unsubscribe_link(self):
        bootstrap.ensure_owner_subscription()
        user = mail.confirmed_subscribers()[0]
        messages = []
        sender = lambda email, subject, body: messages.append((email, subject, body))
        self.assertTrue(mail.send_digest(user, 'thursday_lineup', '2026-7', 'Lineup', 'report', transport=sender))
        self.assertFalse(mail.send_digest(user, 'thursday_lineup', '2026-7', 'Lineup', 'report', transport=sender))
        self.assertEqual(len(messages), 1)
        self.assertNotIn('localhost', messages[0][2])
        self.assertIn('github.com/privateowner/fantasy-coach/actions', messages[0][2])
        self.assertTrue(mail.was_sent('owner@example.com','thursday_lineup','2026-7'))

    def test_rejects_public_repository(self):
        with patch.dict(os.environ, {'FANTASY_REPOSITORY_PRIVATE':'false'}):
            with self.assertRaises(RuntimeError):
                bootstrap.ensure_owner_subscription()

    def test_rejects_another_confirmed_recipient(self):
        bootstrap.ensure_owner_subscription()
        with mail._db() as db:
            db.execute("INSERT INTO subscribers (email,confirmed,confirm_hash,confirm_expiry,unsubscribe_hash,thursday_lineup,monday_recap,injury_news,bye_alerts,auto_swap,created_at) VALUES ('other@example.com',1,'x','x','x',1,1,1,1,0,'x')")
        with self.assertRaises(RuntimeError):
            bootstrap.ensure_owner_subscription()

    def test_rejects_lineup_writes(self):
        with patch.dict(os.environ, {'ESPN_WRITES_ENABLED':'1'}):
            with self.assertRaises(RuntimeError):
                bootstrap.ensure_owner_subscription()

    def test_valid_latest_archive_restored(self):
        bootstrap.ensure_owner_subscription()
        before = self.path.read_bytes()
        self.path.unlink()
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as archive:
            archive.writestr('fantasy_subscriptions.sqlite3', before)
        def fake_get(uri):
            if uri.endswith('per_page=100'):
                return json.dumps({'artifacts': [
                    {'id':8,'created_at':'2026-10-22T10:00:00Z','expired':False},
                    {'id':9,'created_at':'2026-10-23T10:00:00Z','expired':False},
                ]}).encode()
            self.assertIn('/9/zip', uri)
            return raw.getvalue()
        with patch.object(state, 'github_get', side_effect=fake_get):
            self.assertEqual(state.restore('privateowner/fantasy-coach', self.path),'restored')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(mail.confirmed_subscribers()), 1)

    def test_missing_state_can_be_initialized(self):
        with patch.object(state, 'github_get', return_value=b'{"artifacts":[]}'):
            self.assertEqual(state.restore('privateowner/fantasy-coach', self.path), 'new')
        self.assertEqual(bootstrap.ensure_owner_subscription(), 'initialized')

    def test_bad_state_fails_closed(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as archive:
            archive.writestr('fantasy_subscriptions.sqlite3', b'not sqlite')
        values = iter([b'{"artifacts":[{"id":99,"created_at":"2026-10-23T12:00:00Z","expired":false}]}',raw.getvalue()])
        with patch.object(state, 'github_get', side_effect=lambda _: next(values)):
            with self.assertRaises(RuntimeError):
                state.restore('privateowner/fantasy-coach', self.path)
        self.assertFalse(self.path.exists())

if __name__ == '__main__':
    unittest.main()
