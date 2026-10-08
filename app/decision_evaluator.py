import json
import sys
from itertools import combinations
from pathlib import Path

import nflreadpy as nfl
import polars as pl

from week_status import get_week_status


DECISIONS_DIR = Path("data/decisions")


def load_snapshot(season, week):
    path = DECISIONS_DIR / f"{season}_week{week}.json"

    if not path.exists():
        raise FileNotFoundError(
            f"No decision snapshot found at {path}"
        )

    with open(path, "r") as file:
        return json.load(file), path


def get_actual_points(season, week):
    """
    Get actual PPR fantasy points for the completed week.

    This currently covers offensive players from nflverse.
    K and D/ST will be handled separately later.
    """

    stats = nfl.load_player_stats(
        seasons=[season],
        summary_level="week",
    )

    week_stats = stats.filter(
        pl.col("week") == week
    )

    points = {}

    for row in week_stats.iter_rows(named=True):
        name = row.get("player_display_name")

        if not name:
            continue

        points[name] = row.get("fantasy_points_ppr")

    return points


def actual_value(player, actual_points):
    """
    Return the player's actual PPR points.
    """

    if player is None:
        return 0.0

    value = actual_points.get(player["name"])

    if value is None:
        return 0.0

    return float(value)


def is_available(player):
    """
    Use the player's PRE-GAME injury status from the
    saved snapshot.
    """

    return (
        player.get("injury_status")
        != "INJURY_RESERVE"
    )


def optimize_actual_lineup(roster, actual_points):
    """
    Build the best possible lineup in hindsight using
    actual Week N fantasy points.

    For now we optimize:
        1 QB
        2 RB
        2 WR
        1 TE
        1 FLEX

    K and D/ST are excluded because nflverse's player
    stats are not our source for those yet.
    """

    available = [
        player
        for player in roster
        if is_available(player)
    ]

    qbs = [
        p for p in available
        if p["position"] == "QB"
    ]

    rbs = [
        p for p in available
        if p["position"] == "RB"
    ]

    wrs = [
        p for p in available
        if p["position"] == "WR"
    ]

    tes = [
        p for p in available
        if p["position"] == "TE"
    ]

    if (
        len(qbs) < 1
        or len(rbs) < 2
        or len(wrs) < 2
        or len(tes) < 1
    ):
        return None, 0.0

    best_lineup = None
    best_total = -1

    for qb in qbs:

        for rb_combo in combinations(rbs, 2):

            for wr_combo in combinations(wrs, 2):

                for te in tes:

                    used_ids = {
                        player["espn_id"]
                        for player in (
                            [qb]
                            + list(rb_combo)
                            + list(wr_combo)
                            + [te]
                        )
                    }

                    flex_candidates = [
                        player
                        for player in (
                            rbs + wrs + tes
                        )
                        if player["espn_id"]
                        not in used_ids
                    ]

                    if not flex_candidates:
                        continue

                    flex = max(
                        flex_candidates,
                        key=lambda player: actual_value(
                            player,
                            actual_points,
                        ),
                    )

                    lineup = {
                        "QB": qb,
                        "RB1": rb_combo[0],
                        "RB2": rb_combo[1],
                        "WR1": wr_combo[0],
                        "WR2": wr_combo[1],
                        "TE": te,
                        "FLEX": flex,
                    }

                    total = sum(
                        actual_value(
                            player,
                            actual_points,
                        )
                        for player in lineup.values()
                    )

                    if total > best_total:
                        best_total = total
                        best_lineup = lineup

    return best_lineup, best_total


def get_started_skill_players(snapshot):
    """
    Extract QB/RB/WR/TE/FLEX from the lineup that
    was recommended before the games.
    """

    lineup = snapshot["lineup"]

    slots = [
        "QB",
        "RB1",
        "RB2",
        "WR1",
        "WR2",
        "TE",
        "FLEX",
    ]

    return {
        slot: lineup.get(slot)
        for slot in slots
    }


def projected_value(player):
    if player is None:
        return 0.0

    value = player.get("projection_ppr")

    if value is None:
        return 0.0

    return float(value)


def ecr_value(player):
    if player is None:
        return None

    value = player.get("ecr")

    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def recent_ppr(player):
    if player is None:
        return None

    usage = player.get("recent_usage") or {}

    value = usage.get("ppr_points_per_game")

    if value is None:
        return None

    return float(value)


