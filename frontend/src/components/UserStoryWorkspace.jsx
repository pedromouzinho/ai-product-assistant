import React, { useEffect, useMemo, useState } from 'react';
import { API_URL } from '../utils/constants.js';
import { authFetch, getAuthHeaders } from '../utils/auth.js';
import UserStoryEvalPanel from './UserStoryEvalPanel.jsx';

const EMPTY_FORM = {
    objective: '',
    team_scope: '',
    epic_or_feature: '',
    context: '',
    reference_author: '',
};

function stageLabel(name) {
    const labels = {
        intake: 'Intake',
        retrieval: 'Retrieval',
        context_pack: 'Context Pack',
        draft: 'Draft',
        validator: 'Validator',
    };
    return labels[name] || name;
}

function confidenceTone(value) {
    const score = Number(value || 0);
    if (score >= 0.82) return { bg: 'rgba(16, 185, 129, 0.12)', color: '#0f766e', label: 'Alta confiança' };
    if (score >= 0.62) return { bg: 'rgba(245, 158, 11, 0.12)', color: '#b45309', label: 'Confiança média' };
    return { bg: 'rgba(99, 102, 241, 0.10)', color: '#b0103a', label: 'Confiança baixa' };
}

function listOrFallback(items, fallback) {
    return Array.isArray(items) && items.length > 0 ? items : [fallback];
}

function cloneDraft(draft) {
    if (!draft || typeof draft !== 'object') return null;
    try {
        return JSON.parse(JSON.stringify(draft));
    } catch {
        return { ...draft };
    }
}

function linesFromList(items) {
    return (Array.isArray(items) ? items : []).map(item => String(item || '').trim()).filter(Boolean).join('\n');
}

function listFromLines(text) {
    return String(text || '').split('\n').map(line => line.trim()).filter(Boolean);
}

function pickPreviewEvidence(contextPreview) {
    if (!contextPreview || typeof contextPreview !== 'object') return [];
    const items = [
        ...(Array.isArray(contextPreview.curated_examples) ? contextPreview.curated_examples.map(item => ({
            title: item.title || 'Exemplo curado',
            meta: [item.domain, item.author].filter(Boolean).join(' · '),
            snippet: item.provenance_excerpt || item.behavior_excerpt || item.title_pattern || '',
        })) : []),
        ...(Array.isArray(contextPreview.design_flow) ? contextPreview.design_flow.map(item => ({
            title: item.title || 'Frame',
            meta: [item.domain, Array.isArray(item.ui_components) ? item.ui_components.slice(0, 3).join(', ') : ''].filter(Boolean).join(' · '),
            snippet: item.snippet || '',
        })) : []),
        ...(Array.isArray(contextPreview.sources) ? contextPreview.sources.map(item => ({
            title: item.title || item.key || 'Fonte',
            meta: item.type || item.origin || '',
            snippet: item.snippet || '',
        })) : []),
    ];
    const seen = new Set();
    return items.filter(item => {
        const key = `${item.title}|${item.meta}`.toLowerCase();
        if (!item.title || seen.has(key)) return false;
        seen.add(key);
        return true;
    }).slice(0, 4);
}

function summarizePlacement(placement) {
    const data = placement && typeof placement === 'object' ? placement : {};
    const feature = data.selected_feature && typeof data.selected_feature === 'object' ? data.selected_feature : {};
    const epic = data.selected_epic && typeof data.selected_epic === 'object' ? data.selected_epic : {};
    return feature.title || epic.title || data.resolved_area_path || 'Ainda sem placement claro';
}

function summarizeOpenQuestions(contextPreview) {
    if (!contextPreview || typeof contextPreview !== 'object') return [];
    const missing = Array.isArray(contextPreview.missing_fields) ? contextPreview.missing_fields : [];
    const questions = Array.isArray(contextPreview.clarification_questions) ? contextPreview.clarification_questions : [];
    return [...missing.map(item => `Falta: ${item}`), ...questions].slice(0, 4);
}

function acceptanceCriteriaToText(items) {
    return (Array.isArray(items) ? items : []).map(item => {
        if (item && typeof item === 'object') {
            const id = String(item.id || '').trim();
            const text = String(item.text || '').trim();
            return id ? `${id} | ${text}` : text;
        }
        return String(item || '').trim();
    }).filter(Boolean).join('\n');
}

function acceptanceCriteriaFromText(text) {
    return String(text || '').split('\n').map((line, index) => {
        const raw = String(line || '').trim();
        if (!raw) return null;
        const [left, ...rest] = raw.split('|');
        const hasExplicitId = rest.length > 0;
        const id = hasExplicitId ? String(left || '').trim() : `CA-${String(index + 1).padStart(2, '0')}`;
        const body = hasExplicitId ? rest.join('|').trim() : raw;
        return body ? { id, text: body } : null;
    }).filter(Boolean);
}

