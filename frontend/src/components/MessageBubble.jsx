import React from 'react';
import { renderMarkdown } from '../utils/markdown.js';
import { BRAND_LOGO_DARK_TILE_URI } from '../utils/constants.js';
import { getFileDownloads, getChartSpecs, messageHasExportableData, getEmailClassificationData } from '../utils/messageHelpers.js';
import ToolBadges from './ToolBadges.jsx';
import ToolExecutionSummary from './ToolExecutionSummary.jsx';
import ChartBlock from './ChartBlock.jsx';
import EmailClassificationPreview from './EmailClassificationPreview.jsx';
import FeedbackWidget from './FeedbackWidget.jsx';
import QuickReplyBar from './QuickReplyBar.jsx';
import WriterProfileSuggestion from './WriterProfileSuggestion.jsx';

export default function MessageBubble({
  message,
  isLastAssistant,
  conversationId,
  messageIndex,
  onFeedback,
  onExport,
  onExportBundle,
  onFileDownload,
  onQuickReply,
  onLearnStyleAccept,
  onLearnStyleDismiss,
}) {
  const isUser = message.role === 'user';
  const content = typeof message.content === 'string' ? message.content : (message.text || '');
  const fileDownloads = getFileDownloads(message.tool_results);
  const tierLabels = { fast: 'Fast', standard: 'Thinking', pro: 'Pro' };
  const requestStatus = String(message.request_status || '').trim().toLowerCase();
  const requestErrorKind = String(message.request_error_kind || '').trim().toLowerCase();
  const requestOutcomeChip = requestStatus === 'blocked'
    ? {
        label: 'Pedido bloqueado',
        title: requestErrorKind ? `Bloqueio: ${requestErrorKind}` : 'Pedido bloqueado',
        background: 'rgba(168, 112, 0, 0.10)',
        border: '1px solid rgba(168, 112, 0, 0.18)',
        color: '#8A5A00',
      }
    : requestStatus === 'error'
      ? {
          label: 'Pedido com erro',
          title: requestErrorKind ? `Erro: ${requestErrorKind}` : 'Pedido com erro',
          background: 'rgba(160, 34, 34, 0.08)',
          border: '1px solid rgba(160, 34, 34, 0.14)',
          color: '#A02222',
        }
      : null;
  const metaBits = [];
  if (message.model_tier) {
    metaBits.push(tierLabels[message.model_tier] || String(message.model_tier));
  }
  // Modelo usado (nome curto para badge)
  const modelUsed = message.model_used || '';
  const modelShort = modelUsed
    ? modelUsed.replace(/^(azure-|anthropic-)/, '').replace(/-\d{4}.*$/, '').replace(/-latest$/, '')
    : '';
  // Fallback detection: use explicit backend signal
  const isFallback = !!message.is_fallback;
  if (message.tokens_used && message.tokens_used.total_tokens) {
    metaBits.push(`${Number(message.tokens_used.total_tokens).toLocaleString('pt-PT')} tokens`);
  }
  if (message.total_time_ms) {
    metaBits.push(`${(message.total_time_ms / 1000).toFixed(1)}s`);
  }

  if (isUser) {
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16, animation: 'fadeUp 0.3s ease' }}>
        <div>
          <div
            style={{
              background: '#1A1A1A',
              color: 'white',
              borderRadius: '18px 18px 4px 18px',
              padding: '14px 20px',
              maxWidth: 600,
              fontSize: 14,
              lineHeight: 1.6,
              wordBreak: 'break-word',
              boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
            }}
          >
            {content}
          </div>

          {message.images && message.images.length > 0 ? (
            <div style={{ display: 'flex', gap: 6, marginTop: 6, justifyContent: 'flex-end' }}>
              {message.images.map((img, i) => (
                <img
                  key={i}
                  src={img.url || img.dataUrl}
                  style={{ width: 60, height: 60, borderRadius: 10, objectFit: 'cover', border: '2px solid rgba(255,255,255,0.3)' }}
                />
              ))}
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 16, animation: 'fadeUp 0.3s ease' }}>
      <img src={BRAND_LOGO_DARK_TILE_URI} alt="AI Product Assistant" style={{ width: 32, height: 32, borderRadius: 10, flexShrink: 0 }} />

      <div style={{ flex: 1, minWidth: 0, maxWidth: 900 }}>
        <ToolBadges tools={message.tools_used} details={message.tool_details} />
        <ToolExecutionSummary details={message.tool_details} />
        {requestOutcomeChip ? (
          <div style={{ display: 'flex', marginBottom: 8 }}>
            <span
              title={requestOutcomeChip.title}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: '4px 10px',
                borderRadius: 999,
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: '0.01em',
                background: requestOutcomeChip.background,
                border: requestOutcomeChip.border,
                color: requestOutcomeChip.color,
              }}
            >
              {requestOutcomeChip.label}
            </span>
          </div>
        ) : null}

        <div
          className="msg-content"
          style={{
            background: 'white',
            borderRadius: '4px 18px 18px 18px',
            padding: '16px 22px',
            boxShadow: '0 1px 4px rgba(0,0,0,0.05)',
            fontSize: 14,
            lineHeight: 1.7,
            color: '#1a1a1a',
            wordBreak: 'break-word',
            border: '1px solid rgba(0,0,0,0.04)',
          }}
          dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }}
        />

        {/* Proactive quick replies (e.g. after CSV upload) */}
        {Array.isArray(message.quick_replies) && message.quick_replies.length > 0 && isLastAssistant ? (
          <QuickReplyBar options={message.quick_replies} onSelect={onQuickReply} />
        ) : null}

        {/* S4-01: Writer profile learning suggestion */}
        {message.suggest_learn_style && isLastAssistant ? (
          <WriterProfileSuggestion
            onAccept={onLearnStyleAccept}
            onDismiss={onLearnStyleDismiss}
          />
        ) : null}

        {getChartSpecs(message.tool_results).map((spec, ci) => (
          <ChartBlock key={`chart-${ci}`} chartSpec={spec} chartId={`chart-${messageIndex}-${ci}`} />
        ))}

        {/* S4-04: Email classification preview */}
        {(() => {
          const emailData = getEmailClassificationData(message.tool_results);
          return emailData ? <EmailClassificationPreview data={emailData} /> : null;
        })()}

        {fileDownloads.length > 0 ? (
          <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
            {fileDownloads.map((fd, fi) => (
              <button
                key={`${fd.download_id || 'file'}-${fi}`}
                className="export-btn"
                onClick={() => onFileDownload(fd)}
                style={{
                  background: fd.primary ? '#1A1A1A' : 'rgba(0,0,0,0.03)',
                  border: fd.primary ? '1px solid #1A1A1A' : '1px solid rgba(0,0,0,0.08)',
                  borderRadius: 8,
                  padding: '5px 12px',
                  cursor: 'pointer',
                  fontSize: 11,
                  color: fd.primary ? '#fff' : '#666',
                  fontWeight: 600,
                  fontFamily: "'Montserrat', sans-serif",
                  transition: 'all 0.2s ease',
                }}
                title={fd.description || fd.filename || 'download'}
              >
                {fd.label ? fd.label : `Download ${String(fd.format || 'file').toUpperCase()}`}
              </button>
            ))}
          </div>
        ) : null}

        {messageHasExportableData(message) ? (
          <div style={{ display: 'flex', gap: 4, marginTop: 8, flexWrap: 'wrap' }}>
            {['csv', 'xlsx', 'pdf', 'html'].map((fmt) => (
              <button
                key={fmt}
                className="export-btn"
                onClick={() => onExport(fmt, message.tool_results, message.export_index, messageIndex)}
                style={{
                  background: 'rgba(0,0,0,0.03)',
                  border: '1px solid rgba(0,0,0,0.08)',
                  borderRadius: 8,
                  padding: '5px 12px',
                  cursor: 'pointer',
                  fontSize: 11,
                  color: '#666',
                  fontWeight: 600,
                  fontFamily: "'Montserrat', sans-serif",
                  transition: 'all 0.2s ease',
                }}
              >
                {fmt.toUpperCase()}
              </button>
            ))}
            <button
              key="bundle"
              className="export-btn"
              onClick={() => onExportBundle(message.tool_results, message.export_index, messageIndex)}
              style={{
                background: 'rgba(0,0,0,0.03)',
                border: '1px solid rgba(0,0,0,0.08)',
                borderRadius: 8,
                padding: '5px 12px',
                cursor: 'pointer',
                fontSize: 11,
                color: '#666',
                fontWeight: 600,
                fontFamily: "'Montserrat', sans-serif",
                transition: 'all 0.2s ease',
              }}
              >
              Bundle ZIP
            </button>
          </div>
        ) : null}

        {(metaBits.length > 0 || modelShort) ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6, paddingLeft: 2, flexWrap: 'wrap' }}>
            {modelShort && (
              <span className={`model-badge${isFallback ? ' model-badge-fallback' : ''}`}>
                {modelShort}
              </span>
            )}
            {isFallback && (
              <span className="model-fallback-note">Modelo alternativo usado</span>
            )}
            {metaBits.length > 0 && (
              <span style={{ fontSize: 10, color: '#ccc', fontWeight: 500 }}>
                {metaBits.join(' · ')}
              </span>
            )}
          </div>
        ) : null}

        {!isUser && conversationId ? (
          <FeedbackWidget
            conversationId={conversationId}
            messageIndex={messageIndex}
            onSubmit={onFeedback}
            compact={!isLastAssistant}
          />
        ) : null}
      </div>
    </div>
  );
}
