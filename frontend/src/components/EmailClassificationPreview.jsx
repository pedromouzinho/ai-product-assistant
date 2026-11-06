import React, { useState } from 'react';

/**
 * EmailClassificationPreview — S4-04: In-chat preview of classified emails.
 *
 * Renders a compact, expandable table showing the classification results
 * from `tool_classify_uploaded_emails`. Shows counts by label at top,
 * then a scrollable preview of the first 20 classified items.
 *
 * Props:
 *  - data: { items, counts_by_label, total_count, source_filename, summary }
 */

const LABEL_COLORS = {
  Urgente:   { bg: '#fee2e2', text: '#b91c1c', border: '#fca5a5' },
  Importante:{ bg: '#fef3c7', text: '#92400e', border: '#fcd34d' },
  FYI:       { bg: '#dbeafe', text: '#1e40af', border: '#93c5fd' },
  Arquivo:   { bg: '#f3f4f6', text: '#4b5563', border: '#d1d5db' },
  Spam:      { bg: '#f3f4f6', text: '#9ca3af', border: '#e5e7eb' },
};

function getLabelStyle(label) {
  const normalized = (label || '').trim();
  for (const [key, style] of Object.entries(LABEL_COLORS)) {
    if (normalized.toLowerCase().includes(key.toLowerCase())) return style;
  }
  return { bg: '#f0fdf4', text: '#166534', border: '#86efac' };
}

export default function EmailClassificationPreview({ data }) {
  const [expanded, setExpanded] = useState(false);

  if (!data || !Array.isArray(data.items) || data.items.length === 0) return null;

  const { items, counts_by_label, total_count, source_filename } = data;
  const counts = counts_by_label || {};
  const sortedLabels = Object.entries(counts).sort((a, b) => b[1] - a[1]);

  return (
    <div className="email-class-preview">
      {/* Header */}
      <div className="email-class-header">
        <div className="email-class-header-left">
          <svg width="16" height="16" viewBox="0 0 20 20" fill="none" style={{ flexShrink: 0 }}>
            <rect x="2" y="4" width="16" height="12" rx="2" stroke="currentColor" strokeWidth="1.4" />
            <path d="M2 7l8 5 8-5" stroke="currentColor" strokeWidth="1.4" />
          </svg>
          <span className="email-class-title">
            Classificação de emails
            {source_filename ? ` — ${source_filename}` : ''}
          </span>
        </div>
        <span className="email-class-count">{total_count || items.length} emails</span>
      </div>

      {/* Label distribution pills */}
      {sortedLabels.length > 0 && (
        <div className="email-class-pills">
          {sortedLabels.map(([label, count]) => {
            const style = getLabelStyle(label);
            return (
              <span
                key={label}
                className="email-class-pill"
                style={{
                  background: style.bg,
                  color: style.text,
                  border: `1px solid ${style.border}`,
                }}
              >
                {label}: {count}
              </span>
            );
          })}
        </div>
      )}

      {/* Toggle */}
      <button
        className="email-class-toggle"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? 'Esconder detalhes ▾' : `Ver detalhes (${items.length} de ${total_count || items.length}) ▸`}
      </button>

      {/* Expanded table */}
      {expanded && (
        <div className="email-class-table-wrap">
          <table className="email-class-table">
            <thead>
              <tr>
                <th>De</th>
                <th>Assunto</th>
                <th>Label</th>
                <th>Ação</th>
                <th>Conf.</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, idx) => {
                const labelStyle = getLabelStyle(item.label);
                const conf = typeof item.confidence === 'number'
                  ? `${Math.round(item.confidence * 100)}%`
                  : '—';
                return (
                  <tr key={item.entry_id || idx} title={item.reason || ''}>
                    <td className="email-class-cell-from">{item.from || '—'}</td>
                    <td className="email-class-cell-subject">{item.subject || '(sem assunto)'}</td>
                    <td>
                      <span
                        className="email-class-label-badge"
                        style={{
                          background: labelStyle.bg,
                          color: labelStyle.text,
                          border: `1px solid ${labelStyle.border}`,
                        }}
                      >
                        {item.label || '—'}
                      </span>
                    </td>
                    <td className="email-class-cell-action">
                      {item.action_type || '—'}
                      {item.action_target ? ` → ${item.action_target}` : ''}
                    </td>
                    <td className="email-class-cell-conf">{conf}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