export default function UserStoryWorkspace({ conversation, uploadedFiles = [], onConversationUpdate, user = null }) {
    const [form, setForm] = useState(EMPTY_FORM);
    const [contextPreview, setContextPreview] = useState(null);
    const [draftPayload, setDraftPayload] = useState(null);
    const [editableDraft, setEditableDraft] = useState(null);
    const [workspaceError, setWorkspaceError] = useState('');
    const [busyAction, setBusyAction] = useState('');
    const [publishResult, setPublishResult] = useState(null);
    const [feedbackOutcome, setFeedbackOutcome] = useState('');
    const [feedbackNote, setFeedbackNote] = useState('');
    const [showAdvancedInputs, setShowAdvancedInputs] = useState(false);
    const [showDiagnostics, setShowDiagnostics] = useState(false);

    const conversationId = (conversation && conversation.id) ? conversation.id : '';
    const confidenceMeta = useMemo(
        () => confidenceTone(draftPayload && draftPayload.confidence),
        [draftPayload]
    );
    const activeDraft = useMemo(
        () => editableDraft || (draftPayload && draftPayload.draft) || null,
        [editableDraft, draftPayload]
    );
    const previewEvidence = useMemo(() => pickPreviewEvidence(contextPreview), [contextPreview]);
    const previewPlacement = useMemo(() => summarizePlacement(contextPreview && contextPreview.placement), [contextPreview]);
    const previewOpenQuestions = useMemo(() => summarizeOpenQuestions(contextPreview), [contextPreview]);
    const uploadedFileNames = useMemo(
        () => (Array.isArray(uploadedFiles) ? uploadedFiles.map(item => String(item && item.filename ? item.filename : '')).filter(Boolean) : []),
        [uploadedFiles]
    );

    useEffect(() => {
        setContextPreview(null);
        setDraftPayload(null);
        setEditableDraft(null);
        setWorkspaceError('');
        setPublishResult(null);
        setFeedbackOutcome('');
        setFeedbackNote('');
        setShowAdvancedInputs(false);
        setShowDiagnostics(false);
    }, [conversationId]);

    async function postJson(path, body) {
        const res = await authFetch(API_URL + path, {
            method: 'POST',
            headers: getAuthHeaders(),
            body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            throw new Error(data.detail || data.error || `Erro ${res.status}`);
        }
        return data;
    }

    function buildPayload() {
        return {
            conversation_id: conversationId || null,
            objective: String(form.objective || '').trim(),
            team_scope: String(form.team_scope || '').trim(),
            epic_or_feature: String(form.epic_or_feature || '').trim() || null,
            context: String(form.context || '').trim() || null,
            reference_author: String(form.reference_author || '').trim() || null,
        };
    }

    function syncConversation(data) {
        if (!data || !onConversationUpdate) return;
        const nextId = data.conversation_id || conversationId || null;
        const nextTitle = String(form.objective || '').trim().slice(0, 48) || (conversation && conversation.title) || 'Nova interação';
        onConversationUpdate({
            id: nextId,
            title: nextTitle,
        });
    }

    async function handlePreview() {
        const payload = buildPayload();
        if (!payload.objective || !payload.team_scope) {
            setWorkspaceError('Objetivo e equipa/área são obrigatórios.');
            return;
        }
        setWorkspaceError('');
        setBusyAction('preview');
        try {
            const data = await postJson('/api/user-stories/context-preview', payload);
            setContextPreview(data);
            syncConversation(data);
        } catch (err) {
            setWorkspaceError(err.message || 'Falha ao montar contexto.');
        } finally {
            setBusyAction('');
        }
    }

    async function handleGenerate() {
        const payload = buildPayload();
        if (!payload.objective || !payload.team_scope) {
            setWorkspaceError('Objetivo e equipa/área são obrigatórios.');
            return;
        }
        setWorkspaceError('');
        setBusyAction('generate');
        try {
            const data = await postJson('/api/user-stories/generate', payload);
            setContextPreview({
                conversation_id: data.conversation_id,
                stages: data.stages,
                sources: data.sources,
                similar_items: data.similar_items,
                domain_profile: data.domain_profile,
                story_policy_pack: data.story_policy_pack,
                design_flow: data.design_flow,
                feature_siblings: data.feature_siblings,
                feature_pack: data.feature_pack,
                curated_workitem_refs: data.curated_workitem_refs,
                missing_fields: data.missing_fields,
                clarification_questions: data.clarification_questions,
                design_sources: data.design_sources,
                curated_examples: data.curated_examples,
                placement: data.placement,
            });
            setDraftPayload(data);
            setEditableDraft(cloneDraft(data.draft));
            setPublishResult(null);
            syncConversation(data);
        } catch (err) {
            setWorkspaceError(err.message || 'Falha a gerar draft.');
        } finally {
            setBusyAction('');
        }
    }

    async function handleValidate() {
        if (!draftPayload || !draftPayload.draft_id) return;
        setWorkspaceError('');
        setBusyAction('validate');
        try {
            const data = await postJson('/api/user-stories/validate', {
                draft_id: draftPayload.draft_id,
                draft: editableDraft || undefined,
            });
            setDraftPayload(prev => ({
                ...(prev || {}),
                draft: data.draft,
                validation: data.validation,
                publish_ready: data.publish_ready,
            }));
            setEditableDraft(cloneDraft(data.draft));
        } catch (err) {
            setWorkspaceError(err.message || 'Falha a validar draft.');
        } finally {
            setBusyAction('');
        }
    }

    async function handlePublish() {
        if (!draftPayload || !draftPayload.draft_id) return;
        setWorkspaceError('');
        setBusyAction('publish');
        try {
            const data = await postJson('/api/user-stories/publish', {
                draft_id: draftPayload.draft_id,
                area_path: form.team_scope,
                tags: 'AI-Draft',
                final_draft: editableDraft || undefined,
            });
            setPublishResult(data);
        } catch (err) {
            setWorkspaceError(err.message || 'Falha a publicar no DevOps.');
        } finally {
            setBusyAction('');
        }
    }

    async function handleFeedback(outcome) {
        if (!draftPayload || !draftPayload.draft_id) return;
        setWorkspaceError('');
        setBusyAction(`feedback:${outcome}`);
        try {
            await postJson('/api/user-stories/feedback', {
                draft_id: draftPayload.draft_id,
                outcome,
                note: feedbackNote || null,
                final_draft: editableDraft || undefined,
            });
            setFeedbackOutcome(outcome);
        } catch (err) {
            setWorkspaceError(err.message || 'Falha a registar feedback.');
        } finally {
            setBusyAction('');
        }
    }

    return React.createElement('div', {
        style: {
            background: 'white',
            border: '1px solid rgba(0,0,0,0.06)',
            borderRadius: 20,
            padding: 22,
            marginBottom: 20,
            boxShadow: '0 8px 30px rgba(0,0,0,0.04)',
            animation: 'fadeUp 0.25s ease',
        }
    },
        React.createElement('div', {
            style: {
                display: 'flex',
                justifyContent: 'space-between',
                gap: 16,
                alignItems: 'flex-start',
                flexWrap: 'wrap',
                marginBottom: 18,
            }
        },
            React.createElement('div', null,
                React.createElement('div', {
                    style: { fontSize: 18, fontWeight: 700, color: '#1a1a1a', marginBottom: 4 }
                }, 'Lane dedicada de User Stories'),
                React.createElement('div', {
                    style: { fontSize: 12, color: '#777', maxWidth: 760, lineHeight: 1.6 }
                },
                    'Dá-me a jornada, a área e os anexos certos. A lane tenta encaixar a story na feature certa, usa exemplos parecidos e devolve um rascunho mais próximo das stories atuais. O diagnóstico técnico fica escondido por defeito.'
                )
            ),
            React.createElement('div', {
                style: {
                    minWidth: 240,
                    background: 'linear-gradient(135deg, rgba(26,26,26,0.96), rgba(56,56,56,0.96))',
                    color: 'white',
                    borderRadius: 18,
                    padding: '14px 16px',
                }
            },
                React.createElement('div', { style: { fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.6, opacity: 0.7, marginBottom: 4 } }, 'Inputs ideais'),
                React.createElement('div', { style: { fontSize: 12, lineHeight: 1.7, opacity: 0.95 } },
                    'CSV de user stories boas, sitemap, mockups e SVG anexados melhoram muito o grounding. Para SVG grande, anexa o ficheiro; não vale a pena colar o XML no texto.'
                )
            )
        ),

        React.createElement('div', {
            style: { display: 'grid', gridTemplateColumns: '1.1fr 1.1fr', gap: 12, marginBottom: 12 }
        },
            React.createElement('div', null,
                React.createElement('label', {
                    style: { display: 'block', fontSize: 11, fontWeight: 700, color: '#666', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }
                }, 'Jornada / objetivo pretendido'),
                React.createElement('textarea', {
                    value: form.objective,
                    onChange: e => setForm(prev => ({ ...prev, objective: e.target.value })),
                    rows: 4,
                    placeholder: 'Ex: o utilizador entra em Cartões > Via Verde, escolhe ativar o contrato, preenche os dados e chega ao Step 2 de resumo e autorização.',
                    style: textareaStyle(),
                })
            ),
            React.createElement('div', null,
                React.createElement('label', {
                    style: { display: 'block', fontSize: 11, fontWeight: 700, color: '#666', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }
                }, 'Equipa / área funcional'),
                React.createElement('input', {
                    value: form.team_scope,
                    onChange: e => setForm(prev => ({ ...prev, team_scope: e.target.value })),
                    placeholder: 'Ex: Produto Digital · ACME',
                    style: inputStyle(),
                }),
                React.createElement('label', {
                    style: { display: 'block', fontSize: 11, fontWeight: 700, color: '#666', textTransform: 'uppercase', letterSpacing: 0.5, margin: '12px 0 6px' }
                }, 'Épico / feature / item DevOps (opcional)'),
                React.createElement('input', {
                    value: form.epic_or_feature,
                    onChange: e => setForm(prev => ({ ...prev, epic_or_feature: e.target.value })),
                    placeholder: 'ID, URL ou nome do épico/feature',
                    style: inputStyle(),
                })
            )
        ),

        React.createElement('div', {
            style: { display: 'grid', gridTemplateColumns: '1.2fr 0.8fr', gap: 12, marginBottom: 14 }
        },
            React.createElement('div', null,
                React.createElement('label', {
                    style: { display: 'block', fontSize: 11, fontWeight: 700, color: '#666', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }
                }, 'Contexto adicional, termos do site e notas de proveniência'),
                React.createElement('textarea', {
                    value: form.context,
                    onChange: e => setForm(prev => ({ ...prev, context: e.target.value })),
                    rows: 4,
                    placeholder: 'Inclui entrypoint da jornada, step/estado, regras de negócio, CTA, labels, sitemap, referência a story anterior ou observações do mockup.',
                    style: textareaStyle(),
                })
            ),
            React.createElement('div', null,
                React.createElement('div', {
                    style: {
                        borderRadius: 14,
                        background: '#FAF7F2',
                        border: '1px solid rgba(0,0,0,0.06)',
                        padding: '12px 14px',
                    }
                },
                    React.createElement('div', {
                        style: { fontSize: 11, fontWeight: 700, color: '#8b6a45', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }
                    }, 'Contexto já anexado'),
                    React.createElement('div', { style: { fontSize: 12, color: '#6b5a45', lineHeight: 1.6 } },
                        uploadedFileNames.length > 0
                            ? `${uploadedFileNames.length} ficheiro(s) disponíveis nesta conversa: ${uploadedFileNames.slice(0, 3).join(', ')}${uploadedFileNames.length > 3 ? '…' : ''}.`
                            : 'Sem ficheiros anexados nesta conversa. Se quiseres reforçar o grounding, anexa CSVs, docs, sitemap ou mockups.'
                    )
                ),
                React.createElement('button', {
                    type: 'button',
                    onClick: () => setShowAdvancedInputs(prev => !prev),
                    style: {
                        marginTop: 10,
                        padding: '10px 12px',
                        borderRadius: 12,
                        border: '1px solid rgba(0,0,0,0.08)',
                        background: '#fff',
                        color: '#444',
                        fontSize: 12,
                        fontWeight: 700,
                        cursor: 'pointer',
                    }
                }, showAdvancedInputs ? 'Esconder opções avançadas' : 'Mostrar opções avançadas'),
                showAdvancedInputs && React.createElement('div', {
                    style: {
                        marginTop: 10,
                        borderRadius: 14,
                        background: '#FBFBFB',
                        border: '1px solid rgba(0,0,0,0.06)',
                        padding: '12px 14px',
                    }
                },
                    React.createElement('label', {
                        style: { display: 'block', fontSize: 11, fontWeight: 700, color: '#666', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }
                    }, 'Autor de referência (opcional)'),
                    React.createElement('input', {
                        value: form.reference_author,
                        onChange: e => setForm(prev => ({ ...prev, reference_author: e.target.value })),
                        placeholder: 'Ex: Jane Doe',
                        style: inputStyle(),
                    }),
                    React.createElement('div', {
                        style: { marginTop: 8, fontSize: 12, color: '#777', lineHeight: 1.6 }
                    }, 'Isto só serve para afinar estilo de escrita quando fizer mesmo sentido. Não é preciso para a lane funcionar.')
                )
            )
        ),

        React.createElement('div', {
            style: { display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }
        },
            React.createElement('button', {
                onClick: handlePreview,
                disabled: !!busyAction,
                style: actionButtonStyle('#ffffff', '#1a1a1a', '1.5px solid rgba(0,0,0,0.08)'),
            }, busyAction === 'preview' ? 'A analisar contexto...' : 'Analisar contexto'),
            React.createElement('button', {
                onClick: handleGenerate,
                disabled: !!busyAction,
                style: actionButtonStyle('#1A1A1A', 'white', '1px solid #1A1A1A'),
            }, busyAction === 'generate' ? 'A gerar rascunho...' : 'Gerar rascunho'),
            draftPayload && React.createElement('button', {
                onClick: handleValidate,
                disabled: !!busyAction,
                style: actionButtonStyle('rgba(99, 102, 241,0.08)', '#b0103a', '1px solid rgba(99, 102, 241,0.16)'),
            }, busyAction === 'validate' ? 'A validar...' : 'Validar'),
            draftPayload && React.createElement('button', {
                onClick: handlePublish,
                disabled: !!busyAction || !(draftPayload.publish_ready),
                style: actionButtonStyle('#6366F1', 'white', '1px solid #6366F1', !(draftPayload.publish_ready)),
            }, busyAction === 'publish' ? 'A publicar...' : 'Publicar draft no DevOps'),
            workspaceError && React.createElement('div', {
                style: { fontSize: 12, color: '#b0103a', fontWeight: 600 }
            }, workspaceError)
        ),

        contextPreview && React.createElement('div', {
            style: {
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
                gap: 14,
                marginBottom: 16,
            }
        },
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'Onde isto encaixa'),
                React.createElement('div', { style: { fontSize: 14, fontWeight: 700, color: '#222', lineHeight: 1.5, marginBottom: 8 } }, previewPlacement),
                React.createElement('div', { style: { fontSize: 12, color: '#666', lineHeight: 1.6 } }, `Confiança ${Number((contextPreview.placement && contextPreview.placement.confidence) || 0).toFixed(2)}`)
            ),
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'Evidência principal'),
                React.createElement('div', { style: { fontSize: 12, color: '#444', lineHeight: 1.7 } },
                    listOrFallback(previewEvidence, { title: 'Sem evidência destacada', meta: '', snippet: 'Anexa CSV, sitemap, docs ou mockups para reforçar o grounding.' }).map((item, idx) =>
                        React.createElement('div', { key: `evidence-${idx}`, style: { marginBottom: idx === previewEvidence.length - 1 ? 0 : 8 } },
                            React.createElement('div', { style: { fontWeight: 700, color: '#222' } }, item.title),
                            item.meta && React.createElement('div', { style: { color: '#777' } }, item.meta),
                            item.snippet && React.createElement('div', { style: { color: '#555' } }, item.snippet)
                        )
                    )
                )
            ),
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'O que falta confirmar'),
                React.createElement('div', { style: { fontSize: 12, color: '#444', lineHeight: 1.7 } },
                    listOrFallback(previewOpenQuestions, 'Sem gaps críticos detetados.').map(item =>
                        React.createElement('div', { key: `open-${item}` }, `• ${item}`)
                    )
                )
            )
        ),

        contextPreview && React.createElement('div', {
            style: { marginBottom: 16, display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center' }
        },
            React.createElement('div', { style: { fontSize: 12, color: '#666', lineHeight: 1.6 } },
                'O detalhe técnico continua disponível, mas fica escondido por defeito para não poluir o fluxo.'
            ),
            React.createElement('button', {
                type: 'button',
                onClick: () => setShowDiagnostics(prev => !prev),
                style: actionButtonStyle('#ffffff', '#1a1a1a', '1.5px solid rgba(0,0,0,0.08)'),
            }, showDiagnostics ? 'Esconder diagnóstico' : 'Mostrar diagnóstico')
        ),

        contextPreview && showDiagnostics && React.createElement('div', {
            style: {
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: 14,
                marginBottom: 16,
            }
        },
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'Etapas do pipeline'),
                React.createElement('div', {
                    style: { display: 'flex', flexDirection: 'column', gap: 8 }
                },
                    (contextPreview.stages || []).map(stage =>
                        React.createElement('div', {
                            key: stage.name,
                            style: {
                                display: 'grid',
                                gridTemplateColumns: '110px 1fr auto',
                                gap: 8,
                                alignItems: 'center',
                                fontSize: 12,
                            }
                        },
                            React.createElement('div', { style: { fontWeight: 700, color: '#333' } }, stageLabel(stage.name)),
                            React.createElement('div', {
                                style: {
                                    height: 7,
                                    borderRadius: 99,
                                    background: 'rgba(99, 102, 241,0.08)',
                                    overflow: 'hidden',
                                }
                            },
                                React.createElement('div', {
                                    style: {
                                        width: stage.status === 'completed' ? '100%' : '45%',
                                        height: '100%',
                                        background: stage.status === 'completed' ? '#6366F1' : 'rgba(99, 102, 241,0.5)',
                                    }
                                })
                            ),
                            React.createElement('div', { style: { color: '#777' } }, `${stage.duration_ms || 0}ms`)
                        )
                    )
                )
            ),
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'Clarificações e gaps'),
                React.createElement('div', { style: { fontSize: 12, color: '#555', lineHeight: 1.7 } },
                    React.createElement('div', { style: { marginBottom: 10 } },
                        React.createElement('div', { style: { fontWeight: 700, color: '#333', marginBottom: 4 } }, 'Missing fields'),
                        listOrFallback(contextPreview.missing_fields, 'Sem gaps críticos detetados.').map(item =>
                            React.createElement('div', { key: `gap-${item}` }, `• ${item}`)
                        )
                    ),
                    React.createElement('div', null,
                        React.createElement('div', { style: { fontWeight: 700, color: '#333', marginBottom: 4 } }, 'Perguntas máximas a fazer'),
                        listOrFallback(contextPreview.clarification_questions, 'Sem necessidade de clarificação adicional.').map(item =>
                            React.createElement('div', { key: `question-${item}` }, `• ${item}`)
                        )
                    )
                )
            )
        ),

        contextPreview && showDiagnostics && React.createElement('div', { style: { marginBottom: 16 } },
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'Placement provável'),
                renderPlacement(contextPreview.placement)
            )
        ),

        contextPreview && showDiagnostics && React.createElement('div', { style: { marginBottom: 16 } },
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'Policy pack ativo'),
                renderPolicyPack(contextPreview.story_policy_pack)
            )
        ),

        contextPreview && showDiagnostics && React.createElement('div', { style: { marginBottom: 16 } },
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'Perfil de domínio'),
                renderDomainProfile(contextPreview.domain_profile)
            )
        ),

        contextPreview && showDiagnostics && React.createElement('div', {
            style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 16 }
        },
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'Similar items'),
                renderSources(contextPreview.similar_items)
            ),
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'Sources usadas'),
                renderSources(contextPreview.sources)
            )
        ),

        contextPreview && showDiagnostics && React.createElement('div', { style: { marginBottom: 16 } },
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'Figma / fluxo provável'),
                renderSources(contextPreview.design_sources)
            )
        ),

        contextPreview && showDiagnostics && React.createElement('div', { style: { marginBottom: 16 } },
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'Frames / steps prováveis'),
                renderDesignFlow(contextPreview.design_flow)
            )
        ),

        contextPreview && showDiagnostics && React.createElement('div', { style: { marginBottom: 16 } },
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'Histórias irmãs da feature'),
                renderSources(contextPreview.feature_siblings)
            )
        ),

        contextPreview && showDiagnostics && React.createElement('div', { style: { marginBottom: 16 } },
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'Pack da feature'),
                renderFeaturePack(contextPreview.feature_pack)
            )
        ),

        contextPreview && showDiagnostics && React.createElement('div', { style: { marginBottom: 16 } },
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'Refs DevOps de proveniência'),
                renderSources(contextPreview.curated_workitem_refs)
            )
        ),

        contextPreview && showDiagnostics && React.createElement('div', { style: { marginBottom: 16 } },
            React.createElement('div', { style: panelStyle() },
                React.createElement('div', { style: panelTitleStyle() }, 'Corpus curado'),
                renderCuratedExamples(contextPreview.curated_examples)
            )
        ),

        contextPreview && showDiagnostics && user && user.role === 'admin' && React.createElement('div', { style: { marginBottom: 16 } },
            React.createElement('div', { style: panelStyle('#FFFDFC') },
                React.createElement('div', { style: panelTitleStyle() }, 'Admin / learning'),
                React.createElement('div', { style: { fontSize: 12, color: '#666', lineHeight: 1.6, marginBottom: 12 } },
                    'Este painel é só para avaliação/admin. Fica escondido por defeito para não interferir com o fluxo normal.'
                ),
                React.createElement(UserStoryEvalPanel, { user, conversationId })
            )
        ),

        draftPayload && activeDraft && React.createElement('div', {
            style: {
                borderRadius: 18,
                border: '1px solid rgba(0,0,0,0.06)',
                background: '#FFFDFC',
                padding: 18,
            }
        },
            React.createElement('div', {
                style: { display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap', marginBottom: 16 }
            },
                React.createElement('div', null,
                    React.createElement('div', {
                        style: { fontSize: 20, fontWeight: 700, color: '#1a1a1a', marginBottom: 6, lineHeight: 1.35 }
                    }, activeDraft.title),
                    React.createElement('div', {
                        style: { fontSize: 13, color: '#555', maxWidth: 760, lineHeight: 1.7 }
                    },
                        `Como ${activeDraft.narrative?.as_a || 'utilizador'}, quero ${activeDraft.narrative?.i_want || 'atingir o objetivo descrito'}, para que ${activeDraft.narrative?.so_that || 'o fluxo entregue valor claro ao negócio'}.`
                    )
                ),
                React.createElement('div', {
                    style: {
                        background: confidenceMeta.bg,
                        color: confidenceMeta.color,
                        borderRadius: 999,
                        padding: '8px 12px',
                        fontSize: 12,
                        fontWeight: 700,
                    }
                }, `${confidenceMeta.label} · ${Number(draftPayload.confidence || 0).toFixed(2)}`)
            ),

            React.createElement('div', {
                style: { display: 'grid', gridTemplateColumns: '1.15fr 0.85fr', gap: 14, marginBottom: 14 }
            },
                React.createElement('div', { style: panelStyle('#fff') },
                    React.createElement('div', { style: panelTitleStyle() }, 'Objetivo e proveniência'),
                    React.createElement('div', { style: sectionTextStyle() },
                        React.createElement('div', { style: { marginBottom: 10 } },
                            React.createElement('strong', null, 'Objetivo de negócio'),
                            React.createElement('div', { style: { marginTop: 4 } }, activeDraft.business_goal || 'Sem objetivo explícito.')
                        ),
                        React.createElement('div', null,
                            React.createElement('strong', null, 'Proveniência'),
                            listOrFallback(activeDraft.provenance, 'Proveniência a confirmar.').map(item =>
                                React.createElement('div', { key: `prov-${item}`, style: { marginTop: 4 } }, `• ${item}`)
                            )
                        )
                    )
                ),
                React.createElement('div', { style: panelStyle('#fff') },
                    React.createElement('div', { style: panelTitleStyle() }, 'Estado do draft'),
                    React.createElement('div', { style: sectionTextStyle() },
                        React.createElement('div', { style: { marginBottom: 8 } }, `Publish ready: ${draftPayload.publish_ready ? 'Sim' : 'Ainda não'}`),
                        React.createElement('div', null, `Quality score: ${Number(draftPayload.validation?.quality_score || 0).toFixed(2)}`),
                        React.createElement('div', { style: { marginTop: 8, color: '#666' } },
                            listOrFallback(draftPayload.validation?.quality_issues, 'Sem issues relevantes detetados.').slice(0, 5).map(item =>
                                React.createElement('div', { key: `issue-${item}` }, `• ${item}`)
                            )
                        )
                    )
                )
            ),

            React.createElement('div', { style: { ...panelStyle('#fff'), marginBottom: 14 } },
                React.createElement('div', { style: panelTitleStyle() }, 'Refinar draft antes de validar/publicar'),
                React.createElement('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 } },
                    React.createElement('div', null,
                        React.createElement('label', { style: editorLabelStyle() }, 'Título'),
                        React.createElement('input', {
                            value: activeDraft.title || '',
                            onChange: e => setEditableDraft(prev => ({ ...(prev || {}), title: e.target.value })),
                            style: inputStyle(),
                        }),
                        React.createElement('label', { style: { ...editorLabelStyle(), marginTop: 10 } }, 'Objetivo de negócio'),
                        React.createElement('textarea', {
                            value: activeDraft.business_goal || '',
                            onChange: e => setEditableDraft(prev => ({ ...(prev || {}), business_goal: e.target.value })),
                            rows: 4,
                            style: textareaStyle(),
                        }),
                        React.createElement('label', { style: { ...editorLabelStyle(), marginTop: 10 } }, 'Narrativa'),
                        React.createElement('input', {
                            value: activeDraft.narrative?.as_a || '',
                            onChange: e => setEditableDraft(prev => ({ ...(prev || {}), narrative: { ...(prev?.narrative || {}), as_a: e.target.value } })),
                            placeholder: 'Eu como...',
                            style: inputStyle(),
                        }),
                        React.createElement('input', {
                            value: activeDraft.narrative?.i_want || '',
                            onChange: e => setEditableDraft(prev => ({ ...(prev || {}), narrative: { ...(prev?.narrative || {}), i_want: e.target.value } })),
                            placeholder: 'Quero...',
                            style: { ...inputStyle(), marginTop: 8 },
                        }),
                        React.createElement('input', {
                            value: activeDraft.narrative?.so_that || '',
                            onChange: e => setEditableDraft(prev => ({ ...(prev || {}), narrative: { ...(prev?.narrative || {}), so_that: e.target.value } })),
                            placeholder: 'Para que...',
                            style: { ...inputStyle(), marginTop: 8 },
                        })
                    ),
                    React.createElement('div', null,
                        React.createElement('label', { style: editorLabelStyle() }, 'Proveniência'),
                        React.createElement('textarea', {
                            value: linesFromList(activeDraft.provenance),
                            onChange: e => setEditableDraft(prev => ({ ...(prev || {}), provenance: listFromLines(e.target.value) })),
                            rows: 4,
                            style: textareaStyle(),
                        }),
                        React.createElement('label', { style: { ...editorLabelStyle(), marginTop: 10 } }, 'Condições'),
                        React.createElement('textarea', {
                            value: linesFromList(activeDraft.conditions),
                            onChange: e => setEditableDraft(prev => ({ ...(prev || {}), conditions: listFromLines(e.target.value) })),
                            rows: 4,
                            style: textareaStyle(),
                        }),
                        React.createElement('label', { style: { ...editorLabelStyle(), marginTop: 10 } }, 'Composição e regras'),
                        React.createElement('textarea', {
                            value: linesFromList(activeDraft.rules_constraints),
                            onChange: e => setEditableDraft(prev => ({ ...(prev || {}), rules_constraints: listFromLines(e.target.value) })),
                            rows: 5,
                            style: textareaStyle(),
                        }),
                        React.createElement('label', { style: { ...editorLabelStyle(), marginTop: 10 } }, 'Acceptance criteria (`ID | texto`)'),
                        React.createElement('textarea', {
                            value: acceptanceCriteriaToText(activeDraft.acceptance_criteria),
                            onChange: e => setEditableDraft(prev => ({ ...(prev || {}), acceptance_criteria: acceptanceCriteriaFromText(e.target.value) })),
                            rows: 6,
                            style: textareaStyle(),
                        }),
                        React.createElement('label', { style: { ...editorLabelStyle(), marginTop: 10 } }, 'Observações'),
                        React.createElement('textarea', {
                            value: linesFromList(activeDraft.observations),
                            onChange: e => setEditableDraft(prev => ({ ...(prev || {}), observations: listFromLines(e.target.value) })),
                            rows: 4,
                            style: textareaStyle(),
                        })
                    )
                )
            ),

            React.createElement('div', {
                style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }
            },
                React.createElement('div', { style: panelStyle('#fff') },
                    React.createElement('div', { style: panelTitleStyle() }, 'Condições, composição e dependências'),
                    React.createElement('div', { style: sectionTextStyle() },
                        React.createElement('div', { style: { marginBottom: 12, fontWeight: 700, color: '#333' } }, 'Condições'),
                        listOrFallback(activeDraft.conditions, 'Sem condições explícitas.').map(item =>
                            React.createElement('div', { key: `condition-${item}` }, `• ${item}`)
                        ),
                        React.createElement('div', { style: { marginTop: 12, fontWeight: 700, color: '#333' } }, 'Composição e regras'),
                        listOrFallback(activeDraft.rules_constraints, 'Sem constraints explícitas.').map(item =>
                            React.createElement('div', { key: `rule-${item}` }, `• ${item}`)
                        ),
                        React.createElement('div', { style: { marginTop: 12, fontWeight: 700, color: '#333' } }, 'Dependências'),
                        listOrFallback(activeDraft.dependencies, 'Sem dependências explícitas.').map(item =>
                            React.createElement('div', { key: `dep-${item}` }, `• ${item}`)
                        )
                    )
                ),
                React.createElement('div', { style: panelStyle('#fff') },
                    React.createElement('div', { style: panelTitleStyle() }, 'Clarificações pendentes'),
                    React.createElement('div', { style: sectionTextStyle() },
                        listOrFallback(activeDraft.clarification_questions, 'Sem clarificações pendentes.').map(item =>
                            React.createElement('div', { key: `cq-${item}` }, `• ${item}`)
                        )
                    )
                )
            ),

            React.createElement('div', { style: panelStyle('#fff') },
                React.createElement('div', { style: panelTitleStyle() }, 'Acceptance criteria'),
                React.createElement('div', { style: sectionTextStyle() },
                    listOrFallback(activeDraft.acceptance_criteria, { id: 'CA-01', text: 'Sem critérios gerados.' }).map((item, idx) =>
                        React.createElement('div', { key: `ac-${idx}`, style: { marginBottom: 8 } },
                            React.createElement('strong', null, `${item.id || 'CA'}:`),
                            React.createElement('span', { style: { marginLeft: 6 } }, item.text || item)
                        )
                    )
                )
            ),

            React.createElement('div', { style: { ...panelStyle('#fff'), marginTop: 14 } },
                React.createElement('div', { style: panelTitleStyle() }, 'Test scenarios'),
                React.createElement('div', { style: sectionTextStyle() },
                    listOrFallback(activeDraft.test_scenarios, { id: 'CT-01', title: 'Sem cenários gerados.' }).map((item, idx) =>
                        React.createElement('div', {
                            key: `scenario-${idx}`,
                            style: {
                                borderBottom: idx === (activeDraft.test_scenarios || []).length - 1 ? 'none' : '1px solid rgba(0,0,0,0.06)',
                                paddingBottom: 10,
                                marginBottom: 10,
                            }
                        },
                            React.createElement('div', { style: { fontWeight: 700, color: '#333', marginBottom: 4 } }, `${item.id || 'CT'} · ${item.title || 'Cenário'}`),
                            React.createElement('div', null, `Dado ${item.given || '...'} · Quando ${item.when || '...'} · Então ${item.then || '...'}`),
                            item.covers && item.covers.length > 0 && React.createElement('div', { style: { marginTop: 4, color: '#666' } }, `Cobre: ${item.covers.join(', ')}`)
                        )
                    )
                )
            ),

            React.createElement('div', {
                style: { display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginTop: 16 }
            },
                React.createElement('input', {
                    value: feedbackNote,
                    onChange: e => setFeedbackNote(e.target.value),
                    placeholder: 'Nota opcional para ensinar melhor o sistema',
                    style: {
                        ...inputStyle(),
                        flex: 1,
                        minWidth: 280,
                    },
                }),
                feedbackChip('Bom draft', feedbackOutcome === 'accepted', () => handleFeedback('accepted'), busyAction === 'feedback:accepted'),
                feedbackChip('Precisei editar', feedbackOutcome === 'edited', () => handleFeedback('edited'), busyAction === 'feedback:edited'),
                feedbackChip('Não serve', feedbackOutcome === 'rejected', () => handleFeedback('rejected'), busyAction === 'feedback:rejected')
            ),

            publishResult && publishResult.work_item && React.createElement('div', {
                style: {
                    marginTop: 16,
                    background: 'rgba(16,185,129,0.10)',
                    border: '1px solid rgba(16,185,129,0.2)',
                    borderRadius: 14,
                    padding: '12px 14px',
                    fontSize: 13,
                    color: '#0f766e',
                }
            },
                React.createElement('div', { style: { fontWeight: 700, marginBottom: 4 } }, 'Publicado no Azure DevOps'),
                React.createElement('div', null, `Work item #${publishResult.work_item.id} criado como draft.`),
                publishResult.work_item.url && React.createElement('a', {
                    href: publishResult.work_item.url,
                    target: '_blank',
                    rel: 'noreferrer',
                    style: { color: '#0f766e', fontWeight: 700, textDecoration: 'none', marginTop: 6, display: 'inline-block' }
                }, 'Abrir no DevOps')
            )
        )
    );
}

