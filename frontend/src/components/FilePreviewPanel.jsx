import React from 'react';

/**
 * FilePreviewPanel — S5-03: Preview files before confirming upload.
 *
 * Shows a compact list of selected files with:
 *  - File type icon/badge
 *  - Filename
 *  - Size (formatted)
 *  - Row estimate for tabular files (if available)
 *  - Remove button per file
 *  - Confirm upload / Cancel all buttons
 *
 * Props:
 *  - files: Array<{ file: File, rowEstimate?: number }>
 *  - onRemoveFile: (index) => void
 *  - onConfirmUpload: () => void
 *  - onCancelAll: () => void
 */

const EXT_BADGES = {
  '.csv':  { label: 'CSV',  color: '#059669', bg: '#d1fae5' },
  '.tsv':  { label: 'TSV',  color: '#059669', bg: '#d1fae5' },
  '.xlsx': { label: 'XLSX', color: '#1d4ed8', bg: '#dbeafe' },
  '.xls':  { label: 'XLS',  color: '#1d4ed8', bg: '#dbeafe' },
  '.xlsb': { label: 'XLSB', color: '#1d4ed8', bg: '#dbeafe' },
  '.pdf':  { label: 'PDF',  color: '#dc2626', bg: '#fee2e2' },
  '.pptx': { label: 'PPTX', color: '#c2410c', bg: '#ffedd5' },
  '.txt':  { label: 'TXT',  color: '#6b7280', bg: '#f3f4f6' },
  '.png':  { label: 'PNG',  color: '#7c3aed', bg: '#ede9fe' },
  '.jpg':  { label: 'JPG',  color: '#7c3aed', bg: '#ede9fe' },
  '.jpeg': { label: 'JPEG', color: '#7c3aed', bg: '#ede9fe' },
  '.gif':  { label: 'GIF',  color: '#7c3aed', bg: '#ede9fe' },
  '.webp': { label: 'WEBP', color: '#7c3aed', bg: '#ede9fe' },
  '.svg':  { label: 'SVG',  color: '#7c3aed', bg: '#ede9fe' },
  '.bmp':  { label: 'BMP',  color: '#7c3aed', bg: '#ede9fe' },
};

function getExtension(filename) {
  const safe = String(filename || '').trim().toLowerCase();
  const idx = safe.lastIndexOf('.');
  return idx >= 0 ? safe.slice(idx) : '';
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isImageExt(ext) {
  return ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp'].includes(ext);
}

export default function FilePreviewPanel({ files, onRemoveFile, onConfirmUpload, onCancelAll }) {
  if (!files || files.length === 0) return null;

  return (
    <div className="file-preview-panel">
      <div className="file-preview-header">
        <span className="file-preview-title">
          <svg width="14" height="14" viewBox="0 0 20 20" fill="none" style={{ verticalAlign: '-2px', marginRight: 6 }}>
            <path d="M14 3v4h4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M4 3h10l4 4v10a1 1 0 01-1 1H4a1 1 0 01-1-1V4a1 1 0 011-1z" stroke="currentColor" strokeWidth="1.4" />
          </svg>
          {files.length} ficheiro{files.length !== 1 ? 's' : ''} selecionado{files.length !== 1 ? 's' : ''}
        </span>
        <button type="button" className="file-preview-cancel" onClick={onCancelAll}>
          Cancelar
        </button>
      </div>

      <div className="file-preview-list">
        {files.map((entry, idx) => {
          const file = entry.file;
          const ext = getExtension(file.name);
          const badge = EXT_BADGES[ext] || { label: ext.replace('.', '').toUpperCase() || '?', color: '#6b7280', bg: '#f3f4f6' };
          const isImage = isImageExt(ext);

          return (
            <div key={`${file.name}-${idx}`} className="file-preview-item">
              {/* Thumbnail or badge */}
              {isImage && entry.thumbnailUrl ? (
                <img
                  src={entry.thumbnailUrl}
                  alt={file.name}
                  className="file-preview-thumbnail"
                />
              ) : (
                <span
                  className="file-preview-badge"
                  style={{ color: badge.color, background: badge.bg }}
                >
                  {badge.label}
                </span>
              )}

              {/* File info */}
              <div className="file-preview-info">
                <div className="file-preview-name" title={file.name}>{file.name}</div>
                <div className="file-preview-meta">
                  {formatSize(file.size || 0)}
                  {entry.rowEstimate != null && entry.rowEstimate > 0
                    ? ` · ~${entry.rowEstimate.toLocaleString('pt-PT')} linhas`
                    : null}
                </div>
              </div>

              {/* Remove */}
              <button
                type="button"
                className="file-preview-remove"
                onClick={() => onRemoveFile(idx)}
                title="Remover"
              >
                ×
              </button>
            </div>
          );
        })}
      </div>

      <button type="button" className="file-preview-confirm" onClick={onConfirmUpload}>
        Carregar {files.length > 1 ? `${files.length} ficheiros` : 'ficheiro'}
      </button>
    </div>
  );
}
