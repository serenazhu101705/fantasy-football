# Fantasy Coach v7.19 — Weekly Claude Recap Hotfix

Incremental patch for repositories running v7.18. No dependency changes, frontend edits, or additional FantasyPros calls.

## Install

From the `fantasy_football` repository root:

```bash
unzip -o ~/Downloads/fantasy_coach_v7_19_recap_hotfix.zip
uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

Restart the FastAPI process (Ctrl+C in its terminal), then:

```bash
FANTASY_DEBUG=1 uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

Keep Vite running and refresh the browser. Open Weekly Matchup > Weekly recap and select a week ESPN marks complete. Click Generate recap with Claude again. (A previous failed/empty recap was not cached.)

## Changes

- `app/weekly_recap.py`: Sonnet 5.5 requests `thinking={"type":"between_tools"}` and `output_config={"effort":"low"}`, with a 1,400-token allowance. This prevents hidden adaptive-thinking tokens from taking the small allowance before visible text is produced. One attempt only; no automatic paid retries. Other model names retain their default supported request format.
- Empty text raises a typed `RecapEmptyResponseError`, records the provider stop reason and content-block types in an optional `[WEEKLY RECAP]` debug log, and never saves an empty cache file.
- `app/api.py`: The no-text model failure now returns HTTP 502, not a misleading 409 for a week that isn't finished. Error details are sanitized and the debug log doesn't print prompts, API keys, or private ESPN context.
- `tests/test_weekly_recap_v719.py`: Tests the Sonnet settings, successful one-call-plus-cache path, no-text failure and no automatic retry, compatibility with other models, and HTTP error status.

## Verification and limits

The Python test suite passed **131/131 offline tests** on the reconstructed v7.18 code snapshot. No paid Claude request or authenticated ESPN connection was available in the test container. If the live request still fails, share only the `[WEEKLY RECAP]` diagnostic line or the new red error text, not private keys or `.env`.

You can also inspect the status without spending credits:

```bash
curl -s 'http://127.0.0.1:8000/api/recaps/week?week=7' | python -m json.tool
```
