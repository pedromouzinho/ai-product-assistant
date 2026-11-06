import React from 'react';

/**
 * DigestCard — resumo diário do projeto DevOps.
 *
 * Props:
 *  - digest   (object)  — payload de /api/digest
 *  - onAction (fn)      — callback(text) quando user clica num link acionável
 *  - loading  (bool)    — a carregar digest
 *  - error    (string)  — erro ao carregar
 */

const SECTIONS = [
  {
    key: 'created_yesterday',
    title: 'Criadas ontem',
    icon: '📝',
    emptyMsg: 'Nenhuma story criada ontem.',
    actionLabel: 'Ver no DevOps',
  },
  {
    key: 'old_bugs',
    title: 'Bugs antigos (>7 dias)',
    icon: '🐛',
    emptyMsg: 'Sem bugs antigos pendentes.',
    actionLabel: 'Queres que analise este bug?',
  },
  {
    key: 'unassigned',
    title: 'Sem assignee',
    icon: '👤',
    emptyMsg: 'Todos os items têm assignee.',
    actionLabel: 'Queres que proponha um assignee?',
  },
  {
    key: 'closed_this_week',
    title: 'Fechados esta semana',
    icon: '✅',
    emptyMsg: 'Nenhum item fechado esta semana.',
    actionLabel: 'Resumo da semana',
  },
];

function DigestItem({ item, actionLabel, onAction }) {
  const stateColors = {
    Active: '#3B82F6',
    New: '#8B5CF6',
    Closed: '#16A34A',
    Resolved: '#16A34A',
    Removed: '#94A3B8',
  };
  const color = stateColors[item.state] || '#6E7480';

  return (
    <div className="digest-item">
      <div className="digest-item-header">
        <span className="digest-item-id">#{item.id}</span>
        <span className="digest-item-state" style={{ color, borderColor: color }}>
          {item.state}
        </span>
        {item.type && <span className="digest-item-type">{item.type}</span>}
      </div>
      <div className="digest-item-title">{item.title}</div>
      {item.assigned_to && (
        <div className="digest-item-meta">→ {item.assigned_to}</div>
      )}
      <div className="digest-item-actions">
        {item.url && (
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="digest-link"
          >
            DevOps ↗
          </a>
        )}
        {onAction && actionLabel && (
          <button
            type="button"
            className="digest-action-btn"
            onClick={() => onAction(`Olha para o work item #${item.id}: "${item.title}"`)}
          >
            {actionLabel}
          </button>
        )}
      </div>
    </div>
  );
}

function DigestSection({ section, config, onAction }) {
  const data = section || { count: 0, items: [] };
  const items = Array.isArray(data.items) ? data.items : [];
  const count = data.count || items.length;

  return (
    <div className="digest-section">
      <div className="digest-section-header">
        <span className="digest-section-icon">{config.icon}</span>
        <span className="digest-section-title">{config.title}</span>
        {count > 0 && <span className="digest-section-count">{count}</span>}
      </div>
      {items.length === 0 ? (
        <div className="digest-empty">{config.emptyMsg}</div>
      ) : (
        <div className="digest-items">
          {items.slice(0, 3).map((item) => (
            <DigestItem
              key={item.id}
              item={item}
              actionLabel={config.actionLabel}
              onAction={onAction}
            />
          ))}
          {items.length > 3 && (
            <div className="digest-more">+{items.length - 3} mais</div>
          )}
        </div>
      )}
      {data.error && <div className="digest-error">{data.error}</div>}
    </div>
  );
}

export default function DigestCard({ digest, onAction, loading, error }) {
  if (loading) {
    return (
      <div className="digest-card digest-loading">
        <div className="digest-card-header">
          <span className="digest-greeting">Bom dia ☀️</span>
          <span className="digest-date">A carregar resumo...</span>
        </div>
        <div className="digest-spinner-row">
          <span className="upload-file-spinner" />
          <span>A consultar o DevOps...</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="digest-card digest-error-card">
        <div className="digest-card-header">
          <span className="digest-greeting">Resumo diário</span>
        </div>
        <div className="digest-error">{error}</div>
      </div>
    );
  }

  if (!digest) return null;

  const today = digest.date || new Date().toISOString().slice(0, 10);
  const project = digest.project || '';

  return (
    <div className="digest-card">
      <div className="digest-card-header">
        <span className="digest-greeting">Bom dia ☀️</span>
        <span className="digest-date">{today}{project ? ` · ${project}` : ''}</span>
      </div>

      <div className="digest-sections">
        {SECTIONS.map((config) => (
          <DigestSection
            key={config.key}
            section={digest[config.key]}
            config={config}
            onAction={onAction}
          />
        ))}

        {/* S4-07: Weekly feedback summary */}
        {digest.feedback_summary && (digest.feedback_summary.total_feedbacks > 0 || digest.feedback_summary.rules_learned > 0) ? (
          <div className="digest-section">
            <div className="digest-section-header">
              <span className="digest-section-icon">📊</span>
              <span className="digest-section-title">Melhorias esta semana</span>
            </div>
            <div className="digest-feedback-summary">
              {digest.feedback_summary.rules_learned > 0 && (
                <div className="digest-feedback-stat">
                  <span className="digest-feedback-value">{digest.feedback_summary.rules_learned}</span>
                  <span className="digest-feedback-label">regras aprendidas</span>
                </div>
              )}
              {digest.feedback_summary.total_feedbacks > 0 && (
                <div className="digest-feedback-stat">
                  <span className="digest-feedback-value">{digest.feedback_summary.total_feedbacks}</span>
                  <span className="digest-feedback-label">feedbacks recebidos</span>
                </div>
              )}
              {digest.feedback_summary.avg_rating > 0 && (
                <div className="digest-feedback-stat">
                  <span className="digest-feedback-value">{digest.feedback_summary.avg_rating}</span>
                  <span className="digest-feedback-label">rating médio</span>
                </div>
              )}
              {digest.feedback_summary.positive_count > 0 && (
                <div className="digest-feedback-stat positive">
                  <span className="digest-feedback-value">{digest.feedback_summary.positive_count}</span>
                  <span className="digest-feedback-label">positivos (7+)</span>
                </div>
              )}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
