"""Check native connection guards without network/ESPN/Claude access."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app'
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mobile_access import install_mobile_access_guard

class IOSMobileGuardTests(unittest.TestCase):
    def _app(self):
        app = FastAPI()
        install_mobile_access_guard(app)
        @app.get('/api/health')
        def health(): return {'status':'ok'}
        @app.post('/api/test')
        def test(): return {'status':'ok'}
        return app

    def test_remote_requires_key_even_if_unconfigured(self):
        with patch.dict(os.environ, {'FANTASY_MOBILE_ACCESS_KEY':''}):
            with TestClient(self._app()) as c:
                r = c.get('/api/health')
        self.assertEqual(r.status_code, 401)

    def test_remote_valid_key_works(self):
        key = 'abc123' * 8
        with patch.dict(os.environ, {'FANTASY_MOBILE_ACCESS_KEY':key}):
            with TestClient(self._app()) as c:
                self.assertEqual(c.get('/api/health', headers={'X-Fantasy-Mobile-Key':key}).status_code,200)
                self.assertEqual(c.post('/api/test', headers={'X-Fantasy-Mobile-Key':key}).status_code,200)
                self.assertEqual(c.post('/api/test', headers={'X-Fantasy-Mobile-Key':'bad'}).status_code,401)

    def test_localhost_unaffected(self):
        with patch.dict(os.environ, {'FANTASY_MOBILE_ACCESS_KEY':''}):
            with TestClient(self._app(),client=('127.0.0.1',12345)) as c:
                self.assertEqual(c.get('/api/health').status_code,200)

    def test_ios_sources_are_connected(self):
        api = (ROOT/'web/src/api.ts').read_text()
        main = (ROOT/'web/src/main.tsx').read_text()
        config = (ROOT/'web/capacitor.config.ts').read_text()
        self.assertIn('fetch(apiURL(path)',api)
        self.assertIn('MobileApp',main)
        self.assertIn("webDir: 'dist'",config)
        self.assertIn('CapacitorHttp',config)
        self.assertIn('capacitor://localhost',(ROOT/'app/api.py').read_text())

if __name__=='__main__': unittest.main()
