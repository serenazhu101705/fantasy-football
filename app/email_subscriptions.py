"""Explicit opt-in fantasy email digests (SQLite + HTTPS Resend or SMTP).

Subscriptions require confirmation by email. Scheduled delivery is driven by
run_digest_jobs.py / an external cron job; starting Uvicorn doesn't schedule jobs.
No sensitive ESPN cookies or Anthropic keys are stored in the database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from email.message import EmailMessage
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import smtplib
from dotenv import load_dotenv
import sqlite3
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

load_dotenv()

DB_PATH = Path(os.getenv("FANTASY_SUBSCRIPTIONS_DB", "data/fantasy_subscriptions.sqlite3"))
OPTIONS = ("thursday_lineup", "monday_recap", "injury_news", "bye_alerts", "auto_swap")


@contextmanager
def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS subscribers (
       email TEXT PRIMARY KEY, confirmed INTEGER NOT NULL DEFAULT 0,
       confirm_hash TEXT NOT NULL, confirm_expiry TEXT NOT NULL,
       unsubscribe_hash TEXT NOT NULL,
       thursday_lineup INTEGER NOT NULL DEFAULT 1,
       monday_recap INTEGER NOT NULL DEFAULT 1,
       injury_news INTEGER NOT NULL DEFAULT 1,
       bye_alerts INTEGER NOT NULL DEFAULT 1,
       auto_swap INTEGER NOT NULL DEFAULT 0,
       created_at TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS sent_events (
      email TEXT NOT NULL, kind TEXT NOT NULL, event_key TEXT NOT NULL,
      sent_at TEXT NOT NULL, PRIMARY KEY(email,kind,event_key)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS unsubscribe_tokens (
      token_hash TEXT PRIMARY KEY, email TEXT NOT NULL
    )""")
    try:
        with con:
            yield con
    finally:
        con.close()


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _valid_email(email):
    email = email.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", email):
        raise ValueError("Enter a valid email address.")
    return email


def _send_resend_https(to, subject, body):
    """Send an email over HTTPS (Railway Free blocks outbound SMTP).

    Raises a safe error that does not echo the API key or raw provider response.
    Calls the Resend transactional email API once; no automatic paid retries.
    """
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    sender = os.getenv("RESEND_FROM_EMAIL", "").strip() or os.getenv("SMTP_FROM", "").strip()
    if not api_key or not sender:
        raise RuntimeError("Configure RESEND_API_KEY and RESEND_FROM_EMAIL to send through Resend HTTPS.")
    payload = json.dumps({"from": sender, "to": [to], "subject": subject, "text": body}).encode("utf-8")
    request = Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            status = response.status
            body_bytes = response.read(16384)
    except HTTPError as exc:
        # Don't return raw provider errors; they can contain recipient details.
        raise RuntimeError(f"Resend HTTPS email rejected (HTTP {exc.code}). Verify your sending domain and recipient permissions.") from None
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Resend HTTPS email transport failed ({type(exc).__name__}).") from None
    if status not in (200, 201, 202):
        raise RuntimeError(f"Resend HTTPS email returned unexpected status {status}.")
    try:
        record = json.loads(body_bytes)
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("Resend HTTPS email returned an unreadable response.") from None
    if not isinstance(record, dict) or not record.get("id"):
        raise RuntimeError("Resend HTTPS email did not confirm acceptance.")
    return record["id"]


def send_email(to, subject, body, *, transport=None):
    """Email sending is server-only. Prefer HTTPS Resend on cloud deployments."""
    if transport:
        return transport(to, subject, body)
    if os.getenv("FANTASY_EMAIL_TRANSPORT", "").strip().lower() == "resend" or os.getenv("RESEND_API_KEY"):
        return _send_resend_https(to, subject, body)
    host = os.getenv("SMTP_HOST")
    sender = os.getenv("SMTP_FROM")
    if not host or not sender:
        raise RuntimeError("SMTP is not configured. Set SMTP_HOST and SMTP_FROM on the server.")
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    port = int(os.getenv("SMTP_PORT", "465"))
    user, password = os.getenv("SMTP_USERNAME"), os.getenv("SMTP_PASSWORD")
    if port == 465:
        client = smtplib.SMTP_SSL(host, port, timeout=12)
    else:
        client = smtplib.SMTP(host, port, timeout=12)
    with client:
        if port != 465:
            client.starttls()
        if user and password:
            client.login(user, password)
        client.send_message(msg)


