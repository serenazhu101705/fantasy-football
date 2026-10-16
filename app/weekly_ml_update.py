from training_data import (
    build_training_dataset,
    print_dataset_summary,
)

from historical_ecr import (
    build_historical_ecr,
    print_summary as print_ecr_summary,
)

from merge_ecr_training_data import (
    prepare_ecr,
    merge_datasets,
    print_summary as print_merge_summary,
    save_dataset,
)

from rolling_retrain import (
    LATEST_MODEL_PATH,
    get_training_cutoff,
    build_rolling_training_set,
    print_training_summary,
    train_rolling_model,
    save_model,
)

import joblib

# ==================================================
# Weekly ML update pipeline
# ==================================================

def run_weekly_ml_update():

    print(
        "\n"
        + "=" * 72
    )

    print(
        "WEEKLY FANTASY ML UPDATE"
    )

    print(
        "=" * 72
    )

    # ==================================================
    # Determine safe training cutoff
    # ==================================================

    cutoff = get_training_cutoff()

    current_week = (
        cutoff[
            "current_week"
        ]
    )

    completed_through_week = (
        cutoff[
            "completed_through_week"
        ]
    )

    print(
        f"\nCurrent ESPN week: "
        f"{current_week}"
    )

    print(
        f"Safe training cutoff: "
        f"Week "
        f"{completed_through_week}"
    )

    if completed_through_week < 1:

        raise RuntimeError(
            "No completed current-season "
            "weeks are available yet."
        )

    # ==================================================
    # STEP 1
    # Refresh nflverse actual results
    #
    # Completed historical seasons remain cached.
    # Only the CURRENT season is forced fresh.
    # ==================================================

    print(
        "\n"
        + "=" * 72
    )

    print(
        "STEP 1: REFRESH PLAYER-WEEK DATA"
    )

    print(
        "=" * 72
    )

    (
        player_weeks,
        feature_columns,
    ) = build_training_dataset(
        force_refresh_current=True,
    )

    print_dataset_summary(
        player_weeks,
        feature_columns,
    )

    # ==================================================
    # STEP 2
    # Refresh historical FantasyPros ECR archive
    #
    # This is necessary so newly completed/current
    # weeks can receive their leakage-safe pregame ECR.
    # ==================================================

    print(
        "\n"
        + "=" * 72
    )

    print(
        "STEP 2: REFRESH HISTORICAL ECR"
    )

    print(
        "=" * 72
    )

    (
        historical_ecr,
        week_summaries,
        drop_counts,
    ) = build_historical_ecr(
        force_refresh=True,
    )

    print_ecr_summary(
        historical_ecr,
        week_summaries,
        drop_counts,
    )

    # ==================================================
    # STEP 3
    # Merge player-week data with leakage-safe ECR
    # ==================================================

    print(
        "\n"
        + "=" * 72
    )

    print(
        "STEP 3: MERGE PLAYER DATA + ECR"
    )

    print(
        "=" * 72
    )

    prepared_ecr = prepare_ecr(
        historical_ecr
    )

    merged = merge_datasets(
        player_weeks,
        prepared_ecr,
    )

    print_merge_summary(
        merged
    )

    save_dataset(
        merged
    )

    # ==================================================
    # STEP 4
    # Build safe rolling training set
    #
    # Even if current-week nflverse data already contains
    # Thursday/Sunday partial results, they are excluded
    # here because we only allow:
    #
    #     week <= completed_through_week
    #
    # ==================================================

    print(
        "\n"
        + "=" * 72
    )

    print(
        "STEP 4: BUILD ROLLING TRAINING SET"
    )

    print(
        "=" * 72
    )

    training_df = (
        build_rolling_training_set(
            df=merged,
            completed_through_week=(
                completed_through_week
            ),
        )
    )

    print_training_summary(
        training_df=training_df,
        current_week=current_week,
        completed_through_week=(
            completed_through_week
        ),
    )

    # ==================================================
    # STEP 5
    # Retrain ECR-enhanced model
    # ==================================================

    print(
        "\n"
        + "=" * 72
    )

    print(
        "STEP 5: RETRAIN MODEL"
    )

    print(
        "=" * 72
    )

    model = train_rolling_model(
        training_df
    )

    # ==================================================
    # STEP 6
    # Save immutable version + latest
    # ==================================================

    print(
        "\n"
        + "=" * 72
    )

    print(
        "STEP 6: SAVE MODEL"
    )

    print(
        "=" * 72
    )

    paths = save_model(
        model=model,
        training_df=training_df,
        current_week=current_week,
        completed_through_week=(
            completed_through_week
        ),
    )

    # ==================================================
    # Finished
    # ==================================================

    print(
        "\n"
        + "=" * 72
    )

    print(
        "WEEKLY ML UPDATE COMPLETE"
    )

    print(
        "=" * 72
    )

    print(
        f"\nModel trained safely through:"
    )

    print(
        f"  2026 Week "
        f"{completed_through_week}"
    )

    print(
        f"\nNext prediction week:"
    )

    print(
        f"  2026 Week "
        f"{current_week}"
    )

    print(
        f"\nVersioned model:"
    )

    print(
        f"  "
        f"{paths['versioned_path']}"
    )

    print(
        f"\nLatest rolling model:"
    )

    print(
        f"  "
        f"{paths['latest_path']}"
    )

    print(
        "\nThe rolling model remains in "
        "SHADOW MODE."
    )

    print(
        "It is learning from new completed "
        "weeks but does not yet override "
        "live lineup decisions."
    )

