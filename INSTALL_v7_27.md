# Fantasy Coach v7.27 — Personalized Waiver Explanations

This is an **incremental patch** for the existing v7.26 backend and v7.24 frontend. It preserves your Github email workflow, Resend configuration, existing player profiles, and roster calculations.

## What's new

The **Waiver Wire** screen has a new **Best pickups for your team** section:

- Uses the *current ESPN-owned roster* and existing evaluator-ranked add/drop transactions. Genuine evaluator-approved transactions appear first, with the drop player and original model rationale.
- Others appear as **Watchlist—not a recommended add**. A high prescreen rank is never treated as proof of a roster upgrade.
- Every card gives a player-specific roster fit, ESPN/FantasyPros week projections when loaded, a comparison to the least-projected player **at the same position** when ESPN data is available, and verified ROS rankings/market signals when available.
- A claimed weekly **projected lineup points gain** is included **only if both the before- and after-transaction lineups have complete projection coverage**; comparisons to the lowest projection do not mean a starter will change.
- Expanded ESPN free-agent browse pages (10 per page) now give an explanation per player. Click *Full team-fit analysis* for the details, without making Claude calls.
- Positions include QB, RB, WR, TE, K and D/ST where ESPN returns them. The deeper evaluator shortlist currently emphasizes QB/RB/WR/TE; K/DST may be available only in browse.

All calculations are read-only; there is **no ESPN transaction/write**, no new FantasyPros usage and no Claude call.

## Installation

At the **repo root** (`fantasy_football`), check `git status` and protect any uncommitted edits. Then:

```sh
unzip -o ~/Downloads/fantasy_coach_v7_27_waiver_explanations.zip
uv run python -m unittest discover -s tests -p 'test_v727_waiver_explanations.py' -v
```

The following files are included:

- `app/api.py` (based on the v7.26 NFL guard; adds `GET /api/waivers/recommended`)
- `app/browsing.py` (adds per-candidate explanations)
- `app/waiver_explanations.py` (new pure explanation builder)
- `web/src/api.ts` (types)
- `web/src/App.tsx` (new recommendation cards and per-page explanations; based on v7.24 UI)
- `web/src/styles.css` (styles)
- `tests/test_v727_waiver_explanations.py` (10 new tests)

Stop the old FastAPI process with Ctrl-C, then start it again:

```sh
FANTASY_DEBUG=1 uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

In a separate terminal:

```sh
cd web
npm run build
npm run dev
```

Open **Waiver Wire**, and check both *Best pickups for your team* and *Browse all available players*. The first page will take a little time if the ESPN free-agent pool hasn't been cached in this backend process.

### Verify API without the UI

```sh
curl -s 'http://127.0.0.1:8000/api/waivers/recommended?position=RB&limit=5' | python3 -m json.tool
curl -s 'http://127.0.0.1:8000/api/waivers/browse?position=RB&page=1&page_size=10' | python3 -m json.tool
```

Look for `recommendations[].fit`, `.roster_comparison`, `.this_week`, `.rest_of_season`, `.decision` and `candidate_explanations[]`. The example current-week amounts depend on actual loaded ESPN/FantasyPros signals, not hardcoded values.

### Validation status

- Python: 183/183 offline regression tests passed in a reconstructed project baseline including existing shipped v7.6–v7.26 Python modules; 10 new tests cover this feature.
- TypeScript syntax: checked with global `tsc`. A full frontend production build **could not be completed here** because fetching npm dependencies timed out. Run `npm run build` on your Mac before pushing.
- Live ESPN response/actual transactions were **not** exercised.

### Known boundaries

The deeper evaluator's shortlist is limited to players previously enriched and loaded by the app. Other ESPN free agents get a lightweight team-fit explanation in the browser, not a claimed upgrade. The results are a snapshot of the loaded ESPN session. Refresh your dashboard for updated rosters and free agents. A live lineup or scoring projection can change after news, games, or waiver claims.
