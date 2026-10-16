from pathlib import Path
import json
import os
import re
import time
import unicodedata

import requests
from dotenv import load_dotenv


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
    if not API_KEY:

        raise RuntimeError(
            "FANTASYPROS_API_KEY is not set."
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

    response.raise_for_status()

    return response.json()


# ==================================================
# Injury feed
# ==================================================

def get_injuries(
    season,
    week,
    force_refresh=False,
):
    """
    Get the structured FantasyPros NFL injury feed.

    include_probabilities=true is important because
    it can include players who appear on practice
    reports even when they do not yet have a normal
    game-status designation.
    """

    cache_path = (
        CACHE_DIR
        / f"injuries_{season}_week{week}.json"
    )

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

        return load_json_cache(
            cache_path
        )

    print(
        f"[API] FantasyPros injuries "
        f"{season} Week {week}"
    )

    data = fantasypros_get(
        "nfl/injuries",
        params={
            "year":
                season,

            "week":
                week,

            "include_probabilities":
                "true",
        },
    )

    save_json_cache(
        cache_path,
        data,
    )

    return data


# ==================================================
# Injury news
# ==================================================

def get_injury_news(
    limit=100,
    force_refresh=False,
):
    """
    Get recent NFL injury news.

    This is separate from the structured practice
    report because news can contain useful context
    like:

        expected to play
        game-time decision
        setback
        limited snaps expected
        coach says player will play
    """

    cache_path = (
        CACHE_DIR
        / "injury_news.json"
    )

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
    force_refresh=False,
):
    """
    Build injury lookups by FantasyPros ID and
    normalized player name.
    """

    data = get_injuries(
        season=season,
        week=week,
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

    This function DOES NOT yet apply scoring penalties.

    First we want to inspect the real data before
    deciding how practice status should change lineup
    value.
    """

    injury_lookup = (
        build_injury_lookup(
            season=season,
            week=week,
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

        # Prefer FantasyPros ID.
        if fp_id:

            injury = (
                injury_lookup[
                    "by_fp_id"
                ].get(
                    fp_id
                )
            )

        # Name fallback.
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
                    fp_id,
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