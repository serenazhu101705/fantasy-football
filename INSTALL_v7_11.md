# Fantasy Coach v7.11 — fresh waiver request + ownership guards

Install this **overlay** on top of v7.10 from the `fantasy_football` repo root:

```bash
unzip -o ~/Downloads/fantasy_coach_v7_11_waiver_fix.zip
uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

Restart the Python API (Ctrl+C in the backend terminal):

```bash
uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

Leave your React/Vite server running. Reload the website; a fresh browser session
will be created after the backend restarts. Do **not** need to reinstall npm
packages or replace your frontend files.

Test in the website after discussing two named players:

```text
A.J. Brown or Germie Bernard this week?
any waiver moves for me to make
```

The second query should be an independent waiver evaluation, not a continuation
of Brown vs. Bernard. With `FANTASY_DEBUG=1` set on the API server, it should show
`[ROUTER] intent="waiver" players=[] position=null prefetch=true`, with an
actual waiver candidate count (assuming ESPN loaded candidates).

## Changes

- `app/llm.py`: New broad waiver questions reset stale comparison names, ROS
  horizon, position, add count, open-bench state and drop target.
- `app/llm.py`: The waiver tool filters out the loaded ESPN roster and players
  explicitly marked rostered elsewhere; it also filters stale transactions.
- `app/llm.py`: Prefetch now includes roster names and roles, alongside the
  available waiver candidates, and tells Claude that only candidates from the
  waiver pool can be suggested as adds. If no move helps, recommend HOLD.
- `tests/test_coach_regressions.py`: Four new offline regressions for the exact
  Brown/Bernard -> waiver failure, exiting a previous swap, one mocked Claude
  call, and excluding already-rostered candidates.

## Verification

The combined backend test suite passed **58 tests** offline in the patch
workspace. The live ESPN/Claude-to-browser flow has **not** been run here, and
no paid Claude calls were made while developing the patch.

This patch changes **only two files** in your repo: `app/llm.py` and
`tests/test_coach_regressions.py`. It preserves v7.10's lineup consistency fix.
