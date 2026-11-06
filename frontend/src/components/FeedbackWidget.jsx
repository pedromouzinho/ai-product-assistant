import React, { useState } from 'react';

const QUICK_TAGS = [
  { label: 'Impreciso', value: 'impreciso' },
  { label: 'Lento', value: 'lento' },
  { label: 'Off-topic', value: 'off-topic' },
  { label: 'Incompleto', value: 'incompleto' },
  { label: 'Confuso', value: 'confuso' },
];

function ThumbUp({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M7 22V11L2 13V22H7Z" /><path d="M14 9V5.5C14 3.57 12.43 2 10.5 2L7 11V22H18.28C19.14 22 19.88 21.39 20.02 20.54L21.36 12.54C21.54 11.45 20.69 10.46 19.58 10.46H14Z" />
    </svg>
  );
}

function ThumbDown({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 2V13L22 11V2H17Z" /><path d="M10 15V18.5C10 20.43 11.57 22 13.5 22L17 13V2H5.72C4.86 2 4.12 2.61 3.98 3.46L2.64 11.46C2.46 12.55 3.31 13.54 4.42 13.54H10Z" />
    </svg>
  );
}

export default function FeedbackWidget({ conversationId, messageIndex, onSubmit, compact = false }) {
  const [submitted, setSubmitted] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [showNote, setShowNote] = useState(false);
  const [showTags, setShowTags] = useState(false);
  const [note, setNote] = useState('');
  const [selectedRating, setSelectedRating] = useState(0);
  const [selectedTags, setSelectedTags] = useState([]);

  function handleThumb(positive) {
    const rating = positive ? 8 : 3;
    setSelectedRating(rating);
    if (positive) {
      setSubmitted(true);
      onSubmit(conversationId, messageIndex, rating, '');
    } else {
      setShowTags(true);
    }
  }

  function toggleTag(tag) {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  }

  function submitTags() {
    const tagStr = selectedTags.join(', ');
    const combined = [tagStr, note].filter(Boolean).join(' — ');
    setSubmitted(true);
    onSubmit(conversationId, messageIndex, selectedRating, combined);
  }

  function handleRatingClick(n) {
    setSelectedRating(n);
    if (n <= 4) {
      setShowTags(true);
      return;
    }
    setSubmitted(true);
    onSubmit(conversationId, messageIndex, n, '');
  }

  function submitWithNote() {
    const tagStr = selectedTags.join(', ');
    const combined = [tagStr, note].filter(Boolean).join(' — ');
    setSubmitted(true);
    onSubmit(conversationId, messageIndex, selectedRating, combined);
  }

  // ── Submitted state ──
  if (submitted) {
    return (
      <div className="feedback-submitted">
        {'✓ Obrigado! '}
        <span className="feedback-rating">{`${selectedRating}/10`}</span>
      </div>
    );
  }

  // ── Quick tags + optional note (after negative feedback) ──
  if (showTags) {
    return (
      <div className="feedback-tags-panel">
        <div className="feedback-tags-label">
          {`Rating: ${selectedRating}/10 — O que correu mal?`}
        </div>
        <div className="feedback-tags-row">
          {QUICK_TAGS.map((tag) => (
            <button
              key={tag.value}
              type="button"
              className={`feedback-tag${selectedTags.includes(tag.value) ? ' active' : ''}`}
              onClick={() => toggleTag(tag.value)}
            >
              {tag.label}
            </button>
          ))}
        </div>
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Detalhe opcional..."
          onKeyDown={(e) => { if (e.key === 'Enter') submitTags(); }}
          className="feedback-note-input"
        />
        <div className="feedback-actions">
          <button type="button" className="feedback-btn-primary" onClick={submitTags}>
            Enviar
          </button>
          <button
            type="button"
            className="feedback-btn-secondary"
            onClick={() => {
              setSubmitted(true);
              onSubmit(conversationId, messageIndex, selectedRating, '');
            }}
          >
            Saltar
          </button>
        </div>
      </div>
    );
  }

  // ── Expanded 1-10 scale (clicked "Avaliar" on compact) ──
  if (expanded) {
    return (
      <div className="feedback-scale-row">
        <span className="feedback-scale-label">Avaliar:</span>
        {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => (
          <button
            key={n}
            type="button"
            className="feedback-scale-btn"
            onClick={() => handleRatingClick(n)}
            data-tier={n <= 4 ? 'low' : n <= 6 ? 'mid' : 'high'}
          >
            {n}
          </button>
        ))}
      </div>
    );
  }

  // ── Compact: thumbs up / down ──
  return (
    <div className="feedback-compact">
      <button
        type="button"
        className="feedback-thumb-btn up"
        onClick={() => handleThumb(true)}
        title="Boa resposta"
      >
        <ThumbUp size={13} />
      </button>
      <button
        type="button"
        className="feedback-thumb-btn down"
        onClick={() => handleThumb(false)}
        title="Resposta fraca"
      >
        <ThumbDown size={13} />
      </button>
      {!compact && (
        <button
          type="button"
          className="feedback-expand-btn"
          onClick={() => setExpanded(true)}
          title="Avaliar de 1 a 10"
        >
          1-10
        </button>
      )}
    </div>
  );
}
