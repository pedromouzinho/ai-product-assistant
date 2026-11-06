import React, { useState } from 'react';
import TOOLS_CATALOG from '../data/toolsCatalog.js';

/**
 * ToolsManual — renders the full tools manual from toolsCatalog.js data.
 *
 * Data-driven: all descriptions, prompts and categories live in toolsCatalog.js.
 * This component only handles layout, icons and interaction.
 */

// Icon map keyed by iconKey from catalog
const SECTION_ICONS = {
    devops: (
        <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
            <rect x="2" y="3" width="16" height="14" rx="2" stroke="currentColor" strokeWidth="1.4" />
            <path d="M6 7h8M6 10h5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
    ),
    analytics: (
        <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
            <path d="M3 14l4-5 3 3 4-6 3 4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
            <rect x="2" y="3" width="16" height="14" rx="2" stroke="currentColor" strokeWidth="1.4" />
        </svg>
    ),
    export: (
        <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
            <path d="M4 4h5v5H4zM11 4h5v5h-5zM4 11h5v5H4zM11 11h5v5h-5z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
        </svg>
    ),
    upload: (
        <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
            <path d="M14 3v4h4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M4 3h10l4 4v10a1 1 0 01-1 1H4a1 1 0 01-1-1V4a1 1 0 011-1z" stroke="currentColor" strokeWidth="1.4" />
            <path d="M7 11h6M7 14h4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
    ),
    email: (
        <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
            <rect x="2" y="4" width="16" height="12" rx="2" stroke="currentColor" strokeWidth="1.4" />
            <path d="M2 6l8 5 8-5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
    ),
    search: (
        <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
            <circle cx="9" cy="9" r="6" stroke="currentColor" strokeWidth="1.4" />
            <path d="M13.5 13.5L17 17" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
    ),
    design: (
        <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
            <rect x="3" y="3" width="14" height="14" rx="2" stroke="currentColor" strokeWidth="1.4" />
            <path d="M7 7h6M7 10h4M7 13h5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
    ),
    star: (
        <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
            <path d="M10 2l2.5 5.5L18 8.5l-4 4 1 5.5L10 15.5 4.5 18l1-5.5-4-4 5.5-1z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
        </svg>
    ),
};

const PLAY_ICON = (
    <svg width="12" height="12" viewBox="0 0 20 20" fill="none" style={{ flexShrink: 0, marginTop: 2 }}>
        <path d="M7 4l9 6-9 6V4z" fill="currentColor" />
    </svg>
);

export default function ToolsManual({ onTryPrompt, onClose }) {
    const [expandedSection, setExpandedSection] = useState(0);

    const toggleSection = (idx) => {
        setExpandedSection(expandedSection === idx ? -1 : idx);
    };

    const totalTools = TOOLS_CATALOG.reduce((sum, s) => sum + s.tools.length, 0);

    return (
        <div className="tools-manual-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
            <div className="tools-manual-modal">
                <div className="tools-manual-header">
                    <div>
                        <h2 className="tools-manual-title">Manual de Funcionalidades</h2>
                        <p className="tools-manual-subtitle">
                            {TOOLS_CATALOG.length} categorias · {totalTools} ferramentas disponíveis
                        </p>
                    </div>
                    <button className="tools-manual-close" onClick={onClose}>✕</button>
                </div>

                <div className="tools-manual-body">
                    {TOOLS_CATALOG.map((section, sIdx) => {
                        const SectionIcon = SECTION_ICONS[section.iconKey] || SECTION_ICONS.star;

                        return (
                            <div key={section.id} className="tools-manual-section">
                                <button
                                    type="button"
                                    className={`tools-manual-section-header ${expandedSection === sIdx ? 'is-open' : ''}`}
                                    onClick={() => toggleSection(sIdx)}
                                >
                                    <span className="tools-manual-section-icon">{SectionIcon}</span>
                                    <span className="tools-manual-section-title">{section.title}</span>
                                    <span className="tools-manual-section-count">
                                        {section.tools.length} {section.tools.length === 1 ? 'ferramenta' : 'ferramentas'}
                                    </span>
                                    <span className={`tools-manual-chevron ${expandedSection === sIdx ? 'is-open' : ''}`}>
                                        <svg width="14" height="14" viewBox="0 0 20 20" fill="none">
                                            <path d="M6 8l4 4 4-4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                                        </svg>
                                    </span>
                                </button>

                                {expandedSection === sIdx && (
                                    <div className="tools-manual-section-body">
                                        {section.tools.map((tool) => (
                                            <div key={tool.name} className="tools-manual-tool">
                                                <div className="tools-manual-tool-name">{tool.name}</div>
                                                <div className="tools-manual-tool-desc">{tool.desc}</div>
                                                {tool.prompts.length > 0 && (
                                                    <div className="tools-manual-prompts">
                                                        <div className="tools-manual-prompts-label">Experimenta:</div>
                                                        {tool.prompts.map((prompt) => (
                                                            <button
                                                                key={prompt}
                                                                type="button"
                                                                className="tools-manual-prompt-btn"
                                                                onClick={() => {
                                                                    onTryPrompt(prompt);
                                                                    onClose();
                                                                }}
                                                            >
                                                                {PLAY_ICON}
                                                                <span>{prompt}</span>
                                                            </button>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}
