import React from 'react';
import DigestCard from './DigestCard.jsx';

/**
 * DigestSidePanel — painel lateral colapsável para o digest diário.
 *
 * Mostra o "Bom dia" com dados rápidos do projecto num painel
 * que pode ser aberto/fechado sem interferir no chat principal.
 */

export default function DigestSidePanel({ open, onToggle, onClose, digest, digestLoading, digestError, onDigestAction }) {
    return (
        <>
            {open ? (
                <button
                    type="button"
                    className="digest-panel-backdrop"
                    onClick={onClose}
                    aria-label="Fechar resumo do dia"
                />
            ) : null}

            <button
                type="button"
                className={`digest-tab-toggle ${open ? 'is-open' : ''}`}
                onClick={onToggle}
                aria-label={open ? 'Fechar resumo do dia' : 'Abrir resumo do dia'}
                aria-pressed={open}
                title={open ? 'Esconder resumo do dia' : 'Ver resumo do dia'}
            >
                <svg width="16" height="16" viewBox="0 0 20 20" fill="none">
                    <circle cx="10" cy="10" r="7.5" stroke="currentColor" strokeWidth="1.4" />
                    <path d="M10 6v4l2.5 2.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                </svg>
            </button>

            <div className={`digest-side-panel ${open ? 'is-open' : ''}`}>
                <div className="digest-side-panel-inner">
                    <DigestCard
                        digest={digest}
                        loading={digestLoading}
                        error={digestError}
                        onAction={onDigestAction}
                    />
                </div>
            </div>
        </>
    );
}