def compare_pregame_support(chosen, alternative):
    """
    Determine how many pre-game signals favored each
    player.

    This is NOT yet machine learning. It is simply an
    audit of what the model knew before kickoff.
    """

    chosen_signals = 0
    alternative_signals = 0
    details = {}

    # Projection
    chosen_proj = projected_value(chosen)
    alt_proj = projected_value(alternative)

    details["projection"] = {
        chosen["name"]: chosen_proj,
        alternative["name"]: alt_proj,
    }

    if chosen_proj > alt_proj:
        chosen_signals += 1
    elif alt_proj > chosen_proj:
        alternative_signals += 1

    # Recent production
    chosen_recent = recent_ppr(chosen)
    alt_recent = recent_ppr(alternative)

    details["recent_ppr"] = {
        chosen["name"]: chosen_recent,
        alternative["name"]: alt_recent,
    }

    if (
        chosen_recent is not None
        and alt_recent is not None
    ):
        if chosen_recent > alt_recent:
            chosen_signals += 1
        elif alt_recent > chosen_recent:
            alternative_signals += 1

    # ECR — lower is better
    chosen_ecr = ecr_value(chosen)
    alt_ecr = ecr_value(alternative)

    details["ecr"] = {
        chosen["name"]: chosen_ecr,
        alternative["name"]: alt_ecr,
    }

    if (
        chosen_ecr is not None
        and alt_ecr is not None
    ):
        if chosen_ecr < alt_ecr:
            chosen_signals += 1
        elif alt_ecr < chosen_ecr:
            alternative_signals += 1

    return {
        "chosen_signals": chosen_signals,
        "alternative_signals": alternative_signals,
        "details": details,
    }


def classify_miss(chosen, alternative):
    """
    Give a preliminary classification.

    We deliberately do NOT claim one bad outcome proves
    that the model was wrong.
    """

    support = compare_pregame_support(
        chosen,
        alternative,
    )

    chosen_signals = support["chosen_signals"]
    alternative_signals = (
        support["alternative_signals"]
    )

    projection_gap = (
        projected_value(chosen)
        - projected_value(alternative)
    )

    if chosen_signals >= 2 and projection_gap >= 2:
        classification = (
            "strong_process_bad_outcome"
        )

    elif chosen_signals > alternative_signals:
        classification = (
            "reasonable_process_bad_outcome"
        )

    elif alternative_signals > chosen_signals:
        classification = (
            "possible_model_miss"
        )

    else:
        classification = "close_call"

    return {
        "classification": classification,
        "pregame_support": support,
    }


def evaluate_week(season, week):
    snapshot, path = load_snapshot(
        season,
        week,
    )

    actual_points = get_actual_points(
        season,
        week,
    )

    # --------------------------------------------------
    # Make sure actual stats are available
    # --------------------------------------------------

    if not actual_points:
        raise RuntimeError(
            f"Week {week} is complete, but nflverse "
            f"player stats are not available yet. "
            f"Evaluation will be retried next run."
        )

    roster = snapshot["roster"]

    # --------------------------------------------------
    # Actual results for every offensive roster player
    # --------------------------------------------------

    results = {}

    for player in roster:
        if player["position"] in (
            "QB",
            "RB",
            "WR",
            "TE",
        ):
            results[player["name"]] = (
                actual_points.get(
                    player["name"]
                )
            )

    # --------------------------------------------------
    # Actual total of recommended skill lineup
    # --------------------------------------------------

    started = get_started_skill_players(
        snapshot
    )

    # Make sure our recommended starters have actual
    # stats before permanently evaluating the week.
    missing_started_stats = []

    for player in started.values():
        if player is None:
            continue

        if actual_points.get(player["name"]) is None:
            missing_started_stats.append(
                player["name"]
            )

    if missing_started_stats:
        raise RuntimeError(
            "Actual stats are not ready for: "
            + ", ".join(missing_started_stats)
            + ". Evaluation will be retried next run."
        )

    recommended_actual_total = sum(
        actual_value(
            player,
            actual_points,
        )
        for player in started.values()
        if player is not None
    )

    # --------------------------------------------------
    # Best possible hindsight lineup
    # --------------------------------------------------

    hindsight_lineup, hindsight_total = (
        optimize_actual_lineup(
            roster,
            actual_points,
        )
    )

    if hindsight_lineup is None:
        raise RuntimeError(
            "Could not construct hindsight lineup."
        )

    recommended_names = {
        player["name"]
        for player in started.values()
        if player is not None
    }

    hindsight_names = {
        player["name"]
        for player in hindsight_lineup.values()
        if player is not None
    }

    missed_players = (
        hindsight_names - recommended_names
    )

    replaced_players = (
        recommended_names - hindsight_names
    )

    # --------------------------------------------------
    # Review misses
    # --------------------------------------------------

    reviews = []

    for missed_name in missed_players:

        alternative = next(
            player
            for player in roster
            if player["name"] == missed_name
        )

        # Find a starter that disappeared from the
        # hindsight lineup with a compatible position.
        compatible = []

        for replaced_name in replaced_players:

            chosen = next(
                player
                for player in roster
                if player["name"] == replaced_name
            )

            # Same-position comparison is safest.
            if (
                chosen["position"]
                == alternative["position"]
            ):
                compatible.append(chosen)

            # FLEX can involve RB/WR/TE differences.
            elif (
                chosen["position"]
                in ("RB", "WR", "TE")
                and alternative["position"]
                in ("RB", "WR", "TE")
            ):
                compatible.append(chosen)

        if not compatible:
            continue

        # Review the replaced player whose actual score
        # was lowest.
        chosen = min(
            compatible,
            key=lambda player: actual_value(
                player,
                actual_points,
            ),
        )

        review = classify_miss(
            chosen,
            alternative,
        )

        review["chosen"] = chosen["name"]
        review["alternative"] = (
            alternative["name"]
        )

        review["chosen_actual"] = (
            actual_value(
                chosen,
                actual_points,
            )
        )

        review["alternative_actual"] = (
            actual_value(
                alternative,
                actual_points,
            )
        )

        reviews.append(review)

    regret = (
        hindsight_total
        - recommended_actual_total
    )

    evaluation = {
        "recommended_skill_position_actual":
            round(
                recommended_actual_total,
                2,
            ),

        "hindsight_optimal_actual":
            round(
                hindsight_total,
                2,
            ),

        "points_left_on_bench":
            round(
                regret,
                2,
            ),

        "recommended_players":
            sorted(recommended_names),

        "hindsight_players":
            sorted(hindsight_names),

        "miss_reviews":
            reviews,
    }

    snapshot["actual_results"] = results
    snapshot["evaluation"] = evaluation

    with open(path, "w") as file:
        json.dump(
            snapshot,
            file,
            indent=2,
        )

    return evaluation


