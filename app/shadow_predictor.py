"""Live SHADOW inference for the rolling ECR-enhanced fantasy model.

This module intentionally does NOT alter weekly_score or the optimizer.
It only exposes a current-week ML PPR forecast so the chatbot and logs can
compare the model with ESPN / FantasyPros while we continue validating it.

Leakage rule:
    A Week N prediction is built only from player statistics from weeks < N,
    plus the current pregame ECR fields already attached to the player.
"""

from pathlib import Path
import math
import re
import unicodedata
from datetime import datetime, timezone

import joblib
import pandas as pd
import polars as pl


MODEL_PATH = Path("data/models/weekly_ppr_model_ecr_latest.joblib")
RAW_CACHE_DIR = Path("data/cache/nflverse")
SHADOW_LOG_PATH = Path("data/shadow_predictions/shadow_predictions.csv")

_MODEL_PACKAGE = None
_RAW_SEASON_CACHE = {}
_PREDICTION_CACHE = {}


BASE_NUMERIC_FEATURES = [
    "prev_game_ppr",
    "l3_ppr_avg",
    "season_ppr_avg",
    "l3_completions_avg",
    "l3_attempts_avg",
    "l3_passing_yards_avg",
    "l3_passing_tds_avg",
    "l3_passing_interceptions_avg",
    "l3_carries_avg",
    "l3_rushing_yards_avg",
    "l3_rushing_tds_avg",
    "l3_targets_avg",
    "l3_receptions_avg",
    "l3_receiving_yards_avg",
    "l3_receiving_tds_avg",
    "l3_target_share_avg",
    "l3_air_yards_share_avg",
    "l3_opportunities_avg",
]

ECR_FEATURES = [
    "fp_weekly_ecr",
    "fp_weekly_ecr_score",
    "fp_weekly_sd",
    "fp_weekly_best",
    "fp_weekly_worst",
    "fp_weekly_range",
]

DEFAULT_MODEL_FEATURES = BASE_NUMERIC_FEATURES + ECR_FEATURES + ["position"]

RAW_STAT_COLUMNS = [
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "targets",
    "receptions",
    "receiving_yards",
    "receiving_tds",
    "target_share",
    "air_yards_share",
]

ROLLING_TO_FEATURE = {
    "fantasy_points_ppr": "l3_ppr_avg",
    "completions": "l3_completions_avg",
    "attempts": "l3_attempts_avg",
    "passing_yards": "l3_passing_yards_avg",
    "passing_tds": "l3_passing_tds_avg",
    "passing_interceptions": "l3_passing_interceptions_avg",
    "carries": "l3_carries_avg",
    "rushing_yards": "l3_rushing_yards_avg",
    "rushing_tds": "l3_rushing_tds_avg",
    "targets": "l3_targets_avg",
    "receptions": "l3_receptions_avg",
    "receiving_yards": "l3_receiving_yards_avg",
    "receiving_tds": "l3_receiving_tds_avg",
    "target_share": "l3_target_share_avg",
    "air_yards_share": "l3_air_yards_share_avg",
}


def normalize_name(value):
    if value is None:
        return ""

    name = unicodedata.normalize("NFKD", str(value))
    name = "".join(
        character
        for character in name
        if not unicodedata.combining(character)
    )
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _safe_float(value):
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def _load_model_package():
    global _MODEL_PACKAGE

    if _MODEL_PACKAGE is not None:
        return _MODEL_PACKAGE

    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Shadow model not found: {MODEL_PATH}. "
            "Run app/weekly_ml_update.py first."
        )

    package = joblib.load(MODEL_PATH)

    if isinstance(package, dict):
        if "model" not in package:
            raise RuntimeError(
                f"{MODEL_PATH} is missing the saved 'model' field."
            )
        _MODEL_PACKAGE = package
    else:
        # Defensive support for a bare sklearn model.
        _MODEL_PACKAGE = {"model": package}

    return _MODEL_PACKAGE


def _model_features(package):
    numeric = package.get("numeric_features")
    categorical = package.get("categorical_features")

    if numeric and categorical:
        return list(numeric) + list(categorical)

    explicit = package.get("model_features")
    if explicit:
        return list(explicit)

    return list(DEFAULT_MODEL_FEATURES)


