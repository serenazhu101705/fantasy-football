"""Offline checks for Render Free deployment, without ESPN or Anthropic calls."""
from __future__ import annotations

import base64
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import sys
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from cloud_security import configure_cloud_app


class RenderFreeTests(unittest.TestCase):
    def test_cloud_password_and_inner_remote_guard(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'FANTASY_WEB_USERNAME': 'coach',
            'FANTASY_WEB_PASSWORD': 'strong-and-very-long-password',
            'FANTASY_MOBILE_ACCESS_KEY': 'Z' * 40,
        }):
            assets = Path(tmp)
            (assets / 'index.html').write_text('<html>Fantasy Coach</html>')
            app = FastAPI()

            @app.middleware('http')
            async def mimic_ios_remote_guard(request: Request, call_next):
                if request.url.path.startswith('/api/') and request.url.path != '/api/health':
                    if request.headers.get('x-fantasy-mobile-key') != 'Z' * 40:
                        return JSONResponse({'detail': 'Mobile API access denied'}, status_code=401)
                return await call_next(request)

            @app.get('/api/roster')
            def roster():
                return {'players': ['test player']}

            @app.get('/api/health')
            def health():
                return {'status': 'ok'}

            configure_cloud_app(app, static_dir=assets)
            client = TestClient(app)
            auth = ('coach', 'strong-and-very-long-password')
            self.assertEqual(client.get('/').status_code, 401)
            self.assertEqual(client.get('/api/roster').status_code, 401)
            self.assertEqual(client.get('/api/health').status_code, 200)
            self.assertEqual(client.get('/', auth=auth).status_code, 200)
            self.assertEqual(client.get('/waiver-wire', auth=auth).status_code, 200)
            self.assertEqual(client.get('/api/roster', auth=auth).json()['players'], ['test player'])
            # A forged key never bypasses the site password.
            self.assertEqual(client.get('/api/roster', headers={'X-Fantasy-Mobile-Key': 'Z' * 40}).status_code, 401)

    def test_cloud_password_is_mandatory(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'FANTASY_WEB_USERNAME': 'coach',
            'FANTASY_WEB_PASSWORD': 'short',
        }):
            (Path(tmp) / 'index.html').write_text('ok')
            with self.assertRaises(RuntimeError):
                configure_cloud_app(FastAPI(), static_dir=Path(tmp))


if __name__ == '__main__':
    unittest.main()
