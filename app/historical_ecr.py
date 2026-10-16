from pathlib import Path
import re
import unicodedata

import nflreadpy as nfl
import pandas as pd
import polars as pl


# ==================================================
# Configuration
# ==================================================

SEASONS = [
    2021,
    2022,
    2023,
    2024,
    2025,
    2026,
]

POSITIONS = (
    "QB",
    "RB",
    "WR",
    "TE",
)


RAW_CACHE_PATH = Path(
    "data/cache/nflverse/"
    "fantasypros_rankings_all.parquet"
)

OUTPUT_PATH = Path(
    "data/training/"
    "historical_weekly_ecr.parquet"
)


# ==================================================
# Normalization
# ==================================================

def normalize_name(
    value,
):
    """
    Normalize FantasyPros / nflverse player names
    for later matching.
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

    # Remove common suffixes.
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


def normalize_team(
    value,
):
    """
    Normalize FantasyPros and nflverse team
    abbreviations into one representation.
    """

    if value is None:
        return None

    team = str(
        value
    ).upper().strip()

    if (
        team == ""
        or team == "FA"
        or team == "NONE"
    ):
        return None

    aliases = {
        "ARZ": "ARI",
        "ARI": "ARI",

        "ATL": "ATL",
        "BAL": "BAL",
        "BUF": "BUF",
        "CAR": "CAR",
        "CHI": "CHI",
        "CIN": "CIN",
        "CLE": "CLE",
        "DAL": "DAL",
        "DEN": "DEN",
        "DET": "DET",

        "GB": "GB",
        "GBP": "GB",

        "HOU": "HOU",
        "IND": "IND",

        "JAC": "JAX",
        "JAX": "JAX",

        "KC": "KC",
        "KCC": "KC",

        "LV": "LV",
        "LVR": "LV",
        "OAK": "LV",

        "LAC": "LAC",

        "LA": "LAR",
        "LAR": "LAR",

        "MIA": "MIA",
        "MIN": "MIN",

        "NE": "NE",
        "NEP": "NE",

        "NO": "NO",
        "NOS": "NO",

        "NYG": "NYG",
        "NYJ": "NYJ",

        "PHI": "PHI",
        "PIT": "PIT",
        "SEA": "SEA",

        "SF": "SF",
        "SFO": "SF",

        "TB": "TB",
        "TBB": "TB",

        "TEN": "TEN",

        "WAS": "WSH",
        "WSH": "WSH",
    }

    return aliases.get(
        team,
        team,
    )


# ==================================================
# Raw ranking archive
# ==================================================

def load_rankings_archive(
    force_refresh=False,
):
    """
    Load the full historical FantasyPros ranking
    archive.

    We maintain our own local Parquet cache because
    the archive contains nearly two million rows.
    """

    RAW_CACHE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if (
        RAW_CACHE_PATH.exists()
        and not force_refresh
    ):

        print(
            "[CACHE] Historical FantasyPros "
            "ranking archive"
        )

        return pl.read_parquet(
            RAW_CACHE_PATH
        )

    print(
        "[API] Loading historical FantasyPros "
        "ranking archive..."
    )

    raw = nfl.load_ff_rankings(
        type="all"
    )

    raw.write_parquet(
        RAW_CACHE_PATH
    )

    print(
        f"[SAVED] "
        f"{RAW_CACHE_PATH}"
    )

    return raw


# ==================================================
# Weekly FantasyPros ECR rows
# ==================================================

def get_weekly_position_rankings(
    raw,
):
    """
    Keep only the actual WEEKLY positional ECR pages.

    We intentionally exclude:

        dynasty
        redraft
        overall
        superflex
        IDP

    Desired pages are:

        weekly-qb
        weekly-rb
        weekly-wr
        weekly-te

    ecr_type='wp' is the weekly positional ranking
    category.
    """

    wanted_pages = [
        "weekly-qb",
        "weekly-rb",
        "weekly-wr",
        "weekly-te",
    ]

    weekly = raw.filter(
        (
            pl.col(
                "page_type"
            ).is_in(
                wanted_pages
            )
        )
        &
        (
            pl.col(
                "ecr_type"
            )
            == "wp"
        )
        &
        (
            pl.col(
                "pos"
            ).is_in(
                list(
                    POSITIONS
                )
            )
        )
    )

    print(
        f"Weekly positional ranking rows: "
        f"{weekly.height:,}"
    )

    return weekly


# ==================================================
# NFL schedules
# ==================================================

def load_schedule():
    """
    Load NFL schedules for the seasons represented
    in our training data.
    """

    print(
        "Loading NFL schedules..."
    )

    raw = nfl.load_schedules(
        seasons=SEASONS
    )

    schedule = pd.DataFrame(
        raw.to_dicts()
    )

    # Regular season only.
    if (
        "game_type"
        in schedule.columns
    ):

        schedule = schedule[
            schedule[
                "game_type"
            ]
            == "REG"
        ].copy()

    schedule[
        "gameday"
    ] = pd.to_datetime(
        schedule[
            "gameday"
        ],
        errors="coerce",
    ).dt.date

    schedule = schedule[
        schedule[
            "gameday"
        ].notna()
    ].copy()

    return schedule


# ==================================================
# Schedule lookups
# ==================================================

def build_team_game_lookup(
    schedule,
):
    """
    Map:

        (season, week, team)
            ->
        game date

    This allows us to determine whether the team's
    game had already happened when the FantasyPros
    rankings were scraped.
    """

    lookup = {}

    for (
        _,
        game,
    ) in schedule.iterrows():

        season = int(
            game[
                "season"
            ]
        )

        week = int(
            game[
                "week"
            ]
        )

        gameday = game[
            "gameday"
        ]

        home_team = normalize_team(
            game.get(
                "home_team"
            )
        )

        away_team = normalize_team(
            game.get(
                "away_team"
            )
        )

        if home_team:

            lookup[
                (
                    season,
                    week,
                    home_team,
                )
            ] = gameday

        if away_team:

            lookup[
                (
                    season,
                    week,
                    away_team,
                )
            ] = gameday

    return lookup


def get_first_sunday_by_week(
    schedule,
):
    """
    Find the first Sunday game for each NFL week.

    Our FantasyPros archive is generally scraped on
    Friday.

    We choose the latest archive snapshot BEFORE that
    week's Sunday games.
    """

    lookup = {}

    grouped = schedule.groupby(
        [
            "season",
            "week",
        ]
    )

    for (
        season,
        week,
    ), group in grouped:

        sunday_games = group[
            group[
                "gameday"
            ].apply(
                lambda value:
                    value.weekday()
                    == 6
            )
        ]

        if len(
            sunday_games
        ) == 0:
            continue

        first_sunday = min(
            sunday_games[
                "gameday"
            ]
        )

        lookup[
            (
                int(
                    season
                ),
                int(
                    week
                ),
            )
        ] = first_sunday

    return lookup


# ==================================================
# Snapshot matching
# ==================================================

def choose_snapshot_date(
    available_dates,
    first_sunday,
):
    """
    Choose the most recent FantasyPros scrape before
    Sunday.

    We restrict it to four days before Sunday so an
    old ranking snapshot cannot accidentally be mapped
    to the wrong NFL week.

    Standard result:

        Sunday games
        ^
        Friday snapshot
    """

    minimum_date = (
        first_sunday
        - pd.Timedelta(
            days=4
        )
    )

    candidates = [
        scrape_date
        for scrape_date
        in available_dates
        if (
            scrape_date
            < first_sunday
            and scrape_date
            >= minimum_date
        )
    ]

    if not candidates:
        return None

    return max(
        candidates
    )


# ==================================================
# Build historical weekly ECR
# ==================================================

def build_historical_ecr(
    force_refresh=False,
):
    """
    Build one leakage-safe historical ECR row per
    player/week.

    Important:

    FantasyPros snapshots are often from Friday.

    Therefore players whose game already occurred
    Thursday are NOT allowed to use that Friday ECR.

    Those player-weeks are omitted from this ECR
    dataset rather than contaminated with post-kickoff
    information.
    """

    raw = load_rankings_archive(
        force_refresh=force_refresh
    )

    weekly_raw = (
        get_weekly_position_rankings(
            raw
        )
    )

    # --------------------------------------------------
    # Convert the much smaller weekly subset to pandas.
    # --------------------------------------------------

    weekly = pd.DataFrame(
        weekly_raw.to_dicts()
    )

    weekly[
        "scrape_date_parsed"
    ] = pd.to_datetime(
        weekly[
            "scrape_date"
        ],
        errors="coerce",
    ).dt.date

    weekly = weekly[
        weekly[
            "scrape_date_parsed"
        ].notna()
    ].copy()

    available_dates = sorted(
        weekly[
            "scrape_date_parsed"
        ]
        .unique()
        .tolist()
    )

    # --------------------------------------------------
    # NFL schedule information
    # --------------------------------------------------

    schedule = load_schedule()

    team_game_lookup = (
        build_team_game_lookup(
            schedule
        )
    )

    first_sunday_lookup = (
        get_first_sunday_by_week(
            schedule
        )
    )

    # --------------------------------------------------
    # Build rows
    # --------------------------------------------------

    output_rows = []

    no_snapshot_count = 0
    already_played_count = 0
    unknown_team_count = 0
    no_game_match_count = 0

    week_summaries = []

    for season in SEASONS:

        season_schedule = schedule[
            schedule[
                "season"
            ]
            == season
        ]

        weeks = sorted(
            season_schedule[
                "week"
            ]
            .dropna()
            .astype(
                int
            )
            .unique()
            .tolist()
        )

        for week in weeks:

            first_sunday = (
                first_sunday_lookup.get(
                    (
                        season,
                        week,
                    )
                )
            )

            if first_sunday is None:
                continue

            snapshot_date = (
                choose_snapshot_date(
                    available_dates,
                    first_sunday,
                )
            )

            if snapshot_date is None:

                no_snapshot_count += 1

                continue

            snapshot = weekly[
                weekly[
                    "scrape_date_parsed"
                ]
                == snapshot_date
            ].copy()

            kept_this_week = 0
            dropped_this_week = 0

            for (
                _,
                row,
            ) in snapshot.iterrows():

                position = str(
                    row[
                        "pos"
                    ]
                ).upper()

                if position not in POSITIONS:
                    continue

                # Extra defense against accidentally
                # including a different weekly page.
                expected_page = (
                    f"weekly-"
                    f"{position.lower()}"
                )

                if (
                    row[
                        "page_type"
                    ]
                    != expected_page
                ):
                    continue

                team = normalize_team(
                    row.get(
                        "tm"
                    )
                    or row.get(
                        "team"
                    )
                )

                if team is None:

                    unknown_team_count += 1
                    dropped_this_week += 1

                    continue

                game_date = (
                    team_game_lookup.get(
                        (
                            season,
                            week,
                            team,
                        )
                    )
                )

                if game_date is None:

                    no_game_match_count += 1
                    dropped_this_week += 1

                    continue

                # --------------------------------------
                # CRITICAL LEAKAGE CHECK
                #
                # If the team's game is on or before the
                # scrape date, this ranking snapshot is
                # not guaranteed to be pregame.
                #
                # Do not use it.
                # --------------------------------------

                if (
                    game_date
                    <= snapshot_date
                ):

                    already_played_count += 1
                    dropped_this_week += 1

                    continue

                player_name = str(
                    row[
                        "player"
                    ]
                )

                output_rows.append(
                    {
                        "season":
                            season,

                        "week":
                            week,

                        "scrape_date":
                            str(
                                snapshot_date
                            ),

                        "game_date":
                            str(
                                game_date
                            ),

                        "fp_id":
                            row.get(
                                "id"
                            ),

                        "player":
                            player_name,

                        "normalized_name":
                            normalize_name(
                                player_name
                            ),

                        "position":
                            position,

                        "team":
                            team,

                        "fp_weekly_ecr":
                            row.get(
                                "ecr"
                            ),

                        "fp_weekly_sd":
                            row.get(
                                "sd"
                            ),

                        "fp_weekly_best":
                            row.get(
                                "best"
                            ),

                        "fp_weekly_worst":
                            row.get(
                                "worst"
                            ),
                    }
                )

                kept_this_week += 1

            week_summaries.append(
                {
                    "season":
                        season,

                    "week":
                        week,

                    "snapshot":
                        str(
                            snapshot_date
                        ),

                    "kept":
                        kept_this_week,

                    "dropped":
                        dropped_this_week,
                }
            )

    # ==================================================
    # Final DataFrame
    # ==================================================

    result = pd.DataFrame(
        output_rows
    )

    if len(
        result
    ) == 0:

        raise RuntimeError(
            "No historical ECR rows were produced."
        )

    # --------------------------------------------------
    # Numerical conversion
    # --------------------------------------------------

    numeric_columns = [
        "fp_weekly_ecr",
        "fp_weekly_sd",
        "fp_weekly_best",
        "fp_weekly_worst",
    ]

    for column in numeric_columns:

        result[
            column
        ] = pd.to_numeric(
            result[
                column
            ],
            errors="coerce",
        )

    # --------------------------------------------------
    # Remove accidental duplicates.
    # --------------------------------------------------

    result = (
        result
        .sort_values(
            [
                "season",
                "week",
                "position",
                "fp_weekly_ecr",
            ]
        )
        .drop_duplicates(
            subset=[
                "season",
                "week",
                "normalized_name",
                "position",
            ],
            keep="first",
        )
        .reset_index(
            drop=True
        )
    )

    # ==================================================
    # Save
    # ==================================================

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Use Python records rather than pl.from_pandas()
    # so pyarrow is not required.
    pl.DataFrame(
        result.to_dict(
            orient="records"
        )
    ).write_parquet(
        OUTPUT_PATH
    )

    return (
        result,
        pd.DataFrame(
            week_summaries
        ),
        {
            "no_snapshot":
                no_snapshot_count,

            "already_played":
                already_played_count,

            "unknown_team":
                unknown_team_count,

            "no_game_match":
                no_game_match_count,
        },
    )


# ==================================================
# Diagnostics
# ==================================================

def print_summary(
    result,
    week_summaries,
    drop_counts,
):

    print(
        "\n"
        + "=" * 72
    )

    print(
        "HISTORICAL WEEKLY ECR DATASET"
    )

    print(
        "=" * 72
    )

    print(
        f"Rows: "
        f"{len(result):,}"
    )

    print(
        f"Saved to: "
        f"{OUTPUT_PATH}"
    )

    print(
        "\nSEASON COUNTS"
    )

    print(
        result[
            "season"
        ]
        .value_counts()
        .sort_index()
        .to_string()
    )

    print(
        "\nPOSITION COUNTS"
    )

    print(
        result[
            "position"
        ]
        .value_counts()
        .to_string()
    )

    print(
        "\nLEAKAGE / MATCH FILTERS"
    )

    print(
        f"Player rows dropped because game "
        f"already occurred: "
        f"{drop_counts['already_played']:,}"
    )

    print(
        f"Player rows dropped because team "
        f"was unknown: "
        f"{drop_counts['unknown_team']:,}"
    )

    print(
        f"Player rows dropped because no NFL "
        f"game matched: "
        f"{drop_counts['no_game_match']:,}"
    )

    print(
        f"NFL weeks with no suitable snapshot: "
        f"{drop_counts['no_snapshot']:,}"
    )

    print(
        "\nRECENT WEEK SNAPSHOTS"
    )

    print(
        week_summaries
        .tail(
            20
        )
        .to_string(
            index=False
        )
    )

    print(
        "\nSAMPLE ECR ROWS"
    )

    sample_columns = [
        "season",
        "week",
        "scrape_date",
        "game_date",
        "player",
        "position",
        "team",
        "fp_weekly_ecr",
        "fp_weekly_sd",
        "fp_weekly_best",
        "fp_weekly_worst",
    ]

    print(
        result[
            sample_columns
        ]
        .tail(
            30
        )
        .to_string(
            index=False
        )
    )

    print(
        "\nLeakage rule:"
    )

    print(
        "Every retained FantasyPros ECR row has "
        "game_date > scrape_date."
    )


# ==================================================
# Main
# ==================================================

def main():

    (
        result,
        week_summaries,
        drop_counts,
    ) = build_historical_ecr()

    print_summary(
        result,
        week_summaries,
        drop_counts,
    )


if __name__ == "__main__":
    main()