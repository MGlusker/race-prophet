import os
import math
import httpx
from dotenv import load_dotenv
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

load_dotenv()

app = FastAPI(title="Race Prophet API")

# --- Config ---
STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Strava OAuth ---

@app.get("/api/strava/auth-url")
def get_auth_url():
    """Return the Strava OAuth authorization URL."""
    if not STRAVA_CLIENT_ID:
        raise HTTPException(status_code=500, detail="STRAVA_CLIENT_ID not configured")
    redirect_uri = f"{FRONTEND_URL}/callback"
    scope = "read,activity:read_all,profile:read_all"
    url = (
        f"https://www.strava.com/oauth/authorize"
        f"?client_id={STRAVA_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&approval_prompt=auto"
        f"&scope={scope}"
    )
    return {"url": url}


@app.post("/api/strava/token")
async def exchange_token(code: str = Query(...)):
    """Exchange authorization code for access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": STRAVA_CLIENT_ID,
                "client_secret": STRAVA_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Strava token error: {resp.text}")
    data = resp.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": data["expires_at"],
        "athlete": {
            "id": data["athlete"]["id"],
            "firstname": data["athlete"]["firstname"],
            "lastname": data["athlete"]["lastname"],
            "profile": data["athlete"].get("profile_medium", ""),
        },
    }


@app.post("/api/strava/refresh")
async def refresh_token(refresh_token: str = Query(...)):
    """Refresh an expired access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": STRAVA_CLIENT_ID,
                "client_secret": STRAVA_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Token refresh failed")
    data = resp.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": data["expires_at"],
    }


# --- Strava Data ---

