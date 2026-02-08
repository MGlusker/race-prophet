# Race Prophet 🏃‍♂️

Predict your marathon/race finish time using the Riegel formula with adjustments for training volume, age, and experience. Connect your Strava account to auto-pull recent races and training data.

## Features

- **Strava OAuth integration** — pulls recent runs, races, weekly mileage, and athlete profile
- **Smart race detection** — finds your best efforts at standard distances
- **Adjusted predictions** — Riegel formula tuned for training volume, age grading, and experience
- **Confidence ranges** — see optimistic/pessimistic estimates
- **Mile & km splits** — toggle between units
- **Equivalent times** — see what your fitness translates to across all distances
- **Manual mode** — works without Strava too

## Architecture

```
frontend/          React + Vite (deploys to Vercel)
backend/           FastAPI (deploys to Railway)
```

## Local Development

### 1. Set up Strava API App

1. Go to [strava.com/settings/api](https://www.strava.com/settings/api)
2. Create an app:
   - **Name:** Race Prophet
   - **Category:** Other
   - **Website:** `http://localhost:5173`
   - **Callback Domain:** `localhost`
3. Note your **Client ID** and **Client Secret**

### 2. Backend

```bash
cd backend
cp .env.example .env
# Edit .env with your Strava credentials

python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt

uvicorn main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend
cp .env.example .env
# VITE_API_URL should be http://localhost:8000

npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173)

## Deployment

### Backend → Railway

1. Create a new Railway project
2. Connect your repo (or use `railway up` CLI)
3. Set the root directory to `/backend`
4. Add environment variables:
   - `STRAVA_CLIENT_ID`
   - `STRAVA_CLIENT_SECRET`
   - `FRONTEND_URL` = your Vercel URL (e.g. `https://race-prophet.vercel.app`)
5. Railway auto-detects the Dockerfile

### Frontend → Vercel

1. Import project, set root directory to `/frontend`
2. Framework: Vite
3. Add environment variable:
   - `VITE_API_URL` = your Railway URL (e.g. `https://race-prophet-production.up.railway.app`)
4. Deploy

### Post-deploy: Update Strava callback

Go back to [strava.com/settings/api](https://www.strava.com/settings/api) and update:
- **Website:** your Vercel URL
- **Authorization Callback Domain:** your Vercel domain (e.g. `race-prophet.vercel.app`)

## Tech Stack

- **Frontend:** React 18, React Router, Vite
- **Backend:** FastAPI, httpx (async HTTP), Pydantic
- **Auth:** Strava OAuth 2.0
- **Deployment:** Vercel (frontend) + Railway (backend)

## How the Prediction Works

1. **Riegel formula** — `T2 = T1 × (D2/D1)^1.06` as the base
2. **Training volume adjustment** — higher weekly mileage reduces the fatigue exponent for longer distances
3. **Age grading** — accounts for performance decline after ~35
4. **Experience factor** — longer training history = better pacing efficiency
5. **Confidence range** — wider when extrapolating across bigger distance ratios
