import React from 'react';
import { formatStreamingToolLabel } from '../utils/streaming.js';

function formatDuration(ms) {
  if (ms == null) return null;
  const num = Number(ms);
  if (Number.isNaN(num) || num < 0) return null;
  if (num < 1000) return `${Math.round(num)}ms`;
  return `${(num / 1000).toFixed(1)}s`;
}

function getSummary(detail) {
  return detail && detail.result_summary ? detail.result_summary : {};
}

function getToolStatusMeta(detail) {
  const summary = getSummary(detail);
  const resultStatus = String(summary.result_status || '').trim().toLowerCase();
  const errorKind = String(summary.error_kind || '').trim().toLowerCase();
  const errorDetail = summary.error_detail || 'Erro na execução da ferramenta';

  if (resultStatus === 'blocked') {
    return {
      tone: 'blocked',
      label: 'Bloqueada',
      title: `Bloqueada: ${errorDetail}`,
      dotColor: '#A36A00',
      chipBackground: 'rgba(163, 106, 0, 0.12)',
      chipColor: '#7A4F00',
    };
  }

  if (['tool_error', 'exception'].includes(resultStatus) || errorKind) {
    return {
      tone: 'error',
      label: 'Erro',
      title: `Erro: ${errorDetail}`,
      dotColor: '#A02222',
      chipBackground: 'rgba(160, 34, 34, 0.1)',
      chipColor: '#8E1E1E',
    };
  }

  if (summary.has_error) {
    return {
      tone: 'warning',
      label: 'Alerta',
      title: `Alerta: ${errorDetail}`,
      dotColor: '#A36A00',
      chipBackground: 'rgba(163, 106, 0, 0.12)',
      chipColor: '#7A4F00',
    };
  }

  return {
    tone: 'ok',
    label: '',
    title: '',
    dotColor: '#4F46E5',
    chipBackground: '',
    chipColor: '',
  };
}

function buildSummaryBits(detail) {
  const summary = getSummary(detail);
  const bits = [];
  const durationLabel = formatDuration(detail && detail.duration_ms);
  if (durationLabel) {
    bits.push(durationLabel);
  }
  const totalCount = summary.total_count;
  const itemsReturned = Number(summary.items_returned || 0);
  if (totalCount !== undefined && totalCount !== null && totalCount !== '' && totalCount !== 'N/A') {
    bits.push(`${totalCount} resultados`);
  }
  if (itemsReturned > 0) {
    bits.push(`${itemsReturned} itens visiveis`);
  }
  const resultStatus = String(summary.result_status || '').trim().toLowerCase();
  if (resultStatus === 'blocked') {
    bits.push('bloqueada');
  } else if (['tool_error', 'exception'].includes(resultStatus) || summary.error_kind) {
    bits.push('erro');
  } else if (summary.has_error) {
    bits.push('com alertas');
  }
  return bits;
}

export default function ToolExecutionSummary({ details = [] }) {
  const rows = Array.isArray(details) ? details.filter(Boolean) : [];
  if (!rows.length) return null;

  return (
    <div
      style={{
        marginTop: 8,
        padding: '12px 14px',
        borderRadius: 14,
        background: 'rgba(16, 18, 23, 0.03)',
        border: '1px solid rgba(16, 18, 23, 0.06)',
      }}
    >
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: '0.04em',
          textTransform: 'uppercase',
          color: '#6E7480',
          marginBottom: 10,
        }}
      >
        Contexto usado
      </div>

      <div style={{ display: 'grid', gap: 8 }}>
        {rows.slice(0, 5).map((detail, index) => {
          const bits = buildSummaryBits(detail);
          const statusMeta = getToolStatusMeta(detail);
          return (
            <div
              key={`${detail.tool || 'tool'}-${index}`}
              title={statusMeta.title || undefined}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 10,
                cursor: statusMeta.title ? 'help' : undefined,
              }}
            >
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: statusMeta.dotColor,
                  marginTop: 6,
                  flexShrink: 0,
                }}
              />
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: '#14171D' }}>
                    {formatStreamingToolLabel(detail.tool)}
                  </div>
                  {statusMeta.label ? (
                    <span
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        padding: '2px 8px',
                        borderRadius: 999,
                        fontSize: 10,
                        fontWeight: 700,
                        letterSpacing: '0.01em',
                        background: statusMeta.chipBackground,
                        color: statusMeta.chipColor,
                      }}
                    >
                      {statusMeta.label}
                    </span>
                  ) : null}
                </div>
                {bits.length ? (
                  <div style={{ marginTop: 2, fontSize: 11, color: '#6E7480', lineHeight: 1.45 }}>
                    {bits.join(' · ')}
                  </div>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
