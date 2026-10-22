# Fantasy Coach v7.10: Lineup Consistency Fix

This is a **small overlay patch** for the v7.9 web starter (and v7.8 decision
journal). It does **not** replace your existing repository or require new
packages. It preserves the old waiver-advisory `lineup_score` output.

## Install from the repo root

```bash
unzip -o ~/Downloads/fantasy_coach_v7_10_lineup_fix.zip
uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

**Restart** the Python Uvicorn server (Ctrl+C in the backend terminal, then):

```bash
uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

Leave Vite running and click **Refresh** on the website. Reload the browser
if you need to pick up the new React source.

## What changed

- `app/lineup_optimizer.py`: weekly lineup is now selected based first on
  available ESPN/FantasyPros league-scoring projections; the old `weekly_score`
  is a *small near-tie breaker*. Missing provider data does not count as a
  zero-point projection. Hard unavailable players are excluded. The optimizer's
  `lineup_score` field retains its old composite/advisory meaning for
  compatibility with waiver transactions.
- `app/llm.py`: Claude gets the projection method and is explicitly instructed
  to explain any lineup disagreement. The lineup tool supplies projected
  points on the same basis as the optimizer.
- `web/src/api.ts` and `web/src/App.tsx`: show the lineup's consensus projection
  per player and explain when source projection coverage is incomplete.
- `tests/test_lineup_consistency.py`: eight offline regression tests, including
  a synthetic A.J. Brown vs. Germie Bernard scenario. No paid Claude calls.

## How to verify against *your real ESPN data*

Go to **Optimal Lineup**. If the optimizer still favors Bernard, inspect both
players' ESPN and FantasyPros projections and availability. This patch does not
hard-code either player's name. If A.J. Brown is unavailable or the provider
projections favor Bernard for some reason, the selection could still differ.

For an API-level check while Uvicorn is running:

```bash
curl -s http://127.0.0.1:8000/api/lineup | uv run python -m json.tool
```

Read the `selection_method`, `projection_coverage`, and `lineup` fields.
You can also check `/api/roster` for ESPN vs. FantasyPros player projections.

**Limit:** the current optimizer still assumes 1 QB, 2 RB, 2 WR, 1 TE, 1 flex,
1 D/ST and 1 K. Making all lineup slots ESPN-driven remains future work.
