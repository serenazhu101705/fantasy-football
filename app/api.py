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


def _espn_context(engine):
    context = engine.APP_CONTEXT or {}
    league = context.get("league")
    team = context.get("team")
    if league is None or team is None:
        raise HTTPException(status_code=503, detail="ESPN league data is not loaded.")
    return league, team


def _league_feature(operation):
    try:
        return operation()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"ESPN league feature unavailable ({type(exc).__name__}).") from None




def _include_profile_ids(rows, source_players):
    """Attach ESPN IDs on browser payloads only, not on Claude's compact prompts.

    `tool_get_roster` intentionally compacts model inputs, but the web UI
    needs stable ESPN identifiers to open player profiles. Resolve them from
    the raw loaded ESPN data; never guess from a non-unique surname.
    """
    from collections import Counter
    import re

    def key(row):
        name = re.sub(r"[^a-z0-9]", "", str(row.get("name") or "").lower())
        return (name, str(row.get("position") or "").upper())

    raw_rows = [row for row in (source_players or []) if isinstance(row, dict)]
    counts = Counter(key(row) for row in raw_rows if row.get("espn_id") is not None)
    by_key = {key(row): row["espn_id"] for row in raw_rows
              if row.get("espn_id") is not None and counts[key(row)] == 1}
    for row in rows:
        if not isinstance(row, dict) or row.get("espn_id") is not None:
            continue
        pid = by_key.get(key(row))
        if pid is not None:
            row["espn_id"] = pid
    return rows

