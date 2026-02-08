import os
import math
import httpx
from dotenv import load_dotenv
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from database import (
    init_db, close_db, get_consent, set_consent,
    delete_athlete_data, store_training_snapshot, store_race_result,
    get_dataset_stats, export_training_dataset,
)
from training_processor import process_training_data, detect_race_results

load_dotenv()


# --- App Lifecycle ---

@asynccontextmanager
async def lifespan(app):
    await init_db()
    yield
    await close_db()


app = FastAPI(title="Race Prophet API", lifespan=lifespan)

# --- Config ---
STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Strava OAuth ---

@app.get("/api/strava/auth-url")
def get_auth_url():
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
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.strava.com/api/v3/athlete",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch athlete")
    data = resp.json()

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
        "weight": data.get("weight"),
        "age": age,
        "sex": data.get("sex"),
    }


@app.get("/api/strava/activities")
async def get_activities(
    access_token: str = Query(...),
    weeks: int = Query(default=12, ge=1, le=52),
):
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

    runs = []
    weekly_distances = {}

    for act in all_activities:
        if act.get("type") != "Run":
            continue

        dist_km = act["distance"] / 1000
        time_sec = act["moving_time"]
        date = act["start_date"][:10]

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
            "workout_type": act.get("workout_type"),
        }
        run["is_race"] = act.get("workout_type") == 1
        runs.append(run)

    weekly_miles = [d / 1.60934 for d in weekly_distances.values()] if weekly_distances else [0]
    avg_weekly_miles = sum(weekly_miles) / len(weekly_miles)

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
    targets = {
        "1 Mile": 1.60934,
        "5K": 5.0,
        "10K": 10.0,
        "Half Marathon": 21.0975,
        "Marathon": 42.195,
    }
    best = {}
    for label, target_km in targets.items():
        tolerance = 0.15
        candidates = [
            r for r in runs
            if abs(r["distance_km"] - target_km) / target_km <= tolerance
        ]
        if candidates:
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


# --- Data Collection Endpoints ---

class ConsentRequest(BaseModel):
    athlete_id: int
    opted_in: bool


@app.post("/api/data/consent")
async def update_consent(req: ConsentRequest):
    await set_consent(req.athlete_id, req.opted_in)
    return {"status": "ok", "opted_in": req.opted_in}


@app.get("/api/data/consent")
async def check_consent(athlete_id: int = Query(...)):
    opted_in = await get_consent(athlete_id)
    return {"opted_in": opted_in}


@app.delete("/api/data/my-data")
async def delete_my_data(athlete_id: int = Query(...)):
    await delete_athlete_data(athlete_id)
    return {"status": "deleted"}


class ContributeRequest(BaseModel):
    athlete_id: int
    access_token: str
    ref_distance_km: float
    ref_time_seconds: int
    ref_date: Optional[str] = None
    goal_distance_km: float
    predicted_time_seconds: int
    age: Optional[int] = None
    experience: Optional[str] = "intermediate"
    gender: Optional[str] = None


@app.post("/api/data/contribute")
async def contribute_data(req: ContributeRequest):
    """Store a training snapshot when a user makes a prediction."""
    opted_in = await get_consent(req.athlete_id)
    if not opted_in:
        raise HTTPException(status_code=403, detail="User has not opted in")

    # Fetch recent activities for training snapshot
    after_ts = int((datetime.now() - timedelta(weeks=16)).timestamp())
    all_activities = []
    page = 1

    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                "https://www.strava.com/api/v3/athlete/activities",
                headers={"Authorization": f"Bearer {req.access_token}"},
                params={"after": after_ts, "per_page": 100, "page": page},
            )
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not batch:
                break
            all_activities.extend(batch)
            page += 1
            if len(batch) < 100:
                break

    training_data = process_training_data(all_activities, weeks=16)

    snapshot_id = await store_training_snapshot(
        strava_athlete_id=req.athlete_id,
        training_data=training_data,
        age=req.age,
        experience_level=req.experience,
        gender=req.gender,
    )

    return {
        "status": "ok",
        "snapshot_id": snapshot_id,
        "training_summary": {
            "avg_weekly_miles": training_data["avg_weekly_miles"],
            "total_runs": training_data["total_runs"],
            "longest_run_mi": training_data["longest_run_mi"],
        },
    }


class RaceResultRequest(BaseModel):
    athlete_id: int
    access_token: str
    snapshot_id: Optional[int] = None
    ref_distance_km: float
    ref_time_seconds: int
    ref_date: Optional[str] = None
    goal_distance_km: float
    goal_time_seconds: int
    goal_date: str
    goal_elevation_gain_ft: Optional[float] = None
    predicted_time_seconds: Optional[int] = None


@app.post("/api/data/race-result")
async def record_race_result(req: RaceResultRequest):
    """Record an actual race result to compare against prediction."""
    opted_in = await get_consent(req.athlete_id)
    if not opted_in:
        raise HTTPException(status_code=403, detail="User has not opted in")

    result_id = await store_race_result(
        strava_athlete_id=req.athlete_id,
        snapshot_id=req.snapshot_id,
        ref_distance_km=req.ref_distance_km,
        ref_time_seconds=req.ref_time_seconds,
        ref_date=req.ref_date,
        goal_distance_km=req.goal_distance_km,
        goal_time_seconds=req.goal_time_seconds,
        goal_date=req.goal_date,
        goal_elevation_gain_ft=req.goal_elevation_gain_ft,
        predicted_time_seconds=req.predicted_time_seconds,
    )

    error_min = None
    if req.predicted_time_seconds:
        error_sec = req.predicted_time_seconds - req.goal_time_seconds
        error_min = round(error_sec / 60, 1)

    return {
        "status": "ok",
        "result_id": result_id,
        "prediction_error_minutes": error_min,
    }


@app.get("/api/data/check-race")
async def check_for_race(
    access_token: str = Query(...),
    goal_distance_km: float = Query(...),
    after_date: str = Query(...),
):
    """Check if a user has completed their goal race since a given date."""
    after_ts = int(datetime.strptime(after_date, "%Y-%m-%d").timestamp())
    all_activities = []
    page = 1

    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                "https://www.strava.com/api/v3/athlete/activities",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"after": after_ts, "per_page": 50, "page": page},
            )
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not batch:
                break
            all_activities.extend(batch)
            page += 1
            if len(batch) < 50:
                break

    matches = detect_race_results(all_activities, goal_distance_km)
    return {"matches": matches}


# --- Dataset Stats ---

@app.get("/api/data/stats")
async def dataset_stats():
    stats = await get_dataset_stats()
    return stats


@app.get("/api/data/export")
async def dataset_export(admin_key: str = Query(...)):
    expected = os.environ.get("ADMIN_KEY", "")
    if not expected or admin_key != expected:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    data = await export_training_dataset()
    return {"count": len(data), "records": data}


@app.get("/api/health")
def health():
    return {"status": "ok", "strava_configured": bool(STRAVA_CLIENT_ID)}
