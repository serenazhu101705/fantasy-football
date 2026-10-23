# Fantasy Coach v7.22 — Waiver profiles, emails, and safe ESPN lineup controls

This is an **incremental patch** on top of v7.21. It does not replace `.env`, existing decisions, ESPN scoring, or the CLI. Only modified/new code files are included. It makes **zero provider calls** during its offline test suite.

## Install

From your Git repository root:

```bash
unzip -o ~/Downloads/fantasy_coach_v7_22_email_and_lineup.zip
uv run python -m unittest discover -s tests -p 'test_*.py' -v
```

Restart FastAPI:

```bash
FANTASY_DEBUG=1 uv run uvicorn api:app --app-dir app --host 127.0.0.1 --port 8000
```

Leave `cd web && npm run dev` running and hard-refresh the browser. There is a new **Email & Automation** sidebar tab and an **Apply Optimal Lineup** preview on the lineup page.

## What changed

* **Waiver-player profiles:** `/api/players/{espn_id}` can resolve a player from the already-loaded and paginated ESPN free-agent pool, even when no league team rosters them. It uses only verified, matching ESPN player IDs. Public NFL bio/stats are best-effort; unavailable projections are not fabricated.
* **Mailing-list subscription:** Email confirmation (24-hour token), separate subscription topics, and unsubscribe links. Email settings are stored in local SQLite under `data/` and not in `.env` or in source code.
* **News and weekly digests:** a separate one-shot scheduler can send a Thursday lineup projection, a Claude recap only after all games are final, new injury articles for rostered injured players, and bye-week alerts. The recap costs one Claude call when uncached; other emails use no Claude calls. Per-week/per-article deduplication prevents repeated emails on hourly scheduler runs.
* **Apply Optimal Lineup:** preview the proposed ESPN changes, then explicitly confirm. Only the Python server has ESPN credentials. By default it refuses to write. Live changes use ESPN's undocumented **ROSTER transaction** endpoint, with no automatic retries and a post-write roster check.
* **Automatic injury/bye swap:** an advanced double opt-in. It only tries to replace a starter clearly flagged **OUT / IR / suspended / bye** with a bench candidate chosen by the deterministic optimizer. It never reshuffles healthy starters merely to improve projected points. It rejects unknown roster slots or unverifiable kickoff times.

## Set up email delivery

Add these server-only entries to your `.env` (do not commit this file):

```dotenv
SMTP_HOST=smtp.your-email-provider.example
SMTP_PORT=465
SMTP_USERNAME=your-smtp-login
SMTP_PASSWORD=your-provider-generated-app-password
SMTP_FROM=Fantasy Coach <sender@example.com>
FANTASY_PUBLIC_API_URL=http://127.0.0.1:8000
FANTASY_OWNER_EMAIL=you@example.com
```

You must use the SMTP host and credentials **actually provided by your email service**. The example above is not a working provider.

For local testing, the confirmation link opens `http://127.0.0.1:8000`; it will only work on the computer running the server. Set `FANTASY_PUBLIC_API_URL` to your real HTTPS API domain if deployed. Subscriptions are not active until the user clicks their email confirmation link. Emailing other people from an Internet-exposed instance requires proper login/authentication, abuse protection and consent handling; this starter API is intended for **local use only**.

## Email scheduling (separate from the web server)

Sending newsletters does **not** happen automatically just by opening the UI. Test one run in the repo root:

```bash
uv run python app/run_digest_jobs.py
```

For a server that stays on, schedule this command every hour using cron, launchd, systemd, or a cloud scheduler, with `America/New_York` as its timezone. The script checks if each event is due and whether that confirmed subscriber already received it. Running the command multiple times is safe.

For example, using crontab (`crontab -e`) on a server with a stable Eastern timezone:

```cron
0 * * * * cd /absolute/path/to/fantasy_football && /absolute/path/to/fantasy_football/.venv/bin/python app/run_digest_jobs.py >> data/digest_jobs.log 2>&1
```

**Your Mac must be awake and the scheduled runner active, or host the runner on a server.** The app cannot email you while turned off. Thursday lineup emails are sent during the morning window (7 AM–noon Eastern). The recap is sent after the final Monday game, when ESPN confirms all NFL games and fantasy scores are final (often Tuesday morning). If ESPN data or SMTP fails, no delivery is claimed.

## Applying an ESPN lineup: safety settings

The preview works without write credentials, but actual ESPN writes are disabled until you add:

```dotenv
ESPN_WRITES_ENABLED=1
```

Restart the backend after updating `.env`. Go to **Optimal Lineup → Preview lineup changes**. Review every `From → To` slot, then press **Confirm and apply on ESPN**. Changes cannot be sent when the preview is expired (3 minutes), lineup/roster changed, a player's kickoff is unverifiable, any affected player's game has started, or the lineup is not the supported standard 9-slot format.

ESPN's lineup write endpoint is **undocumented** and can change without warning. A 2xx response is not enough: the server tries to reload the ESPN roster, reports whether it verified the update, and avoids automatic retries on network timeouts. If verification is pending, **look at ESPN directly before attempting another change**.

### Opt-in automatic injury/bye swaps

This is a **separate authorization** from receiving emails:

1. Subscribe in the app with **Automatic injury/bye swaps** checked.
2. Confirm ownership of that email via the email link.
3. Ensure it matches `FANTASY_OWNER_EMAIL` in `.env` (other subscribers cannot control your roster).
4. Only if you are comfortable with independent future lineup writes, set both:

```dotenv
ESPN_WRITES_ENABLED=1
ESPN_AUTO_SWAPS_ENABLED=1
```

5. Run the hourly scheduler on a machine that stays on.

The scheduler does not drop players, submit waiver claims, change healthy starters for projection gains, or make moves when it cannot verify locks. **Questionable/doubtful are never treated as automatic bench commands.** It will refuse rather than guess if a bye or matchup/slot cannot be established. ESPN can enforce additional commissioner lineup-lock rules, so monitor your roster and leave this advanced mode off until you have tested the manual flow.

## Local tests

```bash
uv run python -m unittest discover -s tests -p 'test_*.py' -v
cd web && npm run build
```

The Python integration suite passed offline in the provided workspace. The TypeScript/TSX files passed syntax transpilation; a full React production build still needs the installed npm dependencies on your Mac. No live ESPN write, SMTP send, authenticated ESPN waiver lookup, or paid Claude recap was performed during development.

## Git note

Review before committing:

```bash
git diff --stat
git status
```

Never commit `.env`, the subscription SQLite database, or SMTP/ESPN credentials.
