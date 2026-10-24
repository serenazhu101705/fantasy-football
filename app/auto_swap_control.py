"""Persistent owner-only Auto Swap switch via GitHub Actions repository variables.

Render free's disk is ephemeral, and scheduled lineup jobs run on GitHub Actions.
This module keeps one durable, nonsecret boolean (FANTASY_AUTO_SWAP_ENABLED)
in the GitHub repository settings. The website never receives a GitHub token.

Live actions require BOTH an opt-in in the Actions worker and a fresh read of
that variable before submitting a roster write. If GitHub is unavailable,
we fail closed rather than guessing.
"""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

VARIABLE = "FANTASY_AUTO_SWAP_ENABLED"
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class AutoSwapControlError(RuntimeError):
    """Safe error that deliberately excludes private request headers/tokens."""


def _repository(value: str) -> str:
    repo = value.strip()
    if not REPO_PATTERN.fullmatch(repo) or ".." in repo:
        raise AutoSwapControlError("Configure FANTASY_AUTOSWAP_GITHUB_REPO as owner/repository.")
    return repo


def _send(method: str, repo: str, token: str, *, body: dict | None = None):
    url = f"https://api.github.com/repos/{_repository(repo)}/actions/variables"
    if method != "POST":
        url += "/" + VARIABLE
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "fantasy-coach-autoswap-control",
    }
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    if payload is not None:
        headers["Content-Type"] = "application/json"
    try:
        req = Request(url, data=payload, headers=headers, method=method)
        with urlopen(req, timeout=8) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {})
    except HTTPError as exc:
        # Error bodies may contain untrusted URLs; never expose them in UI/logs.
        return exc.code, {}
    except (URLError, TimeoutError, OSError, ValueError):
        raise AutoSwapControlError("Cannot reach GitHub's Auto Swap setting right now.") from None


def website_status() -> dict:
    repo, token = (os.getenv("FANTASY_AUTOSWAP_GITHUB_REPO", ""),
                   os.getenv("FANTASY_AUTOSWAP_GITHUB_TOKEN", ""))
    if not repo or not token:
        return {"enabled": False, "configurable": False,
                "note": "Connect Render to your private GitHub repository to enable Auto Swap."}
    code, data = _send("GET", repo, token)
    if code == 404:
        # The variable is not yet created, or repo access is denied. Writes
        # will report an error if the latter. Never claim it is enabled.
        return {"enabled": False, "configurable": True,
                "note": "Auto Swap is off. Turning it on will create the GitHub Actions setting."}
    if code != 200:
        raise AutoSwapControlError("Cannot verify Auto Swap setting on GitHub (check repository/token permissions).")
    enabled = str(data.get("value", "0")) == "1"
    return {"enabled": enabled, "configurable": True,
            "note": "GitHub Actions will check your lineup on its existing schedule."}


def website_set(enabled: bool) -> dict:
    repo, token = (os.getenv("FANTASY_AUTOSWAP_GITHUB_REPO", ""),
                   os.getenv("FANTASY_AUTOSWAP_GITHUB_TOKEN", ""))
    if not repo or not token:
        raise AutoSwapControlError("Auto Swap is not configured on this server.")
    code, _ = _send("PATCH", repo, token,
                    body={"name": VARIABLE, "value": "1" if enabled else "0"})
    if code == 404:
        code, _ = _send("POST", repo, token,
                        body={"name": VARIABLE, "value": "1" if enabled else "0"})
        expected = (201,)
    else:
        expected = (204,)
    if code not in expected:
        raise AutoSwapControlError("GitHub declined to save Auto Swap (check Variables: read/write permissions).")
    # Read back the durable server-side state; never report success based on an
    # optimistic UI change or successful HTTP PATCH alone.
    status = website_status()
    if status["enabled"] != enabled:
        raise AutoSwapControlError("GitHub did not confirm the updated Auto Swap setting.")
    return status


def worker_is_enabled() -> bool:
    """Fail-closed, fresh permission check for the GitHub scheduled job.

    Local development can preview or manually apply lineups, but this remote
    toggle must NEVER grant automatic writes to a local arbitrary process.
    """
    if (os.getenv("FANTASY_PERSONAL_EMAIL_MODE") != "1" or
        os.getenv("FANTASY_REPOSITORY_PRIVATE", "").lower() != "true" or
        os.getenv("FANTASY_AUTO_SWAP_ENABLED") != "1" or
        os.getenv("ESPN_WRITES_ENABLED") != "1" or
        os.getenv("ESPN_AUTO_SWAPS_ENABLED") != "1" or
        not os.getenv("FANTASY_OWNER_EMAIL", "").strip()):
        return False
    repo = os.getenv("GITHUB_REPOSITORY", "")
    token = os.getenv("GITHUB_TOKEN", "")
    if not repo or not token:
        return False
    try:
        code, data = _send("GET", repo, token)
        return code == 200 and str(data.get("value", "")) == "1"
    except AutoSwapControlError:
        return False
