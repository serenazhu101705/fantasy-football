# Fantasy Coach v7.25 — Source-first NFL Q&A

This is an **incremental Python-only patch** for the v7.23 backend (v7.24 changed frontend only). It does **not** change the React app, GitHub Actions email workflow, SMTP settings, subscriber database, or ESPN write permissions.

## Install

From your `fantasy_football` repository root:

```bash
# Optional: inspect local changes before overwriting files
git status --short

unzip -o ~/Downloads/fantasy_coach_v7_25_source_first_qa.zip

uv run python -m unittest discover -s tests -p 'test_*.py' -v

# Restart FastAPI after stopping the previous process with Control-C:
FANTASY_DEBUG=1 uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

Open the chat and ask:

- `who leeeads the league in rushing yards`
- `who leads the NFL in passing yards`
- `top 5 receiving yards leaders`
- `who has the most rushing attempts`
- `what are the latest NFL injury updates?`

The first four should route to `nfl_leaderboard` and respond with zero Claude calls **when nflverse or ESPN returns a verifiable answer**. The injury/news question can use one Claude call to summarize **attributed** source headlines. It cannot turn a headline into a verified statistical ranking or injury designation.

## What changed

- `app/nfl_leaderboards.py`: typo-tolerant, more metrics, ESPN-team column fix, source-aware answer attribution.
- `app/nfl_source_lookup.py`: new source registry for NFL-wide statistics (nflverse → ESPN), and ESPN NFL News / FantasyPros Articles / Google News RSS fallback for broader current NFL questions. Has timeouts, fixed HTTPS endpoints, attribution, and 15-minute headline cache.
- `app/llm.py`: routes general NFL questions into evidence gathering; adds an optional `search_nfl_sources` Claude tool, plus a one-time research retry if the model incorrectly says its tools are roster-only. This tool does not write to ESPN.
- `tests/test_coach_regressions.py` and `tests/test_v725_multi_source_research.py`: offline regression coverage.

## Notes and limitations

- **No paid API key** is needed to fetch public NFL statistics and news; Claude may still consume credits when composing an answer to an open-ended question.
- ESPN public endpoints are undocumented and their output can change. If neither ESPN nor nflverse provides a verified statistic, the app should explain that, not guess.
- The currently configured nflverse dataset is the **2026 season**. Explicit historical-season questions do not silently substitute the current season. Historical ESPN-only season lookups are not yet fully validated.
- News evidence includes headlines and links; there is **no complete general-purpose web search engine** in the app yet. Game status, player availability, numerical stats, rankings, and news headlines require different data providers and should not be treated as interchangeable.
- If your app has been running for hours, nflverse's existing in-process cache may be stale until you restart FastAPI. Verify the reported last available week.
- v7.25 does not fix or change your independent GitHub email scheduler, your web UI or the favicon/player headshots from v7.24.

## Expected debug logging

A numeric question that matches a stat route prints `[NFL_LEADERBOARD]` with `api_calls=0` when available. If local nflverse has no answer, the new lookup attempts ESPN public NFL leaders before saying it's unverified. `status=unavailable` is an honest outcome, not a claim the NFL statistic doesn't exist.

## Testing

The tests are offline: they mock provider payloads. They verify the *control flow*, parsing, attribution, and no-guess behavior, **not live 2026 ESPN totals**. Live ESPN endpoint availability and your local dependencies must be verified in your environment.
