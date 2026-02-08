# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Frontend (React + Vite)
```bash
cd frontend
npm install
npm run dev        # Dev server on http://localhost:5173
npm run build      # Production build
npm run preview    # Preview production build
```

### Backend (FastAPI + asyncpg)
```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

No test framework is configured for either frontend or backend.

## Architecture

**Split deployment:** React SPA on Vercel, FastAPI on Railway. They communicate via CORS-enabled REST API calls (no proxy). The API base URL is set via `VITE_API_URL` env var.

### Frontend
- `main.jsx` — React Router with two routes: `/` (App) and `/callback` (Callback)
- `App.jsx` — Monolithic single component (~1600 lines) containing all UI, state (useState hooks), and inline CSS. No sub-components or state management library.
- `Callback.jsx` — Strava OAuth callback handler; exchanges auth code, stores tokens, redirects to `/`
- `api.js` — HTTP client with localStorage-based Strava token management (store, retrieve, refresh, clear)

### Backend
- `main.py` — All FastAPI route handlers, CORS config, and the prediction engine (Riegel formula with adjustments)
- `database.py` — Raw SQL with asyncpg (no ORM). Contains all table DDL, CRUD operations, Fernet encryption for tokens, and migrations
- `webhook_handler.py` — Processes Strava webhook events: fetches activity details, logs runs, auto-matches pending predictions
- `training_processor.py` — Pure functions that aggregate Strava activity lists into training features (weekly mileage, paces, elevation, HR stats) and detect race results

### Data Flow
1. Strava OAuth via `/api/strava/auth-url` and `/api/strava/token`
2. Frontend fetches athlete profile and activities, user configures prediction parameters
3. `POST /api/predict` runs Riegel formula: `T2 = T1 * (D2/D1)^exponent` with training volume, age, and experience adjustments
4. Optional data contribution stores training snapshots and pending predictions; webhooks auto-match actual race results

### Database
PostgreSQL with raw SQL (asyncpg). Key tables: `training_snapshots`, `race_results`, `pending_predictions`, `athlete_tokens`, `training_log`, `data_consent`. Privacy: athlete data stored under salted SHA-256 hash, age bucketed. Migrations run via `POST /api/admin/migrate?admin_key=...`.

## Environment Variables

### Backend
- `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` — Strava API credentials
- `FRONTEND_URL` — Frontend origin for CORS/redirects
- `DATABASE_URL` — PostgreSQL connection string
- `ENCRYPTION_KEY` — Fernet key for encrypting stored Strava tokens
- `ADMIN_KEY` — Required for admin endpoints (webhook setup, migrations, data export)
- `HASH_SALT` — Salt for hashing athlete IDs (defaults to `"race-prophet-2024"`)

### Frontend
- `VITE_API_URL` — Backend API base URL

## API Endpoints

All endpoints are under `/api/`. Key groups:
- `/api/strava/auth-url`, `/api/strava/token`, `/api/strava/refresh` — OAuth flow
- `/api/strava/athlete`, `/api/strava/activities` — Strava data (pass `access_token` as query param)
- `/api/strava/webhook` (GET/POST) — Webhook validation and event ingestion
- `/api/predict` — Core prediction engine
- `/api/data/*` — Consent, contribution, race results, stats
- `/api/admin/migrate` — Database migrations (requires `admin_key`)
