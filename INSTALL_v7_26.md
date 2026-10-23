# Fantasy Coach v7.26 — NFL leaderboard routing hotfix

This increment fixes the specific problem where an NFL leaderboard question, such as **"who leads the league in rushing yards"**, is sent to Claude and RSS news instead of using the season-wide NFL statistics already accessible to Fantasy Coach.

## What changed

- `app/nfl_chat_guard.py` is new: **FastAPI intercepts a recognized NFL-wide statistical leaderboard query before Claude**. Queries are parsed deterministically and checked against **nflverse first, ESPN NFL leaders second**. No Claude charges for recognized leaderboard questions.
- `app/api.py` now applies that guard inside the existing session-protected chat flow and prints `[NFL_CHAT_GUARD] stat=rushing_yards status=... api_calls=0` to the Uvicorn terminal.
- `/api/health` now returns `"nfl_leaderboard_guard": "v7.26"` so you can verify which server code is **actually running**.
- `/api/nfl/verified-leaders?stat=rushing_yards&limit=5` tests the full nflverse→ESPN fallback, without Claude. The existing `/api/nfl/leaders` stays compatible and nflverse-only.
- Compatible `llm.py`, `nfl_leaderboards.py`, and `nfl_source_lookup.py` versions are bundled together so an old module isn't accidentally left behind.

No website UI files, subscription database, GitHub Actions workflow, or ESPN lineup-write permissions are changed.

## Install from the repository root

```bash
# Review uncommitted work before replacing existing app files.
git status --short

unzip -o ~/Downloads/fantasy_coach_v7_26_nfl_route_guard.zip

uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

### Important: Fully restart FastAPI

Press **Control-C** in the terminal running Uvicorn, then start it again:

```bash
FANTASY_DEBUG=1 uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

Do not leave an older Uvicorn process serving port 8000. To see the listening process on macOS:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

If port 8000 is occupied by an older server, stop **that** process before restarting. Don't kill unrelated processes.

## Validate the version and routing before paying for Claude calls

**1. Confirm the live server version:**

```bash
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```

It **must** contain `"nfl_leaderboard_guard": "v7.26"`. If it doesn't, you haven't updated the server actually answering browser requests.

**2. Confirm the text parser locally, without a network request:**

```bash
PYTHONPATH=app uv run python -c 'from nfl_leaderboards import parse_leaderboard_request; print(parse_leaderboard_request("who leads the league in rushing yards"))'
```

Expected metric: `rushing_yards`.

**3. Inspect actual raw statistical evidence, without Claude:**

```bash
curl -s 'http://127.0.0.1:8000/api/nfl/verified-leaders?stat=rushing_yards&limit=5' | python3 -m json.tool
```

- `"status": "available"` and `"leaders": [...]` means the provider lookup returned verified statistics; read `source`, `season`, and `through_week`.
- `"status": "unavailable"` means neither structured source returned a verifiable result. Look at `source_errors`; these distinguish provider/network failures from missing columns.
- ESPN's public NFL endpoint is undocumented and might change. The fallback isn't guaranteed to return results.

**4. Open the app's chat and ask:** `who leads the league in rushing yards`

Expected terminal line: `[NFL_CHAT_GUARD] stat=rushing_yards status=available api_calls=0` (or `status=unavailable` if both providers truly fail). **You should not see a Claude-only response about checking news feeds for this question.**

If the health marker says v7.26 but chat still gives that old response, provide the health JSON, the `/api/nfl/verified-leaders` JSON, and the Uvicorn `[NFL_CHAT_GUARD]` line. No need to share secrets.

## Tests

7 new offline tests verify exact question routing, typo tolerance, host session isolation, zero-Claude guard, no-news-stat substitution, health version marker, and verified-leader endpoint. Tested together with prior regressions: **177/177 passed offline**. These tests use fake values; they do not prove live 2026 NFL totals on your Mac.
