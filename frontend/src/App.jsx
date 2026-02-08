import { useState, useEffect, useCallback } from 'react';
import { apiGet, apiPost, getStoredToken, clearToken, ensureValidToken } from './api';

const DISTANCES = [
  { label: '1 Mile', km: 1.60934 },
  { label: '5K', km: 5 },
  { label: '10K', km: 10 },
  { label: '15K', km: 15 },
  { label: 'Half Marathon', km: 21.0975 },
  { label: 'Marathon', km: 42.195 },
  { label: '50K', km: 50 },
];

const EXPERIENCE_LEVELS = [
  { label: 'Beginner (<1yr)', value: 'beginner' },
  { label: 'Intermediate (1-3yr)', value: 'intermediate' },
  { label: 'Advanced (3-7yr)', value: 'advanced' },
  { label: 'Elite (7+yr)', value: 'elite' },
];

function formatTime(totalSeconds) {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = Math.round(totalSeconds % 60);
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export default function App() {
  // Auth state
  const [stravaUser, setStravaUser] = useState(null);
  const [accessToken, setAccessToken] = useState(null);
  const [stravaData, setStravaData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadingMsg, setLoadingMsg] = useState('');

  // Form state
  const [mode, setMode] = useState('manual'); // 'strava' | 'manual'
  const [raceDistIdx, setRaceDistIdx] = useState(1);
  const [goalDistIdx, setGoalDistIdx] = useState(5);
  const [hours, setHours] = useState('0');
  const [minutes, setMinutes] = useState('25');
  const [seconds, setSeconds] = useState('00');
  const [weeklyMiles, setWeeklyMiles] = useState('');
  const [age, setAge] = useState('');
  const [experience, setExperience] = useState('intermediate');
  const [selectedStravaRace, setSelectedStravaRace] = useState(null);

  // Results
  const [result, setResult] = useState(null);
  const [showSplits, setShowSplits] = useState(false);
  const [splitUnit, setSplitUnit] = useState('mile');

  // Check for existing Strava session on mount
  useEffect(() => {
    const stored = getStoredToken();
    if (stored && !stored.expired) {
      setAccessToken(stored.access_token);
      setStravaUser(stored.athlete);
      setMode('strava');
    } else if (stored && stored.expired) {
      ensureValidToken().then((refreshed) => {
        if (refreshed) {
          setAccessToken(refreshed.access_token);
          setStravaUser(refreshed.athlete);
          setMode('strava');
        }
      });
    }
  }, []);

  // Load Strava data when we have a token
  useEffect(() => {
    if (!accessToken) return;
    setLoading(true);
    setLoadingMsg('Fetching your runs from Strava...');

    Promise.all([
      apiGet('/api/strava/athlete', { access_token: accessToken }),
      apiGet('/api/strava/activities', { access_token: accessToken, weeks: 16 }),
    ])
      .then(([athlete, activities]) => {
        setStravaData({ athlete, activities });
        // Auto-fill fields
        if (athlete.age) setAge(String(athlete.age));
        if (activities.avg_weekly_miles) setWeeklyMiles(String(activities.avg_weekly_miles));

        // Auto-select best effort if available
        const efforts = activities.best_efforts || {};
        const preferredOrder = ['Half Marathon', '10K', '5K', '1 Mile'];
        for (const dist of preferredOrder) {
          if (efforts[dist]) {
            setSelectedStravaRace(efforts[dist]);
            // Find matching distance index
            const idx = DISTANCES.findIndex(
              (d) => Math.abs(d.km - efforts[dist].distance_km) < 0.5
            );
            if (idx >= 0) setRaceDistIdx(idx);
            // Parse time
            const ts = efforts[dist].time_seconds;
            setHours(String(Math.floor(ts / 3600)));
            setMinutes(String(Math.floor((ts % 3600) / 60)));
            setSeconds(String(ts % 60).padStart(2, '0'));
            break;
          }
        }
        setLoading(false);
      })
      .catch((err) => {
        console.error('Strava load error:', err);
        setLoading(false);
        setLoadingMsg('');
      });
  }, [accessToken]);

  const connectStrava = async () => {
    try {
      const { url } = await apiGet('/api/strava/auth-url');
      window.location.href = url;
    } catch (err) {
      alert('Failed to start Strava connection. Is the backend running?');
    }
  };

  const disconnectStrava = () => {
    clearToken();
    setStravaUser(null);
    setAccessToken(null);
    setStravaData(null);
    setMode('manual');
    setSelectedStravaRace(null);
  };

  const selectStravaEffort = (label, effort) => {
    setSelectedStravaRace(effort);
    const idx = DISTANCES.findIndex((d) => Math.abs(d.km - effort.distance_km) < 0.5);
    if (idx >= 0) setRaceDistIdx(idx);
    const ts = effort.time_seconds;
    setHours(String(Math.floor(ts / 3600)));
    setMinutes(String(Math.floor((ts % 3600) / 60)));
    setSeconds(String(ts % 60).padStart(2, '0'));
  };

  const calculate = useCallback(async () => {
    const timeSec =
      (parseInt(hours) || 0) * 3600 + (parseInt(minutes) || 0) * 60 + (parseInt(seconds) || 0);
    if (timeSec <= 0) return;

    try {
      const prediction = await apiPost('/api/predict', {
        race_time_seconds: timeSec,
        race_distance_km: DISTANCES[raceDistIdx].km,
        goal_distance_km: DISTANCES[goalDistIdx].km,
        weekly_miles: parseFloat(weeklyMiles) || 0,
        age: parseInt(age) || 0,
        experience,
      });
      setResult(prediction);
      setShowSplits(false);
    } catch (err) {
      alert('Prediction failed: ' + err.message);
    }
  }, [hours, minutes, seconds, raceDistIdx, goalDistIdx, weeklyMiles, age, experience]);

  const generateSplits = (totalSeconds, distKm, unit = 'mile') => {
    const splitDist = unit === 'mile' ? 1.60934 : 1;
    const splitLabel = unit === 'mile' ? 'mi' : 'km';
    const numSplits = Math.floor(distKm / splitDist);
    const remainder = distKm - numSplits * splitDist;
    const avgPacePerKm = totalSeconds / distKm;
    const splits = [];

    for (let i = 1; i <= numSplits; i++) {
      splits.push({
        label: `${splitLabel} ${i}`,
        time: formatTime(Math.round(avgPacePerKm * splitDist * i)),
        splitTime: formatTime(Math.round(avgPacePerKm * splitDist)),
      });
    }
    if (remainder > 0.01) {
      splits.push({
        label: `${(remainder / (unit === 'mile' ? 1.60934 : 1)).toFixed(2)} ${splitLabel}`,
        time: formatTime(Math.round(totalSeconds)),
        splitTime: formatTime(Math.round(avgPacePerKm * remainder)),
      });
    }
    return splits;
  };

  return (
    <div className="page">
      <div className="container">
        {/* Header */}
        <header className="header">
          <div className="header-accent" />
          <h1 className="title">RACE PROPHET</h1>
          <p className="subtitle">Predict your finish time with precision</p>
        </header>

        {/* Strava Connection Bar */}
        <div className="strava-bar">
          {stravaUser ? (
            <div className="strava-connected">
              <div className="strava-user">
                {stravaUser.profile && (
                  <img src={stravaUser.profile} alt="" className="strava-avatar" />
                )}
                <div>
                  <div className="strava-name">
                    {stravaUser.firstname} {stravaUser.lastname}
                  </div>
                  <div className="strava-status">Connected to Strava</div>
                </div>
              </div>
              <button onClick={disconnectStrava} className="btn-disconnect">
                Disconnect
              </button>
            </div>
          ) : (
            <button onClick={connectStrava} className="btn-strava">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                <path d="M15.387 17.944l-2.089-4.116h-3.065L15.387 24l5.15-10.172h-3.066m-7.008-5.599l2.836 5.598h4.172L10.463 0l-7 13.828h4.169" />
              </svg>
              Connect with Strava
            </button>
          )}
        </div>

        {/* Loading overlay */}
        {loading && (
          <div className="loading-card">
            <div className="spinner" />
            <div className="loading-text">{loadingMsg}</div>
          </div>
        )}

        {/* Strava Data Summary */}
        {stravaData && !loading && (
          <div className="card">
            <div className="section-label">YOUR STRAVA DATA</div>
            <div className="strava-stats">
              <div className="strava-stat">
                <div className="strava-stat-value">{stravaData.activities.total_runs}</div>
                <div className="strava-stat-label">runs ({stravaData.activities.weeks_analyzed}wk)</div>
              </div>
              <div className="strava-stat">
                <div className="strava-stat-value">{stravaData.activities.avg_weekly_miles}</div>
                <div className="strava-stat-label">avg mi/week</div>
              </div>
              <div className="strava-stat">
                <div className="strava-stat-value">
                  {Object.keys(stravaData.activities.best_efforts).length}
                </div>
                <div className="strava-stat-label">race distances</div>
              </div>
            </div>

            {/* Best efforts picker */}
            {Object.keys(stravaData.activities.best_efforts).length > 0 && (
              <div className="efforts-section">
                <div className="label">Select a recent effort as your baseline:</div>
                <div className="efforts-grid">
                  {Object.entries(stravaData.activities.best_efforts).map(([label, effort]) => (
                    <button
                      key={label}
                      onClick={() => selectStravaEffort(label, effort)}
                      className={`effort-card ${selectedStravaRace?.name === effort.name ? 'effort-active' : ''}`}
                    >
                      <div className="effort-dist">{label}</div>
                      <div className="effort-time">{effort.time_formatted}</div>
                      <div className="effort-meta">
                        {effort.pace_per_mile}/mi · {effort.date}
                      </div>
                      <div className="effort-name">{effort.name}</div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Recent races */}
            {stravaData.activities.races.length > 0 && (
              <div className="efforts-section">
                <div className="label">Strava-tagged races:</div>
                <div className="efforts-grid">
                  {stravaData.activities.races.slice(0, 6).map((race) => (
                    <button
                      key={race.id}
                      onClick={() => {
                        const idx = DISTANCES.findIndex(
                          (d) => Math.abs(d.km - race.distance_km) < d.km * 0.15
                        );
                        if (idx >= 0) setRaceDistIdx(idx);
                        setHours(String(Math.floor(race.time_seconds / 3600)));
                        setMinutes(String(Math.floor((race.time_seconds % 3600) / 60)));
                        setSeconds(String(race.time_seconds % 60).padStart(2, '0'));
                        setSelectedStravaRace(race);
                      }}
                      className={`effort-card ${selectedStravaRace?.id === race.id ? 'effort-active' : ''}`}
                    >
                      <div className="effort-dist">{race.distance_mi} mi</div>
                      <div className="effort-time">{race.time_formatted}</div>
                      <div className="effort-meta">{race.pace_per_mile}/mi · {race.date}</div>
                      <div className="effort-name">{race.name}</div>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Input Card */}
        <div className="card">
          <div className="section-label">
            {stravaData ? 'FINE-TUNE YOUR INPUT' : 'YOUR RECENT RACE'}
          </div>

          <div className="field-full">
            <label className="label">Distance</label>
            <div className="pill-group">
              {DISTANCES.map((d, i) => (
                <button
                  key={d.label}
                  onClick={() => setRaceDistIdx(i)}
                  className={`pill ${raceDistIdx === i ? 'pill-active' : ''}`}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </div>

          <div className="field-full" style={{ marginTop: 16 }}>
            <label className="label">Finish Time</label>
            <div className="time-row">
              <div className="time-group">
                <input
                  type="number"
                  min="0"
                  max="23"
                  value={hours}
                  onChange={(e) => setHours(e.target.value)}
                  className="time-input"
                />
                <span className="time-label">hr</span>
              </div>
              <span className="time-sep">:</span>
              <div className="time-group">
                <input
                  type="number"
                  min="0"
                  max="59"
                  value={minutes}
                  onChange={(e) => setMinutes(e.target.value)}
                  className="time-input"
                />
                <span className="time-label">min</span>
              </div>
              <span className="time-sep">:</span>
              <div className="time-group">
                <input
                  type="number"
                  min="0"
                  max="59"
                  value={seconds}
                  onChange={(e) => setSeconds(e.target.value)}
                  className="time-input"
                />
                <span className="time-label">sec</span>
              </div>
            </div>
          </div>

          <div className="divider" />
          <div className="section-label">GOAL RACE</div>

          <div className="field-full">
            <label className="label">Target Distance</label>
            <div className="pill-group">
              {DISTANCES.map((d, i) => (
                <button
                  key={d.label}
                  onClick={() => setGoalDistIdx(i)}
                  className={`pill ${goalDistIdx === i ? 'pill-active' : ''}`}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </div>

          <div className="divider" />
          <div className="section-label">ADJUSTMENTS</div>

          <div className="row">
            <div className="field">
              <label className="label">Weekly Mileage</label>
              <input
                type="number"
                value={weeklyMiles}
                onChange={(e) => setWeeklyMiles(e.target.value)}
                className="input"
                placeholder="e.g. 35"
              />
              <span className="hint">
                {stravaData ? 'Auto-filled from Strava' : 'miles/week average'}
              </span>
            </div>
            <div className="field">
              <label className="label">Age</label>
              <input
                type="number"
                value={age}
                onChange={(e) => setAge(e.target.value)}
                className="input"
                placeholder="e.g. 30"
              />
              {stravaData?.athlete?.age && (
                <span className="hint">From Strava profile</span>
              )}
            </div>
          </div>

          <div className="field-full" style={{ marginTop: 8 }}>
            <label className="label">Experience Level</label>
            <div className="pill-group">
              {EXPERIENCE_LEVELS.map((e) => (
                <button
                  key={e.value}
                  onClick={() => setExperience(e.value)}
                  className={`pill ${experience === e.value ? 'pill-active' : ''}`}
                >
                  {e.label}
                </button>
              ))}
            </div>
          </div>

          <button onClick={calculate} className="btn-calculate">
            PREDICT MY TIME →
          </button>
        </div>

        {/* Results */}
        {result && (
          <div className="results-card">
            <div className="result-header">
              <span className="result-distance">{DISTANCES[goalDistIdx].label}</span>
              <span className="result-tag">PREDICTED FINISH</span>
            </div>

            <div className="big-time">{result.predicted_formatted}</div>

            <div className="stats-row">
              <div className="stat">
                <div className="stat-value">{result.pace_per_mile}</div>
                <div className="stat-label">pace/mile</div>
              </div>
              <div className="stat-divider" />
              <div className="stat">
                <div className="stat-value">{result.pace_per_km}</div>
                <div className="stat-label">pace/km</div>
              </div>
              <div className="stat-divider" />
              <div className="stat">
                <div className="stat-value">±{result.uncertainty_pct}%</div>
                <div className="stat-label">confidence</div>
              </div>
            </div>

            <div className="range-bar">
              <div className="range-labels">
                <span><span className="range-tag">FAST</span> {result.low_formatted}</span>
                <span>{result.high_formatted} <span className="range-tag">SLOW</span></span>
              </div>
              <div className="range-track">
                <div className="range-fill" />
                <div className="range-marker" />
              </div>
            </div>

            {/* Splits */}
            <button
              onClick={() => setShowSplits(!showSplits)}
              className="btn-splits"
            >
              {showSplits ? 'HIDE' : 'SHOW'} SPLITS ▾
            </button>

            {showSplits && (
              <div className="splits-container">
                <div className="splits-unit-row">
                  <button
                    onClick={() => setSplitUnit('mile')}
                    className={`split-unit-btn ${splitUnit === 'mile' ? 'active' : ''}`}
                  >
                    Miles
                  </button>
                  <button
                    onClick={() => setSplitUnit('km')}
                    className={`split-unit-btn ${splitUnit === 'km' ? 'active' : ''}`}
                  >
                    Kilometers
                  </button>
                </div>
                <div className="splits-grid">
                  <div className="splits-header">
                    <span>Split</span>
                    <span>Pace</span>
                    <span>Elapsed</span>
                  </div>
                  {generateSplits(result.predicted_seconds, DISTANCES[goalDistIdx].km, splitUnit).map(
                    (s, i) => (
                      <div key={i} className={`split-row ${i % 2 === 0 ? 'split-even' : ''}`}>
                        <span className="split-label">{s.label}</span>
                        <span className="split-pace">{s.splitTime}</span>
                        <span className="split-elapsed">{s.time}</span>
                      </div>
                    )
                  )}
                </div>
              </div>
            )}

            {/* Equivalent times */}
            {result.equivalents && (
              <div className="equiv-section">
                <div className="section-label">EQUIVALENT TIMES</div>
                <div className="equiv-grid">
                  {Object.entries(result.equivalents).map(([label, data]) =>
                    label === DISTANCES[goalDistIdx].label ? null : (
                      <div key={label} className="equiv-item">
                        <div className="equiv-dist">{label}</div>
                        <div className="equiv-time">{data.time_formatted}</div>
                        <div className="equiv-pace">{data.pace_per_mile}/mi</div>
                      </div>
                    )
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        <footer className="footer">
          Based on the Riegel formula with adjustments for training volume, age grading, and
          experience. For best results, use a recent race time from the last 8 weeks.
        </footer>
      </div>

      <style>{`
        .page {
          min-height: 100vh;
          padding: 24px 16px;
        }
        .container {
          max-width: 660px;
          margin: 0 auto;
        }
        .header {
          text-align: center;
          margin-bottom: 28px;
        }
        .header-accent {
          width: 48px;
          height: 3px;
          background: var(--accent);
          margin: 0 auto 16px;
          border-radius: 2px;
        }
        .title {
          font-family: var(--font-display);
          font-size: 30px;
          font-weight: 700;
          letter-spacing: 0.15em;
          color: #fff;
          margin: 0;
        }
        .subtitle {
          font-size: 13px;
          color: var(--text-muted);
          margin-top: 6px;
          letter-spacing: 0.04em;
        }

        /* Strava bar */
        .strava-bar {
          margin-bottom: 20px;
        }
        .btn-strava {
          width: 100%;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 10px;
          padding: 14px;
          background: #fc4c02;
          border: none;
          border-radius: 10px;
          color: #fff;
          font-family: var(--font-display);
          font-size: 15px;
          font-weight: 600;
          cursor: pointer;
          transition: opacity 0.2s;
        }
        .btn-strava:hover { opacity: 0.9; }
        .strava-connected {
          display: flex;
          align-items: center;
          justify-content: space-between;
          background: var(--bg-card);
          border: 1px solid var(--border);
          border-radius: 10px;
          padding: 12px 16px;
        }
        .strava-user {
          display: flex;
          align-items: center;
          gap: 12px;
        }
        .strava-avatar {
          width: 36px;
          height: 36px;
          border-radius: 50%;
          border: 2px solid #fc4c02;
        }
        .strava-name {
          font-size: 14px;
          font-weight: 600;
          color: var(--text);
        }
        .strava-status {
          font-size: 11px;
          color: #fc4c02;
        }
        .btn-disconnect {
          background: none;
          border: 1px solid var(--border);
          border-radius: 6px;
          color: var(--text-muted);
          font-family: var(--font-mono);
          font-size: 11px;
          padding: 6px 12px;
          cursor: pointer;
        }

        /* Strava data */
        .strava-stats {
          display: flex;
          gap: 16px;
          margin-bottom: 20px;
        }
        .strava-stat {
          flex: 1;
          text-align: center;
          background: var(--surface);
          border-radius: 8px;
          padding: 12px 8px;
        }
        .strava-stat-value {
          font-size: 22px;
          font-weight: 700;
          color: var(--text);
        }
        .strava-stat-label {
          font-size: 10px;
          color: var(--text-faint);
          text-transform: uppercase;
          letter-spacing: 0.1em;
          margin-top: 2px;
        }
        .efforts-section {
          margin-top: 16px;
        }
        .efforts-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
          gap: 8px;
          margin-top: 8px;
        }
        .effort-card {
          text-align: left;
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 8px;
          padding: 12px;
          cursor: pointer;
          font-family: var(--font-mono);
          transition: all 0.15s;
          color: var(--text);
        }
        .effort-card:hover {
          border-color: var(--accent);
        }
        .effort-active {
          border-color: var(--accent) !important;
          box-shadow: 0 0 12px var(--accent-glow);
        }
        .effort-dist {
          font-size: 10px;
          font-weight: 700;
          color: var(--accent);
          letter-spacing: 0.1em;
          text-transform: uppercase;
        }
        .effort-time {
          font-size: 20px;
          font-weight: 700;
          margin: 4px 0 2px;
        }
        .effort-meta {
          font-size: 10px;
          color: var(--text-faint);
        }
        .effort-name {
          font-size: 11px;
          color: var(--text-muted);
          margin-top: 6px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        /* Cards */
        .card {
          background: var(--bg-card);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 24px;
          margin-bottom: 20px;
        }
        .section-label {
          font-size: 10px;
          font-weight: 700;
          letter-spacing: 0.2em;
          color: var(--accent);
          margin-bottom: 14px;
        }
        .label {
          display: block;
          font-size: 11px;
          font-weight: 600;
          letter-spacing: 0.08em;
          color: var(--text-dim);
          text-transform: uppercase;
          margin-bottom: 8px;
        }
        .row {
          display: flex;
          gap: 16px;
          margin-top: 8px;
        }
        .field { flex: 1; }
        .field-full { width: 100%; }
        .input {
          width: 100%;
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 8px;
          padding: 10px 14px;
          font-size: 16px;
          color: var(--text);
          font-family: var(--font-mono);
          outline: none;
        }
        .input:focus { border-color: var(--accent); }
        .hint {
          font-size: 10px;
          color: var(--text-faint);
          margin-top: 4px;
          display: block;
        }
        .pill-group {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
        }
        .pill {
          padding: 7px 14px;
          border-radius: 20px;
          border: 1px solid var(--border);
          background: transparent;
          color: var(--text-muted);
          font-size: 11px;
          font-family: var(--font-mono);
          font-weight: 500;
          cursor: pointer;
          transition: all 0.15s;
        }
        .pill:hover { border-color: var(--text-muted); }
        .pill-active {
          background: var(--accent) !important;
          border-color: var(--accent) !important;
          color: #fff !important;
          font-weight: 700;
        }
        .time-row {
          display: flex;
          align-items: center;
          gap: 4px;
        }
        .time-group {
          display: flex;
          flex-direction: column;
          align-items: center;
        }
        .time-input {
          width: 64px;
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 8px;
          padding: 10px 8px;
          font-size: 20px;
          color: var(--text);
          font-family: var(--font-mono);
          text-align: center;
          outline: none;
        }
        .time-input:focus { border-color: var(--accent); }
        .time-label {
          font-size: 9px;
          color: var(--text-faint);
          margin-top: 3px;
          text-transform: uppercase;
          letter-spacing: 0.1em;
        }
        .time-sep {
          font-size: 24px;
          color: var(--border);
          font-weight: 700;
          padding-bottom: 16px;
        }
        .divider {
          height: 1px;
          background: var(--border);
          margin: 20px 0;
        }
        .btn-calculate {
          width: 100%;
          padding: 14px 24px;
          background: var(--accent);
          border: none;
          border-radius: 10px;
          color: #fff;
          font-size: 14px;
          font-weight: 700;
          font-family: var(--font-mono);
          letter-spacing: 0.12em;
          cursor: pointer;
          margin-top: 16px;
          transition: opacity 0.2s;
        }
        .btn-calculate:hover { opacity: 0.9; }

        /* Results */
        .results-card {
          background: var(--bg-card);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 28px 24px;
          margin-bottom: 20px;
          text-align: center;
        }
        .result-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 8px;
        }
        .result-distance {
          font-size: 13px;
          font-weight: 700;
          color: var(--accent);
          letter-spacing: 0.08em;
        }
        .result-tag {
          font-size: 10px;
          color: var(--text-faint);
          letter-spacing: 0.15em;
        }
        .big-time {
          font-family: var(--font-display);
          font-size: 56px;
          font-weight: 700;
          color: #fff;
          letter-spacing: 0.04em;
          margin-bottom: 20px;
          line-height: 1.1;
        }
        .stats-row {
          display: flex;
          justify-content: center;
          align-items: center;
          gap: 20px;
          margin-bottom: 24px;
        }
        .stat { text-align: center; }
        .stat-value {
          font-size: 18px;
          font-weight: 700;
          color: var(--text);
        }
        .stat-label {
          font-size: 10px;
          color: var(--text-faint);
          letter-spacing: 0.1em;
          text-transform: uppercase;
          margin-top: 2px;
        }
        .stat-divider {
          width: 1px;
          height: 32px;
          background: var(--border);
        }
        .range-bar {
          background: var(--surface);
          border-radius: 10px;
          padding: 16px 20px;
          margin-bottom: 20px;
        }
        .range-labels {
          display: flex;
          justify-content: space-between;
          font-size: 13px;
          color: var(--text-dim);
        }
        .range-tag {
          font-size: 9px;
          font-weight: 700;
          letter-spacing: 0.15em;
          color: var(--text-faint);
          margin: 0 6px;
        }
        .range-track {
          height: 4px;
          background: var(--border);
          border-radius: 2px;
          margin: 10px 0 0;
          position: relative;
        }
        .range-fill {
          position: absolute;
          left: 20%;
          right: 20%;
          top: 0;
          bottom: 0;
          background: var(--accent-glow);
          border-radius: 2px;
        }
        .range-marker {
          position: absolute;
          left: 50%;
          top: -4px;
          width: 12px;
          height: 12px;
          background: var(--accent);
          border-radius: 50%;
          transform: translateX(-50%);
          box-shadow: 0 0 12px var(--accent-glow);
        }
        .btn-splits {
          background: none;
          border: 1px solid var(--border);
          border-radius: 8px;
          color: var(--text-muted);
          font-size: 10px;
          font-weight: 700;
          font-family: var(--font-mono);
          letter-spacing: 0.15em;
          padding: 10px 20px;
          cursor: pointer;
          margin-bottom: 16px;
        }
        .splits-container { text-align: left; }
        .splits-unit-row {
          display: flex;
          gap: 8px;
          margin-bottom: 12px;
          justify-content: center;
        }
        .split-unit-btn {
          padding: 5px 14px;
          border-radius: 16px;
          border: 1px solid var(--border);
          background: transparent;
          color: var(--text-muted);
          font-size: 11px;
          font-family: var(--font-mono);
          cursor: pointer;
        }
        .split-unit-btn.active {
          background: var(--border);
          color: var(--text);
        }
        .splits-grid { font-size: 12px; }
        .splits-header {
          display: flex;
          justify-content: space-between;
          padding: 8px 12px;
          color: var(--text-faint);
          font-size: 9px;
          font-weight: 700;
          letter-spacing: 0.15em;
          text-transform: uppercase;
          border-bottom: 1px solid var(--border);
        }
        .split-row {
          display: flex;
          justify-content: space-between;
          padding: 8px 12px;
          border-bottom: 1px solid rgba(30, 37, 56, 0.2);
        }
        .split-even { background: rgba(255,255,255,0.015); }
        .split-label { color: var(--text-dim); flex: 1; }
        .split-pace { color: var(--text); flex: 1; text-align: center; }
        .split-elapsed { color: var(--text-muted); flex: 1; text-align: right; }

        .equiv-section {
          margin-top: 24px;
          text-align: left;
        }
        .equiv-grid {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 8px;
        }
        .equiv-item {
          background: var(--surface);
          border-radius: 8px;
          padding: 12px 10px;
          text-align: center;
        }
        .equiv-dist {
          font-size: 10px;
          color: var(--text-muted);
          font-weight: 700;
          letter-spacing: 0.1em;
          margin-bottom: 4px;
        }
        .equiv-time {
          font-size: 15px;
          font-weight: 700;
          color: var(--text);
        }
        .equiv-pace {
          font-size: 10px;
          color: var(--text-faint);
          margin-top: 2px;
        }

        .loading-card {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 12px;
          padding: 32px;
          background: var(--bg-card);
          border: 1px solid var(--border);
          border-radius: 12px;
          margin-bottom: 20px;
        }
        .spinner {
          width: 32px;
          height: 32px;
          border: 3px solid var(--border);
          border-top-color: var(--accent);
          border-radius: 50%;
          animation: spin 0.8s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .loading-text {
          font-size: 13px;
          color: var(--text-dim);
        }

        .footer {
          text-align: center;
          padding: 16px 0;
          font-size: 10px;
          color: var(--text-faint);
          line-height: 1.6;
          max-width: 420px;
          margin: 0 auto;
        }

        @media (max-width: 500px) {
          .big-time { font-size: 40px; }
          .equiv-grid { grid-template-columns: repeat(2, 1fr); }
          .efforts-grid { grid-template-columns: 1fr; }
        }
      `}</style>
    </div>
  );
}
