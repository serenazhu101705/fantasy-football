# Fantasy Coach v7.14 — Live Matchups, Player Cards, Fantasy-Only News, Lineup Explanations

**Prerequisite:** v7.13 dashboard patch installed. This is an incremental patch; your existing `.env`, `.git`, `data/`, `uv.lock`, and `web/node_modules` remain untouched.

## Install

From your `fantasy_football` repo root:

```bash
git status
unzip -o ~/Downloads/fantasy_coach_v7_14_live_matchup.zip
uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

Stop the previous FastAPI server with Ctrl+C. Restart:

```bash
FANTASY_DEBUG=1 uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

If your React dev server is running, it should hot reload; otherwise:

```bash
cd web
npm run dev
```

Open `http://127.0.0.1:5173` and check **Weekly Matchup → Optimal Lineup → News Feed**.

## What changed

- **Weekly Matchup:** Starter-by-starter QB/RB/WR/TE/FLEX/DST/K comparison. ESPN projected-point winner highlighted. NFL game states (`IN PLAY`, `UPCOMING`, `FINAL`, `BYE`) come from a cached public ESPN NFL scoreboard request. Player fantasy points and weekly stat breakdowns come from ESPN Fantasy. The browser requests refreshed matchup data every 60 seconds while that tab is open; actual refresh can lag ESPN's box scores and broadcast.
- **Player details:** Click a player's name. Shows ESPN fantasy projected and actual points, available passing/rushing/receiving stat breakdowns, limited ESPN public NFL athlete bio, prior games, and an explicitly labeled partial season-stat aggregate. Missing ESPN stat breakdowns stay unknown, not zero.
- **Optional player analysis:** The button `Analyze with Claude` makes one short paid Claude call for a grounded season/last-game/this-week summary. Merely opening a player panel costs **zero Claude tokens**. Successful summaries are cached for identical player facts.
- **Fantasy-only News Feed:** Filters irrelevant off-field NFL business/entertainment stories. Adds public FantasyPros article RSS alongside ESPN and RotoWire; this is **not** the paid FantasyPros projection/rankings API.
- **My Team News:** Separately shows player-matched reporting across the current roster, with public ESPN athlete-specific fallback. It requests articles only when the tab is opened and caches athlete lookups for an hour. Not every player will have a current, verifiable article, so those players say **No recent verified fantasy article**.
- **Optimal Lineup explained:** Deterministic per-slot justification, actual ESPN/FantasyPros projections, and the highest-projected eligible nonstarter for a direct swap. Separate notes clarify that missing FantasyPros ECR is **not** missing projections or all player evidence. The optimizer's selection algorithm is unchanged by this patch.

## Read-only API endpoints

- `/api/strategy/week` now includes `head_to_head`, game status and live refresh metadata.
- `GET /api/players/{espn_id}` loads factual ESPN player details. No Claude.
- `POST /api/players/{espn_id}/analysis` triggers explicitly requested Claude analysis.
- `GET /api/news` returns relevance-filtered headlines.
- `GET /api/news/roster` returns player-by-player matched articles.
- `GET /api/lineup` now includes `explanation` with per-slot baseline and comparisons.

## Accuracy and cost guardrails

- Projection advantages are point differences, **not** win probabilities. ESPN fantasy `winProbability` remains the only source of the displayed win probability when available.
- Actual fantasy points and statistics update only as ESPN publishes them; there is no game feed over WebSockets. The UI refreshes every minute while you view the matchup.
- Missing opponent, projections, ECR, player stats, or articles are not fabricated or silently interpreted as zeros.
- A direct roster alternative is not the same as a globally reoptimized team under all possible swaps.
- Authenticated ESPN league details and private keys stay on the Python backend. Do not deploy this local development API publicly without authentication.

## Testing

All **91 Python offline regression tests passed** in the build environment; Python compilation and React TypeScript *syntax transpilation* passed. A complete TypeScript typecheck/production Vite build wasn't possible here because npm dependency installation timed out. Live ESPN response shapes and browser rendering must be verified locally.

To inspect requests: `http://127.0.0.1:8000/docs`.
