import React, { useState, useEffect } from 'react';

/**
 * FeedbackDashboard — admin-only feedback analytics panel.
 *
 * Shows:
 * 1. Summary KPIs (total, average, NPS-like)
 * 2. Rating distribution bar chart
 * 3. Daily trend (sparkline-style)
 * 4. Tag cloud from negative feedback
 * 5. Recent feedback entries
 */

function RatingBar({ rating, count, maxCount }) {
  const pct = maxCount > 0 ? (count / maxCount) * 100 : 0;
  const isGood = rating >= 7;
  const isBad = rating <= 3;
  const color = isGood ? '#16A34A' : isBad ? '#DC2626' : '#F59E0B';
  return (
    <div className="fb-dash-bar-row">
      <span className="fb-dash-bar-label">{rating}</span>
      <div className="fb-dash-bar-track">
        <div className="fb-dash-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="fb-dash-bar-count">{count}</span>
    </div>
  );
}

function DailyTrend({ data }) {
  if (!data || data.length === 0) return <div className="fb-dash-empty">Sem dados de tendência.</div>;
  const maxCount = Math.max(...data.map(d => d.count), 1);
  return (
    <div className="fb-dash-trend">
      {data.slice(-14).map((d) => (
        <div key={d.date} className="fb-dash-trend-bar" title={`${d.date}: ${d.count} feedback(s), avg ${d.avg}`}>
          <div
            className="fb-dash-trend-fill"
            style={{ height: `${(d.count / maxCount) * 100}%`, background: d.avg >= 7 ? '#16A34A' : d.avg <= 3 ? '#DC2626' : '#F59E0B' }}
          />
          <span className="fb-dash-trend-label">{d.date.slice(5)}</span>
        </div>
      ))}
    </div>
  );
}

export default function FeedbackDashboard({ apiUrl, authFetch }) {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        setLoading(true);
        const res = await authFetch(`${apiUrl}/feedback/stats`, { method: 'GET' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) setStats(data);
      } catch (e) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [apiUrl, authFetch]);

  if (loading) {
    return (
      <div className="fb-dash-card">
        <div className="fb-dash-title">Feedback Dashboard</div>
        <div className="fb-dash-loading"><span className="upload-file-spinner" /> A carregar...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="fb-dash-card">
        <div className="fb-dash-title">Feedback Dashboard</div>
        <div className="fb-dash-error">{error}</div>
      </div>
    );
  }

  if (!stats || stats.total === 0) {
    return (
      <div className="fb-dash-card">
        <div className="fb-dash-title">Feedback Dashboard</div>
        <div className="fb-dash-empty">Sem feedback registado.</div>
      </div>
    );
  }

  const dist = stats.distribution || {};
  const maxDist = Math.max(...Object.values(dist).map(Number), 1);
  const positiveCount = [7, 8, 9, 10].reduce((s, r) => s + (Number(dist[String(r)]) || 0), 0);
  const negativeCount = [1, 2, 3].reduce((s, r) => s + (Number(dist[String(r)]) || 0), 0);
  const npsLike = stats.total > 0 ? Math.round(((positiveCount - negativeCount) / stats.total) * 100) : 0;
  const tags = stats.tags || {};

  return (
    <div className="fb-dash-card">
      <div className="fb-dash-title">Feedback Dashboard</div>

      {/* KPI Row */}
      <div className="fb-dash-kpi-row">
        <div className="fb-dash-kpi">
          <div className="fb-dash-kpi-value">{stats.total}</div>
          <div className="fb-dash-kpi-label">Total</div>
        </div>
        <div className="fb-dash-kpi">
          <div className="fb-dash-kpi-value">{stats.average_rating}</div>
          <div className="fb-dash-kpi-label">Media</div>
        </div>
        <div className="fb-dash-kpi">
          <div className={`fb-dash-kpi-value ${npsLike >= 0 ? 'fb-dash-positive' : 'fb-dash-negative'}`}>{npsLike > 0 ? '+' : ''}{npsLike}</div>
          <div className="fb-dash-kpi-label">NPS</div>
        </div>
      </div>

      {/* Distribution */}
      <div className="fb-dash-section">
        <div className="fb-dash-section-title">Distribuicao</div>
        {[10, 9, 8, 7, 6, 5, 4, 3, 2, 1].map(r => (
          <RatingBar key={r} rating={r} count={Number(dist[String(r)]) || 0} maxCount={maxDist} />
        ))}
      </div>

      {/* Daily Trend */}
      <div className="fb-dash-section">
        <div className="fb-dash-section-title">Tendencia diaria (ultimos 14 dias)</div>
        <DailyTrend data={stats.daily_trend} />
      </div>

      {/* Tags */}
      {Object.keys(tags).length > 0 && (
        <div className="fb-dash-section">
          <div className="fb-dash-section-title">Tags negativas</div>
          <div className="fb-dash-tags">
            {Object.entries(tags).map(([tag, count]) => (
              <span key={tag} className="fb-dash-tag">{tag} <span className="fb-dash-tag-count">{count}</span></span>
            ))}
          </div>
        </div>
      )}

      {/* Recent */}
      {stats.recent && stats.recent.length > 0 && (
        <div className="fb-dash-section">
          <div className="fb-dash-section-title">Feedback recente</div>
          <div className="fb-dash-recent">
            {stats.recent.slice(0, 10).map((fb, i) => (
              <div key={i} className="fb-dash-recent-row">
                <span className={`fb-dash-recent-rating ${fb.rating >= 7 ? 'fb-dash-positive' : fb.rating <= 3 ? 'fb-dash-negative' : ''}`}>
                  {fb.rating}/10
                </span>
                <span className="fb-dash-recent-q" title={fb.question}>{fb.question || '—'}</span>
                {fb.note && <span className="fb-dash-recent-note" title={fb.note}>{fb.note}</span>}
                <span className="fb-dash-recent-date">{fb.date}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
