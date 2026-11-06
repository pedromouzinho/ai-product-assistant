import React from 'react';

/**
 * QuickReplyBar — renders clickable pill buttons for clarification options.
 * Auto-sends the selected option on click (no editing step).
 */
export default function QuickReplyBar({ options, onSelect, disabled = false }) {
  if (!Array.isArray(options) || options.length === 0) return null;
  return (
    <div
      style={{
        display: 'flex',
        gap: 6,
        marginTop: 10,
        flexWrap: 'wrap',
      }}
      role="group"
      aria-label="Opcoes de resposta rapida"
    >
      {options.map((opt, i) => (
        <button
          key={i}
          type="button"
          onClick={() => !disabled && onSelect(opt)}
          disabled={disabled}
          className="export-btn"
          style={{
            background: 'rgba(var(--brand-accent-rgb, 0,0,0), 0.04)',
            border: '1px solid rgba(var(--brand-accent-rgb, 0,0,0), 0.15)',
            borderRadius: 20,
            padding: '7px 16px',
            cursor: disabled ? 'default' : 'pointer',
            fontSize: 12,
            color: disabled ? '#999' : '#333',
            fontWeight: 600,
            fontFamily: "'Montserrat', sans-serif",
            transition: 'all 0.15s ease',
            opacity: disabled ? 0.5 : 1,
          }}
        >
          {opt}
        </button>
      ))}
    </div>
  );
}
