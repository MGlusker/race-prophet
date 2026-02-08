import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { apiPost, storeToken } from './api';

export default function Callback() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState(null);

  useEffect(() => {
    const code = searchParams.get('code');
    const errParam = searchParams.get('error');

    if (errParam) {
      setError('Authorization was denied.');
      return;
    }
    if (!code) {
      setError('No authorization code received.');
      return;
    }

    apiPost('/api/strava/token', null, { code })
      .then((data) => {
        storeToken(data);
        navigate('/', { replace: true });
      })
      .catch((err) => {
        setError(err.message || 'Failed to complete authorization.');
      });
  }, [searchParams, navigate]);

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      flexDirection: 'column',
      gap: 16,
    }}>
      {error ? (
        <>
          <div style={{ color: '#ef4444', fontSize: 16 }}>⚠ {error}</div>
          <a href="/" style={{ color: 'var(--accent)', textDecoration: 'none' }}>
            ← Back to Pace Prophet
          </a>
        </>
      ) : (
        <>
          <div style={{ fontSize: 14, color: 'var(--text-dim)' }}>
            Connecting to Strava...
          </div>
          <div style={{
            width: 32, height: 32,
            border: '3px solid var(--border)',
            borderTopColor: 'var(--accent)',
            borderRadius: '50%',
            animation: 'spin 0.8s linear infinite',
          }} />
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </>
      )}
    </div>
  );
}