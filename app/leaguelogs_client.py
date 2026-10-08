import json
import re
import time
import unicodedata
from io import StringIO
from pathlib import Path

import pandas as pd
import requests


# --------------------------------------------------
# LeagueLogs public sources
# --------------------------------------------------

MARKET_URL = (
    "https://leaguelogs.com/"
    "rankings/redraft/ppr"
)

ROS_URL = (
    "https://leaguelogs.com/"
    "rankings/ros"
)


# LeagueLogs says its Market Index refreshes every
# six hours, so there is no reason for us to hit the
# page more frequently than that.
CACHE_TTL_SECONDS = 6 * 60 * 60

CACHE_DIR = Path(
    "data/cache/leaguelogs"
)

CACHE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# --------------------------------------------------
# Name normalization
# --------------------------------------------------

def normalize_player_name(name):
    """
    Normalize player names so ESPN / FantasyPros /
    LeagueLogs naming differences are less likely
    to break matching.

    Examples:

        Kenneth Walker III -> kenneth walker
        Chris Godwin Jr.   -> chris godwin
        A.J. Brown         -> aj brown
        Patrick Mahomes II -> patrick mahomes
    """

    if not name:
        return ""

    name = unicodedata.normalize(
        "NFKD",
        str(name),
    )

    name = (
        name
        .encode(
            "ascii",
            "ignore",
        )
        .decode("ascii")
    )

    name = name.lower()

    # Remove punctuation.
    name = re.sub(
        r"[^a-z0-9\s]",
        "",
        name,
    )

    # Remove common suffixes.
    name = re.sub(
        r"\b("
        r"jr|sr|ii|iii|iv"
        r")\b$",
        "",
        name,
    )

    name = re.sub(
        r"\s+",
        " ",
        name,
    )

    return name.strip()


# --------------------------------------------------
# Cache helpers
# --------------------------------------------------

def _cache_path(name):
    return (
        CACHE_DIR
        / f"{name}.json"
    )


def _load_cache(
    name,
    allow_stale=False,
):
    path = _cache_path(name)

    if not path.exists():
        return None

    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as file:
            payload = json.load(file)

    except Exception:
        return None

    fetched_at = payload.get(
        "fetched_at",
        0,
    )

    age = (
        time.time()
        - fetched_at
    )

    if (
        not allow_stale
        and age > CACHE_TTL_SECONDS
    ):
        return None

    return payload.get(
        "rows",
        [],
    )


def _save_cache(
    name,
    rows,
):
    path = _cache_path(name)

    payload = {
        "fetched_at": (
            time.time()
        ),
        "rows": rows,
    }

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            payload,
            file,
            indent=2,
        )


# --------------------------------------------------
# Parsing helpers
# --------------------------------------------------

def _is_missing(value):
    try:
        return bool(
            pd.isna(value)
        )

    except Exception:
        return value is None


def _parse_int(value):
    if _is_missing(value):
        return None

    text = str(value)

    text = (
        text
        .replace(",", "")
        .strip()
    )

    if text in (
        "",
        "—",
        "-",
        "nan",
    ):
        return None

    try:
        return int(
            float(text)
        )

    except ValueError:
        return None


def _parse_float(value):
    if _is_missing(value):
        return None

    text = str(value)

    text = (
        text
        .replace(",", "")
        .replace("±", "")
        .strip()
    )

    if text in (
        "",
        "—",
        "-",
        "nan",
    ):
        return None

    try:
        return float(text)

    except ValueError:
        return None


def _parse_percent(value):
    if _is_missing(value):
        return None

    text = (
        str(value)
        .replace("%", "")
        .strip()
    )

    number = _parse_float(
        text
    )

    if number is None:
        return None

    return number


def _parse_movement(value):
    """
    Convert values such as:

        +12
        -4
        —

    into integers / None.
    """

    if _is_missing(value):
        return None

    text = str(value).strip()

    if text in (
        "",
        "—",
        "-",
        "nan",
    ):
        return None

    text = text.replace(
        "+",
        "",
    )

    try:
        return int(
            float(text)
        )

    except ValueError:
        return None