def print_evaluation(evaluation):
    print("\n=== WEEKLY DECISION REVIEW ===")

    print(
        "Recommended lineup actual: "
        f"{evaluation['recommended_skill_position_actual']:.2f}"
    )

    print(
        "Best hindsight lineup:      "
        f"{evaluation['hindsight_optimal_actual']:.2f}"
    )

    print(
        "Points left on bench:       "
        f"{evaluation['points_left_on_bench']:.2f}"
    )

    reviews = evaluation["miss_reviews"]

    if not reviews:
        print(
            "\nNo meaningful lineup misses found."
        )
        return

    print("\n=== MISSED DECISIONS ===")

    for review in reviews:

        print(
            f"\n{review['chosen']} "
            f"({review['chosen_actual']:.2f})"
        )

        print(
            f"vs {review['alternative']} "
            f"({review['alternative_actual']:.2f})"
        )

        print(
            "Classification: "
            f"{review['classification']}"
        )

        support = review["pregame_support"]

        print(
            "Pregame signals: "
            f"{review['chosen']} "
            f"{support['chosen_signals']} - "
            f"{review['alternative']} "
            f"{support['alternative_signals']}"
        )


def evaluate_pending_decisions():
    """
    Find decision snapshots that have not been evaluated.

    If their NFL week has finished, automatically run
    the evaluator.
    """

    if not DECISIONS_DIR.exists():
        return

    decision_files = sorted(
        DECISIONS_DIR.glob("*_week*.json")
    )

    for path in decision_files:

        try:
            with open(path, "r") as file:
                snapshot = json.load(file)

            season = snapshot.get("season")
            week = snapshot.get("week")

            # Already evaluated
            if snapshot.get("evaluation") is not None:
                continue

            if season is None or week is None:
                continue

            # Week still has NFL games remaining
            status = get_week_status(
                season,
                week,
            )

            if status != "COMPLETE":
                continue

            print(
                f"[AUTO-EVAL] "
                f"Week {week} is complete. "
                f"Evaluating decisions..."
            )

            evaluation = evaluate_week(
                season,
                week,
            )

            print_evaluation(evaluation)

            print(
                f"[AUTO-EVAL] "
                f"Week {week} evaluation saved."
            )

        except Exception as e:
            # Don't let evaluation problems prevent
            # main.py from running.
            print(
                f"[AUTO-EVAL] "
                f"Could not evaluate {path.name}: {e}"
            )


if __name__ == "__main__":

    if len(sys.argv) != 3:
        print(
            "Usage: "
            "uv run python app/decision_evaluator.py "
            "<season> <week>"
        )

        sys.exit(1)

    season = int(sys.argv[1])
    week = int(sys.argv[2])

    evaluation = evaluate_week(
        season,
        week,
    )

    print_evaluation(evaluation)