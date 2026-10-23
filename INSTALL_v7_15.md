# Fantasy Coach v7.15 — Player Stats, Roster News, and Weekly Recaps

An **incremental patch on top of v7.14**. Extract from the repository root. Do not copy the files into Downloads manually or overwrite `.env`, Git history, or `data`.

## Install (from `fantasy_football/`)

```bash
git status
unzip -o ~/Downloads/fantasy_coach_v7_15_player_intelligence.zip
uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

Stop the existing Uvicorn process and restart with:

```bash
FANTASY_DEBUG=1 uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

Keep the existing Vite frontend running and refresh the browser. From `web/`, you can run `npm run build` to check the full React/TypeScript build on your Mac.

## How to test

1. Open a roster player (e.g. Joe Burrow or Chase Brown). Under **Season NFL statistics**, look for `Full-season NFL totals · ESPN player statistics`. No card should be labeled `27`, `47`, `48`, etc. The season point totals are still from **your ESPN fantasy scoring**.
2. Click **Analyze with Claude**. This is the only paid player-summary action. If it fails, note the red error message and the Uvicorn line starting `[PLAYER ANALYSIS]`; that reports a sanitized exception type and HTTP status, not private credentials. There is **no automatic analysis call** on card open.
3. Open **News Feed > My Team News**. The backend now searches the public ESPN athlete news and an individualized Google News RSS query for every otherwise unmatched player. This may take several seconds to load once; results are cached for an hour. Each player gets a real article if available, or a direct per-player news search link if not. **No publisher can guarantee a fresh fantasy article for every player.** Nothing is fabricated.
4. Open **Weekly Matchup > Weekly recap**. Select a week. Until ESPN verifies that **every NFL game is final** and both fantasy team scores exist, the Generate button remains unavailable. After the final game (typically Monday night), you can generate the 200–300-word recap using **one Claude call**. Repeat views read a local JSON cache in `~/.cache/fantasy_coach/weekly_recaps/` at no extra token cost. Weekly recaps do not send alerts or run while the local app is closed.

## Read-only endpoints

- `GET /api/players/{id}?week=7`: ESPN athlete season stats and game log + your ESPN Fantasy points and projections; no Claude cost.
- `POST /api/players/{id}/analysis?week=7`: player summary, one Claude call unless cached.
- `GET /api/news/roster`: independently targeted article searches for each roster player; no Claude or paid FantasyPros API calls.
- `GET /api/recaps/week?week=7`: check final game/score status; no Claude cost.
- `POST /api/recaps/week?week=7`: recap only after all NFL games and matchup scores finalize; one Claude call unless saved.

## Accuracy notes

The previous profile aggregated only the weekly `Player.stats` entries already loaded on the fantasy roster; that could be one week. v7.15 retrieves real season NFL totals via ESPN's public athlete `/stats` endpoint and NFL game logs via `/gamelog`. These endpoints sometimes fail or differ for team D/ST. In that case the UI explicitly says the data is partial or unavailable. Unrecognized numeric stat IDs are now hidden, not silently relabeled. `season_fantasy_points` and `season_fantasy_ppg` remain league-scored values.

ESPN's current live projection is not necessarily the projection before kickoff. The weekly recap avoids pretending that a final game-week projection was frozen pregame or that hindsight lineup swaps were feasible. No extra FantasyPros API requests are added. Google News RSS and ESPN's public athlete endpoints are unofficial/undocumented APIs and may change.

**Verification:** 103 Python offline tests passed for the combined patch chain, plus React TypeScript syntax transpilation. Complete production TypeScript build and live ESPN/Claude requests have **not** been verified in the build environment.
