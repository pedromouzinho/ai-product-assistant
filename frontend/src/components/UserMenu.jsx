import React, { useEffect, useRef, useState } from 'react';
import { API_URL } from '../utils/constants.js';
import { authFetch, getAuthHeaders } from '../utils/auth.js';
import { ChevronDownIcon, LockIcon, LogoutIcon, UserAddIcon } from './AppIcons.jsx';

function getUserInitials(user) {
  const source = String(user?.display_name || user?.username || '').trim();
  if (!source) return 'U';
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0] || ''}${parts[1][0] || ''}`.toUpperCase();
}

export default function UserMenu({ user, onLogout, onShowFeedbackDashboard }) {
  const [open, setOpen] = useState(false);
  const [showPw, setShowPw] = useState(false);
  const [showCreate, setShowCreate] = useState(false);

  const [cpCur, setCpCur] = useState('');
  const [cpNew, setCpNew] = useState('');
  const [cpMsg, setCpMsg] = useState('');

  const [nuU, setNuU] = useState('');
  const [nuP, setNuP] = useState('');
  const [nuN, setNuN] = useState('');
  const [nuMsg, setNuMsg] = useState('');

  const menuRef = useRef(null);
  const initials = getUserInitials(user);

  useEffect(() => {
    function handleClick(event) {
      if (menuRef.current && !menuRef.current.contains(event.target)) setOpen(false);
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  async function changePw() {
    try {
      const res = await authFetch(API_URL + '/api/auth/change-password', {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ current_password: cpCur, new_password: cpNew }),
      });
      const data = await res.json().catch(() => ({}));
      setCpMsg(res.ok ? 'Password alterada.' : `Erro: ${data.detail || 'Erro'}`);
      if (res.ok) {
        setCpCur('');
        setCpNew('');
      }
    } catch (e) {
      setCpMsg(`Erro: ${e.message}`);
    }
  }

  async function createUser() {
    try {
      const res = await authFetch(API_URL + '/api/auth/create-user', {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ username: nuU, password: nuP, display_name: nuN }),
      });
      const data = await res.json().catch(() => ({}));
      setNuMsg(res.ok ? 'Utilizador criado.' : `Erro: ${data.detail || 'Erro'}`);
      if (res.ok) {
        setNuU('');
        setNuP('');
        setNuN('');
      }
    } catch (e) {
      setNuMsg(`Erro: ${e.message}`);
    }
  }

  return (
    <div ref={menuRef} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(!open)}
        className="app-secondary-btn user-menu-trigger"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}
        title={user.display_name || user.username}
      >
        <span className="user-menu-full-name">{user.display_name || user.username}</span>
        <span className="user-menu-initials">{initials}</span>
        <ChevronDownIcon size={14} className="user-menu-chevron" />
      </button>

      {open ? (
        <div
          style={{
            position: 'absolute',
            right: 0,
            top: 'calc(100% + 6px)',
            background: 'white',
            borderRadius: 12,
            boxShadow: '0 8px 32px rgba(0,0,0,0.12)',
            padding: 8,
            minWidth: 260,
            zIndex: 1000,
            border: '1px solid rgba(16, 18, 23, 0.08)',
          }}
        >
          <div style={{ padding: '8px 14px', fontSize: 11, color: '#7A808B', borderBottom: '1px solid rgba(16, 18, 23, 0.06)', marginBottom: 4 }}>
            {`${user.username} · ${user.role}`}
          </div>

          <button className="user-menu-item" onClick={() => { setShowPw(!showPw); setShowCreate(false); }}>
            <LockIcon size={14} /> Alterar password
          </button>

          {showPw ? (
            <div style={{ padding: '8px 14px' }}>
              <input className="login-input" type="password" placeholder="Password actual" value={cpCur} onChange={(e) => setCpCur(e.target.value)} style={{ fontSize: 12, padding: 10, marginBottom: 6 }} />
              <input className="login-input" type="password" placeholder="Nova password" value={cpNew} onChange={(e) => setCpNew(e.target.value)} style={{ fontSize: 12, padding: 10, marginBottom: 6 }} />
              <button className="login-btn" onClick={changePw} style={{ fontSize: 12, padding: 8 }}>Alterar</button>
              {cpMsg ? (
                <div style={{ fontSize: 11, marginTop: 6, color: cpMsg === 'Password alterada.' ? '#22c55e' : 'var(--brand-accent)' }}>{cpMsg}</div>
              ) : null}
            </div>
          ) : null}

          {user.role === 'admin' ? (
            <>
              <button className="user-menu-item" onClick={() => { setShowCreate(!showCreate); setShowPw(false); }}>
                <UserAddIcon size={14} /> Criar utilizador
              </button>
              {onShowFeedbackDashboard ? (
                <button className="user-menu-item" onClick={() => { setOpen(false); onShowFeedbackDashboard(); }}>
                  📊 Feedback Dashboard
                </button>
              ) : null}
            </>
          ) : null}

          {showCreate ? (
            <div style={{ padding: '8px 14px' }}>
              <input className="login-input" placeholder="Username" value={nuU} onChange={(e) => setNuU(e.target.value)} style={{ fontSize: 12, padding: 10, marginBottom: 6 }} />
              <input className="login-input" type="password" placeholder="Password" value={nuP} onChange={(e) => setNuP(e.target.value)} style={{ fontSize: 12, padding: 10, marginBottom: 6 }} />
              <input className="login-input" placeholder="Nome completo" value={nuN} onChange={(e) => setNuN(e.target.value)} style={{ fontSize: 12, padding: 10, marginBottom: 6 }} />
              <button className="login-btn" onClick={createUser} style={{ fontSize: 12, padding: 8 }}>Criar</button>
              {nuMsg ? (
                <div style={{ fontSize: 11, marginTop: 6, color: nuMsg === 'Utilizador criado.' ? '#22c55e' : 'var(--brand-accent)' }}>{nuMsg}</div>
              ) : null}
            </div>
          ) : null}

          <div style={{ borderTop: '1px solid #f0f0f0', marginTop: 4, paddingTop: 4 }}>
            <button className="user-menu-item" onClick={onLogout} style={{ color: 'var(--brand-accent)' }}>
              <LogoutIcon size={14} /> Sair
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
