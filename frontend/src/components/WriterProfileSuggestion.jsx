import React, { useState } from 'react';

/**
 * WriterProfileSuggestion — S4-01: Proactive "Learn your style?" suggestion.
 *
 * Shown after the user has generated N stories (threshold from backend).
 * Offers to save a writer profile based on the stories so far.
 *
 * Props:
 *  - onAccept  (fn) — callback when user accepts ("yes, learn my style")
 *  - onDismiss (fn) — callback when user dismisses
 */
export default function WriterProfileSuggestion({ onAccept, onDismiss }) {
  const [dismissed, setDismissed] = useState(false);

  if (dismissed) return null;

  return (
    <div className="writer-suggest-card">
      <p>
        Já geraste várias user stories. Queres que eu aprenda o teu estilo de escrita
        para as próximas serem mais parecidas contigo?
      </p>
      <div className="writer-suggest-actions">
        <button
          className="writer-suggest-btn primary"
          onClick={() => {
            if (onAccept) onAccept();
            setDismissed(true);
          }}
        >
          Sim, aprende o meu estilo
        </button>
        <button
          className="writer-suggest-btn secondary"
          onClick={() => {
            if (onDismiss) onDismiss();
            setDismissed(true);
          }}
        >
          Agora não
        </button>
      </div>
    </div>
  );
}
