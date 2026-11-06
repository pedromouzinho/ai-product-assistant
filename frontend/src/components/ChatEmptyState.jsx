import React from 'react';
import { BRAND_SYMBOL_DATA_URI } from '../utils/constants.js';
import { RefreshIcon } from './AppIcons.jsx';

/**
 * ChatEmptyState — initial screen of the assistant.
 *
 * Shows:
 * 1. Strong greeting using the user's first name
 * 2. Stable contextual suggestions
 */

export default function ChatEmptyState({
    firstName,
    suggestions,
    onSuggestionClick,
    onRefreshSuggestions,
}) {
    const safeFirstName = (firstName || '').trim() || 'there';
    return (
        <div className="app-empty-state">
            <img
                src={BRAND_SYMBOL_DATA_URI}
                alt="AI Product Assistant"
                style={{ width: 60, height: 60, margin: '0 auto 16px', display: 'block' }}
            />
            <div className="app-empty-kicker">AI Product Assistant</div>
            <div className="app-empty-title">Bom dia, {safeFirstName}. Como te posso ajudar hoje?</div>

            <div className="app-empty-divider" />

            <div className="app-empty-section-label">Experimenta perguntar</div>
            <div className="app-suggestion-grid">
                {suggestions.map((question) => (
                    <button
                        key={question}
                        type="button"
                        className="suggestion-btn"
                        onClick={() => onSuggestionClick(question)}
                    >
                        {question}
                    </button>
                ))}
            </div>
            <button type="button" className="app-ghost-btn" style={{ marginTop: 14 }} onClick={onRefreshSuggestions}>
                <RefreshIcon size={15} />
                Outras sugestões
            </button>
        </div>
    );
}