function panelStyle(background = '#FAFAF8') {
    return {
        background,
        borderRadius: 16,
        border: '1px solid rgba(0,0,0,0.06)',
        padding: '14px 16px',
    };
}

function panelTitleStyle() {
    return {
        fontSize: 11,
        fontWeight: 700,
        color: '#777',
        textTransform: 'uppercase',
        letterSpacing: 0.5,
        marginBottom: 8,
    };
}

function inputStyle() {
    return {
        width: '100%',
        border: '1.5px solid rgba(0,0,0,0.08)',
        borderRadius: 14,
        padding: '12px 14px',
        fontFamily: "'Montserrat', sans-serif",
        fontSize: 13,
        background: '#FAFAF8',
        color: '#1a1a1a',
        outline: 'none',
    };
}

function textareaStyle() {
    return {
        ...inputStyle(),
        resize: 'vertical',
        minHeight: 98,
        lineHeight: 1.6,
    };
}

function actionButtonStyle(background, color, border, disabled = false) {
    return {
        padding: '11px 16px',
        borderRadius: 12,
        border,
        background: disabled ? 'rgba(0,0,0,0.04)' : background,
        color: disabled ? '#999' : color,
        fontSize: 13,
        fontWeight: 700,
        fontFamily: "'Montserrat', sans-serif",
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.7 : 1,
    };
}

