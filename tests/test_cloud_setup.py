"""No ESPN or Claude calls; test real HTTP auth and SPA fallback."""
import base64
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from cloud_security import _authorized, configure_cloud_app


class CloudSetupTests(unittest.TestCase):
    def test_password_guard(self):
        valid = "Basic " + base64.b64encode(b"manager:really-long-example-password").decode()
        self.assertTrue(_authorized(valid, "manager", "really-long-example-password"))
        self.assertFalse(_authorized(valid, "manager", "wrong"))
        self.assertFalse(_authorized("Bearer xyz", "manager", "really-long-example-password"))

    def test_spa_and_subscription_confirm_exemption(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "FANTASY_WEB_USERNAME": "manager",
            "FANTASY_WEB_PASSWORD": "really-long-example-password",
        }):
            directory = Path(tmp)
            (directory / "index.html").write_text("<html>Coach UI</html>")
            app = FastAPI()
            @app.get("/api/subscriptions/confirm")
            def confirm():
                return {"ok": True}
            @app.get("/api/private")
            def private():
                return {"secret": "only yours"}
            configure_cloud_app(app, static_dir=directory)
            client = TestClient(app)
            self.assertEqual(client.get("/").status_code, 401)
            self.assertEqual(client.get("/api/private").status_code, 401)
            self.assertEqual(client.get("/api/subscriptions/confirm").status_code, 200)
            auth = ("manager", "really-long-example-password")
            self.assertEqual(client.get("/", auth=auth).status_code, 200)
            self.assertIn("Coach UI", client.get("/my-roster", auth=auth).text)
            self.assertEqual(client.get("/api/private", auth=auth).json()["secret"], "only yours")

if __name__ == "__main__":
    unittest.main()
