import React, { createContext, useContext, useState, useCallback, useRef } from 'react';

/**
 * ToastProvider — sistema de notificações não-bloqueantes.
 *
 * Tipos suportados: error, warning, info, success
 * Auto-dismiss configurável por tipo.
 * Empilhável até 5 toasts simultâneos.
 */

const ToastContext = createContext(null);

let toastIdCounter = 0;

const DISMISS_MS = {
    error: 8000,
    warning: 6000,
    info: 5000,
    success: 4000,
};

const MAX_VISIBLE = 5;

const ICONS = {
    error: (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
            <circle cx="9" cy="9" r="8" stroke="currentColor" strokeWidth="1.5" />
            <path d="M9 5v4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            <circle cx="9" cy="12.75" r="0.75" fill="currentColor" />
        </svg>
    ),
    warning: (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
            <path d="M9 2L16.5 15H1.5L9 2Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
            <path d="M9 7v3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            <circle cx="9" cy="12.5" r="0.65" fill="currentColor" />
        </svg>
    ),
    info: (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
            <circle cx="9" cy="9" r="8" stroke="currentColor" strokeWidth="1.5" />
            <path d="M9 8v5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            <circle cx="9" cy="5.5" r="0.75" fill="currentColor" />
        </svg>
    ),
    success: (
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
            <circle cx="9" cy="9" r="8" stroke="currentColor" strokeWidth="1.5" />
            <path d="M5.5 9.5L8 12L12.5 6.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
    ),
};

export function ToastProvider({ children }) {
    const [toasts, setToasts] = useState([]);
    const timersRef = useRef({});

    const removeToast = useCallback((id) => {
        if (timersRef.current[id]) {
            clearTimeout(timersRef.current[id]);
            delete timersRef.current[id];
        }
        setToasts(prev => prev.filter(t => t.id !== id));
    }, []);

    const addToast = useCallback((message, type = 'info') => {
        const id = ++toastIdCounter;
        const toast = { id, message, type, createdAt: Date.now() };

        setToasts(prev => {
            const next = [...prev, toast];
            // Limitar a MAX_VISIBLE, remover os mais antigos
            if (next.length > MAX_VISIBLE) {
                const removed = next.splice(0, next.length - MAX_VISIBLE);
                removed.forEach(r => {
                    if (timersRef.current[r.id]) {
                        clearTimeout(timersRef.current[r.id]);
                        delete timersRef.current[r.id];
                    }
                });
            }
            return next;
        });

        const dismissTime = DISMISS_MS[type] || DISMISS_MS.info;
        timersRef.current[id] = setTimeout(() => removeToast(id), dismissTime);

        return id;
    }, [removeToast]);

    const toast = useCallback({
        error: (msg) => addToast(msg, 'error'),
        warning: (msg) => addToast(msg, 'warning'),
        info: (msg) => addToast(msg, 'info'),
        success: (msg) => addToast(msg, 'success'),
    }, [addToast]);

    // Wrapper com métodos directos
    const api = useCallback((msg, type) => addToast(msg, type), [addToast]);
    api.error = (msg) => addToast(msg, 'error');
    api.warning = (msg) => addToast(msg, 'warning');
    api.info = (msg) => addToast(msg, 'info');
    api.success = (msg) => addToast(msg, 'success');

    return (
        <ToastContext.Provider value={api}>
            {children}
            <ToastContainer toasts={toasts} onDismiss={removeToast} />
        </ToastContext.Provider>
    );
}

export function useToast() {
    const ctx = useContext(ToastContext);
    if (!ctx) throw new Error('useToast deve ser usado dentro de <ToastProvider>');
    return ctx;
}

/* ---------- Componente visual ---------- */

function ToastContainer({ toasts, onDismiss }) {
    if (!toasts.length) return null;

    return (
        <div className="toast-container" role="status" aria-live="polite">
            {toasts.map((t) => (
                <ToastItem key={t.id} toast={t} onDismiss={onDismiss} />
            ))}
        </div>
    );
}

function ToastItem({ toast, onDismiss }) {
    const [exiting, setExiting] = React.useState(false);

    const handleDismiss = () => {
        setExiting(true);
        setTimeout(() => onDismiss(toast.id), 200);
    };

    return (
        <div
            className={`toast-item toast-${toast.type}${exiting ? ' toast-exit' : ''}`}
            role={toast.type === 'error' ? 'alert' : 'status'}
            aria-live={toast.type === 'error' ? 'assertive' : 'polite'}
        >
            <span className="toast-icon">{ICONS[toast.type]}</span>
            <span className="toast-message">{toast.message}</span>
            <button
                className="toast-close"
                onClick={handleDismiss}
                aria-label="Fechar notificação"
            >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                    <path d="M3.5 3.5L10.5 10.5M10.5 3.5L3.5 10.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
            </button>
        </div>
    );
}

export default ToastProvider;