function sectionTextStyle() {
    return {
        fontSize: 12.5,
        color: '#444',
        lineHeight: 1.7,
    };
}

function editorLabelStyle() {
    return {
        display: 'block',
        fontSize: 11,
        fontWeight: 700,
        color: '#666',
        textTransform: 'uppercase',
        letterSpacing: 0.5,
        marginBottom: 6,
    };
}

function renderSources(items) {
    const sources = Array.isArray(items) ? items : [];
    if (sources.length === 0) {
        return React.createElement('div', { style: { fontSize: 12, color: '#777', lineHeight: 1.6 } }, 'Sem sources suficientes ainda. Se quiseres melhorar o grounding, anexa sitemap, glossary ou screenshots.');
    }
    return React.createElement('div', {
        style: { display: 'flex', flexDirection: 'column', gap: 10 }
    },
        sources.map(source =>
            React.createElement('div', {
                key: source.key || source.title,
                style: { fontSize: 12, color: '#444', lineHeight: 1.6 }
            },
                React.createElement('div', { style: { fontWeight: 700, color: '#222' } }, source.title || source.key),
                React.createElement('div', { style: { color: '#666' } }, source.snippet || 'Sem snippet.'),
                source.url && React.createElement('a', {
                    href: source.url,
                    target: '_blank',
                    rel: 'noreferrer',
                    style: { display: 'inline-block', marginTop: 4, color: '#b0103a', textDecoration: 'none', fontWeight: 700 }
                }, 'Abrir source')
            )
        )
    );
}

