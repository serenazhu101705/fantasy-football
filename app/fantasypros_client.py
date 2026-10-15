import json
import os
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv


load_dotenv()


BASE_URL = "https://api.fantasypros.com/public/v2/json"

API_KEY = os.getenv(
    "FANTASYPROS_API_KEY"
)

HEADERS = {
    "x-api-key": API_KEY
}


# ==================================================
# Cache settings
# ==================================================

CACHE_DIR = Path(
    "data/cache/fantasypros"
)

CACHE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

# Refresh FantasyPros data every 3 hours.
CACHE_TTL = timedelta(
    hours=3
)

# FantasyPros rate-limit protection.
MIN_REQUEST_INTERVAL = 2.0

_last_request_time = 0.0


# ==================================================
# Internal helpers
# ==================================================

def _cache_is_valid(
    cache_path,
):
    """
    Return True if the cache file exists and is younger
    than CACHE_TTL.
    """

    if not cache_path.exists():
        return False

    modified_time = datetime.fromtimestamp(
        cache_path.stat().st_mtime,
        tz=timezone.utc,
    )

    age = (
        datetime.now(
            timezone.utc
        )
        - modified_time
    )

    return (
        age
        < CACHE_TTL
    )


def _load_cache(
    cache_path,
):
    with open(
        cache_path,
        "r",
    ) as f:
        return json.load(
            f
        )


def _save_cache(
    cache_path,
    data,
):
    with open(
        cache_path,
        "w",
    ) as f:
        json.dump(
            data,
            f,
            indent=2,
        )


def _rate_limit():
    """
    Ensure at least MIN_REQUEST_INTERVAL seconds between
    FantasyPros API calls.
    """

    global _last_request_time

    now = time.time()

    elapsed = (
        now
        - _last_request_time
    )

    if (
        elapsed
        < MIN_REQUEST_INTERVAL
    ):
        time.sleep(
            MIN_REQUEST_INTERVAL
            - elapsed
        )

    _last_request_time = (
        time.time()
    )


def _request_json(
    url,
    params,
    cache_path,
    force_refresh=False,
):
    """
    Generic cached FantasyPros API request.
    """

    # --------------------------------------------------
    # Fresh cache
    # --------------------------------------------------

    if (
        not force_refresh
        and _cache_is_valid(
            cache_path
        )
    ):
        print(
            f"[CACHE] "
            f"{cache_path.name}"
        )

        return _load_cache(
            cache_path
        )

    # --------------------------------------------------
    # API request
    # --------------------------------------------------

    print(
        f"[API] "
        f"{cache_path.name}"
    )

    _rate_limit()

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            params=params,
            timeout=10,
        )

        # ----------------------------------------------
        # Rate limit
        # ----------------------------------------------

        if (
            response.status_code
            == 429
        ):

            # If we have stale data, it is much better
            # than throwing away the entire pipeline.
            if cache_path.exists():

                print(
                    f"[STALE CACHE] "
                    f"{cache_path.name} "
                    f"(FantasyPros 429)"
                )

                return _load_cache(
                    cache_path
                )

            raise RuntimeError(
                "FantasyPros returned HTTP 429 "
                "Too Many Requests and no cached "
                "copy is available."
            )

        response.raise_for_status()

        data = response.json()

        _save_cache(
            cache_path,
            data,
        )

        return data

    except Exception:

        # Network/server failure:
        # fall back to stale data if possible.
        if cache_path.exists():

            print(
                f"[STALE CACHE] "
                f"{cache_path.name}"
            )

            return _load_cache(
                cache_path
            )

        raise


# ==================================================
# Projections for known FantasyPros player IDs
# ==================================================

def get_projections_for_players(
    season,
    week,
    position,
    player_ids,
    force_refresh=False,
):
    """
    Get projections for specific FantasyPros player IDs.
    """

    if not player_ids:
        return []

    player_ids = sorted(
        player_ids
    )

    ids_string = "-".join(
        str(pid)
        for pid in player_ids
    )

    cache_path = (
        CACHE_DIR
        / (
            f"projections_"
            f"{season}_"
            f"week{week}_"
            f"{position}_"
            f"{ids_string}.json"
        )
    )

    url = (
        f"{BASE_URL}/"
        f"nfl/{season}/projections"
    )

    params = {
        "week":
            week,

        "position":
            position,

        "players":
            ":".join(
                str(pid)
                for pid in player_ids
            ),
    }

    data = _request_json(
        url=url,
        params=params,
        cache_path=cache_path,
        force_refresh=force_refresh,
    )

    return data.get(
        "players",
        [],
    )


# ==================================================
# Entire-position projections
#
# Used mainly for K and DST where ESPN IDs do not
# reliably map to FantasyPros player IDs.
# ==================================================

def get_position_projections(
    season,
    week,
    position,
    force_refresh=False,
):
    """
    Get every FantasyPros projection for a position.

    Unlike get_projections_for_players(), this does not
    require FantasyPros player IDs.

    This is useful for:

        K
        DST

    because we can match:
        kicker -> player name
        defense -> NFL team
    """

    cache_path = (
        CACHE_DIR
        / (
            f"projections_"
            f"{season}_"
            f"week{week}_"
            f"{position}_ALL.json"
        )
    )

    url = (
        f"{BASE_URL}/"
        f"nfl/{season}/projections"
    )

    params = {
        "week":
            week,

        "position":
            position,
    }

    data = _request_json(
        url=url,
        params=params,
        cache_path=cache_path,
        force_refresh=force_refresh,
    )

    return data.get(
        "players",
        [],
    )


# ==================================================
# ECR / Consensus rankings
# ==================================================

def get_consensus_rankings(
    season,
    week,
    position,
    scoring="PPR",
    force_refresh=False,
):
    cache_path = (
        CACHE_DIR
        / (
            f"ecr_"
            f"{season}_"
            f"week{week}_"
            f"{position}_"
            f"{scoring}.json"
        )
    )

    url = (
        f"{BASE_URL}/"
        f"nfl/{season}/"
        f"consensus-rankings"
    )

    params = {
        "week":
            week,

        "position":
            position,

        "scoring":
            scoring,

        "experts":
            "show",
    }

    return _request_json(
        url=url,
        params=params,
        cache_path=cache_path,
        force_refresh=force_refresh,
    )


# ==================================================
# Individual player rankings
# ==================================================

def get_rankings_for_player(
    season,
    week,
    player_id,
    force_refresh=False,
):
    cache_path = (
        CACHE_DIR
        / (
            f"rankings_"
            f"{season}_"
            f"week{week}_"
            f"player{player_id}.json"
        )
    )

    url = (
        f"{BASE_URL}/"
        f"nfl/{season}/rankings"
    )

    params = {
        "week":
            week,

        "player":
            player_id,

        "range":
            "true",

        "rankstats":
            "true",
    }

    return _request_json(
        url=url,
        params=params,
        cache_path=cache_path,
        force_refresh=force_refresh,
    )


# ==================================================
# Manual testing
# ==================================================

if __name__ == "__main__":

    data = get_position_projections(
        season=2026,
        week=6,
        position="K",
    )

    print(
        f"Kickers returned: "
        f"{len(data)}"
    )

    for player in data[:5]:
        print(
            player
        )