def _clean_market_player_name(
    value,
):
    """
    Clean LeagueLogs Market Index player names.

    Handles formats such as:

        Jahmyr Gibbs RB1
        Jahmyr GibbsRB1
        Joe Burrow QB5 Contested
        Nico Collins WR9🔥
    """

    name = str(value)

    # Remove labels / icons.
    name = re.sub(
        r"contested",
        "",
        name,
        flags=re.IGNORECASE,
    )

    name = name.replace(
        "🔥",
        "",
    )

    # Remove positional rank appended to name.
    #
    # Important: \s* instead of \s+ because pandas
    # may return "Jahmyr GibbsRB1".
    name = re.sub(
        r"\s*(QB|RB|WR|TE)\s*\d+\s*$",
        "",
        name,
        flags=re.IGNORECASE,
    )

    return name.strip()

def _clean_ros_player_name(
    value,
):
    """
    Clean player names from the LeagueLogs ROS table.
    """

    name = str(value)

    name = re.sub(
        r"\s*start/sit\s*→?\s*",
        "",
        name,
        flags=re.IGNORECASE,
    )

    return name.strip()

# --------------------------------------------------
# Generic table fetcher
# --------------------------------------------------

def _fetch_table(
    url,
    cache_name,
    required_columns,
):
    """
    Load fresh cache first.

    If cache is stale:
        try LeagueLogs.

    If LeagueLogs fails:
        use stale cache.

    This is the behavior we eventually want for
    FantasyPros too.
    """

    cached = _load_cache(
        cache_name
    )

    if cached is not None:
        print(
            f"[CACHE] LeagueLogs "
            f"{cache_name}"
        )

        return cached

    print(
        f"[WEB] LeagueLogs "
        f"{cache_name}"
    )

    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": (
                    "fantasy-football-"
                    "research-project/1.0"
                )
            },
            timeout=20,
        )

        response.raise_for_status()

        tables = pd.read_html(
            StringIO(
                response.text
            )
        )

        matching_table = None

        for table in tables:

            columns = {
                str(column).strip()
                for column
                in table.columns
            }

            if required_columns.issubset(
                columns
            ):
                matching_table = table
                break

        if matching_table is None:
            raise ValueError(
                "Could not find the expected "
                "LeagueLogs rankings table."
            )

        rows = (
            matching_table
            .where(
                pd.notnull(
                    matching_table
                ),
                None,
            )
            .to_dict(
                orient="records"
            )
        )

        _save_cache(
            cache_name,
            rows,
        )

        return rows

    except Exception as error:

        stale = _load_cache(
            cache_name,
            allow_stale=True,
        )

        if stale is not None:

            print(
                "[STALE CACHE] "
                "LeagueLogs request failed; "
                "using old data."
            )

            return stale

        raise RuntimeError(
            "Could not load LeagueLogs "
            f"data: {error}"
        ) from error


# --------------------------------------------------
# Market Index
# --------------------------------------------------

def get_market_index():
    """
    Return LeagueLogs PPR redraft Market Index.

    Each player gets:

        market_rank
        market_pos_rank
        market_value
        market_move_7d
        market_move_30d
    """

    raw_rows = _fetch_table(
        url=MARKET_URL,
        cache_name="market_ppr",
        required_columns={
            "#",
            "Player",
            "Pos",
            "Team",
            "Value",
            "7d",
            "30d",
        },
    )

    players = []

    position_counts = {}

    for row in raw_rows:

        position = str(
            row.get("Pos", "")
        ).strip()

        if position not in (
            "QB",
            "RB",
            "WR",
            "TE",
        ):
            continue

        name = _clean_market_player_name(
            row.get(
                "Player",
                "",
            )
        )

        overall_rank = _parse_int(
            row.get("#")
        )

        market_value = _parse_int(
            row.get("Value")
        )

        if not name:
            continue

        position_counts[
            position
        ] = (
            position_counts.get(
                position,
                0,
            )
            + 1
        )

        players.append({
            "name": name,
            "normalized_name":
                normalize_player_name(
                    name
                ),
            "position": position,
            "team": (
                str(
                    row.get(
                        "Team",
                        "",
                    )
                )
                .strip()
            ),
            "market_rank":
                overall_rank,
            "market_pos_rank":
                position_counts[
                    position
                ],
            "market_value":
                market_value,
            "market_move_7d":
                _parse_movement(
                    row.get("7d")
                ),
            "market_move_30d":
                _parse_movement(
                    row.get("30d")
                ),
        })

    return players


