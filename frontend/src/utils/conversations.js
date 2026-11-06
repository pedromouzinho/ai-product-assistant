import { EMPTY_CONVERSATION } from './constants.js';

export function createConversation(mode = 'general') {
  return {
    ...EMPTY_CONVERSATION,
    title: 'Nova conversa',
    messages: [],
    mode,
    uploadedFiles: [],
    titleManuallyEdited: false,
    updatedAt: '',
    savedOnServer: false,
  };
}

export function getConversationKey(conv, idx) {
  return conv && conv.id ? `saved:${conv.id}` : `draft:${idx}`;
}

export function sanitizeConversationTitle(value) {
  const normalized = String(value || '').replace(/\s+/g, ' ').trim();
  return normalized ? normalized.slice(0, 100) : 'Nova conversa';
}

export function formatRelativeTimestamp(value) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  const diffMs = Date.now() - parsed.getTime();
  if (diffMs < 0) return parsed.toLocaleDateString('pt-PT', { day: '2-digit', month: 'short' });
  const diffMinutes = Math.round(diffMs / 60000);
  if (diffMinutes < 1) return 'agora';
  if (diffMinutes < 60) return `há ${diffMinutes} min`;
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) return `há ${diffHours} h`;
  const diffDays = Math.round(diffHours / 24);
  if (diffDays === 1) return 'ontem';
  if (diffDays < 7) return `há ${diffDays} dias`;
  return parsed.toLocaleDateString('pt-PT', { day: '2-digit', month: 'short' });
}

export function getConversationMetaLabel(conv) {
  const msgLen = Array.isArray(conv && conv.messages) ? conv.messages.length : 0;
  const count = msgLen > 0 ? msgLen : Number((conv && conv.message_count) || 0);
  const countLabel = count > 0 ? `${count} msgs` : 'Sem mensagens';
  const relative = formatRelativeTimestamp(conv && conv.updatedAt);
  return relative ? `${countLabel} · ${relative}` : countLabel;
}