def _load_raw_season(season):
    if season in _RAW_SEASON_CACHE:
        return _RAW_SEASON_CACHE[season]

    path = RAW_CACHE_DIR / f"player_stats_{season}.parquet"

    if not path.exists():
        raise FileNotFoundError(
            f"Missing current-season nflverse cache: {path}. "
            "Run app/weekly_ml_update.py first."
        )

    raw = pl.read_parquet(path)
    df = pd.DataFrame(raw.to_dicts())

    # nflverse has used both names historically.
    if "passing_interceptions" not in df.columns:
        if "interceptions" in df.columns:
            df["passing_interceptions"] = df["interceptions"]
        else:
            df["passing_interceptions"] = 0.0

    for column in RAW_STAT_COLUMNS + ["fantasy_points_ppr", "season", "week"]:
        if column not in df.columns:
            df[column] = 0.0
        df[column] = pd.to_numeric(df[column], errors="coerce")

    if "season_type" in df.columns:
        df = df[df["season_type"] == "REG"].copy()

    if "player_display_name" not in df.columns:
        raise RuntimeError(
            "nflverse cache is missing player_display_name."
        )

    df["normalized_name"] = df["player_display_name"].apply(normalize_name)

    _RAW_SEASON_CACHE[season] = df
    return df


def _match_history(player, season, week):
    df = _load_raw_season(season)
    name_key = normalize_name(player.get("name"))
    position = str(player.get("position") or "").upper()

    rows = df[
        (df["season"] == int(season))
        & (df["week"] < int(week))
        & (df["normalized_name"] == name_key)
    ].copy()

    if "position" in rows.columns and position:
        same_position = rows[rows["position"].astype(str).str.upper() == position]
        if not same_position.empty:
            rows = same_position

    if rows.empty:
        return rows

    # If a name collision somehow survives, use the current NFL team when the
    # cache gives us a team column.
    team = str(player.get("nfl_team") or "").upper()
    team_column = None
    for candidate in ("team", "recent_team"):
        if candidate in rows.columns:
            team_column = candidate
            break

    if team and team_column is not None:
        same_team = rows[rows[team_column].astype(str).str.upper() == team]
        if not same_team.empty:
            rows = same_team

    return rows.sort_values("week")


def _mean_last_three(rows, column):
    values = pd.to_numeric(rows[column], errors="coerce").tail(3)
    if len(values) == 0:
        return 0.0
    value = values.mean()
    return 0.0 if pd.isna(value) else float(value)


def _build_feature_row(player, season, week):
    rows = _match_history(player, season, week)

    if rows.empty:
        raise RuntimeError(
            "No prior same-season nflverse games were found for this player."
        )

    row = {
        "position": str(player.get("position") or ""),
        "prev_game_ppr": float(rows.iloc[-1]["fantasy_points_ppr"]),
        "l3_ppr_avg": _mean_last_three(rows, "fantasy_points_ppr"),
        "season_ppr_avg": float(
            pd.to_numeric(rows["fantasy_points_ppr"], errors="coerce").mean()
        ),
    }

    for raw_column, feature_name in ROLLING_TO_FEATURE.items():
        if raw_column == "fantasy_points_ppr":
            continue
        row[feature_name] = _mean_last_three(rows, raw_column)

    opportunities = (
        pd.to_numeric(rows["carries"], errors="coerce").fillna(0.0)
        + pd.to_numeric(rows["targets"], errors="coerce").fillna(0.0)
    )
    row["l3_opportunities_avg"] = float(opportunities.tail(3).mean())

    # Current, pregame ECR features already attached by player_enrichment.py.
    ecr = _safe_float(player.get("ecr"))
    best = _safe_float(player.get("rank_min"))
    worst = _safe_float(player.get("rank_max"))
    sd = _safe_float(player.get("rank_std"))

    if ecr is None:
        raise RuntimeError(
            "Current weekly ECR is missing, so the ECR-enhanced shadow model "
            "cannot make a comparable prediction."
        )

    row["fp_weekly_ecr"] = ecr
    row["fp_weekly_ecr_score"] = 1.0 / (1.0 + 0.1 * (ecr - 1.0))
    row["fp_weekly_sd"] = sd
    row["fp_weekly_best"] = best
    row["fp_weekly_worst"] = worst
    row["fp_weekly_range"] = (
        worst - best
        if worst is not None and best is not None
        else None
    )

    return row, len(rows)



