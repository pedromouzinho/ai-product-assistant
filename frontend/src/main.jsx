import React from 'react';
import ReactDOM from 'react-dom/client';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import { ToastProvider } from './components/ToastProvider.jsx';
import App from './App.jsx';
import './styles/index.css';

function readClientRuntimeContext() {
  try {
    const ctx = window.__APP_RUNTIME_CONTEXT__ || {};
    return {
      request_id: String(ctx.request_id || '').slice(0, 100),
      conversation_id: String(ctx.conversation_id || '').slice(0, 100),
      agent_mode: String(ctx.agent_mode || '').slice(0, 50),
    };
  } catch (_) {
    return { request_id: '', conversation_id: '', agent_mode: '' };
  }
}

window.addEventListener('error', function(event) {
  try {
    const runtimeCtx = readClientRuntimeContext();
    fetch('/api/client-error', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        error_type: 'uncaught_error',
        message: String(event.message || '').slice(0, 500),
        stack: String(event.error?.stack || '').slice(0, 2000),
        component: '',
        url: String(window.location.href).slice(0, 500),
        user_agent: String(navigator.userAgent).slice(0, 200),
        request_id: runtimeCtx.request_id,
        conversation_id: runtimeCtx.conversation_id,
        agent_mode: runtimeCtx.agent_mode,
      }),
    }).catch(() => {});
  } catch (_) {}
});

window.addEventListener('unhandledrejection', function(event) {
  try {
    const runtimeCtx = readClientRuntimeContext();
    fetch('/api/client-error', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        error_type: 'unhandled_rejection',
        message: String(event.reason?.message || event.reason || '').slice(0, 500),
        stack: String(event.reason?.stack || '').slice(0, 2000),
        component: '',
        url: String(window.location.href).slice(0, 500),
        user_agent: String(navigator.userAgent).slice(0, 200),
        request_id: runtimeCtx.request_id,
        conversation_id: runtimeCtx.conversation_id,
        agent_mode: runtimeCtx.agent_mode,
      }),
    }).catch(() => {});
  } catch (_) {}
});

ReactDOM.createRoot(document.getElementById('root')).render(
  <ErrorBoundary name="App">
    <ToastProvider>
      <App />
    </ToastProvider>
  </ErrorBoundary>
);
