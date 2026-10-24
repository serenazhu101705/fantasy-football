"""Restore latest saved SQLite database from a PRIVATE repository Actions artifact.

Fail closed on API problems or bad archives to prevent duplicate emails caused
by silently starting a fresh notification ledger. Do not expose database bytes
in workflow logs. Never commit the SQLite database to git.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
import zipfile

API = "https://api.github.com"
NAME = "fantasy-coach-email-state"



def github_get(url: str) -> bytes:
    token = os.environ["GH_TOKEN"].strip()
    if not token:
        raise RuntimeError("GH_TOKEN was not configured")

    req = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "fantasy-coach-email-state",
    })

    # Authenticate with GitHub, but don't forward the token
    # when GitHub redirects to the artifact storage server.
    req.add_unredirected_header("Authorization", f"Bearer {token}")

    with urlopen(req, timeout=25) as res:
        return res.read()


def restore(repo: str, destination: Path) -> str:
    if not repo or "/" not in repo:
        raise ValueError("GITHUB_REPOSITORY must have an owner/repo name")
    response = json.loads(github_get(f"{API}/repos/{repo}/actions/artifacts?name={NAME}&per_page=100"))
    items = [x for x in response.get("artifacts", []) if not x.get("expired")]
    if not items:
        destination.parent.mkdir(parents=True, exist_ok=True)
        return "new"
    newest = max(items, key=lambda x: x["created_at"])
    blob = github_get(f"{API}/repos/{repo}/actions/artifacts/{newest['id']}/zip")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        paths = [p for p in archive.namelist() if p == "fantasy_subscriptions.sqlite3" or p.endswith("/fantasy_subscriptions.sqlite3")]
        if len(paths) != 1:
            raise RuntimeError("Stored email state artifact is missing its database")
        data = archive.read(paths[0])
    if not data.startswith(b"SQLite format 3\x00"):
        raise RuntimeError("Stored email state artifact is invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(destination)
    # Preserve cached Claude recaps, avoiding another paid generation when the
    # digest is retried. Only allow known JSON recap filenames under this path.
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        recap_root = Path("data/cloud_home/.cache/fantasy_coach/weekly_recaps")
        for member in archive.namelist():
            rel = "cloud_home/.cache/fantasy_coach/weekly_recaps/"
            if not member.startswith(rel):
                continue
            name = member[len(rel):]
            if not name.startswith("recap_") or not name.endswith(".json") or "/" in name:
                continue
            if archive.getinfo(member).file_size > 200000:
                raise RuntimeError("Stored recap cache is unexpectedly large")
            recap_root.mkdir(parents=True, exist_ok=True)
            (recap_root / name).write_bytes(archive.read(member))
    return "restored"


if __name__ == "__main__":
    path = Path(os.getenv("FANTASY_SUBSCRIPTIONS_DB", "data/fantasy_subscriptions.sqlite3"))
    print("Email state:", restore(os.environ["GITHUB_REPOSITORY"], path))