def create_app(engine_loader: Callable | None = None) -> FastAPI:
    host = EngineHost(engine_loader or (lambda: import_module("llm")))
    api = FastAPI(title="Fantasy Coach API", version="7.16.0", docs_url="/docs")
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
                    "roster_size": len(data.get("players") or []),
                    "my_team_id": getattr((engine.APP_CONTEXT or {}).get("team"), "team_id", None),
                    "version": engine.APP_VERSION}

    @api.get("/api/roster")
    def roster():
        from league_intelligence import nfl_opponents
        with host.lock:
            engine = host.ensure_ready()
            data = engine.tool_get_roster()
            league = (engine.APP_CONTEXT or {}).get("league")
            try:
                opponents = nfl_opponents(league) if league is not None else {}
            except Exception:
                opponents = {}
            _include_profile_ids(data.get("players", []), (engine.APP_CONTEXT or {}).get("roster"))
            for player in data.get("players", []):
                if player.get("on_bye_week"):
                    player["opponent"] = "BYE"
                elif not player.get("opponent"):
                    player["opponent"] = opponents.get(str(player.get("team") or "").upper())
            return data

    @api.get("/api/lineup")
    def lineup():
        from league_intelligence import nfl_opponents
        with host.lock:
            engine = host.ensure_ready()
            data = engine.tool_get_optimal_lineup()
            league = (engine.APP_CONTEXT or {}).get("league")
            try:
                opponents = nfl_opponents(league) if league is not None else {}
            except Exception:
                opponents = {}
            for player in (data.get("lineup") or {}).values():
                if isinstance(player, dict) and not player.get("opponent"):
                    player["opponent"] = "BYE" if player.get("on_bye_week") else opponents.get(str(player.get("team") or "").upper())
            _include_profile_ids(
                [p for p in (data.get("lineup") or {}).values() if isinstance(p, dict)],
                (engine.APP_CONTEXT or {}).get("roster"),
            )
            from lineup_explanations import explain_lineup
            data["explanation"] = explain_lineup(data, engine.tool_get_roster().get("players") or [])
            return data

    @api.get("/api/waivers")
    def waivers(position: str | None = Query(default=None), limit: int = Query(default=5, ge=1, le=12)):
        with host.lock:
            result = host.ensure_ready().tool_get_waiver_recommendations(position=position, limit=limit)
            if result.get("success") is False:
                raise HTTPException(status_code=422, detail=result.get("error", "Invalid position"))
            return result

    @api.get("/api/waivers/browse")
    def browse_waivers(position: str = Query(default="ALL"),
                      page: int = Query(default=1, ge=1),
                      page_size: int = Query(default=10, ge=1, le=50)):
        from browsing import waiver_browser
        with host.lock:
            context = host.ensure_ready().APP_CONTEXT or {}
            return _league_feature(lambda: waiver_browser(
                context, position=position, page=page, page_size=page_size))

    # All league-intelligence reads are deterministic (no Claude calls).
    # They share the existing engine lock so a concurrent refresh is safe.
    @api.get("/api/league/standings")
    def get_standings():
        from league_intelligence import standings
        with host.lock:
            league, _ = _espn_context(host.ensure_ready())
            return _league_feature(lambda: standings(league))

    @api.get("/api/league/faab")
    def get_faab():
        from league_intelligence import faab
        with host.lock:
            league, _ = _espn_context(host.ensure_ready())
            return _league_feature(lambda: faab(league))

    @api.get("/api/league/schedule")
    def get_schedule(team_id: int | None = Query(default=None, ge=1)):
        from league_intelligence import schedule
        with host.lock:
            league, _ = _espn_context(host.ensure_ready())
            return _league_feature(lambda: schedule(league, team_id))

    @api.get("/api/league/matchups")
    def get_matchups(week: int | None = Query(default=None, ge=1, le=18)):
        from league_intelligence import matchups
        with host.lock:
            league, _ = _espn_context(host.ensure_ready())
            return _league_feature(lambda: matchups(league, week))

    @api.get("/api/league/teams/{team_id}/roster")
    def get_team_roster(team_id: int, week: int | None = Query(default=None, ge=1, le=18)):
        from league_intelligence import team_roster
        with host.lock:
            league, _ = _espn_context(host.ensure_ready())
            return _league_feature(lambda: team_roster(league, team_id, week))

    @api.get("/api/league/transactions")
    def get_transactions(limit: int = Query(default=12, ge=1, le=25)):
        from league_intelligence import transactions
        with host.lock:
            league, _ = _espn_context(host.ensure_ready())
            return _league_feature(lambda: transactions(league, limit))

    @api.get("/api/news")
    def get_news(limit: int = Query(default=24, ge=1, le=50)):
        # Independent of ESPN/Claude and engine lock. Cached RSS providers.
        from news_feed import news_feed
        return news_feed(limit=limit)

    @api.get("/api/news/browse")
    def browse_news(page: int = Query(default=1, ge=1),
                    page_size: int = Query(default=10, ge=1, le=50),
                    source: str = Query(default="ALL")):
        # Keep the endpoint and installed news module in sync. A missing
        # function is a deployment mismatch, not a failure of RSS providers.
        try:
            from news_feed import news_feed_page
        except ImportError:
            raise HTTPException(
                status_code=503,
                detail="News module is outdated: install the current app/news_feed.py and restart FastAPI.",
            ) from None
        return news_feed_page(page=page, page_size=page_size, source=source)

    @api.get("/api/news/roster")
    def get_roster_news():
        # Only requested by the "My Team News" tab: no background fan-out on load.
        from news_feed import roster_news
        with host.lock:
            players = deepcopy(host.ensure_ready().tool_get_roster().get("players") or [])
        # Network/RSS fan-out must not hold the Claude/ESPN conversation lock.
        return roster_news(players)

    @api.get("/api/league/pending-claims")
    def get_pending_claims():
        from league_intelligence import pending_claims
        return pending_claims()

    @api.get("/api/strategy/week")
    def get_week_strategy(week: int | None = Query(default=None, ge=1, le=18)):
        from league_intelligence import matchup_strategy
        with host.lock:
            league, team = _espn_context(host.ensure_ready())
            return _league_feature(lambda: matchup_strategy(league, team.team_id, week))

    @api.get("/api/players/{player_id}")
    def get_player_profile(player_id: int, week: int | None = Query(default=None, ge=1, le=18)):
        """Read-only ESPN profile; clicking a name never invokes Claude."""
        from matchup_live import player_profile
        with host.lock:
            league, _ = _espn_context(host.ensure_ready())
        # Public bio/stats network I/O must not hold the engine/chat lock.
        try:
            return player_profile(league, player_id, week)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except Exception as exc:
            if os.getenv("FANTASY_DEBUG") == "1":
                print(f"[PLAYER PROFILE] error={type(exc).__name__}")
            raise HTTPException(status_code=503, detail=f"Player profile unavailable ({type(exc).__name__}).") from None

    @api.post("/api/players/{player_id}/analysis")
    def generate_player_analysis(player_id: int, week: int | None = Query(default=None, ge=1, le=18)):
        """The *only* player-detail endpoint that spends Claude tokens."""
        from matchup_live import player_profile
        from player_analysis import summarize_player
        with host.lock:
            engine = host.ensure_ready()
            league, _ = _espn_context(engine)
            client, model = engine.client, engine.MODEL
        # Claude requests are independent of CLI conversation global state.
        # Do not block roster/chat/matchup requests during the network call.
        try:
            profile = player_profile(league, player_id, week)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Player profile unavailable ({type(exc).__name__}).") from None
        try:
            return summarize_player(profile, client, model)
        except Exception as exc:
            # Minimal operational diagnostics: no prompts, keys, headers or
            # private roster data in logs or HTTP responses.
            if os.getenv("FANTASY_DEBUG") == "1":
                stop = str(exc).split("stop_reason=", 1)[-1].split(")", 1)[0] if "stop_reason=" in str(exc) else "n/a"
                print(f"[PLAYER ANALYSIS] error={type(exc).__name__} http_status={getattr(exc, 'status_code', 'n/a')} stop_reason={stop} model={model}")
            code = getattr(exc, "status_code", None)
            if code == 429:
                detail = "Claude is rate-limited or out of credits (HTTP 429)."
            elif code == 401:
                detail = "Claude API authentication failed (HTTP 401). Check server credentials."
            elif code in (400, 404):
                detail = f"Claude rejected the player-summary model/request (HTTP {code}). Check ANTHROPIC_MODEL."
            elif isinstance(exc, ValueError) and "no visible player analysis" in str(exc):
                detail = "Claude returned no visible player summary. " \
                         "Check the server's [PLAYER ANALYSIS] stop reason and model settings."
            else:
                detail = f"Claude player analysis unavailable ({type(exc).__name__}). See backend log with FANTASY_DEBUG=1."
            raise HTTPException(status_code=502, detail=detail) from None

    @api.get("/api/recaps/week")
    def get_recap_status(week: int | None = Query(default=None, ge=1, le=18)):
        from weekly_recap import recap_status
        with host.lock:
            league, team = _espn_context(host.ensure_ready())
            return _league_feature(lambda: recap_status(league, team.team_id, week))

    @api.post("/api/recaps/week")
    def post_weekly_recap(week: int | None = Query(default=None, ge=1, le=18)):
        """Opt-in paid Claude recap; never call until all NFL games are final."""
        from weekly_recap import generate_weekly_recap, RecapEmptyResponseError
        with host.lock:
            engine = host.ensure_ready()
            league, team = _espn_context(engine)
            client, model = engine.client, engine.MODEL
        try:
            return generate_weekly_recap(league, team.team_id, client, model, week)
        except RecapEmptyResponseError as exc:
            # A completed NFL week is not a conflict; it is a failed model
            # response. Surface only a safe stop reason, never prompt or keys.
            raise HTTPException(
                status_code=502,
                detail=f"Claude returned no visible weekly recap (stop_reason={exc.stop_reason}). "
                       "No recap was cached. See [WEEKLY RECAP] in backend logs."
            ) from None
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except Exception as exc:
            if os.getenv("FANTASY_DEBUG") == "1":
                print(f"[WEEKLY RECAP] error={type(exc).__name__} http_status={getattr(exc, 'status_code', 'n/a')}")
            raise HTTPException(status_code=502, detail=f"Weekly recap unavailable ({type(exc).__name__}).") from None

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
            from league_intelligence import clear_cache
            from matchup_live import clear_live_cache
            from player_analysis import clear_analysis_cache
            from espn_player_stats import clear_public_player_cache
            clear_public_player_cache()
            clear_cache()
            clear_live_cache()
            clear_analysis_cache()
            engine.EXTERNAL_PLAYER_CACHE.clear()
            engine.CHAT_HISTORY.clear()
            engine._reset_decision_context()
            host.default_context = deepcopy(engine.DECISION_CONTEXT)
            host.sessions.clear()
            return {"refreshed": True, "week": engine.APP_CONTEXT.get("current_week"),
                    "sessions_reset": True}

    return api


app = create_app()
