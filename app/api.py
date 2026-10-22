"""Local-only FastAPI adapter for the existing Fantasy Coach v7.8 engine.

Start from repository root:
    uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000

This module does not import Anthropic or load ESPN until an endpoint needs it.
Existing CLI behavior and cache/fallback code remain unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import import_module
import os
from threading import RLock
from time import monotonic
from typing import Callable
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

MAX_SESSIONS = 32
SESSION_TTL_SECONDS = 60 * 60 * 3


@dataclass
class SessionState:
    history: list = field(default_factory=list)
    decision: dict = field(default_factory=dict)
    touched: float = field(default_factory=monotonic)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class EngineHost:
    """Guards shared engine state and swaps conversation data per session."""

    def __init__(self, engine_loader: Callable):
        self.load_engine = engine_loader
        self.lock = RLock()
        self.engine = None
        self.default_context = None
        self.sessions: dict[str, SessionState] = {}

    def get_engine(self):
        if self.engine is None:
            try:
                self.engine = self.load_engine()
                self.default_context = deepcopy(self.engine.DECISION_CONTEXT)
            except Exception as exc:
                # Prevent leaking credentials, internal paths or API responses.
                raise HTTPException(status_code=503, detail=f"Coach unavailable ({type(exc).__name__}).") from None
        return self.engine

    def ensure_ready(self):
        engine = self.get_engine()
        if engine.APP_CONTEXT is None:
            try:
                engine.load_app_context()
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"Fantasy data unavailable ({type(exc).__name__}).") from None
        return engine

    def prune(self):
        now = monotonic()
        for key, value in list(self.sessions.items()):
            if now - value.touched >= SESSION_TTL_SECONDS:
                self.sessions.pop(key, None)
        if len(self.sessions) >= MAX_SESSIONS:
            oldest = min(self.sessions, key=lambda key: self.sessions[key].touched)
            self.sessions.pop(oldest, None)

    def session(self, session_id: str | None) -> SessionState:
        if not session_id or session_id not in self.sessions:
            raise HTTPException(status_code=404, detail="Session expired or unknown; create a new session.")
        state = self.sessions[session_id]
        if monotonic() - state.touched >= SESSION_TTL_SECONDS:
            del self.sessions[session_id]
            raise HTTPException(status_code=404, detail="Session expired; create a new session.")
        state.touched = monotonic()
        return state

    def chat(self, engine, state: SessionState, message: str):
        # llm.py's CHAT_HISTORY and DECISION_CONTEXT are module globals. Protect
        # them with one lock; never let two browser sessions interleave here.
        prior_history = engine.CHAT_HISTORY
        prior_decision = engine.DECISION_CONTEXT
        try:
            engine.CHAT_HISTORY = state.history
            engine.DECISION_CONTEXT = state.decision
            result = engine.ask_claude(message, return_details=True)
            state.history = engine.CHAT_HISTORY
            state.decision = engine.DECISION_CONTEXT
            return result
        finally:
            # Persist in case a late exception occurred after a partial turn.
            state.history = engine.CHAT_HISTORY
            state.decision = engine.DECISION_CONTEXT
            engine.CHAT_HISTORY = prior_history
            engine.DECISION_CONTEXT = prior_decision


def create_app(engine_loader: Callable | None = None) -> FastAPI:
    host = EngineHost(engine_loader or (lambda: import_module("llm")))
    api = FastAPI(title="Fantasy Coach API", version="7.9.0", docs_url="/docs")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Fantasy-Session"],
    )
    api.state.coach_host = host

    @api.get("/api/health")
    def health():
        return {"status": "ok", "ready": bool(host.engine and host.engine.APP_CONTEXT is not None),
                "server_time": datetime.now(timezone.utc).isoformat()}

    @api.post("/api/sessions", status_code=201)
    def create_session():
        with host.lock:
            host.get_engine()
            host.prune()
            session_id = uuid4().hex
            host.sessions[session_id] = SessionState(decision=deepcopy(host.default_context))
            return {"session_id": session_id, "expires_in_seconds": SESSION_TTL_SECONDS}

    @api.get("/api/league")
    def league():
        with host.lock:
            engine = host.ensure_ready()
            data = engine.tool_get_roster()
            return {"name": data.get("league"), "week": data.get("week"),
                    "scoring": data.get("league_settings"),
                    "roster_size": len(data.get("players") or []), "version": engine.APP_VERSION}

    @api.get("/api/roster")
    def roster():
        with host.lock:
            return host.ensure_ready().tool_get_roster()

    @api.get("/api/lineup")
    def lineup():
        with host.lock:
            return host.ensure_ready().tool_get_optimal_lineup()

    @api.get("/api/waivers")
    def waivers(position: str | None = Query(default=None), limit: int = Query(default=5, ge=1, le=12)):
        with host.lock:
            result = host.ensure_ready().tool_get_waiver_recommendations(position=position, limit=limit)
            if result.get("success") is False:
                raise HTTPException(status_code=422, detail=result.get("error", "Invalid position"))
            return result

    @api.post("/api/chat")
    def chat(body: ChatRequest, x_fantasy_session: str | None = Header(default=None)):
        message = body.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Message cannot be empty.")
        with host.lock:
            state = host.session(x_fantasy_session)
            engine = host.ensure_ready()
            try:
                return host.chat(engine, state, message)
            except Exception as exc:
                # Never expose client tokens, API request headers or stack traces.
                raise HTTPException(status_code=502, detail=f"Coach request failed ({type(exc).__name__}).") from None

    @api.post("/api/refresh")
    def refresh():
        with host.lock:
            engine = host.get_engine()
            try:
                # First refresh the underlying ESPN and cached fantasy data.
                # Only clear sessions if the refresh succeeds.
                engine.load_app_context()
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"Refresh failed ({type(exc).__name__}).") from None
            engine.EXTERNAL_PLAYER_CACHE.clear()
            engine.CHAT_HISTORY.clear()
            engine._reset_decision_context()
            host.default_context = deepcopy(engine.DECISION_CONTEXT)
            host.sessions.clear()
            return {"refreshed": True, "week": engine.APP_CONTEXT.get("current_week"),
                    "sessions_reset": True}

    return api


app = create_app()