function renderCuratedExamples(items) {
    const examples = Array.isArray(items) ? items : [];
    if (examples.length === 0) {
        return React.createElement('div', { style: { fontSize: 12, color: '#777', lineHeight: 1.6 } }, 'Sem exemplos curados carregados ainda.');
    }
    return React.createElement('div', {
        style: { display: 'flex', flexDirection: 'column', gap: 12 }
    },
        examples.map(example =>
            React.createElement('div', {
                key: example.title,
                style: { fontSize: 12, color: '#444', lineHeight: 1.65 }
            },
                React.createElement('div', { style: { fontWeight: 700, color: '#222', marginBottom: 4 } }, example.title),
                React.createElement('div', { style: { color: '#666' } }, `${example.domain || 'Domínio n/a'} · ${example.author || 'Autor n/a'} · score ${Number(example.score || 0).toFixed(2)}`),
                example.title_pattern && React.createElement('div', { style: { marginTop: 4, color: '#666' } }, `Padrão: ${example.title_pattern}`),
                example.provenance_excerpt && React.createElement('div', { style: { marginTop: 4 } }, `Proveniência: ${example.provenance_excerpt}`),
                example.behavior_excerpt && React.createElement('div', { style: { marginTop: 4 } }, `Comportamento: ${example.behavior_excerpt}`),
                Array.isArray(example.ux_terms) && example.ux_terms.length > 0 && React.createElement('div', { style: { marginTop: 4, color: '#666' } }, `Léxico: ${example.ux_terms.join(', ')}`),
                example.url && React.createElement('a', {
                    href: example.url,
                    target: '_blank',
                    rel: 'noreferrer',
                    style: { display: 'inline-block', marginTop: 4, color: '#b0103a', textDecoration: 'none', fontWeight: 700 }
                }, 'Abrir exemplo')
            )
        )
    );
}

