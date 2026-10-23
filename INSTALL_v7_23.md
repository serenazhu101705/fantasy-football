# Fantasy Coach v7.23 — My Team News + NFL statistical leaders

Incremental patch based on the v7.22 backend / GitHub email scheduler code. It does not change subscriptions, existing sent-email records, ESPN writes, GitHub scheduling, or your frontend.

## Changes

- `app/news_feed.py`: matching, ranked My Team News module with `roster_news(roster, ..., refresh=False)` and `news_feed_page`. Ships as a pair with `api.py` because your installed `news_feed.py` is outdated.
- `app/api.py`: compatible `/api/news/roster`, clear 503 if an outdated news module is accidentally reinstalled, new read-only `/api/nfl/leaders?stat=passing_yards&limit=10`.
- `app/nfl_leaderboards.py`: NFL-wide current-season leaderboard using **existing nflverse weekly stats**, without FantasyPros or Claude. Categories: `passing_yards`, `passing_tds`, `rushing_yards`, `rushing_tds`, `receiving_yards`, `receiving_tds`, `receptions`, `targets`, `completions`.
- `app/llm.py`: routes season-leader questions (including `who leads the league in passing yard`) to a deterministic answer using nflverse; no Claude calls, no stale A/B context. Answers label the statistic's **season** and **through week** so stale data cannot masquerade as live results. Individual player stats remain on the existing player detail path.
- Two regression test files cover routing, aggregation, news-refresh contract and API errors.

## Installation

Open Terminal in the `fantasy_football` repository root. Ensure your own local changes have been committed to Git before applying code updates.

```bash
unzip -o ~/Downloads/fantasy_coach_v7_23_news_and_nfl_leaders.zip
uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

Stop and restart the backend to reload imported Python modules:

```bash
FANTASY_DEBUG=1 uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

The frontend does not need new dependencies. Refresh the browser.

## Verify news fix

```bash
PYTHONPATH=app uv run python - <<'PY'
import inspect
from news_feed import roster_news
print(inspect.signature(roster_news))
assert 'refresh' in inspect.signature(roster_news).parameters
print('My Team News API and module versions match.')
PY

curl -i 'http://127.0.0.1:8000/api/news/roster?refresh=true'
```

The GET can take time because it searches multiple public news providers. It should no longer fail with `roster_news() got an unexpected keyword argument 'refresh'`. Individual provider failures may still yield fewer articles rather than fabricated coverage.

## Verify general NFL leaderboard

```bash
curl -s 'http://127.0.0.1:8000/api/nfl/leaders?stat=passing_yards&limit=5' | python3 -m json.tool
```

Then ask the Fantasy Coach chat `who leads the league in passing yard` or `top 5 receiving yards leaders`. These queries should show an `[NFL_LEADERBOARD]` debug trace with `api_calls=0` (enable `FANTASY_DEBUG=1`). A stat lookup can download nflverse records on the first call and depends on that source being available. The latest week may lag ongoing games; the response always shows the season and highest loaded week.

This feature is for NFL-wide statistical *leaders*. It is not a live news/web-search agent for every general sports question. More categories can be added without changing how the core fantasy coach makes roster decisions.

No changes to `.env`, Resend, your confirmed subscription, or GitHub Actions secrets are needed.
