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
# Per-run FantasyPros circuit breaker
# ==================================================

_fantasypros_blocked = False

_fantasypros_block_reason = None


def fantasypros_is_blocked():
    """
    Return True when FantasyPros has already returned
    HTTP 429 during this Python process.
    """

    return _fantasypros_blocked


def mark_fantasypros_blocked(
    reason=None,
):
    """
    Disable additional FantasyPros network requests for
    the remainder of this run.
    """

    global _fantasypros_blocked
    global _fantasypros_block_reason

    _fantasypros_blocked = True

    _fantasypros_block_reason = (
        reason
        or "FantasyPros returned HTTP 429."
    )


def get_fantasypros_block_reason():
    return _fantasypros_block_reason

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

    # --------------------------------------------------
    # Circuit breaker
    #
    # A previous FantasyPros request in this same run
    # already returned HTTP 429.
    #
    # Do not waste another network request.
    # --------------------------------------------------

    if fantasypros_is_blocked():

        if cache_path.exists():

            print(
                f"[CIRCUIT CACHE] "
                f"{cache_path.name}"
            )

            return _load_cache(
                cache_path
            )

        raise RuntimeError(
            "FantasyPros API skipped because an earlier "
            "request returned HTTP 429 during this run."
        )
    
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

            mark_fantasypros_blocked(
                reason=(
                    "FantasyPros returned HTTP 429 "
                    f"for {cache_path.name}"
                )
            )

            print(
                "[FANTASYPROS CIRCUIT OPEN] "
                "Skipping additional FantasyPros API "
                "requests for the rest of this run."
            )

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

def _projection_last_good_cache_path(
    season,
    week,
    position,
):
    """
    Shared projection cache for one position/week.

    Unlike the normal projection cache, this is not tied
    to one exact set of requested player IDs.
    """

    return (
        CACHE_DIR
        / (
            f"projections_"
            f"{season}_"
            f"week{week}_"
            f"{position}_"
            f"last_good.json"
        )
    )


def _get_projection_player_id(
    player,
):
    """
    FantasyPros projection responses have used slightly
    different player-ID field names. Normalize them here.
    """

    value = (
        player.get("fpid")
        or player.get("player_id")
        or player.get("id")
    )

    if value is None:
        return None

    return str(
        value
    )


def _merge_projection_payloads(
    *payloads,
):
    """
    Merge FantasyPros projection responses by player ID.

    Later payloads overwrite earlier ones so newer data
    wins.
    """

    merged = {}

    for payload in payloads:

        if not payload:
            continue

        players = (
            payload.get(
                "players"
            )
            or []
        )

        for player in players:

            player_id = (
                _get_projection_player_id(
                    player
                )
            )

            if player_id is None:
                continue

            merged[
                player_id
            ] = player

    return {
        "players":
            list(
                merged.values()
            )
    }


def _update_projection_last_good_cache(
    season,
    week,
    position,
    new_data,
):
    """
    Merge newly available projection data into the
    shared position-wide last-good cache.
    """

    path = (
        _projection_last_good_cache_path(
            season=season,
            week=week,
            position=position,
        )
    )

    existing = {}

    if path.exists():

        try:

            existing = _load_cache(
                path
            )

        except Exception:

            existing = {}

    merged = (
        _merge_projection_payloads(
            existing,
            new_data,
        )
    )

    if merged.get(
        "players"
    ):

        _save_cache(
            path,
            merged,
        )

    return merged


def _bootstrap_projection_last_good_cache(
    season,
    week,
    position,
):
    """
    Build the position-wide last-good projection cache
    from all projection cache files already stored for
    this season/week/position.

    Older files are loaded first so newer cached values
    overwrite them.
    """

    last_good_path = (
        _projection_last_good_cache_path(
            season=season,
            week=week,
            position=position,
        )
    )

    payloads = []

    # Existing shared cache.
    if last_good_path.exists():

        try:

            payloads.append(
                _load_cache(
                    last_good_path
                )
            )

        except Exception:

            pass

    # Existing exact-player and ALL-position caches.
    cache_files = list(
        CACHE_DIR.glob(
            f"projections_"
            f"{season}_"
            f"week{week}_"
            f"{position}_"
            f"*.json"
        )
    )

    cache_files = [
        path
        for path in cache_files
        if path != last_good_path
    ]

    cache_files.sort(
        key=lambda path:
            path.stat().st_mtime
    )

    for path in cache_files:

        try:

            payloads.append(
                _load_cache(
                    path
                )
            )

        except Exception:

            continue

    merged = (
        _merge_projection_payloads(
            *payloads
        )
    )

    if merged.get(
        "players"
    ):

        _save_cache(
            last_good_path,
            merged,
        )

    return merged


