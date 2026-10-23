# Fantasy Coach v7.21 — Correct lineup comparisons, scored game logs, ranked My Team News

Apply **only after v7.20**. This is an incremental patch; it does not touch your `.env`, ESPN cookies, cache, Claude API key, or recommendation journal. Commit or stash your existing work with Git before unzipping.

## Install

From the `fantasy_football` repository root:

```bash
unzip -o ~/Downloads/fantasy_coach_v7_21_correctness_fix.zip
uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

Restart the backend (stop the old Uvicorn process with Ctrl+C first):

```bash
FANTASY_DEBUG=1 uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

Leave Vite running, then hard-refresh Chrome (`Cmd+Shift+R`). There is no need to run npm install if v7.20 is already working.

## What changed

- `app/lineup_explanations.py` and `app/api.py`: use consistently restored browser-side player IDs and normalized full names when excluding all selected starters from each slot's alternative pool. The best alternative can only be an eligible, available *nonstarter*. No more Burrow-versus-Burrow, Gibbs-versus-Gibbs, or Gibbs as a hypothetical replacement for starting Chase Brown.
- `app/matchup_live.py`: when loaded ESPN `Player.stats` only has one week, ask the authenticated ESPN fantasy league's historical box scores for the remaining completed weeks by exact `playerId`; keep authoritative ESPN fantasy scores ahead of nflverse-derived points and use public NFL gamelogs only when no verified weekly records exist. Include the current week in Recent Performances only after ESPN NFL scoreboard confirms the player's game is FINAL.
- `app/news_feed.py`: gather roster-matched articles from general RSS, ESPN player news, and Google News **before** ranking. Rank using exact-player relevance, published date, fantasy/injury relevance and deduplication, with 15-minute cache and a manual refresh option.
- `web/src/App.tsx`, `web/src/api.ts`, and `web/src/styles.css`: show only the top two ranked stories initially; `View all N ranked articles` expands additional items in the app. Display the source and freshness timestamp. `Refresh coverage` bypasses the roster-news cache. Recent Performances already displays real scores, and now its data endpoint has the missing ESPN fallback.
- `tests/test_v721_integration_fixes.py` adds new end-to-end/mocked regressions; `tests/test_live_matchup_and_feed.py` updates an outdated assumption (previously it intentionally skipped ESPN news if a general feed happened to have a hit).

No extra Claude or paid FantasyPros calls are added. Accessing a player profile may make up to five additional authenticated ESPN **fantasy box-score read** requests on the first uncached visit if nflverse and the loaded ESPN Player.stats lack those weeks. The existing league box-score cache is shared with league intelligence. My Team News searches all three article sources per player when you open the tab, so its first load can be slower.

## Diagnose the live responses (no keys/cookies)

The endpoint data can be checked directly, avoiding stale React UI state.

Check the Week 7 lineup explanation:

```bash
curl -fsS http://127.0.0.1:8000/api/lineup | python3 -c 'import json,sys; d=json.load(sys.stdin); print("API version: lineup"); [(print(slot, ":", e["player"], "vs", (e["best_eligible_nonstarter"] or {}).get("name", "no eligible bench alternative"))) for slot,e in d["explanation"]["slot_explanations"].items()]'
```

Check A.J. Brown's weekly scores (ESPN player ID from your prior console log):

```bash
curl -fsS 'http://127.0.0.1:8000/api/players/4567104?week=7' | python3 -c 'import json,sys; p=json.load(sys.stdin); print(p["name"]); [(print("Week",g.get("week"),"points",g.get("fantasy_points"),"source",g.get("fantasy_points_source"))) for g in p["recent_games"]]'
```

Check recent roster news ranking and refresh time:

```bash
curl -fsS 'http://127.0.0.1:8000/api/news/roster?refresh=true' | python3 -c 'import json,sys; d=json.load(sys.stdin); print("Refreshed:",d.get("refreshed_at")); [(print(p["name"],":",[(a["title"],a.get("published_at")) for a in p["articles"][:2]])) for p in d["players"][:3]]'
```

If a past-week fantasy score still says unavailable, check whether the ESPN box score contained the player in **any** lineup that week. A player who was a free agent at the time may not appear on any fantasy team's historical box score; if nflverse cannot provide their stats either, the app will still say unavailable rather than fabricate an exact league score. The backend tests used mocked ESPN shapes, not access to your private league.

## Verification completed here

- 132 Python offline regression tests passed.
- TypeScript files parsed using TypeScript's transpiler; CSS passed parser validation.
- A full React/Vite production build was not performed here because the local sandbox lacks the project's npm dependencies.
- No live paid Claude calls or ESPN authenticated requests were performed.
