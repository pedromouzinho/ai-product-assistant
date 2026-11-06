import React, { useState } from 'react';
import { API_URL, APP_VERSION, BRAND_MARK_URL } from '../utils/constants.js';

export default function LoginScreen({ onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit() {
    if (!username.trim() || !password) return;
    setLoading(true);
    setError('');
    try {
      const res = await fetch(API_URL + '/api/auth/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Erro');
      }
      const data = await res.json();
      onLogin({
        username: data.username,
        role: data.role,
        display_name: data.display_name,
      });
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '100vh',
        background: 'radial-gradient(circle at top, rgba(79, 70, 229, 0.08), transparent 28%), linear-gradient(180deg, #F6F4EF 0%, #EEE9E0 100%)',
        padding: '24px',
      }}
    >
      <div
        style={{
          width: 420,
          padding: '46px 44px 42px',
          background: 'rgba(255,255,255,0.88)',
          borderRadius: 32,
          boxShadow: '0 28px 76px rgba(30, 23, 28, 0.10), 0 2px 12px rgba(15,17,22,0.04)',
          border: '1px solid rgba(16, 18, 23, 0.05)',
          backdropFilter: 'blur(10px)',
        }}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <img
            src={BRAND_MARK_URL}
            alt="AI Product Assistant"
            style={{ width: 92, height: 92, margin: '0 auto 18px', display: 'block' }}
          />
          <div style={{ fontSize: 12, color: '#707784', marginBottom: 8, fontWeight: 800, letterSpacing: '1.1px', textTransform: 'uppercase' }}>
            Portfolio edition
          </div>
          <div style={{ fontSize: 24, fontWeight: 800, color: '#171922', letterSpacing: '-0.6px' }}>AI Product Assistant</div>
          <div style={{ fontSize: 11, color: '#8D94A0', marginTop: 12, fontWeight: 700, letterSpacing: '0.7px', textTransform: 'uppercase' }}>
            Versão {APP_VERSION}
          </div>
        </div>

        {error ? (
          <div
            style={{
              background: 'rgba(99, 102, 241,0.06)',
              color: '#B0103A',
              padding: '10px 16px',
              borderRadius: 10,
              fontSize: 12,
              marginBottom: 16,
              textAlign: 'center',
              fontWeight: 500,
              border: '1px solid rgba(99, 102, 241,0.12)',
            }}
          >
            {error}
          </div>
        ) : null}

        <input
          className="login-input"
          placeholder="Username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
          style={{ marginBottom: 14 }}
        />

        <input
          className="login-input"
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
          style={{ marginBottom: 24 }}
        />

        <button
          className="login-btn"
          onClick={handleSubmit}
          disabled={loading || !username.trim() || !password}
        >
          {loading ? 'A autenticar...' : 'Entrar'}
        </button>
      </div>
    </div>
  );
}
