from datetime import datetime
from zoneinfo import ZoneInfo

import nflreadpy as nfl
import polars as pl


EASTERN = ZoneInfo("America/New_York")


def get_week_status(season, week):
    """
    Returns one of:

        "NOT_STARTED"
        "IN_PROGRESS"
        "COMPLETE"
        "UNKNOWN"
    """

    schedule = nfl.load_schedules(
        seasons=season
    )

    games = schedule.filter(
        (pl.col("week") == week)
        & (pl.col("game_type") == "REG")
    )

    if games.height == 0:
        return "UNKNOWN"

    # --------------------------------------------------
    # If every game has a result, week is finished
    # --------------------------------------------------

    unfinished = games.filter(
        pl.col("result").is_null()
    )

    if unfinished.height == 0:
        return "COMPLETE"

    # --------------------------------------------------
    # Find first kickoff of the week
    # --------------------------------------------------

    kickoff_times = []

    for game in games.iter_rows(named=True):
        gameday = game.get("gameday")
        gametime = game.get("gametime")

        if not gameday or not gametime:
            continue

        kickoff = datetime.fromisoformat(
            f"{gameday}T{gametime}"
        ).replace(
            tzinfo=EASTERN
        )

        kickoff_times.append(kickoff)

    if not kickoff_times:
        return "UNKNOWN"

    first_kickoff = min(kickoff_times)

    now = datetime.now(EASTERN)

    # --------------------------------------------------
    # No game has kicked off yet
    # --------------------------------------------------

    if now < first_kickoff:
        return "NOT_STARTED"

    # --------------------------------------------------
    # At least one game has started, but not every
    # game has a final result
    # --------------------------------------------------

    return "IN_PROGRESS"