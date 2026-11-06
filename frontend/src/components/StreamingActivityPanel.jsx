import React from 'react';
import {
  ConversationIcon,
  RefreshIcon,
  SearchIcon,
  ThinkingIcon,
  WarningIcon,
} from './AppIcons.jsx';

const PHASE_CONFIG = {
  thinking: { icon: ThinkingIcon, badge: 'Em curso' },
  tools: { icon: SearchIcon, badge: 'Contexto' },
  writing: { icon: ConversationIcon, badge: 'Resposta' },
  blocked: { icon: WarningIcon, badge: 'Bloqueado' },
  done: { icon: RefreshIcon, badge: 'Concluido' },
  error: { icon: WarningIcon, badge: 'Erro' },
  idle: { icon: ThinkingIcon, badge: 'A preparar' },
};

// S5-01: Timeline phase definitions (ordered)
const TIMELINE_PHASES = [
  { key: 'thinking', label: 'Análise', icon: ThinkingIcon },
  { key: 'tools',    label: 'Ferramentas', icon: SearchIcon },
  { key: 'writing',  label: 'Resposta', icon: ConversationIcon },
];

const PHASE_ORDER = { idle: -1, thinking: 0, tools: 1, writing: 2, done: 3, blocked: -1, error: -1 };

function normalizeLabel(value) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

function formatDuration(ms) {
  if (ms == null) return null;
  const num = Number(ms);
  if (Number.isNaN(num) || num < 0) return null;
  if (num < 1000) return `${Math.round(num)}ms`;
  return `${(num / 1000).toFixed(1)}s`;
}

/**
 * S5-01: StreamingTimeline — horizontal phase bar showing Análise → Ferramentas → Resposta
 */
function StreamingTimeline({ currentPhase, events }) {
  const isError = currentPhase === 'error' || currentPhase === 'blocked';
  // For error state, determine which phase was last active from event history
  let effectiveIdx;
  if (isError) {
    // Scan events backwards to find last phase before error
    const phaseEvents = (events || []).filter(e => e && e.kind);
    let lastPhase = 'thinking'; // default: error during analysis
    for (let i = phaseEvents.length - 1; i >= 0; i--) {
      const ev = phaseEvents[i];
      if (ev.kind === 'tool') { lastPhase = 'tools'; break; }
      if (ev.kind === 'system' && ev.title === 'A redigir resposta') { lastPhase = 'writing'; break; }
    }
    effectiveIdx = PHASE_ORDER[lastPhase] ?? 0;
  } else {
    effectiveIdx = PHASE_ORDER[currentPhase] ?? -1;
  }

  return (
    <div className="stream-timeline">
      {TIMELINE_PHASES.map((tp, idx) => {
        const PhIcon = tp.icon;
        let state = 'pending';
        if (isError) {
          state = idx < effectiveIdx ? 'completed' : idx === effectiveIdx ? 'error' : 'pending';
        } else if (idx < effectiveIdx || currentPhase === 'done') {
          state = 'completed';
        } else if (idx === effectiveIdx) {
          state = 'active';
        }

        return (
          <React.Fragment key={tp.key}>
            {idx > 0 && (
              <div className={`stream-timeline-connector is-${state === 'pending' ? 'pending' : 'filled'}`} />
            )}
            <div className={`stream-timeline-step is-${state}`} title={tp.label}>
              <div className="stream-timeline-icon">
                <PhIcon size={12} />
              </div>
              <span className="stream-timeline-label">{tp.label}</span>
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
}

export default function StreamingActivityPanel({ trace, statusText }) {
  const rawEvents = Array.isArray(trace && trace.events) ? trace.events.slice(-5) : [];
  const phase = trace && trace.phase ? trace.phase : 'idle';
  const phaseLabel = statusText || (trace && trace.label) || 'A preparar resposta';
  const phaseConfig = PHASE_CONFIG[phase] || PHASE_CONFIG.idle;
  const PhaseIcon = phaseConfig.icon;
  const normalizedPhaseLabel = normalizeLabel(phaseLabel);

  const events = rawEvents.filter((event) => {
    if (!event) return false;
    if (phase === 'thinking' && event.kind === 'system') return false;

    const title = normalizeLabel(event.title);
    const detail = normalizeLabel(event.detail);
    if (title && title === normalizedPhaseLabel) return false;
    if (detail && detail === normalizedPhaseLabel) return false;
    return true;
  });

  if (!phaseLabel && events.length === 0) {
    return null;
  }

  return (
    <div className="stream-panel">
      {/* S5-01: Visual timeline bar */}
      <StreamingTimeline currentPhase={phase} events={rawEvents} />

      <div className="stream-panel-header">
        <div className={`stream-phase-pill is-${phase}`}>
          <PhaseIcon size={14} />
          <span>{phaseConfig.badge}</span>
        </div>
        <div className="stream-panel-status">{phaseLabel}</div>
      </div>

      {events.length ? (
        <div className="stream-panel-list">
          {events.map((event) => (
            <div
              key={event.id}
              className="stream-event-row"
              title={event.status === 'error' && event.detail ? `Erro: ${event.detail}` : undefined}
            >
              <span className={`stream-event-dot is-${event.status}`} />
              <div className="stream-event-copy">
                <div className="stream-event-title">
                  {event.title}
                  {event.status === 'completed' && formatDuration(event.durationMs) ? (
                    <span className="stream-event-duration">{formatDuration(event.durationMs)}</span>
                  ) : null}
                </div>
                {event.detail ? <div className="stream-event-detail">{event.detail}</div> : null}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
