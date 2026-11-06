import React from 'react';

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

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, errorInfo) {
    try {
      const runtimeCtx = readClientRuntimeContext();
      fetch('/api/client-error', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          error_type: 'react_error_boundary',
          message: String(error?.message || 'React error').slice(0, 500),
          stack: String(error?.stack || '').slice(0, 2000),
          component: String(this.props?.name || 'unknown').slice(0, 100),
          url: String(window.location.href).slice(0, 500),
          user_agent: String(navigator.userAgent).slice(0, 200),
          component_stack: String(errorInfo?.componentStack || '').slice(0, 2000),
          request_id: runtimeCtx.request_id,
          conversation_id: runtimeCtx.conversation_id,
          agent_mode: runtimeCtx.agent_mode,
        }),
      }).catch(() => {});
    } catch (_) {}
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 24, fontFamily: "'Montserrat', sans-serif" }}>
          <h2>Ocorreu um erro no frontend.</h2>
          <p>Por favor, recarrega a página.</p>
        </div>
      );
    }
    return this.props.children;
  }
}
