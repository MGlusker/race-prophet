"""
Database module for Race Prophet.
Stores anonymized training + race data for model improvement.
Includes encrypted token storage for webhook-based race result detection
and continuous training log ingestion.
"""

import os
import hashlib
import asyncpg
from datetime import datetime, timedelta
from typing import Optional
from cryptography.fernet import Fernet

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")

# --- Encryption ---

_fernet = None

def get_fernet():
    global _fernet
    if _fernet is None and ENCRYPTION_KEY:
        _fernet = Fernet(ENCRYPTION_KEY.encode())
    return _fernet

def encrypt_token(token: str) -> str:
    f = get_fernet()
    if not f:
        return ""
    return f.encrypt(token.encode()).decode()

def decrypt_token(encrypted: str) -> str:
    f = get_fernet()
    if not f or not encrypted:
        return ""
    return f.decrypt(encrypted.encode()).decode()


# --- Connection Pool ---

pool: Optional[asyncpg.Pool] = None


async def init_db():
    global pool
    if not DATABASE_URL:
        print("WARNING: DATABASE_URL not set, data collection disabled")
        return
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    await create_tables()


async def close_db():
    global pool
    if pool:
        await pool.close()


async def create_tables():
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS data_consent (
                athlete_hash TEXT PRIMARY KEY,
                opted_in BOOLEAN NOT NULL DEFAULT FALSE,
                opted_in_at TIMESTAMPTZ,
                opted_out_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS training_snapshots (
                id SERIAL PRIMARY KEY,
                athlete_hash TEXT NOT NULL,
                snapshot_date DATE NOT NULL,
                weeks_of_data INT,
                avg_weekly_miles REAL,
                peak_weekly_miles REAL,
                total_miles REAL,
                total_runs INT,
                avg_run_distance_mi REAL,
                longest_run_mi REAL,
                avg_pace_per_mile_sec INT,
                fastest_pace_per_mile_sec INT,
                total_elevation_gain_ft REAL,
                avg_elevation_per_run_ft REAL,
                runs_with_heartrate INT,
                avg_heartrate REAL,
                weekly_mileage_progression JSONB,
                age_bucket TEXT,
                experience_level TEXT,
                gender TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(athlete_hash, snapshot_date)
            );

            CREATE TABLE IF NOT EXISTS race_results (
                id SERIAL PRIMARY KEY,
                snapshot_id INT REFERENCES training_snapshots(id),
                athlete_hash TEXT NOT NULL,
                ref_distance_km REAL NOT NULL,
                ref_time_seconds INT NOT NULL,
                ref_date DATE,
                goal_distance_km REAL NOT NULL,
                goal_time_seconds INT NOT NULL,
                goal_date DATE NOT NULL,
                goal_elevation_gain_ft REAL,
                predicted_time_seconds INT,
                prediction_error_seconds INT,
                prediction_error_pct REAL,
                model_version TEXT DEFAULT 'riegel_v1',
                source TEXT DEFAULT 'manual',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS model_evaluations (
                id SERIAL PRIMARY KEY,
                model_version TEXT NOT NULL,
                eval_date DATE NOT NULL,
                sample_count INT,
                mae_seconds REAL,
                mape REAL,
                median_error_seconds REAL,
                p90_error_seconds REAL,
                distance_pair TEXT,
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS athlete_tokens (
                athlete_id BIGINT PRIMARY KEY,
                athlete_hash TEXT NOT NULL,
                encrypted_access_token TEXT NOT NULL,
                encrypted_refresh_token TEXT NOT NULL,
                expires_at BIGINT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS pending_predictions (
                id SERIAL PRIMARY KEY,
                athlete_id BIGINT NOT NULL,
                athlete_hash TEXT NOT NULL,
                snapshot_id INT REFERENCES training_snapshots(id),
                ref_distance_km REAL NOT NULL,
                ref_time_seconds INT NOT NULL,
                ref_date DATE,
                goal_distance_km REAL NOT NULL,
                predicted_time_seconds INT NOT NULL,
                goal_race_name TEXT,
                goal_race_date DATE,
                match_window_days INT DEFAULT 3,
                distance_tolerance REAL DEFAULT 0.15,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                matched_at TIMESTAMPTZ,
                matched_activity_id BIGINT
            );

            CREATE TABLE IF NOT EXISTS webhook_state (
                id INT PRIMARY KEY DEFAULT 1,
                subscription_id BIGINT,
                verify_token TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Continuous training log from webhook events
            CREATE TABLE IF NOT EXISTS training_log (
                id SERIAL PRIMARY KEY,
                athlete_hash TEXT NOT NULL,
                activity_date DATE NOT NULL,
                distance_km REAL NOT NULL,
                moving_time_seconds INT NOT NULL,
                elapsed_time_seconds INT,
                elevation_gain_m REAL,
                average_heartrate REAL,
                max_heartrate REAL,
                workout_type INT,
                is_race BOOLEAN DEFAULT FALSE,
                pace_per_km_sec REAL,
                suffer_score INT,
                strava_activity_id BIGINT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(athlete_hash, strava_activity_id)
            );

            -- Indexes
            CREATE INDEX IF NOT EXISTS idx_snapshots_athlete ON training_snapshots(athlete_hash);
            CREATE INDEX IF NOT EXISTS idx_results_athlete ON race_results(athlete_hash);
            CREATE INDEX IF NOT EXISTS idx_results_distances ON race_results(ref_distance_km, goal_distance_km);
            CREATE INDEX IF NOT EXISTS idx_pending_athlete ON pending_predictions(athlete_id, status);
            CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_predictions(status, expires_at);
            CREATE INDEX IF NOT EXISTS idx_tokens_hash ON athlete_tokens(athlete_hash);
            CREATE INDEX IF NOT EXISTS idx_training_log_athlete ON training_log(athlete_hash, activity_date);
            CREATE INDEX IF NOT EXISTS idx_training_log_activity ON training_log(strava_activity_id);
        """)


# --- Helpers ---

def hash_athlete_id(strava_athlete_id: int) -> str:
    salt = os.environ.get("HASH_SALT", "race-prophet-2024")
    raw = f"{salt}:{strava_athlete_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def age_to_bucket(age: Optional[int]) -> Optional[str]:
    if not age:
        return None
    if age < 18: return "under-18"
    elif age <= 24: return "18-24"
    elif age <= 34: return "25-34"
    elif age <= 44: return "35-44"
    elif age <= 54: return "45-54"
    elif age <= 64: return "55-64"
    else: return "65+"


# --- Consent ---

async def get_consent(strava_athlete_id: int) -> bool:
    if not pool: return False
    athlete_hash = hash_athlete_id(strava_athlete_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT opted_in FROM data_consent WHERE athlete_hash = $1", athlete_hash)
        return row["opted_in"] if row else False


async def set_consent(strava_athlete_id: int, opted_in: bool):
    if not pool: return
    athlete_hash = hash_athlete_id(strava_athlete_id)
    now = datetime.utcnow()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO data_consent (athlete_hash, opted_in, opted_in_at, opted_out_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (athlete_hash) DO UPDATE SET
                opted_in = $2,
                opted_in_at = CASE WHEN $2 THEN $3 ELSE data_consent.opted_in_at END,
                opted_out_at = CASE WHEN NOT $2 THEN $4 ELSE data_consent.opted_out_at END
        """, athlete_hash, opted_in,
             now if opted_in else None,
             now if not opted_in else None)


async def delete_athlete_data(strava_athlete_id: int):
    if not pool: return
    athlete_hash = hash_athlete_id(strava_athlete_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM race_results WHERE athlete_hash = $1", athlete_hash)
            await conn.execute("DELETE FROM training_snapshots WHERE athlete_hash = $1", athlete_hash)
            await conn.execute("DELETE FROM training_log WHERE athlete_hash = $1", athlete_hash)
            await conn.execute("DELETE FROM pending_predictions WHERE athlete_hash = $1", athlete_hash)
            await conn.execute("DELETE FROM athlete_tokens WHERE athlete_hash = $1", athlete_hash)
            await conn.execute("DELETE FROM data_consent WHERE athlete_hash = $1", athlete_hash)


# --- Token Storage ---

async def store_tokens(athlete_id: int, access_token: str, refresh_token: str, expires_at: int):
    if not pool or not get_fernet(): return
    athlete_hash = hash_athlete_id(athlete_id)
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO athlete_tokens (athlete_id, athlete_hash, encrypted_access_token,
                                        encrypted_refresh_token, expires_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT (athlete_id) DO UPDATE SET
                encrypted_access_token = $3, encrypted_refresh_token = $4,
                expires_at = $5, updated_at = NOW()
        """, athlete_id, athlete_hash,
             encrypt_token(access_token), encrypt_token(refresh_token), expires_at)


async def get_tokens(athlete_id: int) -> Optional[dict]:
    if not pool or not get_fernet(): return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM athlete_tokens WHERE athlete_id = $1", athlete_id)
        if not row: return None
        return {
            "athlete_id": row["athlete_id"],
            "access_token": decrypt_token(row["encrypted_access_token"]),
            "refresh_token": decrypt_token(row["encrypted_refresh_token"]),
            "expires_at": row["expires_at"],
        }


async def update_tokens(athlete_id: int, access_token: str, refresh_token: str, expires_at: int):
    await store_tokens(athlete_id, access_token, refresh_token, expires_at)


# --- Training Log (continuous ingestion) ---

async def log_activity(athlete_id: int, activity: dict) -> Optional[int]:
    """Log a single activity from webhook. Returns log ID or None."""
    if not pool: return None
    if not await get_consent(athlete_id): return None

    athlete_hash = hash_athlete_id(athlete_id)
    dist_km = activity.get("distance", 0) / 1000
    if dist_km <= 0: return None

    moving_time = activity.get("moving_time", 0)
    pace = moving_time / dist_km if dist_km > 0 else None

    try:
        activity_date = datetime.strptime(activity.get("start_date", "")[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO training_log (
                athlete_hash, activity_date, distance_km, moving_time_seconds,
                elapsed_time_seconds, elevation_gain_m, average_heartrate,
                max_heartrate, workout_type, is_race, pace_per_km_sec,
                suffer_score, strava_activity_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (athlete_hash, strava_activity_id) DO UPDATE SET
                distance_km = EXCLUDED.distance_km,
                moving_time_seconds = EXCLUDED.moving_time_seconds,
                elapsed_time_seconds = EXCLUDED.elapsed_time_seconds,
                elevation_gain_m = EXCLUDED.elevation_gain_m,
                average_heartrate = EXCLUDED.average_heartrate,
                max_heartrate = EXCLUDED.max_heartrate,
                workout_type = EXCLUDED.workout_type,
                is_race = EXCLUDED.is_race,
                pace_per_km_sec = EXCLUDED.pace_per_km_sec
            RETURNING id
        """,
            athlete_hash, activity_date, dist_km, moving_time,
            activity.get("elapsed_time"),
            activity.get("total_elevation_gain"),
            activity.get("average_heartrate"),
            activity.get("max_heartrate"),
            activity.get("workout_type"),
            activity.get("workout_type") == 1,
            pace,
            activity.get("suffer_score"),
            activity.get("id"),
        )
        return row["id"] if row else None


async def get_training_log_stats(athlete_hash: str, weeks: int = 16) -> dict:
    """Compute training stats from the continuous log for a given athlete."""
    if not pool:
        return {}
    cutoff = datetime.utcnow().date() - timedelta(weeks=weeks)
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM training_log
            WHERE athlete_hash = $1 AND activity_date >= $2
            ORDER BY activity_date
        """, athlete_hash, cutoff)

    if not rows:
        return {}

    total_km = sum(r["distance_km"] for r in rows)
    total_mi = total_km / 1.60934
    distances_mi = [r["distance_km"] / 1.60934 for r in rows]
    paces = [r["pace_per_km_sec"] * 1.60934 for r in rows if r["pace_per_km_sec"]]
    heartrates = [r["average_heartrate"] for r in rows if r["average_heartrate"]]
    elevations = [r["elevation_gain_m"] * 3.281 for r in rows if r["elevation_gain_m"]]

    # Weekly buckets
    weekly = {}
    for r in rows:
        wk = r["activity_date"].strftime("%Y-W%W")
        weekly[wk] = weekly.get(wk, 0) + r["distance_km"] / 1.60934

    weekly_values = list(weekly.values())

    return {
        "total_runs": len(rows),
        "total_miles": round(total_mi, 1),
        "avg_weekly_miles": round(total_mi / weeks, 1),
        "peak_weekly_miles": round(max(weekly_values), 1) if weekly_values else 0,
        "avg_run_distance_mi": round(sum(distances_mi) / len(distances_mi), 1),
        "longest_run_mi": round(max(distances_mi), 1),
        "avg_pace_per_mile_sec": round(sum(paces) / len(paces)) if paces else None,
        "fastest_pace_per_mile_sec": round(min(paces)) if paces else None,
        "avg_heartrate": round(sum(heartrates) / len(heartrates), 1) if heartrates else None,
        "total_elevation_gain_ft": round(sum(elevations), 0) if elevations else 0,
        "races_logged": sum(1 for r in rows if r["is_race"]),
        "weeks_of_data": weeks,
    }


# --- Pending Predictions ---

async def store_pending_prediction(
    athlete_id: int,
    snapshot_id: Optional[int],
    ref_distance_km: float,
    ref_time_seconds: int,
    ref_date: Optional[str],
    goal_distance_km: float,
    predicted_time_seconds: int,
    goal_race_name: Optional[str] = None,
    goal_race_date: Optional[str] = None,
) -> Optional[int]:
    if not pool: return None
    athlete_hash = hash_athlete_id(athlete_id)

    # Expiration: 7 days after race date if provided, otherwise 365 days
    if goal_race_date:
        race_dt = datetime.strptime(goal_race_date, "%Y-%m-%d")
        expires_at = race_dt + timedelta(days=7)
    else:
        expires_at = datetime.utcnow() + timedelta(days=365)

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO pending_predictions (
                athlete_id, athlete_hash, snapshot_id,
                ref_distance_km, ref_time_seconds, ref_date,
                goal_distance_km, predicted_time_seconds,
                goal_race_name, goal_race_date, expires_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING id
        """,
            athlete_id, athlete_hash, snapshot_id,
            ref_distance_km, ref_time_seconds,
            datetime.strptime(ref_date, "%Y-%m-%d").date() if ref_date else None,
            goal_distance_km, predicted_time_seconds,
            goal_race_name,
            datetime.strptime(goal_race_date, "%Y-%m-%d").date() if goal_race_date else None,
            expires_at,
        )
        return row["id"] if row else None


async def get_pending_predictions(athlete_id: int) -> list:
    if not pool: return []
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM pending_predictions
            WHERE athlete_id = $1 AND status = 'pending' AND expires_at > NOW()
            ORDER BY created_at DESC
        """, athlete_id)
        return [dict(r) for r in rows]


async def match_prediction(prediction_id: int, activity_id: int):
    if not pool: return
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE pending_predictions
            SET status = 'matched', matched_at = NOW(), matched_activity_id = $2
            WHERE id = $1
        """, prediction_id, activity_id)


async def expire_old_predictions():
    if not pool: return
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE pending_predictions SET status = 'expired'
            WHERE status = 'pending' AND expires_at < NOW()
        """)


# --- Training Snapshots ---

async def store_training_snapshot(
    strava_athlete_id: int, training_data: dict,
    age: Optional[int] = None, experience_level: Optional[str] = None,
    gender: Optional[str] = None,
) -> Optional[int]:
    if not pool: return None
    if not await get_consent(strava_athlete_id): return None
    athlete_hash = hash_athlete_id(strava_athlete_id)
    today = datetime.utcnow().date()

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO training_snapshots (
                athlete_hash, snapshot_date,
                weeks_of_data, avg_weekly_miles, peak_weekly_miles,
                total_miles, total_runs, avg_run_distance_mi, longest_run_mi,
                avg_pace_per_mile_sec, fastest_pace_per_mile_sec,
                total_elevation_gain_ft, avg_elevation_per_run_ft,
                runs_with_heartrate, avg_heartrate,
                weekly_mileage_progression,
                age_bucket, experience_level, gender
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
            ON CONFLICT (athlete_hash, snapshot_date) DO UPDATE SET
                weeks_of_data=EXCLUDED.weeks_of_data, avg_weekly_miles=EXCLUDED.avg_weekly_miles,
                peak_weekly_miles=EXCLUDED.peak_weekly_miles, total_miles=EXCLUDED.total_miles,
                total_runs=EXCLUDED.total_runs, avg_run_distance_mi=EXCLUDED.avg_run_distance_mi,
                longest_run_mi=EXCLUDED.longest_run_mi,
                avg_pace_per_mile_sec=EXCLUDED.avg_pace_per_mile_sec,
                fastest_pace_per_mile_sec=EXCLUDED.fastest_pace_per_mile_sec,
                total_elevation_gain_ft=EXCLUDED.total_elevation_gain_ft,
                avg_elevation_per_run_ft=EXCLUDED.avg_elevation_per_run_ft,
                runs_with_heartrate=EXCLUDED.runs_with_heartrate,
                avg_heartrate=EXCLUDED.avg_heartrate,
                weekly_mileage_progression=EXCLUDED.weekly_mileage_progression,
                age_bucket=EXCLUDED.age_bucket, experience_level=EXCLUDED.experience_level,
                gender=EXCLUDED.gender
            RETURNING id
        """,
            athlete_hash, today,
            training_data.get("weeks_of_data"), training_data.get("avg_weekly_miles"),
            training_data.get("peak_weekly_miles"), training_data.get("total_miles"),
            training_data.get("total_runs"), training_data.get("avg_run_distance_mi"),
            training_data.get("longest_run_mi"), training_data.get("avg_pace_per_mile_sec"),
            training_data.get("fastest_pace_per_mile_sec"),
            training_data.get("total_elevation_gain_ft"),
            training_data.get("avg_elevation_per_run_ft"),
            training_data.get("runs_with_heartrate"), training_data.get("avg_heartrate"),
            training_data.get("weekly_mileage_progression"),
            age_to_bucket(age), experience_level, gender,
        )
        return row["id"] if row else None


async def store_race_result(
    strava_athlete_id: int, snapshot_id: Optional[int],
    ref_distance_km: float, ref_time_seconds: int, ref_date: Optional[str],
    goal_distance_km: float, goal_time_seconds: int, goal_date: str,
    goal_elevation_gain_ft: Optional[float] = None,
    predicted_time_seconds: Optional[int] = None,
    model_version: str = "riegel_v1", source: str = "manual",
) -> Optional[int]:
    if not pool: return None
    if not await get_consent(strava_athlete_id): return None
    athlete_hash = hash_athlete_id(strava_athlete_id)

    prediction_error = prediction_error_pct = None
    if predicted_time_seconds and goal_time_seconds:
        prediction_error = predicted_time_seconds - goal_time_seconds
        prediction_error_pct = (prediction_error / goal_time_seconds) * 100

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO race_results (
                snapshot_id, athlete_hash,
                ref_distance_km, ref_time_seconds, ref_date,
                goal_distance_km, goal_time_seconds, goal_date,
                goal_elevation_gain_ft, predicted_time_seconds,
                prediction_error_seconds, prediction_error_pct,
                model_version, source
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            RETURNING id
        """,
            snapshot_id, athlete_hash,
            ref_distance_km, ref_time_seconds,
            datetime.strptime(ref_date, "%Y-%m-%d").date() if ref_date else None,
            goal_distance_km, goal_time_seconds,
            datetime.strptime(goal_date, "%Y-%m-%d").date(),
            goal_elevation_gain_ft, predicted_time_seconds,
            prediction_error, prediction_error_pct, model_version, source,
        )
        return row["id"] if row else None


# --- Webhook State ---

async def store_webhook_state(subscription_id: int, verify_token: str):
    if not pool: return
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO webhook_state (id, subscription_id, verify_token)
            VALUES (1, $1, $2)
            ON CONFLICT (id) DO UPDATE SET subscription_id = $1, verify_token = $2
        """, subscription_id, verify_token)


async def get_webhook_state() -> Optional[dict]:
    if not pool: return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM webhook_state WHERE id = 1")
        return dict(row) if row else None


# --- Analytics ---

async def get_dataset_stats() -> dict:
    if not pool: return {"enabled": False}
    async with pool.acquire() as conn:
        consent_count = await conn.fetchval("SELECT COUNT(*) FROM data_consent WHERE opted_in = TRUE")
        snapshot_count = await conn.fetchval("SELECT COUNT(*) FROM training_snapshots")
        result_count = await conn.fetchval("SELECT COUNT(*) FROM race_results")
        pending_count = await conn.fetchval("SELECT COUNT(*) FROM pending_predictions WHERE status = 'pending'")
        log_count = await conn.fetchval("SELECT COUNT(*) FROM training_log")

        error_stats = await conn.fetchrow("""
            SELECT COUNT(*) as n,
                AVG(ABS(prediction_error_seconds)) as mae,
                AVG(ABS(prediction_error_pct)) as mape,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ABS(prediction_error_seconds)) as median_error,
                PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY ABS(prediction_error_seconds)) as p90_error
            FROM race_results WHERE predicted_time_seconds IS NOT NULL
        """)

        return {
            "enabled": True,
            "opted_in_users": consent_count,
            "training_snapshots": snapshot_count,
            "race_results": result_count,
            "pending_predictions": pending_count,
            "training_log_entries": log_count,
            "model_accuracy": {
                "sample_count": error_stats["n"] if error_stats else 0,
                "mae_seconds": round(error_stats["mae"], 1) if error_stats and error_stats["mae"] else None,
                "mape": round(error_stats["mape"], 2) if error_stats and error_stats["mape"] else None,
                "median_error_seconds": round(error_stats["median_error"], 1) if error_stats and error_stats["median_error"] else None,
                "p90_error_seconds": round(error_stats["p90_error"], 1) if error_stats and error_stats["p90_error"] else None,
            },
        }


async def export_training_dataset() -> list:
    if not pool: return []
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                ts.weeks_of_data, ts.avg_weekly_miles, ts.peak_weekly_miles,
                ts.total_miles, ts.total_runs, ts.avg_run_distance_mi,
                ts.longest_run_mi, ts.avg_pace_per_mile_sec,
                ts.fastest_pace_per_mile_sec, ts.total_elevation_gain_ft,
                ts.avg_elevation_per_run_ft, ts.runs_with_heartrate,
                ts.avg_heartrate, ts.weekly_mileage_progression,
                ts.age_bucket, ts.experience_level, ts.gender,
                rr.ref_distance_km, rr.ref_time_seconds,
                rr.goal_distance_km, rr.goal_time_seconds,
                rr.goal_elevation_gain_ft,
                rr.predicted_time_seconds, rr.prediction_error_seconds,
                rr.prediction_error_pct, rr.model_version, rr.source
            FROM race_results rr
            JOIN training_snapshots ts ON ts.id = rr.snapshot_id
            ORDER BY rr.created_at
        """)
        return [dict(r) for r in rows]