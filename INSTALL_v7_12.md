# Fantasy Coach v7.12 — League intelligence and weekly matchup strategy

This is an **incremental patch for the v7.11 repository**, not a clean install. It does **not** replace your `.env`, `pyproject.toml`, dependencies, ESPN cookies, Claude keys, or existing caches. Git is your backup.

## Install

Download `fantasy_coach_v7_12_league_intelligence.zip` into Downloads. From your `fantasy_football` **repo root**:

```bash
unzip -o ~/Downloads/fantasy_coach_v7_12_league_intelligence.zip
uv run python -m unittest discover -s tests -p 'test_*.py' -v
git diff --stat
```

Restart backend (stop the old Uvicorn process with Ctrl+C):

```bash
FANTASY_DEBUG=1 uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

Your Vite frontend should hot-reload automatically if still running. If not, in **another terminal** run:

```bash
cd web
npm run dev
```

Open `http://127.0.0.1:5173`. You should see `Weekly Matchup` and `League Explorer` in the sidebar. If a page is stale, refresh your browser once. 

## New read-only API endpoints (do not use Claude credits)

- `GET /api/strategy/week` — your ESPN opponent, projected score margin, conditional points above projection needed to take the lead, both lineup projections, single-swap bench upgrades.
- `GET /api/league/matchups?week=7` — league-wide matchups, box-score projections and current scores for a supported current or completed week. Future-week box scores are explicitly unavailable rather than mislabeled.
- `GET /api/league/standings` — standings, records, points for/against.
- `GET /api/league/faab` — league starting budget, recorded spending and estimated remaining per team, waiver priority.
- `GET /api/league/schedule` or `?team_id=...` — the complete ESPN schedule or a given team's schedule.
- `GET /api/league/teams/{team_id}/roster` — a team's ESPN roster plus current-week projections when available; `?week=` accepts valid nonfuture weeks.
- `GET /api/league/transactions?limit=10` — recent **completed** adds/drops/trades and recorded winning bids where ESPN supplies them.
- `GET /api/league/pending-claims` — explicit `not_supported` status. The ESPN library doesn't reliably reveal unprocessed private claims or bids.

These endpoints use the existing loaded ESPN League object, with a 90-second box-score cache and no extra Anthropic calls. ESPN may still take time to load the first time.

## New chatbot questions

- `Am I favored and by how much this week?`
- `What do I need to do to win this week?`
- `How many points above my projection do I need if my opponent hits theirs?`
- `Show me the FAAB remaining for every team.`
- `Who is on my opponent's roster and what are their projections?`
- `Show me the league standings.`
- `Who do I play next week?` (the schedule is also available in League Explorer)

The chatbot prefetches these league facts **before** calling Claude, preventing an unnecessary tool-selection call for recognized questions. A user question may still incur the normal single Claude response call; dashboard reads do not.

## Meaning of the margin

If ESPN shows your projection as 110 and your opponent's as 121.5, your **projected margin** is **-11.5**. The **extra points above your current projection needed to finish ahead if your opponent meets theirs** is **11.51**, because 121.51 beats 121.5. It is **not** a guaranteed winning total or win probability. ESPN projections change during the week and games, and players may become locked after kickoff.

The matchup page identifies positive single-player bench swaps using ESPN's weekly projected points, but **does not auto-submit lineup changes**, and it cannot fully validate live kickoff locks. Possible swaps should be confirmed in ESPN before acting.

## Limitations to keep visible

- **Pending claims** are not yet supported reliably, and hidden opposing bids generally cannot be viewed before waivers process. The app never equates `not_supported` with "no claims".
- **FAAB remaining** is an estimate from starting budget minus recorded spending. Trades or commissioner adjustments may affect real balances. Check ESPN when bidding.
- **Other-team rosters** are read-only and are from ESPN's loaded snapshot. Week-specific projections may be missing for future weeks or players not represented in the box score.
- **Projected lead is not win probability**. A probabilistic model requires validated player variances/correlations and backtesting.
- **Lineup changes and waiver moves are not automatically executed**.
- The local backend has no public-user authentication; **do not expose port 8000 to the internet**.
- The patch passed Python offline tests and TypeScript source syntax checks, **not** live ESPN API integration or a complete React `npm run build` (npm packages were unavailable here). Verify both locally.

## Smoke checks

With Uvicorn running, in a third terminal:

```bash
curl -s http://127.0.0.1:8000/api/strategy/week | python -m json.tool | head -75
curl -s http://127.0.0.1:8000/api/league/standings | python -m json.tool | head -45
curl -s http://127.0.0.1:8000/api/league/faab | python -m json.tool | head -45
cd web && npm run build
```

If a GET returns HTTP 503, check the Uvicorn terminal and the ESPN authorization/network state; secrets are never included in API errors. `FANTASY_DEBUG=1` traces recognized chatbot intent and evidence.
