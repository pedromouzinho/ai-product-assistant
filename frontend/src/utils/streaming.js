const TOOL_LABELS = {
  query_hierarchy: 'Resolver hierarquia',
  search_figma: 'Pesquisar Figma',
  search_workitems: 'Pesquisar backlog',
  workitem_filter_followup: 'Refinar backlog',
  tool_search_figma: 'Pesquisar Figma',
  tool_search_workitems: 'Pesquisar backlog',
  tool_query_hierarchy: 'Resolver hierarquia',
};

export const EMPTY_STREAMING_TRACE = {
  phase: 'idle',
  label: '',
  events: [],
};

function uniqueId(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function cleanText(value) {
  return String(value || '')
    .replace(/[✅🔍⚠️]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function findLastEventIndex(events, predicate) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (predicate(events[index], index)) {
      return index;
    }
  }
  return -1;
}

export function formatStreamingToolLabel(tool) {
  const raw = String(tool || '').trim();
  if (!raw) return 'Tool';
  if (TOOL_LABELS[raw]) return TOOL_LABELS[raw];
  return raw
    .replace(/^tool_/, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function pushSystemEvent(events, title, status = 'running', detail = '') {
  const cleanedTitle = cleanText(title);
  const cleanedDetail = cleanText(detail);
  if (!cleanedTitle) return events;
  const nextEvents = [...events];
  const last = nextEvents[nextEvents.length - 1];
  if (last && last.kind === 'system' && last.title === cleanedTitle && last.status === status) {
    if (cleanedDetail) {
      last.detail = cleanedDetail;
      last.updatedAt = Date.now();
    }
    return nextEvents;
  }
  nextEvents.push({
    id: uniqueId('system'),
    kind: 'system',
    status,
    title: cleanedTitle,
    detail: cleanedDetail,
    updatedAt: Date.now(),
  });
  return nextEvents;
}

function upsertToolEvent(events, payload, status) {
  const tool = String(payload.tool || '').trim();
  const title = formatStreamingToolLabel(tool);
  const detail = cleanText(payload.error_detail || payload.text || payload.detail || '');
  const durationMs = payload.duration_ms != null ? Number(payload.duration_ms) : null;
  const resultStatus = String(payload.result_status || '').trim().toLowerCase();
  const errorKind = String(payload.error_kind || '').trim().toLowerCase();
  const nextEvents = [...events];
  const currentIndex = findLastEventIndex(
    nextEvents,
    (event) => event.kind === 'tool' && event.tool === tool && event.status === 'running'
  );
  if (currentIndex >= 0) {
    nextEvents[currentIndex] = {
      ...nextEvents[currentIndex],
      status,
      detail: detail || nextEvents[currentIndex].detail,
      durationMs: durationMs ?? nextEvents[currentIndex].durationMs,
      resultStatus: resultStatus || nextEvents[currentIndex].resultStatus,
      errorKind: errorKind || nextEvents[currentIndex].errorKind,
      updatedAt: Date.now(),
    };
    return nextEvents;
  }
  nextEvents.push({
    id: uniqueId('tool'),
    kind: 'tool',
    tool,
    status,
    title,
    detail,
    durationMs,
    resultStatus,
    errorKind,
    updatedAt: Date.now(),
  });
  return nextEvents;
}

function markLatestMatching(events, predicate, status) {
  const nextEvents = [...events];
  const currentIndex = findLastEventIndex(nextEvents, predicate);
  if (currentIndex >= 0) {
    nextEvents[currentIndex] = {
      ...nextEvents[currentIndex],
      status,
      updatedAt: Date.now(),
    };
  }
  return nextEvents;
}

export function applyStreamingTraceEvent(trace, eventType, payload = {}) {
  const current = trace || EMPTY_STREAMING_TRACE;
  const base = {
    phase: current.phase || 'idle',
    label: current.label || '',
    events: Array.isArray(current.events) ? [...current.events] : [],
  };

  switch (eventType) {
    case 'reset':
      return { ...EMPTY_STREAMING_TRACE };
    case 'thinking':
      return {
        phase: 'thinking',
        label: cleanText(payload.text || payload.tool || 'A analisar o pedido'),
        events: pushSystemEvent(base.events, payload.text || payload.tool || 'A analisar o pedido'),
      };
    case 'tool_start':
      return {
        phase: 'tools',
        label: `A executar ${formatStreamingToolLabel(payload.tool)}`,
        events: upsertToolEvent(base.events, payload, 'running'),
      };
    case 'tool_result':
      return {
        phase: 'tools',
        label: `${formatStreamingToolLabel(payload.tool)} concluido`,
        events: upsertToolEvent(
          base.events,
          payload,
          ['blocked', 'tool_error', 'exception'].includes(String(payload.result_status || '').trim().toLowerCase()) || Boolean(payload.error_kind)
            ? 'error'
            : 'completed'
        ),
      };
    case 'token': {
      const nextEvents = markLatestMatching(
        pushSystemEvent(base.events, 'A redigir resposta', 'running'),
        (event) => event.kind === 'system' && event.title === 'A analisar o pedido' && event.status === 'running',
        'completed'
      );
      return {
        phase: 'writing',
        label: 'A redigir resposta',
        events: nextEvents,
      };
    }
    case 'done': {
      const requestStatus = String(payload.request_status || '').trim().toLowerCase();
      const requestErrorKind = cleanText(payload.request_error_kind || '');
      const afterWriting = markLatestMatching(
        base.events,
        (event) => event.kind === 'system' && event.title === 'A redigir resposta' && event.status === 'running',
        'completed'
      );
      if (requestStatus === 'blocked') {
        return {
          phase: 'blocked',
          label: 'Pedido bloqueado',
          events: pushSystemEvent(afterWriting, 'Pedido bloqueado', 'error', requestErrorKind),
        };
      }
      if (requestStatus === 'error') {
        return {
          phase: 'error',
          label: 'Pedido com erro',
          events: pushSystemEvent(afterWriting, 'Pedido com erro', 'error', requestErrorKind),
        };
      }
      return {
        phase: 'done',
        label: 'Resposta pronta',
        events: afterWriting,
      };
    }
    case 'error':
      return {
        phase: 'error',
        label: cleanText(payload.text || payload.message || 'Erro no streaming'),
        events: pushSystemEvent(base.events, payload.text || payload.message || 'Erro no streaming', 'error'),
      };
    default:
      return base;
  }
}
