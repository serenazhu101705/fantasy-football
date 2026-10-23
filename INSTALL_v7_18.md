# Fantasy Coach v7.18 — browser integration hotfix

**Prerequisite:** your repository has v7.17 installed. This patch does not require a new Python or npm dependency.

Fixes:
- Overview, My Roster and Optimal Lineup player names now receive their actual ESPN player IDs via web API payloads, allowing the existing profile drawer to open. Claude's compact context stays unchanged.
- Restores `news_feed_page()` in `app/news_feed.py`, which the live traceback shows is missing locally. `GET /api/news/browse` now supports source filtering and 10-item pages.
- Replaces default-looking paging buttons with styled, responsive controls and numbered pages in Waiver Wire, News, and My Team News.
- Adds mock end-to-end FastAPI checks for roster IDs, player details, cached one-call summary, and news pagination.

## Install

From `fantasy_football` project root, with the downloaded ZIP still in Downloads:

```bash
unzip -o ~/Downloads/fantasy_coach_v7_18_browser_hotfix.zip
uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

Verify you imported the right installed modules:

```bash
PYTHONPATH=app uv run python - <<'PY'
import api, news_feed
assert callable(api._include_profile_ids)
assert callable(news_feed.news_feed_page)
print('v7.18 browser and news functions installed:', api.__file__, news_feed.__file__)
PY
```

**Restart the Python backend** (Ctrl+C in its terminal):

```bash
FANTASY_DEBUG=1 uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

Leave Vite running (or restart with `cd web && npm run dev`) and hard-refresh the Chrome page (`Cmd+Shift+R`) to replace the old CSS.

## Browser/API smoke checks

```bash
curl -fsS 'http://127.0.0.1:8000/api/news/browse?page=1&page_size=10&source=ALL' | python -m json.tool | head -35
curl -fsS 'http://127.0.0.1:8000/api/roster' | PYTHONPATH=app uv run python -c 'import sys,json; data=json.load(sys.stdin); print([(p.get("name"),p.get("espn_id")) for p in data["players"][:5]])'
```

All populated roster players should have non-null `espn_id` (some special-position entries may have no supported bio). Click A.J. Brown's name on Overview or My Roster; it should open the player drawer without a Claude call. Then click Analyze with Claude—this button intentionally makes a single paid call if no cached result exists.

**If Claude player analysis still fails:** check the safe `[PLAYER ANALYSIS]` line in the Uvicorn terminal and the text displayed in the red banner. A live Sonnet API response has not been verified in the offline suite. Don't share API keys, headers, or `.env` contents. The new regression only verifies a mocked successful call, not an actual paid call.

### Testing notes

The rebuilt backend passed 127 Python unit tests. TypeScript transpilation and CSS parsing passed. A complete npm/React production build was not possible in the test container because npm dependency installation timed out; run `cd web && npm run build` locally for the final build check.
