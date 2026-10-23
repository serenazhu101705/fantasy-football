"""Offline tests of Resend HTTPS email delivery; no external requests or charges."""
import io
import json
import os
from tempfile import TemporaryDirectory
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import email_subscriptions as mail


class DummyReply:
    status = 200
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, n=16384):
        return self.payload


class ResendTransportTests(unittest.TestCase):
    def env(self):
        return patch.dict(os.environ, {
            'FANTASY_EMAIL_TRANSPORT': 'resend',
            'RESEND_API_KEY': 're_example_fake_key',
            'RESEND_FROM_EMAIL': 'Fantasy Coach <coach@example.org>',
        }, clear=False)

    def test_delivers_single_https_email_with_expected_payload(self):
        seen = []
        def respond(request, timeout):
            seen.append((request, timeout))
            return DummyReply(b'{"id":"test_123"}')
        with self.env(), patch.object(mail, 'urlopen', side_effect=respond):
            msg_id = mail.send_email('you@example.net', 'Weekly report', 'Your lineup...')
        self.assertEqual(msg_id, 'test_123')
        self.assertEqual(len(seen), 1)
        req, timeout = seen[0]
        self.assertEqual(req.full_url, 'https://api.resend.com/emails')
        self.assertEqual(req.get_method(), 'POST')
        data = json.loads(req.data)
        self.assertEqual(data['to'], ['you@example.net'])
        self.assertEqual(data['text'], 'Your lineup...')
        self.assertEqual(data['from'], 'Fantasy Coach <coach@example.org>')
        self.assertEqual(timeout, 20)

    def test_invalid_sender_fails_without_network(self):
        with self.env(), patch.dict(os.environ, {'RESEND_FROM_EMAIL': '', 'SMTP_FROM': ''}), patch.object(mail, 'urlopen') as send:
            with self.assertRaisesRegex(RuntimeError, 'RESEND_FROM_EMAIL'):
                mail.send_email('you@example.net', 'Hello', 'Body')
            send.assert_not_called()

    def test_provider_failure_does_not_print_secret(self):
        with self.env(), patch.object(mail, 'urlopen', side_effect=HTTPError('https://api.resend.com/emails', 403, 'forbidden', {}, io.BytesIO(b'sensitive response'))):
            with self.assertRaises(RuntimeError) as context:
                mail.send_email('you@example.net', 'Hello', 'Body')
            self.assertIn('HTTP 403', str(context.exception))
            self.assertNotIn('re_example_fake_key', str(context.exception))
            self.assertNotIn('sensitive response', str(context.exception))

    def test_empty_acceptance_id_is_failure(self):
        with self.env(), patch.object(mail, 'urlopen', return_value=DummyReply(b'{"id":null}')):
            with self.assertRaisesRegex(RuntimeError, 'did not confirm acceptance'):
                mail.send_email('you@example.net', 'Hello', 'Body')

    def test_subscription_confirmation_and_digest_use_https_once(self):
        seen = []
        def respond(req, timeout):
            seen.append(json.loads(req.data))
            return DummyReply(json.dumps({"id": f"test_{len(seen)}"}).encode())
        with TemporaryDirectory() as temp_dir, self.env(), \
             patch.dict(os.environ, {'FANTASY_PUBLIC_API_URL':'https://coach.example.net'}), \
             patch.object(mail, 'DB_PATH', Path(temp_dir) / 'subscribers.sqlite3'), \
             patch.object(mail, 'urlopen', side_effect=respond):
            status = mail.subscribe('you@example.net', {'thursday_lineup': True})
            self.assertEqual(status['status'], 'pending_confirmation')
            # The sent confirmation must point to the hosted HTTPS origin.
            self.assertIn('https://coach.example.net/api/subscriptions/confirm?', seen[0]['text'])
            from urllib.parse import urlparse, parse_qs
            link = seen[0]['text'].split('https://coach.example.net/api/subscriptions/confirm?',1)[1].split()[0]
            token = parse_qs(link)['token'][0]
            self.assertTrue(mail.confirm(token))
            sub = mail.confirmed_subscribers()[0]
            self.assertTrue(mail.send_digest(sub, 'thursday_lineup','2026-7','Lineup','Start Burrow'))
            self.assertFalse(mail.send_digest(sub, 'thursday_lineup','2026-7','Lineup','Start Burrow'))
            self.assertEqual(len(seen), 2)
            self.assertIn('Unsubscribe: https://coach.example.net', seen[1]['text'])

    def test_existing_injected_transport_preserved(self):
        sent = []
        with self.env(), patch.object(mail, 'urlopen') as http:
            mail.send_email('a@example.org', 'Subj', 'Body', transport=lambda *a: sent.append(a))
            http.assert_not_called()
        self.assertEqual(sent, [('a@example.org', 'Subj', 'Body')])

if __name__ == '__main__':
    unittest.main()
