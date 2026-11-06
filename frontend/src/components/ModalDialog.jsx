import React from 'react';

export default function ModalDialog({
  title,
  description,
  children,
  primaryAction,
  secondaryAction,
  danger = false,
}) {
  return (
    <div className="modal-overlay" role="presentation">
      <div className="modal-card" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-heading">
          <div className="modal-title">{title}</div>
          {description ? <div className="modal-description">{description}</div> : null}
        </div>

        {children ? <div className="modal-body">{children}</div> : null}

        <div className="modal-actions">
          {secondaryAction ? (
            <button type="button" className="app-secondary-btn" onClick={secondaryAction.onClick}>
              {secondaryAction.label}
            </button>
          ) : null}
          {primaryAction ? (
            <button
              type="button"
              className={`app-primary-btn${danger ? ' danger' : ''}`}
              onClick={primaryAction.onClick}
              disabled={primaryAction.disabled}
            >
              {primaryAction.label}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