# --------------------------------------------------
# Rest-of-season rankings
# --------------------------------------------------

def get_ros_rankings():
    """
    Return LeagueLogs PPR ROS consensus data.

    Each player gets:

        ros_overall_rank
        ros_pos_rank
        ros_consensus
        ros_best
        ros_worst
        ros_split
        rostered_pct
    """

    raw_rows = _fetch_table(
        url=ROS_URL,
        cache_name="ros_ppr",
        required_columns={
            "#",
            "Player",
            "Pos",
            "Team",
            "Consensus",
            "Best",
            "Worst",
        },
    )

    players = []

    position_counts = {}

    for row in raw_rows:

        position = str(
            row.get("Pos", "")
        ).strip()

        if position not in (
            "QB",
            "RB",
            "WR",
            "TE",
        ):
            continue

        name = _clean_ros_player_name(
            row.get(
                "Player",
                "",
            )
        )

        if not name:
            continue

        position_counts[
            position
        ] = (
            position_counts.get(
                position,
                0,
            )
            + 1
        )

        players.append({
            "name": name,
            "normalized_name":
                normalize_player_name(
                    name
                ),
            "position": position,
            "team": (
                str(
                    row.get(
                        "Team",
                        "",
                    )
                )
                .strip()
            ),
            "ros_overall_rank":
                _parse_int(
                    row.get("#")
                ),
            "ros_pos_rank":
                position_counts[
                    position
                ],
            "ros_consensus":
                _parse_float(
                    row.get(
                        "Consensus"
                    )
                ),
            "ros_best":
                _parse_int(
                    row.get(
                        "Best"
                    )
                ),
            "ros_worst":
                _parse_int(
                    row.get(
                        "Worst"
                    )
                ),
            "ros_split":
                _parse_float(
                    row.get(
                        "Split"
                    )
                ),
            "rostered_pct":
                _parse_percent(
                    row.get(
                        "Rostered"
                    )
                ),
        })

    return players


# --------------------------------------------------
# Attach LeagueLogs to our player dictionaries
# --------------------------------------------------

def attach_leaguelogs_signals(
    players,
):
    """
    Add LeagueLogs under:

        player["signals"]["leaguelogs"]

    This keeps provider-specific data organized instead
    of dumping every future field directly onto player.
    """

    market_players = (
        get_market_index()
    )

    ros_players = (
        get_ros_rankings()
    )

    market_lookup = {
        (
            player[
                "normalized_name"
            ],
            player["position"],
        ): player

        for player
        in market_players
    }

    ros_lookup = {
        (
            player[
                "normalized_name"
            ],
            player["position"],
        ): player

        for player
        in ros_players
    }

    for player in players:

        name_key = (
            normalize_player_name(
                player.get(
                    "name"
                )
            )
        )

        position = player.get(
            "position"
        )

        key = (
            name_key,
            position,
        )

        market = market_lookup.get(
            key
        )

        ros = ros_lookup.get(
            key
        )

        player.setdefault(
            "signals",
            {},
        )

        signal = {}

        if market is not None:

            signal.update({
                "market_rank":
                    market.get(
                        "market_rank"
                    ),

                "market_pos_rank":
                    market.get(
                        "market_pos_rank"
                    ),

                "market_value":
                    market.get(
                        "market_value"
                    ),

                "market_move_7d":
                    market.get(
                        "market_move_7d"
                    ),

                "market_move_30d":
                    market.get(
                        "market_move_30d"
                    ),
            })

        if ros is not None:

            signal.update({
                "ros_overall_rank":
                    ros.get(
                        "ros_overall_rank"
                    ),

                "ros_pos_rank":
                    ros.get(
                        "ros_pos_rank"
                    ),

                "ros_consensus":
                    ros.get(
                        "ros_consensus"
                    ),

                "ros_best":
                    ros.get(
                        "ros_best"
                    ),

                "ros_worst":
                    ros.get(
                        "ros_worst"
                    ),

                "ros_split":
                    ros.get(
                        "ros_split"
                    ),

                "rostered_pct":
                    ros.get(
                        "rostered_pct"
                    ),
            })

        signal[
            "matched"
        ] = bool(
            market is not None
            or ros is not None
        )

        player[
            "signals"
        ][
            "leaguelogs"
        ] = signal

    return players