@app.get("/api/strava/athlete")
async def get_athlete(access_token: str = Query(...)):
    """Get full athlete profile (includes weight, age info)."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.strava.com/api/v3/athlete",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch athlete")
    data = resp.json()

    # Calculate age from birthday if available
    age = None
    if data.get("birthday"):
        try:
            bday = datetime.strptime(data["birthday"], "%Y-%m-%d")
            age = (datetime.now() - bday).days // 365
        except Exception:
            pass

    return {
        "id": data["id"],
        "firstname": data.get("firstname", ""),
        "lastname": data.get("lastname", ""),
        "profile": data.get("profile_medium", ""),
        "city": data.get("city", ""),
        "state": data.get("state", ""),
        "country": data.get("country", ""),
        "weight": data.get("weight"),  # kg
        "age": age,
    }


@app.get("/api/strava/activities")
async def get_activities(
    access_token: str = Query(...),
    weeks: int = Query(default=12, ge=1, le=52),
):
    """Fetch recent run activities and compute stats."""
    after_ts = int((datetime.now() - timedelta(weeks=weeks)).timestamp())
    all_activities = []
    page = 1

    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                "https://www.strava.com/api/v3/athlete/activities",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "after": after_ts,
                    "per_page": 100,
                    "page": page,
                    "type": "Run",
                },
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=400, detail="Failed to fetch activities")
            batch = resp.json()
            if not batch:
                break
            all_activities.extend(batch)
            page += 1
            if len(batch) < 100:
                break

    # Process activities
    runs = []
    weekly_distances = {}

    for act in all_activities:
        if act.get("type") != "Run":
            continue

        dist_km = act["distance"] / 1000
        time_sec = act["moving_time"]
        date = act["start_date"][:10]

        # Determine week key (ISO week)
        dt = datetime.strptime(date, "%Y-%m-%d")
        week_key = dt.strftime("%Y-W%W")
        weekly_distances[week_key] = weekly_distances.get(week_key, 0) + dist_km

        run = {
            "id": act["id"],
            "name": act["name"],
            "date": date,
            "distance_km": round(dist_km, 2),
            "distance_mi": round(dist_km / 1.60934, 2),
            "time_seconds": time_sec,
            "time_formatted": format_time(time_sec),
            "pace_per_mile": format_time(int(time_sec / dist_km * 1.60934)) if dist_km > 0 else "N/A",
            "pace_per_km": format_time(int(time_sec / dist_km)) if dist_km > 0 else "N/A",
            "elevation_gain": act.get("total_elevation_gain", 0),
            "average_heartrate": act.get("average_heartrate"),
            "workout_type": act.get("workout_type"),  # 1=race in Strava
        }

        # Flag likely races
        run["is_race"] = act.get("workout_type") == 1

        runs.append(run)

    # Compute weekly mileage average
    weekly_miles = [d / 1.60934 for d in weekly_distances.values()] if weekly_distances else [0]
    avg_weekly_miles = sum(weekly_miles) / len(weekly_miles)

    # Find best race-like efforts for common distances
    races = [r for r in runs if r["is_race"]]
    best_efforts = find_best_efforts(runs)

    return {
        "total_runs": len(runs),
        "weeks_analyzed": weeks,
        "avg_weekly_miles": round(avg_weekly_miles, 1),
        "weekly_mileage": [
            {"week": k, "miles": round(v / 1.60934, 1)}
            for k, v in sorted(weekly_distances.items())
        ],
        "races": sorted(races, key=lambda r: r["date"], reverse=True),
        "best_efforts": best_efforts,
        "recent_runs": sorted(runs, key=lambda r: r["date"], reverse=True)[:20],
    }


def find_best_efforts(runs):
    """Find the fastest effort near each standard race distance."""
    targets = {
        "1 Mile": 1.60934,
        "5K": 5.0,
        "10K": 10.0,
        "Half Marathon": 21.0975,
        "Marathon": 42.195,
    }
    best = {}
    for label, target_km in targets.items():
        tolerance = 0.15  # 15% tolerance
        candidates = [
            r for r in runs
            if abs(r["distance_km"] - target_km) / target_km <= tolerance
        ]
        if candidates:
            # Fastest by time (normalized to exact distance)
            fastest = min(candidates, key=lambda r: r["time_seconds"] / r["distance_km"])
            best[label] = {
                "distance_km": target_km,
                "actual_distance_km": fastest["distance_km"],
                "time_seconds": fastest["time_seconds"],
                "time_formatted": fastest["time_formatted"],
                "date": fastest["date"],
                "name": fastest["name"],
                "pace_per_mile": fastest["pace_per_mile"],
            }
    return best


def format_time(total_seconds):
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# --- Prediction Engine ---

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


class PredictionRequest(BaseModel):
    race_time_seconds: int
    race_distance_km: float
    goal_distance_km: float
    weekly_miles: Optional[float] = 0
    age: Optional[int] = 0
    experience: Optional[str] = "intermediate"


@app.post("/api/predict")
def predict(req: PredictionRequest):
    """Run prediction with Riegel formula + adjustments."""
    exponent = 1.06

    if req.weekly_miles:
        if req.weekly_miles >= 60:
            exponent -= 0.03
        elif req.weekly_miles >= 45:
            exponent -= 0.02
        elif req.weekly_miles >= 30:
            exponent -= 0.01
        elif req.weekly_miles < 15:
            exponent += 0.02

    exp_factor = EXPERIENCE_FACTORS.get(req.experience, 1.0)

    age_factor = 1.0
    if req.age and req.age > 0:
        if req.age < 20:
            age_factor = 1.03
        elif req.age <= 35:
            age_factor = 1.0
        elif req.age <= 45:
            age_factor = 1.0 + (req.age - 35) * 0.004
        elif req.age <= 55:
            age_factor = 1.04 + (req.age - 45) * 0.006
        elif req.age <= 65:
            age_factor = 1.10 + (req.age - 55) * 0.008
        else:
            age_factor = 1.18 + (req.age - 65) * 0.01

    raw = req.race_time_seconds * math.pow(
        req.goal_distance_km / req.race_distance_km, exponent
    )
    adjusted = raw * exp_factor * age_factor

    dist_ratio = req.goal_distance_km / req.race_distance_km
    uncertainty = min(0.06, 0.03 + abs(math.log(dist_ratio)) * 0.008)

    pace_per_mile = (adjusted / req.goal_distance_km) * 1.60934
    pace_per_km = adjusted / req.goal_distance_km

    # Equivalent times for all distances
    equivalents = {}
    for label, dist in DISTANCES.items():
        eq_raw = req.race_time_seconds * math.pow(dist / req.race_distance_km, exponent)
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


@app.get("/api/health")
def health():
    return {"status": "ok", "strava_configured": bool(STRAVA_CLIENT_ID)}
