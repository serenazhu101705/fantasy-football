import json
import os
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.fantasypros.com/public/v2/json"
API_KEY = os.getenv("FANTASYPROS_API_KEY")

HEADERS = {
    "x-api-key": API_KEY
}

# --------------------------------------------------
# Cache settings
# --------------------------------------------------

CACHE_DIR = Path("data/cache/fantasypros")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Refresh FantasyPros data every 3 hours
CACHE_TTL = timedelta(hours=3)

# FantasyPros rate limit protection
MIN_REQUEST_INTERVAL = 2.0

_last_request_time = 0.0


# --------------------------------------------------
# Internal helpers
# --------------------------------------------------

def _cache_is_valid(cache_path):
    """
    Returns True if the cache file exists and is younger
    than CACHE_TTL.
    """

    if not cache_path.exists():
        return False

    modified_time = datetime.fromtimestamp(
        cache_path.stat().st_mtime,
        tz=timezone.utc,
    )

    age = datetime.now(timezone.utc) - modified_time

    return age < CACHE_TTL


def _load_cache(cache_path):
    with open(cache_path, "r") as f:
        return json.load(f)


def _save_cache(cache_path, data):
    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)


def _rate_limit():
    """
    Ensures we wait at least MIN_REQUEST_INTERVAL seconds
    between FantasyPros API calls.
    """

    global _last_request_time

    now = time.time()
    elapsed = now - _last_request_time

    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)

    _last_request_time = time.time()


def _request_json(url, params, cache_path, force_refresh=False):
    """
    Generic cached FantasyPros API request.
    """

    # Use cache if valid
    if not force_refresh and _cache_is_valid(cache_path):
        print(f"[CACHE] {cache_path.name}")
        return _load_cache(cache_path)

    # Otherwise make API request
    print(f"[API] {cache_path.name}")

    _rate_limit()

    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=10,
    )

    # Helpful error message for rate limits
    if response.status_code == 429:
        raise RuntimeError(
            "FantasyPros returned HTTP 429 Too Many Requests. "
            "You may have hit the per-second or daily API limit."
        )

    response.raise_for_status()

    data = response.json()

    _save_cache(cache_path, data)

    return data


# --------------------------------------------------
# Projections
# --------------------------------------------------

def get_projections_for_players(
    season,
    week,
    position,
    player_ids,
    force_refresh=False,
):
    if not player_ids:
        return []

    # Make player IDs deterministic so cache filenames
    # are the same regardless of list ordering.
    player_ids = sorted(player_ids)

    ids_string = "-".join(str(pid) for pid in player_ids)

    cache_path = CACHE_DIR / (
        f"projections_"
        f"{season}_"
        f"week{week}_"
        f"{position}_"
        f"{ids_string}.json"
    )

    url = f"{BASE_URL}/nfl/{season}/projections"

    params = {
        "week": week,
        "position": position,
        "players": ":".join(str(pid) for pid in player_ids),
    }

    data = _request_json(
        url=url,
        params=params,
        cache_path=cache_path,
        force_refresh=force_refresh,
    )

    return data["players"]


# --------------------------------------------------
# ECR / Consensus rankings
# --------------------------------------------------

def get_consensus_rankings(
    season,
    week,
    position,
    scoring="PPR",
    force_refresh=False,
):
    cache_path = CACHE_DIR / (
        f"ecr_"
        f"{season}_"
        f"week{week}_"
        f"{position}_"
        f"{scoring}.json"
    )

    url = f"{BASE_URL}/nfl/{season}/consensus-rankings"

    params = {
        "week": week,
        "position": position,
        "scoring": scoring,
        "experts": "show",
    }

    return _request_json(
        url=url,
        params=params,
        cache_path=cache_path,
        force_refresh=force_refresh,
    )

def get_rankings_for_player(
    season,
    week,
    player_id,
    force_refresh=False,
):
    cache_path = CACHE_DIR / (
        f"rankings_{season}_week{week}_player{player_id}.json"
    )

    url = f"{BASE_URL}/nfl/{season}/rankings"

    params = {
        "week": week,
        "player": player_id,
        "range": "true",
        "rankstats": "true",
    }

    return _request_json(
        url=url,
        params=params,
        cache_path=cache_path,
        force_refresh=force_refresh,
    )


# --------------------------------------------------
# Manual testing
# --------------------------------------------------

if __name__ == "__main__":
    data = get_consensus_rankings(
        season=2026,
        week=5,
        position="RB",
        scoring="PPR",
    )

    print("Week:", data.get("week"))
    print("Position:", data.get("position_id"))
    print("Scoring:", data.get("scoring"))
    print("Experts:", data.get("total_experts"))
    print("Count:", data.get("count"))

    print("\nFIRST PLAYER:")
    print(data["players"][0])