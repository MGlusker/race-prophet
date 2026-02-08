"""
Database module for Race Prophet.
Stores anonymized training + race data for model improvement.

Uses PostgreSQL via asyncpg for async operations.
Schema designed to be fully anonymized - no names, no Strava IDs stored in
the training data tables. A separate consent table tracks opt-in status
using a hashed athlete ID.
"""

import os
import hashlib
import asyncpg
from datetime import datetime
from typing import Optional

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# --- Connection Pool ---

pool: Optional[asyncpg.Pool] = None


async def init_db():
    """Initialize connection pool and create tables."""
    global pool
    if not DATABASE_URL:
        print("WARNING: DATABASE_URL not set, data collection disabled")
        return
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    await create_tables()


async def close_db():
    """Close connection pool."""
    global pool
    if pool:
        await pool.close()


async def create_tables():
    """Create all tables if they don't exist."""
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute("""
            -- Consent tracking (links hashed athlete ID to opt-in status)
            CREATE TABLE IF NOT EXISTS data_consent (
                athlete_hash TEXT PRIMARY KEY,
                opted_in BOOLEAN NOT NULL DEFAULT FALSE,
                opted_in_at TIMESTAMPTZ,
                opted_out_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Training snapshots: anonymized training data leading up to a race
            CREATE TABLE IF NOT EXISTS training_snapshots (
                id SERIAL PRIMARY KEY,
                athlete_hash TEXT NOT NULL,
                snapshot_date DATE NOT NULL,

                -- Training volume (16 weeks leading to goal race)
                weeks_of_data INT,
                avg_weekly_miles REAL,
                peak_weekly_miles REAL,
                total_miles REAL,
                total_runs INT,
                avg_run_distance_mi REAL,
                longest_run_mi REAL,

                -- Training quality
                avg_pace_per_mile_sec INT,
                fastest_pace_per_mile_sec INT,
                total_elevation_gain_ft REAL,
                avg_elevation_per_run_ft REAL,
                runs_with_heartrate INT,
                avg_heartrate REAL,

                -- Weekly mileage progression (JSON array of weekly miles)
                weekly_mileage_progression JSONB,

                -- Runner profile (anonymized)
                age_bucket TEXT,  -- '18-24', '25-34', '35-44', '45-54', '55-64', '65+'
                experience_level TEXT,
                gender TEXT,  -- if available from Strava

                created_at TIMESTAMPTZ DEFAULT NOW(),

                UNIQUE(athlete_hash, snapshot_date)
            );

            -- Race results: the actual race outcome paired with a training snapshot
            CREATE TABLE IF NOT EXISTS race_results (
                id SERIAL PRIMARY KEY,
                snapshot_id INT REFERENCES training_snapshots(id),
                athlete_hash TEXT NOT NULL,

                -- Reference race (the input race used for prediction)
                ref_distance_km REAL NOT NULL,
                ref_time_seconds INT NOT NULL,
                ref_date DATE,

                -- Goal race (the actual outcome)
                goal_distance_km REAL NOT NULL,
                goal_time_seconds INT NOT NULL,
                goal_date DATE NOT NULL,
                goal_elevation_gain_ft REAL,

                -- What our model predicted vs actual
                predicted_time_seconds INT,
                prediction_error_seconds INT,
                prediction_error_pct REAL,

                -- Model version used for prediction
                model_version TEXT DEFAULT 'riegel_v1',

                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Model performance tracking
            CREATE TABLE IF NOT EXISTS model_evaluations (
                id SERIAL PRIMARY KEY,
                model_version TEXT NOT NULL,
                eval_date DATE NOT NULL,
                sample_count INT,
                mae_seconds REAL,  -- mean absolute error
                mape REAL,  -- mean absolute percentage error
                median_error_seconds REAL,
                p90_error_seconds REAL,
                distance_pair TEXT,  -- e.g., '5K->Marathon'
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Indexes
            CREATE INDEX IF NOT EXISTS idx_snapshots_athlete
                ON training_snapshots(athlete_hash);
            CREATE INDEX IF NOT EXISTS idx_results_athlete
                ON race_results(athlete_hash);
            CREATE INDEX IF NOT EXISTS idx_results_distances
                ON race_results(ref_distance_km, goal_distance_km);
        """)


# --- Helpers ---