function renderFeaturePack(featurePack) {
    const data = featurePack && typeof featurePack === 'object' ? featurePack : {};
    if (!data.feature_id) {
        return React.createElement('div', { style: { fontSize: 12, color: '#777', lineHeight: 1.6 } }, 'Sem pack específico da feature para este pedido.');
    }
    return React.createElement('div', {
        style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, fontSize: 12, color: '#444', lineHeight: 1.65 }
    },
        React.createElement('div', null,
            React.createElement('div', { style: { fontWeight: 700, color: '#222', marginBottom: 6 } }, data.feature_title || `Feature ${data.feature_id}`),
            React.createElement('div', { style: { color: '#666', marginBottom: 4 } }, `Feature #${data.feature_id} · ${Number(data.story_count || 0)} user stories`),
            data.area_path && React.createElement('div', { style: { color: '#666', marginBottom: 4 } }, `Area: ${data.area_path}`),
            Array.isArray(data.top_flows) && data.top_flows.length > 0 && React.createElement('div', { style: { marginBottom: 4 } }, `Flows: ${data.top_flows.slice(0, 4).join(', ')}`),
            Array.isArray(data.top_ux_terms) && data.top_ux_terms.length > 0 && React.createElement('div', { style: { marginBottom: 4, color: '#666' } }, `Léxico: ${data.top_ux_terms.slice(0, 8).join(', ')}`),
            data.figma_url && React.createElement('a', {
                href: data.figma_url,
                target: '_blank',
                rel: 'noreferrer',
                style: { display: 'inline-block', marginTop: 4, color: '#b0103a', textDecoration: 'none', fontWeight: 700 }
            }, 'Abrir flow Figma')
        ),
        React.createElement('div', null,
            (Array.isArray(data.notes) ? data.notes : ['Sem notas adicionais.']).slice(0, 3).map(item =>
                React.createElement('div', { key: item, style: { marginBottom: 4 } }, `• ${item}`)
            ),
            Array.isArray(data.stories) && data.stories.length > 0 && React.createElement('div', { style: { marginTop: 8 } },
                React.createElement('div', { style: { fontWeight: 700, color: '#222', marginBottom: 6 } }, 'Stories do pack'),
                data.stories.slice(0, 4).map(item =>
                    React.createElement('div', { key: item.id || item.title, style: { marginBottom: 6 } },
                        React.createElement('div', { style: { fontWeight: 600, color: '#333' } }, item.title || item.id),
                        item.snippet && React.createElement('div', { style: { color: '#666' } }, item.snippet)
                    )
                )
            )
        )
    );
}

