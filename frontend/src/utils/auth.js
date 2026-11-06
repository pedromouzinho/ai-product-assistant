export function getAuthHeaders() {
  return { 'Content-Type': 'application/json' };
}

export function authFetch(url, options = {}) {
  return fetch(url, { credentials: 'include', ...options });
}

let _refreshInterval = null;

/**
 * Inicia auto-refresh do JWT a cada `intervalMs` (default: 25 min).
 * O backend só emite um novo token se o atual estiver nos últimos 30 min de vida.
 * Se o refresh falhar com 401, invoca `onSessionExpired`.
 */
export function startTokenAutoRefresh(apiUrl, onSessionExpired, intervalMs = 25 * 60 * 1000) {
  stopTokenAutoRefresh();

  async function tryRefresh() {
    try {
      const res = await authFetch(`${apiUrl}/api/auth/refresh`, {
        method: 'POST',
        headers: getAuthHeaders(),
      });
      if (res.status === 401) {
        // Sessão expirou — notificar
        if (typeof onSessionExpired === 'function') onSessionExpired();
        stopTokenAutoRefresh();
      }
      // 200 com refreshed=false é normal (token ainda longe de expirar)
    } catch {
      // Erro de rede — ignora, tenta na próxima
    }
  }

  _refreshInterval = window.setInterval(tryRefresh, intervalMs);
  // Primeiro check após 1 minuto (dá tempo ao login)
  window.setTimeout(tryRefresh, 60 * 1000);
}

export function stopTokenAutoRefresh() {
  if (_refreshInterval) {
    window.clearInterval(_refreshInterval);
    _refreshInterval = null;
  }
}