def _log_prediction(player, result, season, week):
    """Upsert one shadow prediction so later evaluation can compare it to actuals.

    Logging is best-effort only. A logging failure must never break the chatbot.
    """

    try:
        SHADOW_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        player_key = str(
            player.get("espn_id")
            or normalize_name(player.get("name"))
        )

        record = {
            "season": int(season),
            "week": int(week),
            "player_key": player_key,
            "player_name": player.get("name"),
            "position": player.get("position"),
            "nfl_team": player.get("nfl_team"),
            "prediction_ppr": result.get("prediction_ppr"),
            "espn_projection": _safe_float(player.get("espn_projected_points")),
            "fantasypros_projection": _safe_float(player.get("fp_ppr")),
            "weekly_ecr": _safe_float(player.get("ecr")),
            "trained_season": result.get("trained_season"),
            "trained_through_week": result.get("trained_through_week"),
            "model_training_rows": result.get("model_training_rows"),
            "player_history_games_used": result.get("player_history_games_used"),
            "availability_adjusted": result.get("availability_adjusted", False),
            "logged_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        if SHADOW_LOG_PATH.exists():
            existing = pd.read_csv(SHADOW_LOG_PATH)
        else:
            existing = pd.DataFrame()

        if not existing.empty:
            match = (
                pd.to_numeric(existing["season"], errors="coerce") == int(season)
            ) & (
                pd.to_numeric(existing["week"], errors="coerce") == int(week)
            ) & (
                existing["player_key"].astype(str) == player_key
            )
            existing = existing.loc[~match].copy()

        updated = pd.concat(
            [existing, pd.DataFrame([record])],
            ignore_index=True,
        )
        updated.to_csv(SHADOW_LOG_PATH, index=False)

    except Exception:
        pass


def get_shadow_prediction(player, season, week):
    """Return a JSON-safe shadow-model prediction record.

    This function never changes the supplied player dictionary.
    """

    position = str(player.get("position") or "").upper()
    if position not in {"QB", "RB", "WR", "TE"}:
        return {
            "available": False,
            "reason": "Shadow model currently covers QB/RB/WR/TE only.",
        }

    cache_key = (
        player.get("espn_id") or normalize_name(player.get("name")),
        int(season),
        int(week),
        _safe_float(player.get("ecr")),
        _safe_float(player.get("rank_std")),
    )

    if cache_key in _PREDICTION_CACHE:
        return dict(_PREDICTION_CACHE[cache_key])

    try:
        package = _load_model_package()

        trained_season = package.get("current_season")
        trained_through = package.get("trained_through_week")

        # Never score a target week with a model that says it trained on that
        # same week or later in the same season.
        if (
            trained_season is not None
            and trained_through is not None
            and int(trained_season) == int(season)
            and int(trained_through) >= int(week)
        ):
            result = {
                "available": False,
                "reason": (
                    "Leakage guard: latest model metadata says it was trained "
                    f"through Week {trained_through}, which is not before target "
                    f"Week {week}."
                ),
            }
            _PREDICTION_CACHE[cache_key] = result
            return dict(result)

        feature_row, history_games = _build_feature_row(player, season, week)
        features = _model_features(package)

        # Fill truly unknown model columns defensively. Known historical
        # counting/usage features default to zero; unknown numeric values use NaN.
        for feature in features:
            if feature in feature_row:
                continue
            if feature == "position":
                feature_row[feature] = position
            elif feature in BASE_NUMERIC_FEATURES:
                feature_row[feature] = 0.0
            else:
                feature_row[feature] = float("nan")

        X = pd.DataFrame([feature_row])[features]
        prediction = float(package["model"].predict(X)[0])

        result = {
            "available": True,
            "prediction_ppr": round(prediction, 3),
            # This is ONLY the current player's same-season history used to
            # build the Week-N feature row. It is NOT the model's training size.
            "player_history_games_used": int(history_games),
            "trained_through_week": trained_through,
            "trained_season": trained_season,
            "model_training_rows": package.get("training_rows"),
            "model_role": package.get("model_role", "shadow"),
            "live_decision_weight": package.get("live_decision_weight", 0.0),
            "availability_adjusted": False,
            "model_file": MODEL_PATH.name,
        }

        _log_prediction(
            player=player,
            result=result,
            season=season,
            week=week,
        )

    except Exception as exc:
        result = {
            "available": False,
            "reason": str(exc),
        }

    _PREDICTION_CACHE[cache_key] = result
    return dict(result)