def subscribe(email, preferences, *, transport=None):
    email = _valid_email(email)
    selected = {key: bool(preferences.get(key, False)) for key in OPTIONS}
    if not any(selected.values()):
        raise ValueError("Choose at least one subscription topic.")
    # An automatic lineup action requires a separately confirmed setting and
    # server-side ESPN writes opt-in; email confirmation alone is not enough.
    confirm = secrets.token_urlsafe(32)
    unsubscribe = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expiry = (now + timedelta(hours=24)).isoformat()
    with _db() as db:
        db.execute("""INSERT INTO subscribers
            (email,confirmed,confirm_hash,confirm_expiry,unsubscribe_hash,
             thursday_lineup,monday_recap,injury_news,bye_alerts,auto_swap,created_at)
             VALUES(?,?,?,?,?,?,?,?,?,?,?)
             ON CONFLICT(email) DO UPDATE SET confirmed=0,confirm_hash=excluded.confirm_hash,
              confirm_expiry=excluded.confirm_expiry,unsubscribe_hash=excluded.unsubscribe_hash,
              thursday_lineup=excluded.thursday_lineup,monday_recap=excluded.monday_recap,
              injury_news=excluded.injury_news,bye_alerts=excluded.bye_alerts,
              auto_swap=excluded.auto_swap""",
           (email, 0, _token_hash(confirm), expiry, _token_hash(unsubscribe),
            *[int(selected[k]) for k in OPTIONS], now.isoformat()))
    base = os.getenv("FANTASY_PUBLIC_API_URL", "http://127.0.0.1:8000").rstrip("/")
    link = f"{base}/api/subscriptions/confirm?token={quote(confirm)}"
    send_email(email, "Confirm your Fantasy Coach emails",
               f"Confirm the newsletters you requested by opening:\n{link}\n\n"+
               "This link expires in 24 hours. Ignore it if you didn't sign up.\n", transport=transport)
    return {"status": "pending_confirmation", "message": "Check your email and confirm within 24 hours."}


def confirm(token):
    if not token or len(token) > 512:
        return False
    with _db() as db:
        record = db.execute("SELECT email,confirm_expiry FROM subscribers WHERE confirm_hash=?", (_token_hash(token),)).fetchone()
        if not record:
            return False
        if datetime.fromisoformat(record["confirm_expiry"]) < datetime.now(timezone.utc):
            return False
        db.execute("UPDATE subscribers SET confirmed=1,confirm_hash='' WHERE email=?", (record["email"],))
    return True


def unsubscribe(token):
    if not token or len(token) > 512:
        return False
    with _db() as db:
        token_hash = _token_hash(token)
        record = db.execute("SELECT email FROM unsubscribe_tokens WHERE token_hash=?",(token_hash,)).fetchone()
        if record is None:
            record = db.execute("SELECT email FROM subscribers WHERE unsubscribe_hash=?",(token_hash,)).fetchone()
        if record is None:
            return False
        email = record["email"]
        db.execute("DELETE FROM subscribers WHERE email=?",(email,))
        db.execute("DELETE FROM unsubscribe_tokens WHERE email=?",(email,))
        return True


def confirmed_subscribers():
    with _db() as db:
        return [dict(row) for row in db.execute("SELECT * FROM subscribers WHERE confirmed=1")]


def was_sent(email, kind, event_key):
    with _db() as db:
        return bool(db.execute("SELECT 1 FROM sent_events WHERE email=? AND kind=? AND event_key=?",
                               (email,kind,event_key)).fetchone())


def mark_sent(email, kind, event_key):
    with _db() as db:
        db.execute("INSERT OR IGNORE INTO sent_events(email,kind,event_key,sent_at) VALUES(?,?,?,?)",
                   (email,kind,event_key,datetime.now(timezone.utc).isoformat()))


def send_digest(subscriber, kind, event_key, subject, content, *, transport=None):
    if not subscriber.get("confirmed") or not subscriber.get(kind):
        return False
    if was_sent(subscriber["email"],kind,event_key):
        return False
    # Each email includes its own one-time random unsubscribe token, stored as
    # a hash. Old unsubscribe links remain valid after later digest messages.
    if os.getenv("FANTASY_PERSONAL_EMAIL_MODE") == "1":
        # The personal GitHub runner has no public HTTP server, so it must not
        # embed a localhost unsubscribe URL that cannot work when the Mac is off.
        repo = os.getenv("GITHUB_REPOSITORY", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise RuntimeError("Personal email mode requires GITHUB_REPOSITORY.")
        footer = ("\n\nTo stop these personal notifications, disable the "
                  f"Fantasy Coach Emails workflow in https://github.com/{repo}/actions.\n")
        send_email(subscriber["email"], subject, content + footer, transport=transport)
    else:
        token = secrets.token_urlsafe(30)
        base = os.getenv("FANTASY_PUBLIC_API_URL", "http://127.0.0.1:8000").rstrip("/")
        footer = f"\n\nUnsubscribe: {base}/api/subscriptions/unsubscribe?token={quote(token)}\n"
        send_email(subscriber["email"], subject, content + footer, transport=transport)
        with _db() as db:
            db.execute("INSERT OR IGNORE INTO unsubscribe_tokens(token_hash,email) VALUES(?,?)",
                       (_token_hash(token),subscriber["email"]))
    mark_sent(subscriber["email"],kind,event_key)
    return True
