import React from 'react';
import { ConversationIcon, EditIcon, TrashIcon } from './AppIcons.jsx';

export default function ConversationListItem({
  title,
  meta,
  active = false,
  onSelect,
  onRename,
  onDelete,
  canDelete = true,
}) {
  return (
    <div
      className={`conversation-item${active ? ' active' : ''}`}
      onClick={onSelect}
      onDoubleClick={onRename}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onSelect();
        }
      }}
    >
      <div className="conversation-item-icon">
        <ConversationIcon size={16} />
      </div>

      <div className="conversation-item-copy">
        <div className="conversation-item-title">{title}</div>
        <div className="conversation-item-meta">{meta}</div>
      </div>

      <div className="conversation-item-actions">
        <button
          type="button"
          className="chat-action-btn"
          title="Renomear conversa"
          onClick={(event) => {
            event.stopPropagation();
            onRename();
          }}
        >
          <EditIcon size={15} />
        </button>
        {canDelete ? (
          <button
            type="button"
            className="chat-action-btn danger"
            title="Apagar conversa"
            onClick={(event) => {
              event.stopPropagation();
              onDelete();
            }}
          >
            <TrashIcon size={15} />
          </button>
        ) : null}
      </div>
    </div>
  );
}
