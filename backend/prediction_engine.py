"""
Prediction engine extracted from main.py to avoid circular imports.
Pure functions only — no FastAPI or database dependencies.
"""

import math

DISTANCES = {
    "1 Mile": 1.60934,
    "5K": 5.0,
    "10K": 10.0,
    "15K": 15.0,
    "Half Marathon": 21.0975,
    "Marathon": 42.195,
    "50K": 50.0,
}

EXPERIENCE_FACTORS = {
    "beginner": 1.06,
    "intermediate": 1.0,
    "advanced": 0.97,
    "elite": 0.94,
}


def format_time(total_seconds):
    total_seconds = int(round(total_seconds))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def calculate_prediction(
    race_time_seconds: int,
    race_distance_km: float,
    goal_distance_km: float,
    weekly_miles: float = 0,
    age: int = 0,
    experience: str = "intermediate",
) -> dict:
    """
    Riegel formula with training volume, age, and experience adjustments.
    Returns dict with predicted time, confidence range, pace, and equivalents.
    """
    exponent = 1.06

    if weekly_miles:
        if weekly_miles >= 60:
            exponent -= 0.03
        elif weekly_miles >= 45:
            exponent -= 0.02
        elif weekly_miles >= 30:
            exponent -= 0.01
        elif weekly_miles < 15:
            exponent += 0.02

    exp_factor = EXPERIENCE_FACTORS.get(experience, 1.0)

    age_factor = 1.0
    if age and age > 0:
        if age < 20:
            age_factor = 1.03
        elif age <= 35:
            age_factor = 1.0
        elif age <= 45:
            age_factor = 1.0 + (age - 35) * 0.004
        elif age <= 55:
            age_factor = 1.04 + (age - 45) * 0.006
        elif age <= 65:
            age_factor = 1.10 + (age - 55) * 0.008
        else:
            age_factor = 1.18 + (age - 65) * 0.01

    raw = race_time_seconds * math.pow(
        goal_distance_km / race_distance_km, exponent
    )
    adjusted = raw * exp_factor * age_factor

    dist_ratio = goal_distance_km / race_distance_km
    uncertainty = min(0.06, 0.03 + abs(math.log(dist_ratio)) * 0.008)

    pace_per_mile = (adjusted / goal_distance_km) * 1.60934
    pace_per_km = adjusted / goal_distance_km

    equivalents = {}
    for label, dist in DISTANCES.items():
        eq_raw = race_time_seconds * math.pow(dist / race_distance_km, exponent)
        eq_adj = eq_raw * exp_factor * age_factor
        equivalents[label] = {
            "time_seconds": round(eq_adj),
            "time_formatted": format_time(round(eq_adj)),
            "pace_per_mile": format_time(round((eq_adj / dist) * 1.60934)),
        }

    return {
        "predicted_seconds": round(adjusted),
        "predicted_formatted": format_time(round(adjusted)),
        "low_seconds": round(adjusted * (1 - uncertainty)),
        "low_formatted": format_time(round(adjusted * (1 - uncertainty))),
        "high_seconds": round(adjusted * (1 + uncertainty)),
        "high_formatted": format_time(round(adjusted * (1 + uncertainty))),
        "uncertainty_pct": round(uncertainty * 100, 1),
        "pace_per_mile": format_time(round(pace_per_mile)),
        "pace_per_km": format_time(round(pace_per_km)),
        "equivalents": equivalents,
    }
