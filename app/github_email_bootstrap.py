"""Provision only the repo owner's own verified destination in a private Actions runner.

This is an explicit single-user deployment mode; it does not replace the local
web UI's email confirmation flow and never allows automatic ESPN writes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import secrets

from email_subscriptions import _db, _token_hash, _valid_email


def ensure_owner_subscription() -> str:
    if os.getenv("FANTASY_PERSONAL_EMAIL_MODE") != "1":
        raise RuntimeError("Personal runner mode must be explicitly enabled.")
    if os.getenv("FANTASY_REPOSITORY_PRIVATE", "").lower() != "true":
        raise RuntimeError("Personal subscriber records require a private GitHub repository.")
    enabled = os.getenv("FANTASY_AUTO_SWAP_ENABLED", "0")
    if enabled not in ("0", "1"):
        raise RuntimeError("Auto Swap requires an explicit 0/1 setting.")
    if (os.getenv("ESPN_WRITES_ENABLED", "0") != enabled or
            os.getenv("ESPN_AUTO_SWAPS_ENABLED", "0") != enabled):
        raise RuntimeError("Auto Swap and ESPN write safety flags must agree.")
    email = _valid_email(os.environ["FANTASY_OWNER_EMAIL"])
    now = datetime.now(timezone.utc)
    with _db() as db:
        other = db.execute("SELECT email FROM subscribers WHERE confirmed=1 AND email <> ?", (email,)).fetchone()
        if other is not None:
            raise RuntimeError("State contains another subscriber; refusing personal-only delivery.")
        existing = db.execute("SELECT email,confirmed FROM subscribers WHERE email=?", (email,)).fetchone()
        if existing is not None:
            # Never overwrite the persisted preferences of an existing subscriber.
            if not existing["confirmed"]:
                raise RuntimeError("Restored owner subscription is unconfirmed; check database state.")
            # Always honor the current owner switch, including turning it OFF.
            db.execute("UPDATE subscribers SET auto_swap=? WHERE email=?",
                       (int(enabled), email))
            return "existing"
        db.execute("""INSERT INTO subscribers
            (email, confirmed, confirm_hash, confirm_expiry, unsubscribe_hash,
             thursday_lineup, monday_recap, injury_news, bye_alerts, auto_swap, created_at)
            VALUES (?,1,?,?,?,1,1,1,1,?,?)""",
            (email, "", (now + timedelta(days=365)).isoformat(),
             _token_hash(secrets.token_urlsafe(30)), int(enabled), now.isoformat()))
    return "initialized"


if __name__ == "__main__":
    status = ensure_owner_subscription()
    print(f"Personal Fantasy Coach email state: {status}. Owner Auto Swap preference synced.")
