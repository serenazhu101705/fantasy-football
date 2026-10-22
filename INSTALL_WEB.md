# Fantasy Coach: FastAPI v7.9 + React dashboard starter

This patch builds on **your installed v7.8**. It does not replace your entire Python project.

## Files added or changed

- `app/api.py` — local FastAPI API. Imports the existing coach lazily; serializes access; isolates each browser chat session from other tabs.
- `app/llm.py` — one backward-compatible option: `ask_claude(message, return_details=True)` returns the visible response plus the actual token usage to the API. Plain `ask_claude(message)` still returns a string for the CLI.
- `tests/test_api.py` — mocked local API contract and isolation tests.
- `tests/test_coach_regressions.py` — existing v7.8 regression tests plus the API metadata test.
- `web/` — responsive React + TypeScript + Vite starter. No frontend credentials.

## 1. Install the archive

From the `fantasy_football` repository root (commit existing work with Git first if desired):

```bash
unzip -o ~/Downloads/fantasy_coach_web_starter_v7_9.zip
```

## 2. Add Python dependencies

The backend continues to use your existing `uv` environment. Install FastAPI and its local server:

```bash
uv add 'fastapi>=0.115' 'uvicorn>=0.30' 'httpx>=0.27'
```

`httpx` is used by FastAPI's offline test client. Keep your `.env` file in the repo root; **do not copy keys into `web/` or commit `.env`**.

Check the offline tests (no Claude charges):

```bash
uv run python -m unittest discover -s tests -p 'test_api.py' -v
uv run python -m unittest discover -s tests -p 'test_coach_regressions.py' -v
uv run python -m unittest discover -s tests -p 'test_recommendation_journal.py' -v
```

## 3. Start the local Python API (terminal 1)

From the repository root:

```bash
uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

Check [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health).
It initially returns `ready: false` until ESPN data is loaded by a dashboard read.
FastAPI's generated API reference is at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## 4. Start the frontend (terminal 2)

From the *same* repository root, in a separate terminal:

```bash
cd web
npm install
npm run dev
```

Open **http://127.0.0.1:5173**. Vite forwards `/api` requests to your local Python process on port 8000.

### UI features

- Current league, scoring, week and ESPN-verified roster; no Claude call.
- Deterministic projected optimal lineup; no Claude call.
- Available waiver candidates and position filters; no Claude call.
- Chat with follow-up memory isolated by browser session, showing actual token usage. Chat makes the normal Claude API call(s).
- Refresh ESPN data and clear local chat sessions.

No automatic ESPN transactions or waiver pickups are submitted. The UI is for **recommendations only**.

## Security and limits

**Local personal-development app only.** Keep the server bound to `127.0.0.1`, as shown. It has no production authentication, rate limiting, per-user ESPN credentials, database-backed sessions, or public deployment hardening. Never host it publicly as written. The React dev server does not receive your Claude key or ESPN cookies. Session tokens are temporary and are held only in browser memory; reloading the page starts a new chat session. The recommendation journal remains in the existing local `data/` folder.

The API is serialized behind one lock because the current CLI uses module-level context. That ensures chat isolation but also means one expensive request holds up all local API requests. Later, if we want multiple concurrent users, we should refactor the engine into instance-based state rather than making this server publicly accessible.

Add these generated frontend directories to `.gitignore` if not already present:

```
web/node_modules/
web/dist/
```

## Verification

- Python API and engine tests: **46 offline tests passed**, including mocked single-call metadata path and per-session isolation.
- TypeScript and TSX files: **syntax-transpilation checked**, no diagnostics.
- **Not yet verified**: full `npm install` / `npm run build` or live ESPN/Claude browser integration. The creation environment cannot access npm registry (DNS unavailable); your Mac can finish these steps. If TypeScript compilation or a browser interaction fails, paste the exact error.

Your existing `uv run python app/llm.py` CLI remains available and retains its original string response behavior.
