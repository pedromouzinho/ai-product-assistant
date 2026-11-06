import React from 'react';
import { AttachmentIcon, WarningIcon } from './AppIcons.jsx';

// Aviso visual informativo (sem retry) — job está lento mas backend ainda o considera válido
const UPLOAD_SLOW_MS = 5 * 60 * 1000; // 5 min

// Retry disponível — alinhado com UPLOAD_JOB_STALE_SECONDS do backend (900s = 15 min)
const UPLOAD_STALE_MS = 15 * 60 * 1000; // 15 min

function fileStatusIcon(status) {
  if (status === 'completed' || status === 'done') return '✓';
  if (status === 'error' || status === 'failed') return '✕';
  if (status === 'timeout') return '⏱';
  return null; // spinning handled by CSS
}

function fileStatusClass(status) {
  if (status === 'completed' || status === 'done') return 'upload-file-done';
  if (status === 'error' || status === 'failed') return 'upload-file-error';
  if (status === 'timeout') return 'upload-file-timeout';
  if (status === 'slow') return 'upload-file-slow';
  return 'upload-file-active';
}

function elapsed(queuedAt) {
  if (!queuedAt) return null;
  const ms = Date.now() - new Date(queuedAt).getTime();
  if (Number.isNaN(ms) || ms < 0) return null;
  if (ms < 60000) return `${Math.round(ms / 1000)}s`;
  return `${Math.floor(ms / 60000)}m${Math.round((ms % 60000) / 1000)}s`;
}

/**
 * Panel que mostra progresso individual por ficheiro durante upload.
 *
 * Props:
 *  - syncActive      (bool)   — upload síncrono em curso
 *  - syncText        (string) — texto genérico do sync
 *  - pendingJobs     (array)  — jobs assíncronos [{job_id, filename, status, queued_at}]
 *  - onRetryJob      (fn)     — callback(job) para retry de jobs stale (>=15 min)
 */
export default function UploadProgressPanel({
  syncActive = false,
  syncText = '',
  pendingJobs = [],
  onRetryJob,
}) {
  const jobs = Array.isArray(pendingJobs) ? pendingJobs.filter(Boolean) : [];
  const hasAny = syncActive || jobs.length > 0;
  if (!hasAny) return null;

  const now = Date.now();
  const enrichedJobs = jobs.map((job) => {
    const queuedMs = job.queued_at ? now - new Date(job.queued_at).getTime() : 0;
    const isActive = job.status === 'queued' || job.status === 'processing';
    // isSlow: informativo (>5 min), sem retry
    const isSlow = isActive && queuedMs > UPLOAD_SLOW_MS && queuedMs <= UPLOAD_STALE_MS;
    // isStale: retry disponível (>15 min, alinhado com backend)
    const isStale = isActive && queuedMs > UPLOAD_STALE_MS;
    return { ...job, isSlow, isStale, queuedMs };
  });

  const slowCount = enrichedJobs.filter((j) => j.isSlow).length;
  const staleCount = enrichedJobs.filter((j) => j.isStale).length;

  return (
    <div className="upload-progress-panel">
      <div className="upload-progress-header">
        <AttachmentIcon size={16} />
        <span className="upload-progress-title">
          {syncActive
            ? syncText || 'A processar anexos...'
            : `${jobs.length} anexo(s) em processamento`}
        </span>
      </div>

      {enrichedJobs.length > 0 && (
        <div className="upload-progress-list">
          {enrichedJobs.slice(0, 8).map((job) => {
            const displayStatus = job.isStale
              ? 'timeout'
              : job.isSlow
                ? 'slow'
                : job.status;
            const icon = fileStatusIcon(displayStatus);
            const cls = fileStatusClass(displayStatus);
            const time = elapsed(job.queued_at);
            return (
              <div key={job.job_id || job.filename} className={`upload-file-row ${cls}`}>
                <span className="upload-file-indicator">
                  {icon || <span className="upload-file-spinner" />}
                </span>
                <span className="upload-file-name">{job.filename || 'ficheiro'}</span>
                {time && <span className="upload-file-elapsed">{time}</span>}
                {job.isStale && onRetryJob && (
                  <button
                    type="button"
                    className="upload-retry-btn"
                    onClick={() => onRetryJob(job)}
                    title="Re-tentar upload"
                  >
                    Retry
                  </button>
                )}
              </div>
            );
          })}
          {enrichedJobs.length > 8 && (
            <div className="upload-file-row upload-file-more">
              +{enrichedJobs.length - 8} ficheiro(s)
            </div>
          )}
        </div>
      )}

      {staleCount > 0 && (
        <div className="upload-stale-warning">
          <WarningIcon size={14} />
          <span>
            {staleCount === 1
              ? '1 ficheiro expirou (>15 min). Usa Retry para re-tentar.'
              : `${staleCount} ficheiros expiraram (>15 min). Usa Retry para re-tentar.`}
          </span>
        </div>
      )}

      {slowCount > 0 && staleCount === 0 && (
        <div className="upload-slow-notice">
          <WarningIcon size={14} />
          <span>
            {slowCount === 1
              ? '1 ficheiro está a demorar mais que o esperado. Ainda em processamento...'
              : `${slowCount} ficheiros estão a demorar mais que o esperado. Ainda em processamento...`}
          </span>
        </div>
      )}
    </div>
  );
}