def hash_athlete_id(strava_athlete_id: int) -> str:
    """One-way hash of Strava athlete ID for anonymization."""
    salt = os.environ.get("HASH_SALT", "race-prophet-2024")
    raw = f"{salt}:{strava_athlete_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def age_to_bucket(age: Optional[int]) -> Optional[str]:
    """Convert exact age to anonymized bucket."""
    if not age:
        return None
    if age < 18:
        return "under-18"
    elif age <= 24:
        return "18-24"
    elif age <= 34:
        return "25-34"
    elif age <= 44:
        return "35-44"
    elif age <= 54:
        return "45-54"
    elif age <= 64:
        return "55-64"
    else:
        return "65+"


# --- Consent Management ---

async def get_consent(strava_athlete_id: int) -> bool:
    """Check if athlete has opted in to data collection."""
    if not pool:
        return False
    athlete_hash = hash_athlete_id(strava_athlete_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT opted_in FROM data_consent WHERE athlete_hash = $1",
            athlete_hash
        )
        return row["opted_in"] if row else False


async def set_consent(strava_athlete_id: int, opted_in: bool):
    """Set athlete's data collection consent."""
    if not pool:
        return
    athlete_hash = hash_athlete_id(strava_athlete_id)
    now = datetime.utcnow()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO data_consent (athlete_hash, opted_in, opted_in_at, opted_out_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (athlete_hash)
            DO UPDATE SET
                opted_in = $2,
                opted_in_at = CASE WHEN $2 THEN $3 ELSE data_consent.opted_in_at END,
                opted_out_at = CASE WHEN NOT $2 THEN $4 ELSE data_consent.opted_out_at END
        """, athlete_hash, opted_in,
             now if opted_in else None,
             now if not opted_in else None)


async def delete_athlete_data(strava_athlete_id: int):
    """Delete all data for an athlete (GDPR-style right to deletion)."""
    if not pool:
        return
    athlete_hash = hash_athlete_id(strava_athlete_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Delete race results (cascade from snapshots)
            await conn.execute(
                "DELETE FROM race_results WHERE athlete_hash = $1",
                athlete_hash
            )
            await conn.execute(
                "DELETE FROM training_snapshots WHERE athlete_hash = $1",
                athlete_hash
            )
            await conn.execute(
                "DELETE FROM data_consent WHERE athlete_hash = $1",
                athlete_hash
            )


# --- Training Data Collection ---

async def store_training_snapshot(
    strava_athlete_id: int,
    training_data: dict,
    age: Optional[int] = None,
    experience_level: Optional[str] = None,
    gender: Optional[str] = None,
) -> Optional[int]:
    """Store an anonymized training snapshot. Returns snapshot ID."""
    if not pool:
        return None

    # Check consent first
    if not await get_consent(strava_athlete_id):
        return None

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
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                $12, $13, $14, $15, $16, $17, $18, $19
            )
            ON CONFLICT (athlete_hash, snapshot_date)
            DO UPDATE SET
                weeks_of_data = EXCLUDED.weeks_of_data,
                avg_weekly_miles = EXCLUDED.avg_weekly_miles,
                peak_weekly_miles = EXCLUDED.peak_weekly_miles,
                total_miles = EXCLUDED.total_miles,
                total_runs = EXCLUDED.total_runs,
                avg_run_distance_mi = EXCLUDED.avg_run_distance_mi,
                longest_run_mi = EXCLUDED.longest_run_mi,
                avg_pace_per_mile_sec = EXCLUDED.avg_pace_per_mile_sec,
                fastest_pace_per_mile_sec = EXCLUDED.fastest_pace_per_mile_sec,
                total_elevation_gain_ft = EXCLUDED.total_elevation_gain_ft,
                avg_elevation_per_run_ft = EXCLUDED.avg_elevation_per_run_ft,
                runs_with_heartrate = EXCLUDED.runs_with_heartrate,
                avg_heartrate = EXCLUDED.avg_heartrate,
                weekly_mileage_progression = EXCLUDED.weekly_mileage_progression,
                age_bucket = EXCLUDED.age_bucket,
                experience_level = EXCLUDED.experience_level,
                gender = EXCLUDED.gender
            RETURNING id
        """,
            athlete_hash, today,
            training_data.get("weeks_of_data"),
            training_data.get("avg_weekly_miles"),
            training_data.get("peak_weekly_miles"),
            training_data.get("total_miles"),
            training_data.get("total_runs"),
            training_data.get("avg_run_distance_mi"),
            training_data.get("longest_run_mi"),
            training_data.get("avg_pace_per_mile_sec"),
            training_data.get("fastest_pace_per_mile_sec"),
            training_data.get("total_elevation_gain_ft"),
            training_data.get("avg_elevation_per_run_ft"),
            training_data.get("runs_with_heartrate"),
            training_data.get("avg_heartrate"),
            training_data.get("weekly_mileage_progression"),
            age_to_bucket(age),
            experience_level,
            gender,
        )
        return row["id"] if row else None