# ==================================================
# Automatic cached weekly update
# ==================================================

def maybe_run_weekly_ml_update():
    """
    Make the ML retraining pipeline behave like a cache.

    Called whenever the main application / chatbot
    starts.

    Example:

        ESPN current week = 6

        Required training cutoff =
            Week 5

        If latest model already says:
            trained_through_week = 5

        -> SKIP

        If latest model says:
            trained_through_week = 4

        -> RUN UPDATE

    This means the expensive data refresh / retraining
    happens at most once when a new fantasy week begins.
    """

    # --------------------------------------------------
    # Determine what the latest safe training week is.
    # --------------------------------------------------

    try:

        cutoff = (
            get_training_cutoff()
        )

        current_week = int(
            cutoff[
                "current_week"
            ]
        )

        required_week = int(
            cutoff[
                "completed_through_week"
            ]
        )

    except Exception as e:

        print(
            "[ML UPDATE] Could not determine "
            f"current week: {e}"
        )

        print(
            "[ML UPDATE] Continuing without "
            "automatic retraining."
        )

        return False

    # --------------------------------------------------
    # No completed week yet.
    # --------------------------------------------------

    if required_week < 1:

        print(
            "[ML CACHE] No completed current-season "
            "week available for retraining yet."
        )

        return False

    # --------------------------------------------------
    # Check latest saved rolling model.
    # --------------------------------------------------

    if LATEST_MODEL_PATH.exists():

        try:

            package = joblib.load(
                LATEST_MODEL_PATH
            )

            model_season = (
                package.get(
                    "current_season"
                )
            )

            trained_through_week = (
                package.get(
                    "trained_through_week"
                )
            )

            if (
                model_season == 2026
                and trained_through_week
                is not None
                and int(
                    trained_through_week
                )
                >= required_week
            ):

                print(
                    "[ML CACHE] Rolling model "
                    f"current through 2026 Week "
                    f"{trained_through_week}"
                )

                return False

            print(
                "[ML STALE] Rolling model needs "
                f"2026 Week {required_week}"
            )

        except Exception as e:

            print(
                "[ML CACHE] Could not read latest "
                f"model metadata: {e}"
            )

            print(
                "[ML UPDATE] Rebuilding rolling model."
            )

    else:

        print(
            "[ML MISS] No latest rolling model found."
        )

    # --------------------------------------------------
    # Model is missing / stale.
    # Run the full weekly update.
    # --------------------------------------------------

    print(
        "[ML UPDATE] New completed week detected."
    )

    print(
        f"[ML UPDATE] Refreshing data and training "
        f"through 2026 Week {required_week}..."
    )

    try:

        run_weekly_ml_update()

    except Exception as e:

        # --------------------------------------------------
        # IMPORTANT:
        #
        # A failed shadow-model refresh should NOT prevent
        # the fantasy app/chatbot from working.
        #
        # The live decision engine currently does not rely
        # on this model anyway.
        # --------------------------------------------------

        print(
            "[ML UPDATE] Automatic update failed:"
        )

        print(
            f"  {e}"
        )

        print(
            "[ML UPDATE] Continuing with the "
            "existing model/data."
        )

        return False

    print(
        "[ML UPDATE] Automatic weekly update complete."
    )

    return True


if __name__ == "__main__":
    run_weekly_ml_update()