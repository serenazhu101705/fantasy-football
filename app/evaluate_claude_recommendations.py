"""Score frozen Claude weekly head-to-head picks, *after* all games finish.

Usage: uv run python app/evaluate_claude_recommendations.py --season 2026 --week 7
Does not call Anthropic. No future games are used as pregame evidence.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

JOURNAL_DIR = Path("data/decisions/claude")
REPORT_DIR = Path("data/decisions/claude_evaluations")
EASTERN = ZoneInfo("America/New_York")


def kickoff_utc(game):
    """Parse nflverse local Eastern game kickoff, accounting for DST."""
    day, time = game.get("gameday"), game.get("gametime")
    if not day or not time:
        return None
    try:
        dt = datetime.fromisoformat(f"{day}T{time}").replace(tzinfo=EASTERN)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def score_record(record, actual_points, first_kickoff):
    """No guessing about missing NFL stats or ambiguous/missing Claude choices."""
    result = {"record_id": record.get("record_id"), "type": record.get("type")}
    if record.get("evaluation_scope") != "weekly_head_to_head":
        return {**result, "status": "NOT_WEEKLY_SCORABLE"}
    recorded = record.get("recorded_at_utc")
    try:
        made_at = datetime.fromisoformat(recorded.replace("Z", "+00:00"))
        if made_at.tzinfo is None:
            raise ValueError("Naive time")
    except (AttributeError, ValueError):
        return {**result, "status": "MISSING_DECISION_TIME"}
    if first_kickoff is None:
        return {**result, "status": "MISSING_KICKOFF_TIME"}
    if made_at.astimezone(timezone.utc) >= first_kickoff:
        return {**result, "status": "AFTER_WEEK_KICKOFF"}
    if record.get("choice_verification") != "receipt":
        return {**result, "status": "NO_VERIFIED_CLAUDE_CHOICE"}
    p = record.get("candidate_players") or []
    if len(p) != 2 or p[0].get("name") == p[1].get("name"):
        return {**result, "status": "INVALID_PAIR"}
    name_a, name_b = p[0]["name"], p[1]["name"]
    if any(x.get("position") not in {"QB", "RB", "WR", "TE"} for x in p):
        return {**result, "status": "UNSUPPORTED_POSITION"}
    choice = record.get("claude_choice")
    if choice not in (name_a, name_b):
        return {**result, "status": "INVALID_CHOICE"}
    a, b = actual_points.get(name_a), actual_points.get(name_b)
    if a is None or b is None:
        return {**result, "status": "MISSING_ACTUAL_STATS", "missing": [n for n, v in ((name_a, a), (name_b, b)) if v is None]}
    points = {name_a: float(a), name_b: float(b)}
    other = name_b if choice == name_a else name_a
    regret = max(points[other] - points[choice], 0.0)
    baseline = None
    # Compare against ESPN and FantasyPros independently, only when both
    # candidates have projections frozen from the original question.
    for label, field in (("espn", "espn_projection"), ("fantasypros", "fantasypros_projection"), ("advisory", "weekly_score_advisory")):
        v_a, v_b = p[0].get(field), p[1].get(field)
        if v_a is not None and v_b is not None:
            baseline = baseline or {}
            pick = name_a if float(v_a) >= float(v_b) else name_b
            baseline[label] = {"choice": pick, "regret_points": round(max(points[name_b if pick == name_a else name_a] - points[pick], 0), 3)}
    return {**result, "status": "SCORED", "choice": choice, "actual_points": points,
            "claude_regret_points": round(regret, 3), "hit": points[choice] >= points[other],
            "baseline": baseline or {}}


def report_week(season, week, journal_dir=JOURNAL_DIR, report_dir=REPORT_DIR):
    """Read once-frozen records; return an audit report without altering them."""
    import nflreadpy as nfl
    import polars as pl
    from decision_evaluator import _league_points_from_nflverse
    from week_status import get_week_status

    if get_week_status(season, week) != "COMPLETE":
        raise RuntimeError("Week is not complete; refusing to evaluate future or live games.")
    files = sorted(Path(journal_dir).glob(f"{season}_week{week}_*.json"))
    records = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    schedules = nfl.load_schedules(seasons=season).filter(
        (pl.col("week") == week) & (pl.col("game_type") == "REG"))
    kicks = [kickoff_utc(row) for row in schedules.iter_rows(named=True)]
    first_kickoff = min((k for k in kicks if k is not None), default=None)

    stats = nfl.load_player_stats(seasons=[season], summary_level="week")
    rows = list(stats.filter(pl.col("week") == week).iter_rows(named=True))
    rec_map = {}
    for rec in records:
        scoring = rec.get("scoring") or {}
        rp = scoring.get("reception_points")
        if rp is not None:
            rec_map[float(rp)] = None
    for rp in rec_map:
        rec_map[rp] = {row["player_display_name"]: _league_points_from_nflverse(row, rp)
                       for row in rows if row.get("player_display_name")}

    outcomes = []
    supported_formats = {"STANDARD": 0.0, "HALF_PPR": 0.5, "PPR": 1.0}
    for rec in records:
        scoring = rec.get("scoring") or {}
        rp = scoring.get("reception_points")
        label = scoring.get("label")
        if rec.get("evaluation_scope") == "weekly_head_to_head" and (
            rp is None or label not in supported_formats
            or abs(float(rp) - supported_formats[label]) > 0.001
        ):
            outcomes.append({"record_id": rec.get("record_id"), "status": "UNSUPPORTED_OR_UNKNOWN_LEAGUE_SCORING"})
        else:
            outcomes.append(score_record(rec, rec_map.get(float(rp), {}) if rp is not None else {}, first_kickoff))
    scored = [r for r in outcomes if r["status"] == "SCORED"]
    summary = {
        "season": season, "week": week, "decisions_logged": len(records), "weekly_scored": len(scored),
        "mean_claude_regret_points": round(sum(r["claude_regret_points"] for r in scored) / len(scored), 3) if scored else None,
        "claude_hit_rate": round(sum(r["hit"] for r in scored) / len(scored), 3) if scored else None,
    }
    for source in ("espn", "fantasypros", "advisory"):
        valid = [r["baseline"][source]["regret_points"] for r in scored if source in r.get("baseline", {})]
        summary[f"{source}_mean_regret_points"] = round(sum(valid) / len(valid), 3) if valid else None
        summary[f"{source}_n"] = len(valid)
    report = {"summary": summary, "outcomes": outcomes}
    Path(report_dir).mkdir(parents=True, exist_ok=True)
    target = Path(report_dir) / f"{season}_week{week}.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report, target


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--week", type=int, required=True)
    args = p.parse_args()
    report, target = report_week(args.season, args.week)
    print(json.dumps(report["summary"], indent=2))
    print(f"Audit saved: {target}")
    for row in report["outcomes"]:
        if row["status"] != "SCORED":
            print(f"Skipped {row.get('record_id')}: {row['status']}")


if __name__ == "__main__":
    main()