function renderDomainProfile(profile) {
    const data = profile && typeof profile === 'object' ? profile : {};
    if (!data.domain) {
        return React.createElement('div', { style: { fontSize: 12, color: '#777', lineHeight: 1.6 } }, 'Sem perfil de domínio suficiente ainda.');
    }
    return React.createElement('div', {
        style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, fontSize: 12, color: '#444', lineHeight: 1.65 }
    },
        React.createElement('div', null,
            React.createElement('div', { style: { fontWeight: 700, color: '#222', marginBottom: 6 } }, data.domain),
            data.design_file_title && React.createElement('div', { style: { color: '#666', marginBottom: 4 } }, `Design: ${data.design_file_title}`),
            React.createElement('div', { style: { color: '#666', marginBottom: 4 } }, `Cobertura: ${Number(data.coverage_score || 0).toFixed(2)} · exemplos ${Number(data.curated_example_count || 0)}`),
            Array.isArray(data.top_journeys) && data.top_journeys.length > 0 && React.createElement('div', { style: { marginTop: 6 } }, `Jornadas: ${data.top_journeys.join(', ')}`),
            Array.isArray(data.top_flows) && data.top_flows.length > 0 && React.createElement('div', { style: { marginTop: 4 } }, `Flows: ${data.top_flows.slice(0, 4).join(', ')}`)
        ),
        React.createElement('div', null,
            Array.isArray(data.preferred_lexicon) && data.preferred_lexicon.length > 0 && React.createElement('div', { style: { marginBottom: 6 } }, `Léxico: ${data.preferred_lexicon.slice(0, 8).join(', ')}`),
            Array.isArray(data.top_title_patterns) && data.top_title_patterns.length > 0 && React.createElement('div', { style: { marginBottom: 6, color: '#666' } }, `Padrão: ${data.top_title_patterns[0]}`),
            (Array.isArray(data.routing_notes) ? data.routing_notes : ['Sem notas de routing adicionais.']).slice(0, 3).map(item =>
                React.createElement('div', { key: item, style: { marginBottom: 4 } }, `• ${item}`)
            )
        )
    );
}

