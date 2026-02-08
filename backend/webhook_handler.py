"""
Strava Webhook Handler.

Two responsibilities:
1. Log every run to training_log (continuous training data)
2. Check if a run matches a pending race prediction (date window + distance)
"""

import os
import time
import httpx
from datetime import datetime, timedelta
from typing import Optional

from database import (
    get_tokens, update_tokens, get_pending_predictions,
    match_prediction, store_race_result, get_consent,
    log_activity,
)

STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")


async def refresh_athlete_token(athlete_id: int) -> Optional[str]:
    tokens = await get_tokens(athlete_id)
    if not tokens:
        return None
    if tokens["expires_at"] > time.time() + 300:
        return tokens["access_token"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": STRAVA_CLIENT_ID,
                "client_secret": STRAVA_CLIENT_SECRET,
                "refresh_token": tokens["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code != 200:
        print(f"Token refresh failed for athlete {athlete_id}: {resp.text}")
        return None

    data = resp.json()
    await update_tokens(athlete_id, data["access_token"], data["refresh_token"], data["expires_at"])
    return data["access_token"]


async def fetch_activity(access_token: str, activity_id: int) -> Optional[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://www.strava.com/api/v3/activities/{activity_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        print(f"Failed to fetch activity {activity_id}: {resp.status_code}")
        return None
    return resp.json()


async def process_activity_event(athlete_id: int, activity_id: int) -> dict:
    """
    Process a new activity from Strava webhook:
    1. Always log the run to training_log (if opted in)
    2. Check if it matches a pending race prediction
    """
    result = {
        "athlete_id": athlete_id,
        "activity_id": activity_id,
        "action": "skipped",
        "logged": False,
        "reason": "",
    }

    # Check consent
    if not await get_consent(athlete_id):
        result["reason"] = "not opted in"
        return result

    # Get a valid access token
    access_token = await refresh_athlete_token(athlete_id)
    if not access_token:
        result["reason"] = "token refresh failed"
        return result

    # Fetch the activity details
    activity = await fetch_activity(access_token, activity_id)
    if not activity:
        result["reason"] = "activity fetch failed"
        return result

    # Only process runs
    if activity.get("type") != "Run":
        result["reason"] = "not a run"
        return result

    # --- Step 1: Always log the run ---
    log_id = await log_activity(athlete_id, activity)
    result["logged"] = log_id is not None
    if log_id:
        result["action"] = "logged"

    # --- Step 2: Check for race matches ---
    dist_km = activity.get("distance", 0) / 1000
    time_seconds = activity.get("moving_time", 0)
    activity_date_str = activity.get("start_date", "")[:10]
    elevation_ft = activity.get("total_elevation_gain", 0) * 3.281
    is_race = activity.get("workout_type") == 1

    try:
        activity_date = datetime.strptime(activity_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return result

    pending = await get_pending_predictions(athlete_id)
    if not pending:
        return result

    for pred in pending:
        # Check distance match
        goal_km = pred["goal_distance_km"]
        tolerance = pred["distance_tolerance"]
        if dist_km <= 0 or abs(dist_km - goal_km) / goal_km > tolerance:
            continue

        # If race date specified, only match within window
        if pred.get("goal_race_date"):
            race_date = pred["goal_race_date"]
            window = pred.get("match_window_days", 3)
            earliest = race_date - timedelta(days=1)
            latest = race_date + timedelta(days=window)
            if not (earliest <= activity_date <= latest):
                continue

        # Match found!
        race_result_id = await store_race_result(
            strava_athlete_id=athlete_id,
            snapshot_id=pred["snapshot_id"],
            ref_distance_km=pred["ref_distance_km"],
            ref_time_seconds=pred["ref_time_seconds"],
            ref_date=pred["ref_date"].isoformat() if pred["ref_date"] else None,
            goal_distance_km=dist_km,
            goal_time_seconds=time_seconds,
            goal_date=activity_date_str,
            goal_elevation_gain_ft=elevation_ft,
            predicted_time_seconds=pred["predicted_time_seconds"],
            source="webhook",
        )

        await match_prediction(pred["id"], activity_id)

        error_seconds = pred["predicted_time_seconds"] - time_seconds
        result["action"] = "matched"
        result["prediction_id"] = pred["id"]
        result["race_result_id"] = race_result_id
        result["goal_distance_km"] = pred["goal_distance_km"]
        result["predicted_seconds"] = pred["predicted_time_seconds"]
        result["actual_seconds"] = time_seconds
        result["error_seconds"] = error_seconds
        result["is_race"] = is_race
        result["race_name"] = pred.get("goal_race_name", "")

        print(
            f"WEBHOOK MATCH: athlete={athlete_id}, "
            f"race={pred.get('goal_race_name', 'unknown')}, "
            f"goal={goal_km}km, predicted={pred['predicted_time_seconds']}s, "
            f"actual={time_seconds}s, error={error_seconds}s"
        )
        return result

    return result