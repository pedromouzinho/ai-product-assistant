import React from 'react';
import { AttachmentIcon, ImageIcon, MicrophoneIcon, SendIcon, StopIcon, WarningIcon } from './AppIcons.jsx';
import UploadProgressPanel from './UploadProgressPanel.jsx';
import FilePreviewPanel from './FilePreviewPanel.jsx';

export default function ChatComposer({
  uploadingFiles,
  uploadProgressText,
  pendingUploadJobs,
  onRetryUploadJob,
  activeUploadedFiles,
  activeFileMode,
  maxFilesPerConversation,
  imagePreviews,
  maxImagesPerMessage,
  onRemoveImage,
  onClearImages,
  modelTier,
  showFastAnalyticHint,
  tierRoutingNotice,
  fileInputRef,
  imageInputRef,
  loading,
  onFilePick,
  onImagePick,
  inputRef,
  input,
  onInputChange,
  onInputKeyDown,
  onInputPaste,
  inputPlaceholder,
  onSend,
  maxBatchTotalBytes,
  speechSupported,
  speechListening,
  speechStopping,
  speechProcessing,
  speechInterimText,
  speechNotice,
  speechSubmitMode,
  speechProvider,
  onToggleSpeech,
  onToggleSpeechSubmitMode,
  clarificationOptions,
  onClarificationSelect,
  pendingFiles,
  onRemovePendingFile,
  onConfirmPendingFiles,
  onCancelPendingFiles,
  showProSuggestion,
  onSwitchToPro,
}) {
  const hasClarification = Array.isArray(clarificationOptions) && clarificationOptions.length > 0 && !loading;
  return (
    <div className="app-input-bar">
      <div className="app-input-bar-inner">
        <UploadProgressPanel
          syncActive={uploadingFiles}
          syncText={uploadProgressText}
          pendingJobs={pendingUploadJobs}
          onRetryJob={onRetryUploadJob}
        />

        {/* S5-03: File preview before upload */}
        <FilePreviewPanel
          files={pendingFiles}
          onRemoveFile={onRemovePendingFile}
          onConfirmUpload={onConfirmPendingFiles}
          onCancelAll={onCancelPendingFiles}
        />

        {activeUploadedFiles.length > 0 && activeFileMode ? (
          <div className="app-banner accent">
            <AttachmentIcon size={18} />
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>
                {activeUploadedFiles.length}/{maxFilesPerConversation} ficheiro(s) anexado(s)
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', gap: 2 }}>
                {activeUploadedFiles.slice(-5).map((file, idx) => (
                  <div key={`uf-${idx}`}>
                    {`• ${file.filename} (${file.rows || 0} linhas${Array.isArray(file.columns) ? ` · ${file.columns.length} colunas` : ''})`}
                  </div>
                ))}
                {activeUploadedFiles.length > 5 ? <div>{`... +${activeUploadedFiles.length - 5} ficheiro(s)`}</div> : null}
              </div>
            </div>
          </div>
        ) : null}

        {imagePreviews.length > 0 ? (
          <div
            style={{
              maxWidth: 960,
              margin: '0 auto 10px',
              display: 'flex',
              gap: 8,
              flexWrap: 'wrap',
              background: 'rgba(var(--brand-accent-rgb), 0.04)',
              border: '1px solid rgba(var(--brand-accent-rgb), 0.12)',
              borderRadius: 16,
              padding: '10px 14px',
            }}
          >
            {imagePreviews.map((img, idx) => (
              <div key={idx} style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <img src={img.dataUrl} style={{ width: 44, height: 44, borderRadius: 10, objectFit: 'cover' }} />
                <div style={{ maxWidth: 100 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--brand-accent)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{img.filename}</div>
                  <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{img.size}</div>
                </div>
                <button
                  type="button"
                  className="chat-action-btn danger"
                  style={{ position: 'absolute', top: -8, right: -8, width: 22, height: 22, borderRadius: '50%', background: 'white' }}
                  onClick={() => onRemoveImage(idx)}
                >
                  ×
                </button>
              </div>
            ))}
            <button type="button" className="app-ghost-btn" style={{ marginLeft: 'auto' }} onClick={onClearImages}>
              Limpar
            </button>
            <div style={{ alignSelf: 'center', fontSize: 11, color: 'var(--text-muted)' }}>{imagePreviews.length}/{maxImagesPerMessage}</div>
          </div>
        ) : null}

        {modelTier === 'fast' && (showFastAnalyticHint || tierRoutingNotice) ? (
          <div className="app-banner accent">
            <WarningIcon size={16} />
            <div style={{ fontSize: 12, fontWeight: 600 }}>
              {tierRoutingNotice || 'No modo Fast, pedidos analíticos podem perder qualidade. Ao enviar, o sistema encaminha automaticamente para Thinking.'}
            </div>
          </div>
        ) : null}

        {/* S5-02: Pro tier suggestion for complex prompts */}
        {showProSuggestion && !loading ? (
          <div className="app-banner pro-suggestion">
            <svg width="16" height="16" viewBox="0 0 20 20" fill="none" style={{ flexShrink: 0 }}>
              <path d="M10 2l2.5 5.5L18 8.5l-4 4 1 5.5L10 15.5 4.5 18l1-5.5-4-4 5.5-1z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
            </svg>
            <div style={{ flex: 1, fontSize: 12, fontWeight: 600 }}>
              Pedido complexo detetado. O modelo Pro pode dar melhores resultados.
            </div>
            <button type="button" className="pro-suggestion-btn" onClick={onSwitchToPro}>
              Usar Pro
            </button>
          </div>
        ) : null}

        {speechListening || speechStopping || speechProcessing || speechNotice ? (
          <div className={`app-banner ${speechListening || speechStopping ? 'accent' : speechNotice ? 'warning' : 'accent'}`}>
            {speechListening || speechStopping ? <MicrophoneIcon size={18} /> : <WarningIcon size={16} />}
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: speechInterimText ? 4 : 0 }}>
                {speechListening
                  ? 'A ouvir... fala de forma natural'
                  : speechStopping
                    ? 'A terminar a captação de voz...'
                  : speechProcessing
                    ? 'A interpretar o pedido por voz...'
                    : speechNotice}
              </div>
              {speechInterimText ? (
                <div style={{ fontSize: 11, lineHeight: 1.5, color: 'var(--text-muted)' }}>
                  {speechInterimText}
                </div>
              ) : null}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-soft)', fontWeight: 700 }}>
              {speechProvider === 'azure_speech' ? 'Azure Speech' : 'Browser'}
            </div>
          </div>
        ) : null}

        {hasClarification ? (
          <div className="clarification-bar">
            <div className="clarification-label">Escolhe uma opção ou escreve a tua resposta:</div>
            <div className="clarification-pills">
              {clarificationOptions.map((opt, i) => (
                <button
                  key={i}
                  type="button"
                  className="clarification-pill"
                  onClick={() => onClarificationSelect(opt)}
                >
                  <span className="clarification-pill-number">{i + 1}</span>
                  {opt}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        <div className={`composer-shell${hasClarification ? ' clarification-active' : ''}`}>
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx,.xls,.csv,.txt,.pdf,.svg,.png,.jpg,.jpeg,.gif,.webp,.bmp,.pptx"
            multiple
            style={{ display: 'none' }}
            onChange={onFilePick}
          />
          <input
            ref={imageInputRef}
            type="file"
            accept="image/jpeg,image/png,image/gif,image/webp"
            multiple
            style={{ display: 'none' }}
            onChange={onImagePick}
          />

          <div className="composer-upload-cluster">
            <button
              type="button"
              className="composer-action-btn composer-upload-btn"
              onClick={() => fileInputRef.current && fileInputRef.current.click()}
              disabled={loading || uploadingFiles}
              title="Carregar ficheiro"
            >
              <AttachmentIcon size={18} />
            </button>

            <button
              type="button"
              className="composer-action-btn composer-upload-btn"
              onClick={() => imageInputRef.current && imageInputRef.current.click()}
              disabled={loading}
              title="Anexar imagens"
              style={imagePreviews.length > 0 ? {
                borderColor: 'rgba(var(--brand-accent-rgb), 0.22)',
                color: 'var(--brand-accent)',
                background: 'rgba(var(--brand-accent-rgb), 0.08)',
              } : undefined}
            >
              <ImageIcon size={18} />
            </button>
          </div>

          {speechSupported ? (
            <>
              <button
                type="button"
                className={`composer-action-btn composer-mode-btn${speechSubmitMode === 'auto' ? ' active' : ''}`}
                onClick={onToggleSpeechSubmitMode}
                disabled={loading || uploadingFiles || speechListening || speechStopping || speechProcessing}
                title={speechSubmitMode === 'auto' ? 'Modo atual: Auto-enviar. Clica para passar a Só texto.' : 'Modo atual: Só texto. Clica para passar a Auto-enviar.'}
              >
                {speechSubmitMode === 'auto' ? 'Auto' : 'Texto'}
              </button>
              <button
                type="button"
                className={`composer-action-btn${speechListening || speechStopping ? ' composer-mic-btn active' : ' composer-mic-btn'}`}
                onClick={onToggleSpeech}
                disabled={loading || uploadingFiles || speechProcessing}
                title={speechListening || speechStopping ? 'Parar gravação' : `Falar para o assistente (${speechSubmitMode === 'auto' ? 'Auto-enviar' : 'Só texto'})`}
              >
                {speechListening || speechStopping ? <StopIcon size={16} /> : <MicrophoneIcon size={18} />}
              </button>
            </>
          ) : null}

          <textarea
            ref={inputRef}
            value={input}
            onChange={onInputChange}
            onKeyDown={onInputKeyDown}
            onPaste={onInputPaste}
            placeholder={hasClarification ? 'Ou escreve a tua resposta aqui...' : inputPlaceholder}
            rows={1}
            className="composer-input"
            onInput={(event) => {
              event.target.style.height = 'auto';
              event.target.style.height = `${Math.min(event.target.scrollHeight, 120)}px`;
            }}
          />

          <button
            type="button"
            className="composer-action-btn composer-send-btn"
            onClick={onSend}
            disabled={!input.trim() || loading || uploadingFiles || speechListening || speechStopping || speechProcessing}
          >
            <SendIcon size={17} />
          </button>
        </div>

        <div style={{ maxWidth: 960, margin: '8px auto 0', textAlign: 'center', fontSize: 10, color: 'var(--text-soft)', fontWeight: 500, letterSpacing: '0.02em' }}>
          {speechListening
            ? `Estamos a captar a tua fala. Modo atual: ${speechSubmitMode === 'auto' ? 'Auto-enviar' : 'Só texto'}.`
            : speechStopping
              ? 'A terminar a captação de voz...'
            : speechProcessing
              ? 'A transformar a fala num prompt claro para o assistente...'
            : uploadingFiles
            ? 'A processar anexos. O envio da mensagem fica disponível no fim.'
            : `Enter para enviar · Shift+Enter para nova linha · anexa ficheiros · Ctrl+V para colar imagens · lote até ${Math.max(1, Math.round(maxBatchTotalBytes / (1024 * 1024)))}MB`}
        </div>
      </div>
    </div>
  );
}
