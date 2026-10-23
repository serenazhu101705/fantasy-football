"""Launch one protected cloud web server and one hourly email checker.

The checker runs as a separate Python process sharing the same persistent
SQLite volume. One Railway replica only. Do not use Uvicorn --reload/workers.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent


def run_email_checker() -> None:
    # Let the web service become ready before fetching ESPN data.
    time.sleep(30)
    while True:
        started = time.monotonic()
        try:
            result = subprocess.run(
                [sys.executable, str(ROOT / "app" / "run_digest_jobs.py")],
                cwd=str(ROOT), timeout=1800, check=False,
            )
            if result.returncode:
                print(f"[EMAIL SCHEDULER] Exit code {result.returncode}", flush=True)
        except subprocess.TimeoutExpired:
            print("[EMAIL SCHEDULER] Run timed out; check provider logs", flush=True)
        except Exception as exc:
            print(f"[EMAIL SCHEDULER] Failed: {type(exc).__name__}", flush=True)
        # One check about every hour; the digest script handles Eastern time
        # windows and deduplicates sends using persistent sent_events rows.
        time.sleep(max(60, 3600 - (time.monotonic() - started)))


def main():
    required = ["FANTASY_WEB_USERNAME", "FANTASY_WEB_PASSWORD", "FANTASY_SUBSCRIPTIONS_DB"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise SystemExit("Missing cloud configuration: " + ", ".join(missing))
    db_file = Path(os.environ["FANTASY_SUBSCRIPTIONS_DB"])
    if not db_file.is_absolute() or not db_file.parent.is_dir():
        raise SystemExit("FANTASY_SUBSCRIPTIONS_DB must point inside your mounted persistent volume")
    sys.path.insert(0, str(ROOT / "app"))
    if os.getenv("FANTASY_EMAIL_SCHEDULER_ENABLED", "0") == "1":
        threading.Thread(target=run_email_checker, daemon=True, name="fantasy-email-checker").start()
        print("[EMAIL SCHEDULER] Enabled: hourly one-shot checks", flush=True)
    else:
        print("[EMAIL SCHEDULER] Disabled until email service is verified", flush=True)
    import uvicorn
    uvicorn.run("deploy_api:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), workers=1)


if __name__ == "__main__":
    main()
