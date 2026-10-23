"""Production-only FastAPI entrypoint; local ``api:app`` stays unchanged."""
from api import app
from cloud_security import configure_cloud_app

configure_cloud_app(app)
