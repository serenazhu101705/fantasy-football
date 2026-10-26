# Fantasy Coach: on-demand weekly Claude analysis cache

**What it does:** Opening a player profile makes a read-only GET request for that player’s saved report. If none exists for the current ESPN fantasy week, the green **Analyze with Claude** button remains available. Only clicking it makes a paid POST request. A saved report is reused on later visits (including from other devices) and remains current through that fantasy week. When ESPN changes to the next week, the report no longer appears, and the green button returns. The old reports are periodically discarded. Does not pre-generate reports for any players.

## Exact ZIP contents

- `app/weekly_analysis_cache.py` — **permanent** runtime module; commit to git
- `tests/test_weekly_analysis_cache.py` — **permanent** regression tests; commit to git
- `install_weekly_analysis_cache.py` — temporary guarded installer; may delete after installation
- `INSTALL.md` — installation instructions; optional to keep

The installer **modifies**, but does not replace, your existing `app/api.py` and `web/src/App.tsx`. It deliberately leaves `app/player_analysis.py`, Auto Swap, and email code unchanged. It makes `.before_weekly_cache` backups and aborts if it cannot recognize the source.

## Install from your repository root

```bash
cd ~/fantasy_football
unzip -o ~/Downloads/fantasy_coach_weekly_analysis_cache.zip
python3 install_weekly_analysis_cache.py
python3 -m unittest discover -s tests -p 'test_weekly_analysis_cache.py' -v
uv run python -m py_compile app/api.py app/weekly_analysis_cache.py
cd web && npm run build && cd ..
git diff -- app/api.py web/src/App.tsx
```

Only after tests/build pass, commit changed source files:

```bash
git add app/weekly_analysis_cache.py tests/test_weekly_analysis_cache.py app/api.py web/src/App.tsx
git commit -m "Cache on-demand Claude player analysis per ESPN week"
git push
```

Do **not** commit your API credentials or local database.

## Persistent storage when Render sleeps/restarts — essential

Render Free has an ephemeral filesystem. By default the module uses a local SQLite file (`data/player_analysis_cache.sqlite3`), which survives local Mac app restarts but **does not survive Render Free instance replacement/restart**. To make saved reports truly persistent on Render, create a free Upstash Redis database (https://console.upstash.com/redis), and in **Render → Environment** set:

- `UPSTASH_REDIS_REST_URL` = exact REST URL from your Upstash database's REST API panel (must be HTTPS)
- `UPSTASH_REDIS_REST_TOKEN` = REST token for that database (server-side secret, never put it in React)

Save and deploy. No new Python dependencies are required; the module uses Python's standard library. Only the final analyst report is stored in Redis; no ESPN cookies, Claude API key, or entire prompts are stored there. Choose an appropriate region and restrict access to the private Render service. Reports have a 14-day storage expiry, but are **never reused after the fantasy week changes**, even if an old record remains temporarily in Redis.

If Upstash is configured but temporarily inaccessible, the endpoint returns an error **without invoking Claude**; this avoids surprise charges when it can't confirm whether a report exists.

## Important boundaries

- The week is taken from `engine.APP_CONTEXT.current_week` (ESPN), not the user's local calendar week. Use Refresh if ESPN context is stale at rollover.
- **One paid analysis is expected per player/week** for normal sequential requests. This patch does not implement distributed cross-server request locking; two precisely simultaneous first-time clicks can still invoke Claude twice. Avoid multiple clicks across devices until the first finishes.
- Cached reports are reused even if new injury news arrives midweek. This is intentional per the one-per-week rule. They refresh on the next ESPN fantasy week.
- Changing the browser tab or closing the drawer does not trigger Claude.
- This package does not resolve any unrelated email/Auto Swap issues.
