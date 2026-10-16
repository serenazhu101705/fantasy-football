from pathlib import Path
import json
import os
import re
import time
import unicodedata
import hashlib

import requests
from dotenv import load_dotenv

from fantasypros_client import (
    fantasypros_is_blocked,
    mark_fantasypros_blocked,
)


# ==================================================
# Configuration
# ==================================================

load_dotenv()

API_KEY = os.getenv(
    "FANTASYPROS_API_KEY"
)

BASE_URL = (
    "https://api.fantasypros.com/"
    "public/v2/json"
)

CACHE_DIR = Path(
    "data/cache/fantasypros/injuries"
)

# Injury/practice information changes quickly.
CACHE_TTL_SECONDS = 15 * 60


# ==================================================
# Name normalization
# ==================================================

def normalize_name(
    value,
):
    """
    Normalize player names for fallback matching.
    """

    if value is None:
        return ""

    name = str(
        value
    ).strip()

    name = unicodedata.normalize(
        "NFKD",
        name,
    )

    name = "".join(
        character
        for character in name
        if not unicodedata.combining(
            character
        )
    )

    name = name.lower()

    name = re.sub(
        r"[^a-z0-9\s]",
        "",
        name,
    )

    name = re.sub(
        r"\b(jr|sr|ii|iii|iv|v)\b",
        "",
        name,
    )

    name = re.sub(
        r"\s+",
        " ",
        name,
    ).strip()

    return name


# ==================================================
# Cache helpers
# ==================================================

def cache_is_fresh(
    path,
):
    if not path.exists():
        return False

    age = (
        time.time()
        - path.stat().st_mtime
    )

    return (
        age
        < CACHE_TTL_SECONDS
    )


def load_json_cache(
    path,
):
    with path.open(
        "r",
        encoding="utf-8",
    ) as file:

        return json.load(
            file
        )


def save_json_cache(
    path,
    data,
):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            data,
            file,
            indent=2,
        )


# ==================================================
# FantasyPros request helper
# ==================================================

def fantasypros_get(
    endpoint,
    params=None,
):
    """
    Make a FantasyPros request while respecting the
    shared per-run 429 circuit breaker.
    """

    if not API_KEY:

        raise RuntimeError(
            "FANTASYPROS_API_KEY is not set."
        )

    # --------------------------------------------------
    # Circuit breaker
    # --------------------------------------------------

    if fantasypros_is_blocked():

        raise requests.RequestException(
            "FantasyPros request skipped because an "
            "earlier request returned HTTP 429 "
            "during this run."
        )

    url = (
        f"{BASE_URL}/{endpoint}"
    )

    response = requests.get(
        url,
        headers={
            "x-api-key":
                API_KEY,
        },
        params=params or {},
        timeout=30,
    )

    # --------------------------------------------------
    # Open shared circuit on 429
    # --------------------------------------------------

    if response.status_code == 429:

        mark_fantasypros_blocked(
            reason=(
                "FantasyPros injury/news endpoint "
                "returned HTTP 429."
            )
        )

        print(
            "[FANTASYPROS CIRCUIT OPEN] "
            "Skipping additional FantasyPros API "
            "requests for the rest of this run."
        )

    response.raise_for_status()

    return response.json()

# ==================================================
# Injury feed
# ==================================================
def _last_good_cache_path(
    season,
    week,
):
    """
    Shared per-week injury cache.

    Unlike the normal cache, this is NOT tied to one
    exact set of requested player IDs.
    """

    return (
        CACHE_DIR
        / (
            f"injuries_{season}_"
            f"week{week}_"
            f"last_good.json"
        )
    )


def _merge_injury_payloads(
    *payloads,
):
    """
    Merge injury responses by FantasyPros player ID.

    Later payloads overwrite earlier payloads so the
    newest known report wins.
    """

    merged = {}

    for payload in payloads:

        if not payload:
            continue

        injuries = (
            payload.get(
                "injuries"
            )
            or []
        )

        for injury in injuries:

            player_id = injury.get(
                "player_id"
            )

            if player_id is not None:

                key = (
                    "id:"
                    + str(
                        player_id
                    )
                )

            else:

                # Defensive fallback in case FantasyPros
                # ever returns an injury without an ID.
                name = normalize_name(
                    injury.get(
                        "name"
                    )
                )

                if not name:
                    continue

                key = (
                    "name:"
                    + name
                )

            merged[
                key
            ] = injury

    return {
        "count":
            len(
                merged
            ),

        "injuries":
            list(
                merged.values()
            ),
    }


