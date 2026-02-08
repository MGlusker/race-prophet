import { useState, useEffect, useCallback } from 'react';
import { apiGet, apiPost, apiPatch, apiDelete, getStoredToken, clearToken, ensureValidToken } from './api';
import { ComposedChart, Line, Area, XAxis, YAxis, Tooltip, ReferenceLine, ResponsiveContainer } from 'recharts';

const DISTANCE_PRESETS = [
  { label: '5K', km: 5 },
  { label: '10K', km: 10 },
  { label: 'Half Marathon', km: 21.0975 },
  { label: 'Marathon', km: 42.195 },
  { label: '50K', km: 50 },
];

const BASELINE_PRESETS = [
  { label: '1 Mile', km: 1.60934 },
  { label: '5K', km: 5 },
  { label: '10K', km: 10 },
  { label: 'Half Marathon', km: 21.0975 },
  { label: 'Marathon', km: 42.195 },
];

function formatTime(totalSeconds) {
  if (!totalSeconds) return '--:--';
  totalSeconds = Math.round(totalSeconds);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function daysUntil(dateStr) {
  if (!dateStr) return null;
  const d = typeof dateStr === 'string' ? dateStr : dateStr.split('T')[0];
  const days = Math.ceil((new Date(d) - new Date()) / (1000 * 60 * 60 * 24));
  if (days > 0) return `${days}d away`;
  if (days === 0) return 'Today!';
  return `${Math.abs(days)}d ago`;
}

export default function Dashboard() {
  const [accessToken, setAccessToken] = useState(null);
  const [stravaUser, setStravaUser] = useState(null);
  const [goalRaces, setGoalRaces] = useState([]);
  const [trainingSummary, setTrainingSummary] = useState(null);
  const [selectedRaceId, setSelectedRaceId] = useState(null);
  const [predictions, setPredictions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Add race form
  const [showForm, setShowForm] = useState(false);
  const [formName, setFormName] = useState('');
  const [formDistIdx, setFormDistIdx] = useState(2); // Half Marathon
  const [formDate, setFormDate] = useState('');
  const [formGoalH, setFormGoalH] = useState('');
  const [formGoalM, setFormGoalM] = useState('');
  const [formGoalS, setFormGoalS] = useState('');
  const [formBaseDistIdx, setFormBaseDistIdx] = useState(1); // 5K
  const [formBaseH, setFormBaseH] = useState('0');
  const [formBaseM, setFormBaseM] = useState('25');
  const [formBaseS, setFormBaseS] = useState('00');
  const [formSubmitting, setFormSubmitting] = useState(false);

  // Auth check
  useEffect(() => {
    const stored = getStoredToken();
    if (stored && !stored.expired) {
      setAccessToken(stored.access_token);
      setStravaUser(stored.athlete);
    } else if (stored && stored.expired) {
      ensureValidToken().then((refreshed) => {
        if (refreshed) {
          setAccessToken(refreshed.access_token);
          setStravaUser(refreshed.athlete);
        } else {
          window.location.href = '/';
        }
      });
    } else {
      window.location.href = '/';
    }
  }, []);

  // Load data
  useEffect(() => {
    if (!accessToken) return;
    setLoading(true);
    Promise.all([
      apiGet('/api/me/goal-races', { access_token: accessToken }),
      apiGet('/api/me/training', { access_token: accessToken }),
    ])
      .then(([races, training]) => {
        setGoalRaces(races);
        setTrainingSummary(training);
        if (races.length > 0 && !selectedRaceId) {
          setSelectedRaceId(races[0].id);
        }
        setLoading(false);
      })
      .catch((err) => {
        console.error('Dashboard load error:', err);
        setError(err.message);
        setLoading(false);
      });
  }, [accessToken]);

  // Load predictions when selected race changes
  useEffect(() => {
    if (!accessToken || !selectedRaceId) {
      setPredictions([]);
      return;
    }
    apiGet(`/api/me/goal-races/${selectedRaceId}/predictions`, { access_token: accessToken })
      .then((data) => setPredictions(data.predictions || []))
      .catch((err) => console.error('Predictions load error:', err));
  }, [accessToken, selectedRaceId]);

  const refreshData = useCallback(async () => {
    if (!accessToken) return;
    try {
      const races = await apiGet('/api/me/goal-races', { access_token: accessToken });
      setGoalRaces(races);
    } catch (err) {
      console.error('Refresh error:', err);
    }
  }, [accessToken]);

  const handleAddRace = async () => {
    if (!formName.trim()) return;
    const baseTimeSec = (parseInt(formBaseH) || 0) * 3600 + (parseInt(formBaseM) || 0) * 60 + (parseInt(formBaseS) || 0);
    if (baseTimeSec <= 0) return;

    const goalTimeSec = ((parseInt(formGoalH) || 0) * 3600 + (parseInt(formGoalM) || 0) * 60 + (parseInt(formGoalS) || 0)) || null;

    setFormSubmitting(true);
    try {
      const race = await apiPost('/api/me/goal-races', {
        name: formName,
        distance_km: DISTANCE_PRESETS[formDistIdx].km,
        baseline_distance_km: BASELINE_PRESETS[formBaseDistIdx].km,
        baseline_time_seconds: baseTimeSec,
        race_date: formDate || null,
        goal_time_seconds: goalTimeSec,
        weekly_miles: trainingSummary?.avg_weekly_miles || null,
      }, { access_token: accessToken });

      setGoalRaces(prev => [...prev, race]);
      setSelectedRaceId(race.id);
      setShowForm(false);
      resetForm();
      await refreshData();
    } catch (err) {
      alert('Failed to create goal race: ' + err.message);
    } finally {
      setFormSubmitting(false);
    }
  };

  const resetForm = () => {
    setFormName('');
    setFormDistIdx(2);
    setFormDate('');
    setFormGoalH('');
    setFormGoalM('');
    setFormGoalS('');
    setFormBaseDistIdx(1);
    setFormBaseH('0');
    setFormBaseM('25');
    setFormBaseS('00');
  };

  const handleStatusChange = async (raceId, status) => {
    try {
      await apiPatch(`/api/me/goal-races/${raceId}`, { status }, { access_token: accessToken });
      setGoalRaces(prev => prev.filter(r => r.id !== raceId));
      if (selectedRaceId === raceId) {
        setSelectedRaceId(null);
        setPredictions([]);
      }
    } catch (err) {
      alert('Failed to update race: ' + err.message);
    }
  };

  const handleDelete = async (raceId) => {
    if (!confirm('Delete this goal race and all its prediction history?')) return;
    try {
      await apiDelete(`/api/me/goal-races/${raceId}`, { access_token: accessToken });
      setGoalRaces(prev => prev.filter(r => r.id !== raceId));
      if (selectedRaceId === raceId) {
        setSelectedRaceId(null);
        setPredictions([]);
      }
    } catch (err) {
      alert('Failed to delete: ' + err.message);
    }
  };

  const disconnectStrava = () => {
    clearToken();
    window.location.href = '/';
  };

  const selectedRace = goalRaces.find(r => r.id === selectedRaceId);
  const latestPred = selectedRace?.latest_prediction;

  // Chart data
  const chartData = predictions.map(p => ({
    date: new Date(p.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    predicted: p.predicted_seconds,
    low: p.low_seconds,
    high: p.high_seconds,
    miles: p.avg_weekly_miles,
  }));

  const goalTimeSec = selectedRace?.goal_time_seconds;

  return (
    <div className="page">
      <div className="container">
        {/* Header */}
        <header className="header">
          <div className="header-accent" />
          <h1 className="title">PACE PROPHET</h1>
          <p className="subtitle">Predict your finish time with precision</p>
        </header>

        {/* Navigation Tabs */}
        <nav className="nav-tabs">
          <a href="/" className="nav-tab">Predictor</a>
          <a href="/dashboard" className="nav-tab nav-tab-active">Dashboard</a>
        </nav>

        {/* Strava Connection Bar */}
        {stravaUser && (
          <div className="strava-connected">
            <div className="strava-user">
              {stravaUser.profile && (
                <img src={stravaUser.profile} alt="" className="strava-avatar" />
              )}
              <div>
                <div className="strava-name">{stravaUser.firstname} {stravaUser.lastname}</div>
                <div className="strava-status">Connected to Strava</div>
              </div>
            </div>
            <button onClick={disconnectStrava} className="btn-disconnect">Disconnect</button>
          </div>
        )}

        {loading && (
          <div className="loading-card">
            <div className="spinner" />
            <div className="loading-text">Loading your dashboard...</div>
          </div>
        )}

        {error && (
          <div className="card" style={{ textAlign: 'center', color: '#ef4444' }}>
            Failed to load dashboard: {error}
          </div>
        )}

        {!loading && !error && (
          <>
            {/* Training Summary */}
            {trainingSummary && trainingSummary.total_runs > 0 && (
              <div className="card">
                <div className="section-label">TRAINING SUMMARY</div>
                <div className="stats-grid">
                  <div className="summary-stat">
                    <div className="summary-stat-value">{trainingSummary.avg_weekly_miles || 0}</div>
                    <div className="summary-stat-label">avg mi/week</div>
                  </div>
                  <div className="summary-stat">
                    <div className="summary-stat-value">{trainingSummary.total_runs}</div>
                    <div className="summary-stat-label">total runs</div>
                  </div>
                  <div className="summary-stat">
                    <div className="summary-stat-value">{trainingSummary.longest_run_mi || 0}</div>
                    <div className="summary-stat-label">longest (mi)</div>
                  </div>
                  <div className="summary-stat">
                    <div className="summary-stat-value">{trainingSummary.peak_weekly_miles || 0}</div>
                    <div className="summary-stat-label">peak mi/week</div>
                  </div>
                </div>
              </div>
            )}

            {/* Goal Races */}
            <div className="card">
              <div className="section-header-row">
                <div className="section-label" style={{ marginBottom: 0 }}>GOAL RACES</div>
                <button onClick={() => setShowForm(!showForm)} className="btn-add">
                  {showForm ? 'Cancel' : '+ Add Race'}
                </button>
              </div>

              {/* Add Race Form */}
              {showForm && (
                <div className="add-race-form">
                  <div className="field-full">
                    <label className="label">Race Name</label>
                    <input
                      type="text"
                      value={formName}
                      onChange={e => setFormName(e.target.value)}
                      className="input"
                      placeholder="e.g. Boston Marathon 2026"
                    />
                  </div>

                  <div className="field-full" style={{ marginTop: 12 }}>
                    <label className="label">Race Distance</label>
                    <div className="pill-group">
                      {DISTANCE_PRESETS.map((d, i) => (
                        <button
                          key={d.label}
                          onClick={() => setFormDistIdx(i)}
                          className={`pill ${formDistIdx === i ? 'pill-active' : ''}`}
                        >
                          {d.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="row" style={{ marginTop: 12 }}>
                    <div className="field">
                      <label className="label">Race Date (optional)</label>
                      <input
                        type="date"
                        value={formDate}
                        onChange={e => setFormDate(e.target.value)}
                        className="input input-date"
                      />
                    </div>
                    <div className="field">
                      <label className="label">Goal Time (optional)</label>
                      <div className="time-row-sm">
                        <input type="number" min="0" value={formGoalH} onChange={e => setFormGoalH(e.target.value)} className="time-input-sm" placeholder="h" />
                        <span className="time-sep-sm">:</span>
                        <input type="number" min="0" max="59" value={formGoalM} onChange={e => setFormGoalM(e.target.value)} className="time-input-sm" placeholder="m" />
                        <span className="time-sep-sm">:</span>
                        <input type="number" min="0" max="59" value={formGoalS} onChange={e => setFormGoalS(e.target.value)} className="time-input-sm" placeholder="s" />
                      </div>
                    </div>
                  </div>

                  <div className="divider" />

                  <div className="field-full">
                    <label className="label">Baseline Race (required)</label>
                    <div className="hint" style={{ marginBottom: 8, marginTop: -4 }}>Your recent race used to calibrate predictions</div>
                    <div className="pill-group">
                      {BASELINE_PRESETS.map((d, i) => (
                        <button
                          key={d.label}
                          onClick={() => setFormBaseDistIdx(i)}
                          className={`pill ${formBaseDistIdx === i ? 'pill-active' : ''}`}
                        >
                          {d.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="field-full" style={{ marginTop: 12 }}>
                    <label className="label">Baseline Finish Time</label>
                    <div className="time-row-sm">
                      <input type="number" min="0" value={formBaseH} onChange={e => setFormBaseH(e.target.value)} className="time-input-sm" placeholder="h" />
                      <span className="time-sep-sm">:</span>
                      <input type="number" min="0" max="59" value={formBaseM} onChange={e => setFormBaseM(e.target.value)} className="time-input-sm" placeholder="m" />
                      <span className="time-sep-sm">:</span>
                      <input type="number" min="0" max="59" value={formBaseS} onChange={e => setFormBaseS(e.target.value)} className="time-input-sm" placeholder="s" />
                    </div>
                  </div>

                  <button
                    onClick={handleAddRace}
                    disabled={formSubmitting || !formName.trim()}
                    className="btn-calculate"
                    style={{ marginTop: 16 }}
                  >
                    {formSubmitting ? 'Creating...' : 'CREATE GOAL RACE'}
                  </button>
                </div>
              )}

              {/* Race Cards */}
              {goalRaces.length === 0 && !showForm && (
                <div className="empty-state">
                  No goal races yet. Add one to start tracking your predicted finish time as you train.
                </div>
              )}

              {goalRaces.length > 0 && (
                <div className="race-cards">
                  {goalRaces.map(race => {
                    const pred = race.latest_prediction;
                    const isSelected = race.id === selectedRaceId;
                    return (
                      <div
                        key={race.id}
                        className={`race-card ${isSelected ? 'race-card-selected' : ''}`}
                        onClick={() => setSelectedRaceId(race.id)}
                      >
                        <div className="race-card-header">
                          <div className="race-card-name">{race.name}</div>
                          <div className="race-card-actions">
                            <button
                              onClick={e => { e.stopPropagation(); handleStatusChange(race.id, 'completed'); }}
                              className="race-action-btn"
                              title="Mark completed"
                            >
                              Done
                            </button>
                            <button
                              onClick={e => { e.stopPropagation(); handleDelete(race.id); }}
                              className="race-action-btn race-action-delete"
                              title="Delete"
                            >
                              X
                            </button>
                          </div>
                        </div>
                        <div className="race-card-dist">
                          {DISTANCE_PRESETS.find(d => Math.abs(d.km - race.distance_km) < 0.5)?.label || `${race.distance_km.toFixed(1)}km`}
                          {race.race_date && (
                            <span className="race-card-countdown"> · {daysUntil(race.race_date)}</span>
                          )}
                        </div>
                        <div className="race-card-prediction">
                          {pred ? formatTime(pred.predicted_seconds) : '--:--'}
                        </div>
                        {pred && (
                          <div className="race-card-pace">{pred.pace_per_mile}/mi</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Prediction Trend Chart */}
            {selectedRace && chartData.length > 0 && (
              <div className="card">
                <div className="section-label">PREDICTION TREND — {selectedRace.name.toUpperCase()}</div>

                <div className="chart-container">
                  <ResponsiveContainer width="100%" height={280}>
                    <ComposedChart data={chartData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                      <XAxis
                        dataKey="date"
                        tick={{ fill: '#6b7280', fontSize: 10 }}
                        tickLine={false}
                        axisLine={{ stroke: '#1e2538' }}
                      />
                      <YAxis
                        tickFormatter={v => formatTime(v)}
                        tick={{ fill: '#6b7280', fontSize: 10 }}
                        tickLine={false}
                        axisLine={false}
                        domain={['auto', 'auto']}
                      />
                      <Tooltip
                        contentStyle={{
                          background: '#0d1117',
                          border: '1px solid #1e2538',
                          borderRadius: 8,
                          fontSize: 12,
                          fontFamily: "'JetBrains Mono', monospace",
                        }}
                        labelStyle={{ color: '#9ca3af' }}
                        formatter={(value, name) => {
                          if (name === 'predicted') return [formatTime(value), 'Predicted'];
                          if (name === 'low') return [formatTime(value), 'Fast'];
                          if (name === 'high') return [formatTime(value), 'Slow'];
                          return [value, name];
                        }}
                      />
                      <Area
                        type="monotone"
                        dataKey="low"
                        stroke="none"
                        fill="rgba(139, 92, 246, 0.1)"
                        fillOpacity={1}
                      />
                      <Area
                        type="monotone"
                        dataKey="high"
                        stroke="none"
                        fill="rgba(139, 92, 246, 0.1)"
                        fillOpacity={1}
                      />
                      <Line
                        type="monotone"
                        dataKey="predicted"
                        stroke="#8b5cf6"
                        strokeWidth={2}
                        dot={{ fill: '#8b5cf6', r: 4 }}
                        activeDot={{ r: 6, fill: '#a78bfa' }}
                      />
                      {goalTimeSec && (
                        <ReferenceLine
                          y={goalTimeSec}
                          stroke="#34d399"
                          strokeDasharray="6 4"
                          label={{
                            value: `Goal: ${formatTime(goalTimeSec)}`,
                            fill: '#34d399',
                            fontSize: 10,
                            position: 'right',
                          }}
                        />
                      )}
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>

                {/* Latest prediction details */}
                {latestPred && (
                  <div className="latest-pred">
                    <div className="latest-pred-header">LATEST PREDICTION</div>
                    <div className="latest-pred-time">{formatTime(latestPred.predicted_seconds)}</div>
                    <div className="latest-pred-stats">
                      <span>{latestPred.pace_per_mile}/mi</span>
                      <span className="latest-pred-sep">·</span>
                      <span>{formatTime(latestPred.low_seconds)} — {formatTime(latestPred.high_seconds)}</span>
                      <span className="latest-pred-sep">·</span>
                      <span>{latestPred.avg_weekly_miles || '—'} mi/wk</span>
                    </div>
                    <div className="latest-pred-meta">
                      {latestPred.triggered_by === 'webhook' ? 'Auto-updated from Strava activity' : 'Initial prediction'}
                      {' · '}
                      {new Date(latestPred.created_at).toLocaleDateString()}
                    </div>
                  </div>
                )}
              </div>
            )}

            {selectedRace && chartData.length === 0 && (
              <div className="card" style={{ textAlign: 'center' }}>
                <div className="section-label">PREDICTION TREND — {selectedRace.name.toUpperCase()}</div>
                <div className="empty-state">
                  No prediction data yet. Keep logging runs on Strava — predictions update automatically!
                </div>
              </div>
            )}
          </>
        )}

        <footer className="footer">
          Predictions auto-update as you train. Every run logged on Strava triggers a recalculation
          using your latest training data.
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

        /* Navigation tabs */
        .nav-tabs {
          display: flex;
          gap: 0;
          margin-bottom: 20px;
          background: var(--bg-card);
          border: 1px solid var(--border);
          border-radius: 10px;
          overflow: hidden;
        }
        .nav-tab {
          flex: 1;
          text-align: center;
          padding: 12px 16px;
          font-family: var(--font-mono);
          font-size: 12px;
          font-weight: 600;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          text-decoration: none;
          color: var(--text-muted);
          transition: all 0.15s;
          cursor: pointer;
        }
        .nav-tab:hover:not(.nav-tab-active) { color: var(--text); background: var(--surface); }
        .nav-tab-active {
          color: #fff;
          background: var(--accent);
        }

        /* Strava bar */
        .strava-connected {
          display: flex;
          align-items: center;
          justify-content: space-between;
          background: var(--bg-card);
          border: 1px solid var(--border);
          border-radius: 10px;
          padding: 12px 16px;
          margin-bottom: 20px;
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
        .section-header-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 16px;
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
        .hint {
          font-size: 10px;
          color: var(--text-faint);
          margin-top: 4px;
          display: block;
        }

        /* Training summary stats */
        .stats-grid {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 10px;
        }
        .summary-stat {
          text-align: center;
          background: var(--surface);
          border-radius: 8px;
          padding: 14px 8px;
        }
        .summary-stat-value {
          font-size: 22px;
          font-weight: 700;
          color: var(--text);
        }
        .summary-stat-label {
          font-size: 9px;
          color: var(--text-faint);
          text-transform: uppercase;
          letter-spacing: 0.1em;
          margin-top: 2px;
        }

        /* Add button */
        .btn-add {
          background: transparent;
          border: 1px solid var(--accent);
          border-radius: 8px;
          color: var(--accent);
          font-family: var(--font-mono);
          font-size: 11px;
          font-weight: 600;
          padding: 6px 14px;
          cursor: pointer;
          transition: all 0.15s;
        }
        .btn-add:hover {
          background: var(--accent);
          color: #fff;
        }

        /* Form */
        .add-race-form {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 10px;
          padding: 20px;
          margin-bottom: 16px;
        }
        .field-full { width: 100%; }
        .field { flex: 1; }
        .row {
          display: flex;
          gap: 16px;
        }
        .input {
          width: 100%;
          background: var(--bg);
          border: 1px solid var(--border);
          border-radius: 8px;
          padding: 10px 14px;
          font-size: 14px;
          color: var(--text);
          font-family: var(--font-mono);
          outline: none;
        }
        .input:focus { border-color: var(--accent); }
        .input-date { color-scheme: dark; }
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
        .time-row-sm {
          display: flex;
          align-items: center;
          gap: 2px;
        }
        .time-input-sm {
          width: 48px;
          background: var(--bg);
          border: 1px solid var(--border);
          border-radius: 6px;
          padding: 8px 4px;
          font-size: 14px;
          color: var(--text);
          font-family: var(--font-mono);
          text-align: center;
          outline: none;
        }
        .time-input-sm:focus { border-color: var(--accent); }
        .time-sep-sm {
          font-size: 16px;
          color: var(--border);
          font-weight: 700;
        }
        .divider {
          height: 1px;
          background: var(--border);
          margin: 16px 0;
        }
        .btn-calculate {
          width: 100%;
          padding: 14px 24px;
          background: var(--accent);
          border: none;
          border-radius: 10px;
          color: #fff;
          font-size: 13px;
          font-weight: 700;
          font-family: var(--font-mono);
          letter-spacing: 0.12em;
          cursor: pointer;
          transition: opacity 0.2s;
        }
        .btn-calculate:hover { opacity: 0.9; }
        .btn-calculate:disabled { opacity: 0.5; cursor: not-allowed; }

        /* Race cards grid */
        .race-cards {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
          gap: 10px;
        }
        .race-card {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 10px;
          padding: 16px;
          cursor: pointer;
          transition: all 0.15s;
        }
        .race-card:hover { border-color: rgba(139, 92, 246, 0.4); }
        .race-card-selected {
          border-color: var(--accent) !important;
          box-shadow: 0 0 16px rgba(139, 92, 246, 0.2);
        }
        .race-card-header {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          margin-bottom: 6px;
        }
        .race-card-name {
          font-size: 13px;
          font-weight: 700;
          color: var(--text);
          line-height: 1.3;
          flex: 1;
        }
        .race-card-actions {
          display: flex;
          gap: 4px;
          margin-left: 8px;
        }
        .race-action-btn {
          background: none;
          border: none;
          color: var(--text-faint);
          font-family: var(--font-mono);
          font-size: 9px;
          cursor: pointer;
          padding: 2px 4px;
          border-radius: 4px;
          transition: all 0.15s;
        }
        .race-action-btn:hover { color: var(--text-muted); background: var(--border); }
        .race-action-delete:hover { color: #ef4444; }
        .race-card-dist {
          font-size: 10px;
          color: var(--text-muted);
          letter-spacing: 0.05em;
          margin-bottom: 8px;
        }
        .race-card-countdown {
          color: var(--accent);
        }
        .race-card-prediction {
          font-size: 24px;
          font-weight: 700;
          color: var(--text);
          font-family: var(--font-display);
        }
        .race-card-pace {
          font-size: 11px;
          color: var(--text-faint);
          margin-top: 2px;
        }

        /* Empty state */
        .empty-state {
          font-size: 13px;
          color: var(--text-faint);
          text-align: center;
          padding: 24px 0;
          line-height: 1.6;
        }

        /* Chart */
        .chart-container {
          margin: 0 -8px 16px;
        }

        /* Latest prediction */
        .latest-pred {
          background: var(--surface);
          border-radius: 10px;
          padding: 16px;
          text-align: center;
        }
        .latest-pred-header {
          font-size: 9px;
          font-weight: 700;
          letter-spacing: 0.2em;
          color: var(--text-faint);
          margin-bottom: 4px;
        }
        .latest-pred-time {
          font-size: 32px;
          font-weight: 700;
          color: var(--text);
          font-family: var(--font-display);
        }
        .latest-pred-stats {
          font-size: 12px;
          color: var(--text-dim);
          margin-top: 6px;
        }
        .latest-pred-sep {
          margin: 0 6px;
          color: var(--text-faint);
        }
        .latest-pred-meta {
          font-size: 10px;
          color: var(--text-faint);
          margin-top: 8px;
        }

        /* Loading */
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
          .stats-grid { grid-template-columns: repeat(2, 1fr); }
          .race-cards { grid-template-columns: 1fr; }
          .row { flex-direction: column; gap: 12px; }
        }
      `}</style>
    </div>
  );
}