function renderPolicyPack(policyPack) {
    const data = policyPack && typeof policyPack === 'object' ? policyPack : {};
    if (!data.domain) {
        return React.createElement('div', { style: { fontSize: 12, color: '#777', lineHeight: 1.6 } }, 'Sem policy pack resolvido ainda.');
    }
    return React.createElement('div', {
        style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, fontSize: 12, color: '#444', lineHeight: 1.65 }
    },
        React.createElement('div', null,
            React.createElement('div', { style: { fontWeight: 700, color: '#222', marginBottom: 6 } }, `${data.domain} · detalhe ${data.detail_level || 'n/a'}`),
            data.canonical_title_pattern && React.createElement('div', { style: { marginBottom: 4, color: '#666' } }, `Título: ${data.canonical_title_pattern}`),
            Array.isArray(data.mandatory_sections) && data.mandatory_sections.length > 0 && React.createElement('div', { style: { marginBottom: 4 } }, `Obrigatório: ${data.mandatory_sections.join(', ')}`),
            Array.isArray(data.top_journeys) && data.top_journeys.length > 0 && React.createElement('div', { style: { marginBottom: 4, color: '#666' } }, `Jornadas: ${data.top_journeys.join(', ')}`)
        ),
        React.createElement('div', null,
            Array.isArray(data.preferred_lexicon) && data.preferred_lexicon.length > 0 && React.createElement('div', { style: { marginBottom: 6 } }, `Léxico: ${data.preferred_lexicon.slice(0, 8).join(', ')}`),
            Array.isArray(data.terminology_overrides) && data.terminology_overrides.length > 0 && data.terminology_overrides.slice(0, 3).map(item =>
                React.createElement('div', { key: `${item.from}-${item.to}`, style: { marginBottom: 4, color: '#666' } }, `${item.from} -> ${item.to}`)
            ),
            (Array.isArray(data.notes) ? data.notes : []).slice(0, 3).map(item =>
                React.createElement('div', { key: item, style: { marginBottom: 4 } }, `• ${item}`)
            )
        )
    );
}

function renderDesignFlow(items) {
    const matches = Array.isArray(items) ? items : [];
    if (matches.length === 0) {
        return React.createElement('div', { style: { fontSize: 12, color: '#777', lineHeight: 1.6 } }, 'Sem frames suficientes ainda. O mapa de domínio já está resolvido, mas falta granularidade de flow/frame.');
    }
    return React.createElement('div', {
        style: { display: 'flex', flexDirection: 'column', gap: 12 }
    },
        matches.map(item =>
            React.createElement('div', {
                key: item.key || item.title,
                style: { fontSize: 12, color: '#444', lineHeight: 1.65 }
            },
                React.createElement('div', { style: { fontWeight: 700, color: '#222', marginBottom: 4 } }, item.title || 'Frame Figma'),
                React.createElement('div', { style: { color: '#666' } }, `${item.domain || 'Domínio n/a'}${item.file_title ? ` · ${item.file_title}` : ''} · score ${Number(item.score || 0).toFixed(2)}`),
                item.snippet && React.createElement('div', { style: { marginTop: 4 } }, item.snippet),
                Array.isArray(item.ui_components) && item.ui_components.length > 0 && React.createElement('div', { style: { marginTop: 4, color: '#666' } }, `Componentes: ${item.ui_components.join(', ')}`),
                item.url && React.createElement('a', {
                    href: item.url,
                    target: '_blank',
                    rel: 'noreferrer',
                    style: { display: 'inline-block', marginTop: 4, color: '#b0103a', textDecoration: 'none', fontWeight: 700 }
                }, 'Abrir frame')
            )
        )
    );
}

function renderPlacement(placement) {
    const data = placement && typeof placement === 'object' ? placement : {};
    const epic = data.selected_epic || {};
    const feature = data.selected_feature || {};
    const reasoning = Array.isArray(data.reasoning) ? data.reasoning : [];
    const hasPlacement = epic.title || feature.title;
    if (!hasPlacement) {
        return React.createElement('div', { style: { fontSize: 12, color: '#777', lineHeight: 1.6 } }, 'Sem placement resolvido ainda.');
    }
    return React.createElement('div', {
        style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, fontSize: 12, color: '#444', lineHeight: 1.65 }
    },
        React.createElement('div', null,
            React.createElement('div', { style: { fontWeight: 700, color: '#222', marginBottom: 6 } }, 'Epic e Feature'),
            epic.title && React.createElement('div', { style: { marginBottom: 4 } }, `Epic: ${epic.title}`),
            feature.title && React.createElement('div', { style: { marginBottom: 4 } }, `Feature: ${feature.title}`),
            data.resolved_area_path && React.createElement('div', { style: { color: '#666' } }, `Area: ${data.resolved_area_path}`),
            React.createElement('div', { style: { marginTop: 6, color: '#666' } }, `Confiança: ${Number(data.confidence || 0).toFixed(2)}`)
        ),
        React.createElement('div', null,
            React.createElement('div', { style: { fontWeight: 700, color: '#222', marginBottom: 6 } }, 'Justificação'),
            (reasoning.length ? reasoning : ['Sem justificação adicional.']).map(item =>
                React.createElement('div', { key: item, style: { marginBottom: 4 } }, `• ${item}`)
            )
        )
    );
}

function feedbackChip(label, active, onClick, busy) {
    return React.createElement('button', {
        onClick,
        disabled: !!busy,
        style: {
            padding: '10px 14px',
            borderRadius: 999,
            border: active ? '1px solid rgba(99, 102, 241,0.25)' : '1px solid rgba(0,0,0,0.08)',
            background: active ? 'rgba(99, 102, 241,0.08)' : 'white',
            color: active ? '#b0103a' : '#444',
            fontSize: 12,
            fontWeight: 700,
            cursor: busy ? 'not-allowed' : 'pointer',
            opacity: busy ? 0.7 : 1,
        }
    }, busy ? 'A guardar...' : label);
}