def _filter_projection_data(
    data,
    player_ids,
):
    """
    Return only projection rows belonging to the
    requested FantasyPros IDs.
    """

    requested = {
        str(
            player_id
        )
        for player_id in player_ids
    }

    matched = []

    for player in (
        data.get(
            "players"
        )
        or []
    ):

        player_id = (
            _get_projection_player_id(
                player
            )
        )

        if (
            player_id is not None
            and player_id in requested
        ):

            matched.append(
                player
            )

    return {
        "players":
            matched
    }

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

    Fallback strategy:

        1. Fresh exact-request cache
        2. FantasyPros API
        3. Stale exact-request cache
        4. Shared position-wide last-good cache

    This prevents a slightly different free-agent pool
    from losing all projection data during a 429.
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

    # --------------------------------------------------
    # Normal path
    #
    # _request_json already handles:
    #   fresh exact cache
    #   API request
    #   stale exact cache
    # --------------------------------------------------

    try:

        data = _request_json(
            url=url,
            params=params,
            cache_path=cache_path,
            force_refresh=force_refresh,
        )

        # Whatever we successfully obtained — fresh API
        # or exact stale cache — should contribute to the
        # shared position-wide cache.
        _update_projection_last_good_cache(
            season=season,
            week=week,
            position=position,
            new_data=data,
        )

        return data.get(
            "players",
            [],
        )

    # --------------------------------------------------
    # No exact cache and FantasyPros failed.
    # Recover any matching players from old caches.
    # --------------------------------------------------

    except Exception as error:

        shared_data = (
            _bootstrap_projection_last_good_cache(
                season=season,
                week=week,
                position=position,
            )
        )

        filtered_data = (
            _filter_projection_data(
                data=shared_data,
                player_ids=player_ids,
            )
        )

        recovered = (
            filtered_data.get(
                "players"
            )
            or []
        )

        if recovered:

            print(
                f"[LAST-GOOD PROJECTIONS] "
                f"{position} Week {week}: "
                f"recovered "
                f"{len(recovered)}/"
                f"{len(player_ids)} "
                f"requested players after "
                f"FantasyPros failure."
            )

            return recovered

        print(
            f"[PROJECTION FALLBACK] "
            f"No cached {position} projections "
            f"matched this request after "
            f"FantasyPros failure: {error}"
        )

        return []

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
    ranking_type=None,
    force_refresh=False,
):
    """
    Get FantasyPros consensus rankings for one position.

    ranking_type:
        None  -> normal weekly rankings
        "ROS" -> rest-of-season rankings

    Weekly rankings keep the old cache filename so
    existing cache files continue to work.
    """

    # --------------------------------------------------
    # Normalize ranking type
    # --------------------------------------------------

    normalized_type = None

    if ranking_type is not None:

        normalized_type = str(
            ranking_type
        ).upper()

    # --------------------------------------------------
    # Cache path
    #
    # Preserve existing weekly cache filenames.
    # ROS gets a separate cache so it can never overwrite
    # weekly ECR.
    # --------------------------------------------------

    if normalized_type is None:

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

    else:

        cache_path = (
            CACHE_DIR
            / (
                f"ecr_"
                f"{season}_"
                f"week{week}_"
                f"{position}_"
                f"{scoring}_"
                f"{normalized_type}.json"
            )
        )

    # --------------------------------------------------
    # Endpoint
    # --------------------------------------------------

    url = (
        f"{BASE_URL}/"
        f"nfl/{season}/"
        f"consensus-rankings"
    )

    # --------------------------------------------------
    # Parameters
    # --------------------------------------------------

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

    if normalized_type is not None:

        params[
            "type"
        ] = normalized_type

    # --------------------------------------------------
    # Request
    # --------------------------------------------------

    return _request_json(
        url=url,
        params=params,
        cache_path=cache_path,
        force_refresh=force_refresh,
    )

def get_cached_consensus_rankings(
    season,
    week,
    position,
    scoring="PPR",
    ranking_type=None,
):
    """
    Load an existing consensus-ranking cache without
    making a FantasyPros API request.

    Returns None if that cache does not exist.
    """

    normalized_type = None

    if ranking_type is not None:

        normalized_type = str(
            ranking_type
        ).upper()

    if normalized_type is None:

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

    else:

        cache_path = (
            CACHE_DIR
            / (
                f"ecr_"
                f"{season}_"
                f"week{week}_"
                f"{position}_"
                f"{scoring}_"
                f"{normalized_type}.json"
            )
        )

    if not cache_path.exists():
        return None

    print(
        f"[CACHE-ONLY] "
        f"{cache_path.name}"
    )

    return _load_cache(
        cache_path
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

def get_cached_rankings_for_player(
    season,
    week,
    player_id,
):
    """
    Load an existing individual-player ranking cache
    without ever making a FantasyPros API request.

    Used as a fallback when bulk consensus rankings do
    not contain one of our players or FantasyPros is
    rate-limiting us.
    """

    cache_path = (
        CACHE_DIR
        / (
            f"rankings_"
            f"{season}_"
            f"week{week}_"
            f"player{player_id}.json"
        )
    )

    if not cache_path.exists():
        return None

    print(
        f"[CACHE-ONLY] "
        f"{cache_path.name}"
    )

    return _load_cache(
        cache_path
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