async def store_race_result(
    strava_athlete_id: int,
    snapshot_id: Optional[int],
    ref_distance_km: float,
    ref_time_seconds: int,
    ref_date: Optional[str],
    goal_distance_km: float,
    goal_time_seconds: int,
    goal_date: str,
    goal_elevation_gain_ft: Optional[float],
    predicted_time_seconds: Optional[int],
    model_version: str = "riegel_v1",
) -> Optional[int]:
    """Store a race result paired with prediction for model evaluation."""
    if not pool:
        return None

    if not await get_consent(strava_athlete_id):
        return None

    athlete_hash = hash_athlete_id(strava_athlete_id)

    prediction_error = None
    prediction_error_pct = None
    if predicted_time_seconds and goal_time_seconds:
        prediction_error = predicted_time_seconds - goal_time_seconds
        prediction_error_pct = (prediction_error / goal_time_seconds) * 100

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO race_results (
                snapshot_id, athlete_hash,
                ref_distance_km, ref_time_seconds, ref_date,
                goal_distance_km, goal_time_seconds, goal_date,
                goal_elevation_gain_ft,
                predicted_time_seconds, prediction_error_seconds,
                prediction_error_pct, model_version
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            RETURNING id
        """,
            snapshot_id, athlete_hash,
            ref_distance_km, ref_time_seconds,
            datetime.strptime(ref_date, "%Y-%m-%d").date() if ref_date else None,
            goal_distance_km, goal_time_seconds,
            datetime.strptime(goal_date, "%Y-%m-%d").date(),
            goal_elevation_gain_ft,
            predicted_time_seconds, prediction_error,
            prediction_error_pct, model_version,
        )
        return row["id"] if row else None


# --- Analytics / Model Evaluation ---

async def get_dataset_stats() -> dict:
    """Get summary stats about the collected dataset."""
    if not pool:
        return {"enabled": False}

    async with pool.acquire() as conn:
        consent_count = await conn.fetchval(
            "SELECT COUNT(*) FROM data_consent WHERE opted_in = TRUE"
        )
        snapshot_count = await conn.fetchval(
            "SELECT COUNT(*) FROM training_snapshots"
        )
        result_count = await conn.fetchval(
            "SELECT COUNT(*) FROM race_results"
        )

        # Error stats for current model
        error_stats = await conn.fetchrow("""
            SELECT
                COUNT(*) as n,
                AVG(ABS(prediction_error_seconds)) as mae,
                AVG(ABS(prediction_error_pct)) as mape,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ABS(prediction_error_seconds)) as median_error,
                PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY ABS(prediction_error_seconds)) as p90_error
            FROM race_results
            WHERE predicted_time_seconds IS NOT NULL
        """)

        return {
            "enabled": True,
            "opted_in_users": consent_count,
            "training_snapshots": snapshot_count,
            "race_results": result_count,
            "model_accuracy": {
                "sample_count": error_stats["n"] if error_stats else 0,
                "mae_seconds": round(error_stats["mae"], 1) if error_stats and error_stats["mae"] else None,
                "mape": round(error_stats["mape"], 2) if error_stats and error_stats["mape"] else None,
                "median_error_seconds": round(error_stats["median_error"], 1) if error_stats and error_stats["median_error"] else None,
                "p90_error_seconds": round(error_stats["p90_error"], 1) if error_stats and error_stats["p90_error"] else None,
            },
        }


async def export_training_dataset() -> list:
    """Export anonymized dataset for model training. No athlete hashes included."""
    if not pool:
        return []

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
                rr.prediction_error_pct, rr.model_version
            FROM race_results rr
            JOIN training_snapshots ts ON ts.id = rr.snapshot_id
            ORDER BY rr.created_at
        """)
        return [dict(r) for r in rows]
