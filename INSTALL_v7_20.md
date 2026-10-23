# Fantasy Coach v7.20 — Lineup explanations & weekly fantasy game logs

Incremental patch on top of v7.19. **No secrets or .env files are included.**

## Apply

From the `fantasy_football` repo root:

```bash
unzip -o ~/Downloads/fantasy_coach_v7_20_lineup_and_gamelog.zip
uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

Restart FastAPI (Ctrl+C first):

```bash
FANTASY_DEBUG=1 uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

React/Vite should reload automatically. For a complete frontend validation on your Mac:

```bash
cd web
npm run build
```

Hard-refresh the browser if necessary. Use `git diff` to review the six updated files and new test.

## Fix 1: proper lineup alternatives

`lineup_explanations.py` now matches selected starters by ID when **both** have one, or by their normalized full names when the compact model-facing roster intentionally omits IDs. It filters out *every* chosen starter and players on bye/IR or definitively out, before selecting the highest-projected eligible unselected player for each slot.

For example, if Joe Burrow is your only healthy QB, the explanation correctly says there's **no eligible nonstarter**. It will never say 'Burrow leads Burrow'. A RB2 starter compares against the highest-projected unselected eligible RB—not your already-selected RB1.

## Fix 2: weekly fantasy scores, not meaningless NFL dates

`season_nfl_stats.py` now supplies week-specific nflverse PPR-adjusted fantasy points plus actual stat lines where verified season-specific records exist. `matchup_live.py` merges that history with ESPN Fantasy roster results and gives ESPN's league-scored fantasy points priority for matching weeks. It will not label an unscored public NFL game log as fantasy points.

The player drawer now displays expandable recent performances showing week, verified points, source, and actual NFL stats. A weekly ESPN projection appears where the roster provided it. Missing values are labeled honestly—nothing is invented.

No extra paid Claude or FantasyPros calls. Existing player-summary, caching, waiver and matchup flows are not modified.

## Testing limits

The backend suite runs offline. Frontend TypeScript/JSX syntax was checked, but the full `npm run build` and live ESPN responses have not been verified here. The amount of historical coverage depends on the actual nflverse/ESPN cached records available to your running app.
