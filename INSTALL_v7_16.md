# Fantasy Coach v7.16 — Roster Layouts & Pagination

**Applies on top of v7.15.** Unzip from your repository root. It only replaces the files included in the archive; your `.env`, ESPN cookies, keys and saved decisions are not touched.

## Install

```bash
unzip -o ~/Downloads/fantasy_coach_v7_16_rosters_and_pagination.zip
uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

Stop your existing backend (Ctrl+C), then restart it:

```bash
FANTASY_DEBUG=1 uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

Keep Vite running; refresh http://127.0.0.1:5173 . If Vite is not running:

```bash
cd web
npm install
npm run dev
```

## Improvements

- **Opponent Intelligence:** selected opponent's roster is displayed as two columns—starting slots in ESPN order (QB, RB1, RB2, WR1, WR2, TE, FLEX, D/ST, K), then Bench & IR. The actual roster slot from ESPN is authoritative; nonstandard league slots are supported. Player names open the same player detail drawer.
- **My Roster:** uses the exact same sorted two-column layout, with clickable players.
- **Waiver Wire:** 10 distinct players per page, with position filters and correct ESPN player IDs for the player detail drawer. An initial **on-demand ESPN-only browse request** expands the free-agent list up to 400 players, subject to ESPN response limits. Subsequent pages use the cached list. The API merges previously enriched data into this pool, but **players outside the original preloaded first 100 may have ESPN-only projections**. No extra FantasyPros or Claude calls. If ESPN denies the larger fetch, it transparently falls back to the initial loaded pool.
- **News Feed:** 10 verified articles per page, paginated **after source filtering** (ESPN, RotoWire, FantasyPros Articles), from all articles already present in cached RSS data. My Team News is shown 10 roster players per page. These are publisher links, not scraped article content.
- The deterministic Claude waiver advisor keeps its separately ranked shortlist and one-call conversation behavior. No changes to lineup decision-making.

## Debug/read-only checks

- http://127.0.0.1:8000/api/waivers/browse?position=ALL&page=1&page_size=10
- http://127.0.0.1:8000/api/waivers/browse?position=RB&page=2&page_size=10
- http://127.0.0.1:8000/api/news/browse?source=ALL&page=2&page_size=10

Only one additional ESPN browse call is made per data refresh when a waiver page first loads, not per page. ESPN may return fewer than 400. Public RSS/news calls use existing caching.

## Verification

- Python: 111 offline regression tests passed, including 8 new pagination/browse tests.
- Frontend: TypeScript/TSX **syntax** checked; the shared roster sorting routine executed in Node with example ESPN slots.
- Full `npm run build` was not verified here because React/npm dependencies were unavailable in the offline environment. Run it on your Mac if you want a complete production build check.
- No authenticated ESPN, paid Claude, or live browser session used during testing.