def _update_last_good_cache(
    season,
    week,
    new_data,
):
    """
    Merge newly received injury data into the shared
    weekly last-good cache.
    """

    path = _last_good_cache_path(
        season,
        week,
    )

    existing = {}

    if path.exists():

        try:

            existing = load_json_cache(
                path
            )

        except Exception:

            existing = {}

    merged = _merge_injury_payloads(
        existing,
        new_data,
    )

    if merged.get(
        "injuries"
    ):

        save_json_cache(
            path,
            merged,
        )

    return merged


def _bootstrap_last_good_cache(
    season,
    week,
):
    """
    Build/rebuild the shared weekly injury cache from
    every injury cache file we already have on disk.

    This lets the new cache immediately benefit from
    older exact-player-set caches.
    """

    last_good_path = (
        _last_good_cache_path(
            season,
            week,
        )
    )

    payloads = []

    # --------------------------------------------------
    # Existing shared cache first
    # --------------------------------------------------

    if last_good_path.exists():

        try:

            payloads.append(
                load_json_cache(
                    last_good_path
                )
            )

        except Exception:

            pass

    # --------------------------------------------------
    # Existing exact/request caches
    #
    # Sort by modification time so newer information
    # overwrites older information.
    # --------------------------------------------------

    cache_files = list(
        CACHE_DIR.glob(
            f"injuries_{season}_"
            f"week{week}*.json"
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
                load_json_cache(
                    path
                )
            )

        except Exception:

            continue

    merged = _merge_injury_payloads(
        *payloads
    )

    if merged.get(
        "injuries"
    ):

        save_json_cache(
            last_good_path,
            merged,
        )

    return merged


def _filter_injury_data(
    data,
    player_ids,
):
    """
    Return only injury rows for the requested players.
    """

    if not player_ids:
        return data

    requested = {
        str(
            player_id
        )
        for player_id in player_ids
    }

    injuries = []

    for injury in (
        data.get(
            "injuries"
        )
        or []
    ):

        player_id = injury.get(
            "player_id"
        )

        if player_id is None:
            continue

        if str(
            player_id
        ) in requested:

            injuries.append(
                injury
            )

    return {
        "count":
            len(
                injuries
            ),

        "injuries":
            injuries,
    }

def get_injuries(
    season,
    week,
    player_ids=None,
    force_refresh=False,
):
    """
    Get structured FantasyPros injury data.

    Cache strategy:

        1. Use fresh exact-player cache when available.

        2. Otherwise try FantasyPros.

        3. Save successful responses both to:
               - exact request cache
               - shared weekly last-good cache

        4. If FantasyPros fails:
               - prefer stale exact cache
               - otherwise use any matching players from
                 the shared weekly last-good cache
               - otherwise return an empty injury list so
                 ESPN injury status can still be used
    """

    clean_player_ids = []

    if player_ids:

        clean_player_ids = sorted(
            {
                str(
                    player_id
                )
                for player_id in player_ids
                if player_id is not None
            }
        )

    # --------------------------------------------------
    # Exact-request cache path
    # --------------------------------------------------

    if clean_player_ids:

        player_id_string = ":".join(
            clean_player_ids
        )

        cache_hash = (
            hashlib.sha1(
                player_id_string.encode(
                    "utf-8"
                )
            )
            .hexdigest()[:12]
        )

        cache_path = (
            CACHE_DIR
            / (
                f"injuries_{season}_"
                f"week{week}_"
                f"players_{cache_hash}.json"
            )
        )

    else:

        cache_path = (
            CACHE_DIR
            / (
                f"injuries_{season}_"
                f"week{week}.json"
            )
        )

    # --------------------------------------------------
    # Fresh exact cache
    # --------------------------------------------------

    if (
        not force_refresh
        and cache_is_fresh(
            cache_path
        )
    ):

        print(
            f"[CACHE] FantasyPros injuries "
            f"{season} Week {week}"
        )

        data = load_json_cache(
            cache_path
        )

        # Also make sure the shared cache learns about
        # anything contained in this exact cache.
        _update_last_good_cache(
            season=season,
            week=week,
            new_data=data,
        )

        return data

    # --------------------------------------------------
    # API parameters
    # --------------------------------------------------

    params = {
        "year":
            season,

        "week":
            week,

        "include_probabilities":
            "true",
    }

    if clean_player_ids:

        params[
            "player_ids"
        ] = ":".join(
            clean_player_ids
        )

    # --------------------------------------------------
    # Try FantasyPros
    # --------------------------------------------------

    try:

        print(
            f"[API] FantasyPros injuries "
            f"{season} Week {week}"
        )

        if clean_player_ids:

            print(
                f"      Requesting "
                f"{len(clean_player_ids)} "
                f"players"
            )

        data = fantasypros_get(
            "nfl/injuries",
            params=params,
        )

        injuries = (
            data.get(
                "injuries"
            )
            or []
        )

        print(
            f"      API count="
            f"{data.get('count')} "
            f"returned="
            f"{len(injuries)}"
        )

        # ----------------------------------------------
        # Save exact request cache
        # ----------------------------------------------

        save_json_cache(
            cache_path,
            data,
        )

        # ----------------------------------------------
        # Merge into shared weekly cache
        # ----------------------------------------------

        _update_last_good_cache(
            season=season,
            week=week,
            new_data=data,
        )

        return data

    # --------------------------------------------------
    # FantasyPros failed
    # --------------------------------------------------

    except requests.RequestException as error:

        # ----------------------------------------------
        # Best fallback:
        # stale cache for the exact same request
        # ----------------------------------------------

        if cache_path.exists():

            age_seconds = (
                time.time()
                - cache_path.stat().st_mtime
            )

            age_minutes = (
                age_seconds
                / 60.0
            )

            print(
                f"[STALE CACHE] FantasyPros injuries "
                f"{season} Week {week} "
                f"({age_minutes:.0f} min old) "
                f"because API failed: "
                f"{error}"
            )

            data = load_json_cache(
                cache_path
            )

            _update_last_good_cache(
                season=season,
                week=week,
                new_data=data,
            )

            return data

        # ----------------------------------------------
        # No exact cache.
        #
        # Build a shared cache from every injury response
        # we have ever successfully cached this week.
        # ----------------------------------------------

        shared_data = (
            _bootstrap_last_good_cache(
                season=season,
                week=week,
            )
        )

        filtered_data = (
            _filter_injury_data(
                data=shared_data,
                player_ids=clean_player_ids,
            )
        )

        matched_count = len(
            filtered_data.get(
                "injuries",
                [],
            )
        )

        if matched_count > 0:

            print(
                f"[LAST-GOOD CACHE] "
                f"FantasyPros injuries "
                f"{season} Week {week}: "
                f"recovered {matched_count}/"
                f"{len(clean_player_ids)} "
                f"requested players after API failure."
            )

            return filtered_data

        # ----------------------------------------------
        # No FantasyPros injury data is available for
        # these players.
        #
        # Return an empty feed instead of crashing the
        # entire enrichment pipeline. availability.py
        # can then fall back to ESPN status.
        # ----------------------------------------------

        print(
            f"[INJURY FALLBACK] "
            f"No cached FantasyPros injury rows "
            f"matched this request after API failure. "
            f"Using ESPN injury status instead."
        )

        return {
            "count":
                0,

            "injuries":
                [],
        }

def get_injury_news(
    limit=100,
    force_refresh=False,
):
    """
    Get recent FantasyPros NFL injury news.

    Fresh cache:
        use immediately

    Cache expired:
        try FantasyPros API

    API failure / 429:
        fall back to stale cached news when available
    """

    cache_path = (
        CACHE_DIR
        / "injury_news.json"
    )

    # --------------------------------------------------
    # Fresh cache
    # --------------------------------------------------

    if (
        not force_refresh
        and cache_is_fresh(
            cache_path
        )
    ):

        print(
            "[CACHE] FantasyPros injury news"
        )

        return load_json_cache(
            cache_path
        )

    # --------------------------------------------------
    # Try fresh API data
    # --------------------------------------------------

    try:

        print(
            "[API] FantasyPros injury news"
        )

        data = fantasypros_get(
            "nfl/news",
            params={
                "limit":
                    limit,

                "category":
                    "injury",

                "order_by":
                    "created",
            },
        )

        save_json_cache(
            cache_path,
            data,
        )

        return data

    # --------------------------------------------------
    # API failed
    # --------------------------------------------------

    except requests.RequestException as error:

        if cache_path.exists():

            age_seconds = (
                time.time()
                - cache_path.stat().st_mtime
            )

            age_minutes = (
                age_seconds
                / 60.0
            )

            print(
                "[STALE CACHE] FantasyPros injury news "
                f"({age_minutes:.0f} min old) "
                f"because API failed: "
                f"{error}"
            )

            return load_json_cache(
                cache_path
            )

        raise

# ==================================================
# Injury normalization
# ==================================================

def normalize_practice_status(
    value,
):
    """
    Convert various practice-report strings to a small
    consistent vocabulary.
    """

    if value is None:
        return None

    text = str(
        value
    ).strip().lower()

    if not text:
        return None

    if (
        "did not" in text
        or text in (
            "dnp",
            "none",
            "out",
        )
    ):
        return "DNP"

    if (
        "limit" in text
        or text == "lp"
    ):
        return "LIMITED"

    if (
        "full" in text
        or text == "fp"
    ):
        return "FULL"

    return str(
        value
    ).upper()


def parse_probability(
    value,
):
    if value is None:
        return None

    try:
        probability = float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return None

    # Defensive handling in case a source ever gives
    # percentages instead of 0-1 probabilities.
    if probability > 1:

        probability = (
            probability
            / 100.0
        )

    return max(
        0.0,
        min(
            1.0,
            probability,
        ),
    )


def normalize_injury(
    raw,
):
    """
    Convert a FantasyPros injury object into the
    internal structure our decision system will use.
    """

    return {
        "fp_id":
            raw.get(
                "player_id"
            ),

        "name":
            raw.get(
                "name"
            ),

        "normalized_name":
            normalize_name(
                raw.get(
                    "name"
                )
            ),

        "injury_type":
            raw.get(
                "injury_type"
            ),

        "practice_injury_type":
            raw.get(
                "practice_report_injury_type"
            ),

        "status":
            raw.get(
                "status"
            ),

        "status_short":
            raw.get(
                "status_short"
            ),

        "comment":
            raw.get(
                "comment"
            ),

        "injury_update_date":
            raw.get(
                "injury_update_date"
            ),

        "probability_of_playing":
            parse_probability(
                raw.get(
                    "probability_of_playing"
                )
            ),

        "practice_1":
            normalize_practice_status(
                raw.get(
                    "practice_1"
                )
            ),

        "practice_2":
            normalize_practice_status(
                raw.get(
                    "practice_2"
                )
            ),

        "practice_3":
            normalize_practice_status(
                raw.get(
                    "practice_3"
                )
            ),

        "practice_1_submitted":
            bool(
                raw.get(
                    "team_practice_1_submitted",
                    False,
                )
            ),

        "practice_2_submitted":
            bool(
                raw.get(
                    "team_practice_2_submitted",
                    False,
                )
            ),

        "practice_3_submitted":
            bool(
                raw.get(
                    "team_practice_3_submitted",
                    False,
                )
            ),
    }


# ==================================================
# Injury lookup
# ==================================================

def build_injury_lookup(
    season,
    week,
    player_ids=None,
    force_refresh=False,
):
    """
    Build injury lookups by FantasyPros ID and
    normalized player name.

    Request only the players we actually care about
    whenever FantasyPros IDs are available.
    """

    data = get_injuries(
        season=season,
        week=week,
        player_ids=player_ids,
        force_refresh=force_refresh,
    )

    injuries = (
        data.get(
            "injuries"
        )
        or []
    )

    by_fp_id = {}
    by_name = {}

    for raw in injuries:

        injury = normalize_injury(
            raw
        )

        fp_id = injury[
            "fp_id"
        ]

        normalized_name = injury[
            "normalized_name"
        ]

        if fp_id is not None:

            by_fp_id[
                str(
                    fp_id
                )
            ] = injury

        if normalized_name:

            by_name[
                normalized_name
            ] = injury

    return {
        "by_fp_id":
            by_fp_id,

        "by_name":
            by_name,
    }

# ==================================================
# News lookup
# ==================================================

def build_news_lookup(
    force_refresh=False,
):
    """
    Build recent injury-news lookup by FantasyPros
    player ID.

    We keep several items per player because one news
    item alone can be misleading.
    """

    data = get_injury_news(
        force_refresh=force_refresh
    )

    items = (
        data.get(
            "items"
        )
        or []
    )

    by_fp_id = {}

    for item in items:

        player_id = item.get(
            "player_id"
        )

        if player_id is None:
            continue

        key = str(
            player_id
        )

        if key not in by_fp_id:

            by_fp_id[
                key
            ] = []

        by_fp_id[
            key
        ].append(
            {
                "created":
                    item.get(
                        "created"
                    ),

                "title":
                    item.get(
                        "title"
                    ),

                "description":
                    item.get(
                        "desc"
                    ),

                "impact":
                    item.get(
                        "impact"
                    ),

                "link":
                    item.get(
                        "link"
                    ),
            }
        )

    return by_fp_id


# ==================================================
# Player lookup helper
# ==================================================

def get_player_fp_id(
    player,
):
    """
    Try common keys used elsewhere in this project.

    This keeps injury_monitor.py tolerant of small
    naming differences in player_enrichment.py.
    """

    for key in (
        "fantasypros_id",
        "fp_id",
        "fpid",
    ):

        value = player.get(
            key
        )

        if value is not None:

            return str(
                value
            )

    return None


# ==================================================
# Attach raw injury signals
# ==================================================

def attach_injury_signals(
    players,
    season,
    week,
    include_news=True,
    force_refresh=False,
):
    """
    Attach structured injury/practice information to
    roster or waiver players.
    """

    # --------------------------------------------------
    # Collect FantasyPros IDs first.
    # --------------------------------------------------

    roster_fp_ids = []

    missing_fp_ids = []

    for player in players:

        fp_id = get_player_fp_id(
            player
        )

        if fp_id is not None:

            roster_fp_ids.append(
                fp_id
            )

        else:

            missing_fp_ids.append(
                player.get(
                    "name"
                )
            )

    print(
        f"[INJURY] FantasyPros IDs available: "
        f"{len(roster_fp_ids)}/"
        f"{len(players)}"
    )

    if missing_fp_ids:

        print(
            "[INJURY] Missing FantasyPros IDs:"
        )

        for name in missing_fp_ids:

            print(
                f"  - {name}"
            )

    # --------------------------------------------------
    # Request injury reports specifically for the
    # roster players.
    # --------------------------------------------------

    injury_lookup = (
        build_injury_lookup(
            season=season,
            week=week,
            player_ids=roster_fp_ids,
            force_refresh=force_refresh,
        )
    )

    if include_news:

        news_lookup = (
            build_news_lookup(
                force_refresh=force_refresh
            )
        )

    else:

        news_lookup = {}

    enriched = []

    for original in players:

        player = dict(
            original
        )

        fp_id = get_player_fp_id(
            player
        )

        normalized_name = (
            normalize_name(
                player.get(
                    "name"
                )
            )
        )

        injury = None

        # Prefer exact FantasyPros ID.
        if fp_id:

            injury = (
                injury_lookup[
                    "by_fp_id"
                ].get(
                    str(
                        fp_id
                    )
                )
            )

        # Fallback to normalized name.
        if injury is None:

            injury = (
                injury_lookup[
                    "by_name"
                ].get(
                    normalized_name
                )
            )

        player[
            "injury_report"
        ] = injury

        if fp_id:

            player[
                "injury_news"
            ] = (
                news_lookup.get(
                    str(
                        fp_id
                    ),
                    [],
                )
            )

        else:

            player[
                "injury_news"
            ] = []

        enriched.append(
            player
        )

    return enriched

# ==================================================
# Printing / diagnostics
# ==================================================

def print_injury_report(
    players,
):
    """
    Human-readable inspection of current injury
    information.
    """

    print(
        "\n"
        + "=" * 80
    )

    print(
        "CURRENT INJURY / PRACTICE REPORT"
    )

    print(
        "=" * 80
    )

    found = False

    for player in players:

        injury = player.get(
            "injury_report"
        )

        if injury is None:
            continue

        found = True

        print(
            f"\n{player.get('name')} "
            f"({player.get('position')})"
        )

        print(
            f"  Status: "
            f"{injury.get('status')}"
        )

        print(
            f"  Injury: "
            f"{injury.get('injury_type')}"
        )

        print(
            f"  Practice injury: "
            f"{injury.get('practice_injury_type')}"
        )

        probability = (
            injury.get(
                "probability_of_playing"
            )
        )

        if probability is None:

            probability_text = "N/A"

        else:

            probability_text = (
                f"{probability:.1%}"
            )

        print(
            f"  Probability of playing: "
            f"{probability_text}"
        )

        print(
            "  Practice:"
        )

        print(
            f"    P1: "
            f"{injury.get('practice_1')} "
            f"(submitted="
            f"{injury.get('practice_1_submitted')})"
        )

        print(
            f"    P2: "
            f"{injury.get('practice_2')} "
            f"(submitted="
            f"{injury.get('practice_2_submitted')})"
        )

        print(
            f"    P3: "
            f"{injury.get('practice_3')} "
            f"(submitted="
            f"{injury.get('practice_3_submitted')})"
        )

        comment = injury.get(
            "comment"
        )

        if comment:

            print(
                f"  Comment: "
                f"{comment}"
            )

        news_items = player.get(
            "injury_news"
        ) or []

        if news_items:

            print(
                "  Recent injury news:"
            )

            for item in news_items[:3]:

                print(
                    f"    - "
                    f"{item.get('created')}: "
                    f"{item.get('title')}"
                )

    if not found:

        print(
            "No roster players found in the "
            "current FantasyPros injury feed."
        )


# ==================================================
# Standalone test
# ==================================================

def main():
    """
    Test against the user's current ESPN roster.
    """

    # Local imports avoid circular imports when this
    # module is later used by player_enrichment.py.
    from espn_client import (
        get_league,
        get_my_team,
        get_roster,
    )

    league = get_league()

    week = league.current_week

    team = get_my_team(
        league
    )

    roster = get_roster(
        team,
        league=league,
        week=week,
    )

    # --------------------------------------------------
    # We need FantasyPros IDs for the cleanest match.
    # player_enrichment already performs that mapping.
    # --------------------------------------------------

    try:

        from player_ids import (
            get_espn_to_fantasypros_map,
        )

        id_map = (
            get_espn_to_fantasypros_map()
        )

        for player in roster:

            espn_id = player.get(
                "espn_id"
            )

            if espn_id is None:
                continue

            fp_id = id_map.get(
                str(
                    espn_id
                )
            )

            if fp_id is None:

                fp_id = id_map.get(
                    espn_id
                )

            if fp_id is not None:

                player[
                    "fantasypros_id"
                ] = fp_id

    except Exception as error:

        print(
            f"[WARN] Could not attach "
            f"FantasyPros IDs: {error}"
        )

        print(
            "Falling back to player-name matching."
        )

    roster = attach_injury_signals(
        players=roster,
        season=2026,
        week=week,
        include_news=True,
    )

    print(
        f"\nLeague: "
        f"{league.settings.name}"
    )

    print(
        f"Week: "
        f"{week}"
    )

    print_injury_report(
        roster
    )


if __name__ == "__main__":
    main()