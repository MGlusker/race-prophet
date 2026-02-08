"""
Process raw Strava activities into anonymized training features
for storage in the database.
"""

import json
from datetime import datetime, timedelta
from typing import Optional


def process_training_data(activities: list, weeks: int = 16) -> dict:
    """
    Convert a list of Strava run activities into aggregate training features.

    Args:
        activities: List of activity dicts from Strava API
        weeks: Number of weeks to analyze

    Returns:
        Dict of anonymized training features
    """
    if not activities:
        return _empty_snapshot(weeks)

    runs = [a for a in activities if a.get("type") == "Run"]
    if not runs:
        return _empty_snapshot(weeks)

    # Calculate per-run stats
    distances_mi = []
    paces_sec_per_mile = []
    elevations_ft = []
    heartrates = []
    weekly_distances = {}

    for run in runs:
        dist_km = run.get("distance", 0) / 1000
        dist_mi = dist_km / 1.60934
        time_sec = run.get("moving_time", 0)
        date_str = run.get("start_date", "")[:10]

        if dist_km <= 0 or time_sec <= 0:
            continue

        distances_mi.append(dist_mi)

        # Pace
        pace = (time_sec / dist_km) * 1.60934  # sec per mile
        paces_sec_per_mile.append(pace)

        # Elevation (meters -> feet)
        elev = run.get("total_elevation_gain", 0) * 3.281
        elevations_ft.append(elev)

        # Heart rate
        hr = run.get("average_heartrate")
        if hr:
            heartrates.append(hr)

        # Weekly buckets
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            week_key = dt.strftime("%Y-W%W")
            weekly_distances[week_key] = weekly_distances.get(week_key, 0) + dist_mi
        except (ValueError, TypeError):
            pass

    if not distances_mi:
        return _empty_snapshot(weeks)

    # Weekly mileage stats
    weekly_miles = list(weekly_distances.values())
    avg_weekly = sum(weekly_miles) / len(weekly_miles) if weekly_miles else 0
    peak_weekly = max(weekly_miles) if weekly_miles else 0

    # Build weekly progression (sorted chronologically)
    sorted_weeks = sorted(weekly_distances.items())
    weekly_progression = json.dumps([
        {"week": w, "miles": round(m, 1)} for w, m in sorted_weeks
    ])

    return {
        "weeks_of_data": weeks,
        "avg_weekly_miles": round(avg_weekly, 1),
        "peak_weekly_miles": round(peak_weekly, 1),
        "total_miles": round(sum(distances_mi), 1),
        "total_runs": len(distances_mi),
        "avg_run_distance_mi": round(sum(distances_mi) / len(distances_mi), 1),
        "longest_run_mi": round(max(distances_mi), 1),
        "avg_pace_per_mile_sec": round(sum(paces_sec_per_mile) / len(paces_sec_per_mile)),
        "fastest_pace_per_mile_sec": round(min(paces_sec_per_mile)),
        "total_elevation_gain_ft": round(sum(elevations_ft), 0),
        "avg_elevation_per_run_ft": round(sum(elevations_ft) / len(elevations_ft), 0) if elevations_ft else 0,
        "runs_with_heartrate": len(heartrates),
        "avg_heartrate": round(sum(heartrates) / len(heartrates), 1) if heartrates else None,
        "weekly_mileage_progression": weekly_progression,
    }


def detect_race_results(activities: list, goal_distance_km: float, tolerance: float = 0.15) -> list:
    """
    Look for activities that match a goal race distance.
    Used to find actual race results after a prediction was made.

    Args:
        activities: List of Strava activities
        goal_distance_km: Target race distance in km
        tolerance: Percentage tolerance for distance matching

    Returns:
        List of matching activities sorted by date (newest first)
    """
    matches = []
    for act in activities:
        if act.get("type") != "Run":
            continue

        dist_km = act.get("distance", 0) / 1000
        if dist_km <= 0:
            continue

        # Check if distance matches within tolerance
        if abs(dist_km - goal_distance_km) / goal_distance_km <= tolerance:
            # Prefer activities tagged as races
            is_race = act.get("workout_type") == 1

            matches.append({
                "activity_id": act["id"],
                "name": act.get("name", ""),
                "date": act.get("start_date", "")[:10],
                "distance_km": round(dist_km, 2),
                "time_seconds": act.get("moving_time", 0),
                "elevation_gain_ft": round(act.get("total_elevation_gain", 0) * 3.281, 0),
                "is_race": is_race,
                "average_heartrate": act.get("average_heartrate"),
            })

    # Sort: races first, then by date descending
    matches.sort(key=lambda x: (not x["is_race"], x["date"]), reverse=False)
    matches.sort(key=lambda x: x["date"], reverse=True)

    return matches


def _empty_snapshot(weeks: int) -> dict:
    """Return empty training snapshot."""
    return {
        "weeks_of_data": weeks,
        "avg_weekly_miles": 0,
        "peak_weekly_miles": 0,
        "total_miles": 0,
        "total_runs": 0,
        "avg_run_distance_mi": 0,
        "longest_run_mi": 0,
        "avg_pace_per_mile_sec": 0,
        "fastest_pace_per_mile_sec": 0,
        "total_elevation_gain_ft": 0,
        "avg_elevation_per_run_ft": 0,
        "runs_with_heartrate": 0,
        "avg_heartrate": None,
        "weekly_mileage_progression": "[]",
    }
