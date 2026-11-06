import React, { useEffect, useMemo, useState } from 'react';
import { API_URL } from '../utils/constants.js';
import { authFetch, getAuthHeaders } from '../utils/auth.js';

function cardStyle() {
  return {
    background: 'rgba(255,255,255,0.92)',
    border: '1px solid rgba(0,0,0,0.06)',
    borderRadius: 16,
    padding: '14px 16px',
  };
}

function metricTone(value, kind = 'neutral') {
  const score = Number(value || 0);
  if (kind === 'edit') {
    if (score >= 0.22) return { color: '#b0103a', label: 'Alta fricção' };
    if (score >= 0.12) return { color: '#b45309', label: 'Fricção média' };
    return { color: '#0f766e', label: 'Fricção baixa' };
  }
  if (kind === 'publish') {
    if (score <= 0.4) return { color: '#b0103a', label: 'Baixo' };
    if (score <= 0.65) return { color: '#b45309', label: 'Médio' };
    return { color: '#0f766e', label: 'Bom' };
  }
  if (kind === 'quality') {
    if (score <= 0.78) return { color: '#b0103a', label: 'Atenção' };
    if (score <= 0.86) return { color: '#b45309', label: 'Aceitável' };
    return { color: '#0f766e', label: 'Forte' };
  }
  return { color: '#334155', label: '' };
}

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

const EMPTY_KNOWLEDGE_META = {
  domain: '',
  journey: '',
  flow: '',
  team_scope: '',
  note: '',
};

const EMPTY_MANUAL_KNOWLEDGE = {
  title: '',
  content: '',
};

const EMPTY_BUNDLE_INPUT = `[
  {
    "asset_key": "pagamentos-sitemap",
    "title": "Sitemap Pagamentos",
    "content": "Journey: Pagamentos > Transferências > Recorrências\\nPrimary CTA: Confirmar\\nSecondary CTA: Cancelar",
    "domain": "Pagamentos",
    "journey": "Transferências",
    "flow": "Recorrências",
    "team_scope": "Produto\\...\\ACME",
    "note": "Mapa funcional curado"
  }
]`;

export default function UserStoryEvalPanel({ user, conversationId = '' }) {
  const [scope, setScope] = useState('global');
  const [payload, setPayload] = useState(null);
  const [indexStatus, setIndexStatus] = useState(null);
  const [knowledgeAssets, setKnowledgeAssets] = useState([]);
  const [conversationUploads, setConversationUploads] = useState([]);
  const [loading, setLoading] = useState(false);
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [uploadsLoading, setUploadsLoading] = useState(false);
  const [error, setError] = useState('');
  const [knowledgeError, setKnowledgeError] = useState('');
  const [uploadsError, setUploadsError] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  const [promotingDraftId, setPromotingDraftId] = useState('');
  const [reviewingDraftId, setReviewingDraftId] = useState('');
  const [importingUploadId, setImportingUploadId] = useState('');
  const [reviewingAssetId, setReviewingAssetId] = useState('');
  const [savingManualKnowledge, setSavingManualKnowledge] = useState(false);
  const [savingBundleKnowledge, setSavingBundleKnowledge] = useState(false);
  const [syncingIndexKey, setSyncingIndexKey] = useState('');
  const [knowledgeMeta, setKnowledgeMeta] = useState(EMPTY_KNOWLEDGE_META);
  const [manualKnowledge, setManualKnowledge] = useState(EMPTY_MANUAL_KNOWLEDGE);
  const [bundleInput, setBundleInput] = useState(EMPTY_BUNDLE_INPUT);
  const [refreshTick, setRefreshTick] = useState(0);

  const activeUserSub = useMemo(() => {
    return scope === 'mine' ? String((user && user.username) || '').trim() : '';
  }, [scope, user]);

  useEffect(() => {
    let cancelled = false;
    async function loadSummary() {
      if (!user || user.role !== 'admin') return;
      setLoading(true);
      setError('');
      try {
        const params = new URLSearchParams({ top: '250' });
        if (activeUserSub) params.set('user_sub', activeUserSub);
        const res = await authFetch(`${API_URL}/api/admin/user-stories/eval-summary?${params.toString()}`, {
          headers: getAuthHeaders(),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || data.error || `Erro ${res.status}`);
        let indexData = null;
        try {
          const indexRes = await authFetch(`${API_URL}/api/admin/user-stories/index-status`, {
            headers: getAuthHeaders(),
          });
          indexData = await indexRes.json().catch(() => ({}));
          if (!indexRes.ok) throw new Error(indexData.detail || indexData.error || `Erro ${indexRes.status}`);
        } catch (indexErr) {
          indexData = { indexes: [], error: indexErr.message || 'Falha a carregar estado dos índices.' };
        }
        if (!cancelled) {
          setPayload(data);
          setIndexStatus(indexData);
        }
      } catch (err) {
        if (!cancelled) setError(err.message || 'Falha a carregar avaliação.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadSummary();
    return () => { cancelled = true; };
  }, [activeUserSub, user, refreshTick]);

  useEffect(() => {
    let cancelled = false;
    async function loadKnowledgeAssets() {
      if (!user || user.role !== 'admin') return;
      setKnowledgeLoading(true);
      setKnowledgeError('');
      try {
        const res = await authFetch(`${API_URL}/api/admin/user-stories/knowledge-assets?top=150`, {
          headers: getAuthHeaders(),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || data.error || `Erro ${res.status}`);
        if (!cancelled) setKnowledgeAssets(Array.isArray(data.items) ? data.items : []);
      } catch (err) {
        if (!cancelled) {
          setKnowledgeAssets([]);
          setKnowledgeError(err.message || 'Falha a carregar knowledge assets.');
        }
      } finally {
        if (!cancelled) setKnowledgeLoading(false);
      }
    }
    loadKnowledgeAssets();
    return () => { cancelled = true; };
  }, [user, refreshTick]);

  useEffect(() => {
    let cancelled = false;
    async function loadConversationUploads() {
      if (!user || user.role !== 'admin') return;
      if (!conversationId) {
        setConversationUploads([]);
        setUploadsError('');
        return;
      }
      setUploadsLoading(true);
      setUploadsError('');
      try {
        const res = await authFetch(`${API_URL}/api/upload/index/${encodeURIComponent(conversationId)}`, {
          headers: getAuthHeaders(),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || data.error || `Erro ${res.status}`);
        if (!cancelled) setConversationUploads(Array.isArray(data.items) ? data.items : []);
      } catch (err) {
        if (!cancelled) {
          setConversationUploads([]);
          setUploadsError(err.message || 'Falha a carregar uploads da conversa.');
        }
      } finally {
        if (!cancelled) setUploadsLoading(false);
      }
    }
    loadConversationUploads();
    return () => { cancelled = true; };
  }, [conversationId, user, refreshTick]);

  async function promoteCandidate(candidate) {
    if (!candidate || !candidate.draft_id || !candidate.owner_sub) return;
    setPromotingDraftId(candidate.draft_id);
    setActionMessage('');
    try {
      const res = await authFetch(`${API_URL}/api/admin/user-stories/promote-candidate`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          draft_id: candidate.draft_id,
          user_sub: candidate.owner_sub,
          note: 'Promovido a partir do painel de avaliação da lane.',
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.error || `Erro ${res.status}`);
      setActionMessage(`Submetido para curadoria: ${data.entry && data.entry.title ? data.entry.title : candidate.draft_id}`);
      setRefreshTick(prev => prev + 1);
    } catch (err) {
      setActionMessage(err.message || 'Falha a promover draft para corpus curado.');
    } finally {
      setPromotingDraftId('');
    }
  }

  async function triggerIndexSync(kind) {
    if (!kind) return;
    const routes = {
      examples: { url: '/api/admin/user-stories/sync-search-index', body: { top: 200 } },
      devops: { url: '/api/admin/user-stories/sync-devops-index', body: { since_days: 30, top: 1200, update_cursor: true } },
      knowledge: { url: '/api/admin/user-stories/sync-knowledge-index', body: { max_docs: 600, batch_size: 150, update_state: true } },
    };
    const config = routes[kind];
    if (!config) return;
    setSyncingIndexKey(kind);
    setActionMessage('');
    try {
      const res = await authFetch(`${API_URL}${config.url}`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify(config.body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.error || `Erro ${res.status}`);
      const indexed = data.indexed != null ? data.indexed : data.indexed_count;
      setActionMessage(`Sync ${kind} concluída${indexed != null ? ` · ${indexed} docs` : ''}.`);
      setRefreshTick(prev => prev + 1);
    } catch (err) {
      setActionMessage(err.message || `Falha a sincronizar índice ${kind}.`);
    } finally {
      setSyncingIndexKey('');
    }
  }

  async function importConversationUpload(item) {
    if (!item || !item.file_id || !conversationId) return;
    setImportingUploadId(item.file_id);
    setActionMessage('');
    try {
      const res = await authFetch(`${API_URL}/api/admin/user-stories/knowledge-assets/import-upload`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          conversation_id: conversationId,
          file_id: item.file_id,
          domain: knowledgeMeta.domain || null,
          journey: knowledgeMeta.journey || null,
          flow: knowledgeMeta.flow || null,
          team_scope: knowledgeMeta.team_scope || null,
          note: knowledgeMeta.note || null,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.error || `Erro ${res.status}`);
      const importedTitle = data.entry && data.entry.title ? data.entry.title : item.filename;
      setActionMessage(`Knowledge asset criado: ${importedTitle}`);
      setRefreshTick(prev => prev + 1);
    } catch (err) {
      setActionMessage(err.message || 'Falha a promover upload para knowledge persistente.');
    } finally {
      setImportingUploadId('');
    }
  }

  async function createManualKnowledgeAsset() {
    if (!String(manualKnowledge.title || '').trim() || !String(manualKnowledge.content || '').trim()) {
      setActionMessage('Título e conteúdo são obrigatórios para criar um knowledge asset manual.');
      return;
    }
    setSavingManualKnowledge(true);
    setActionMessage('');
    try {
      const res = await authFetch(`${API_URL}/api/admin/user-stories/knowledge-assets/import-text`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          title: manualKnowledge.title,
          content: manualKnowledge.content,
          domain: knowledgeMeta.domain || null,
          journey: knowledgeMeta.journey || null,
          flow: knowledgeMeta.flow || null,
          team_scope: knowledgeMeta.team_scope || null,
          note: knowledgeMeta.note || null,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.error || `Erro ${res.status}`);
      setManualKnowledge(EMPTY_MANUAL_KNOWLEDGE);
      setActionMessage(`Knowledge asset criado: ${data.entry && data.entry.title ? data.entry.title : 'novo asset'}`);
      setRefreshTick(prev => prev + 1);
    } catch (err) {
      setActionMessage(err.message || 'Falha a criar knowledge asset manual.');
    } finally {
      setSavingManualKnowledge(false);
    }
  }

  async function importKnowledgeBundle() {
    setSavingBundleKnowledge(true);
    setActionMessage('');
    try {
      let parsed = [];
      try {
        const candidate = JSON.parse(String(bundleInput || '').trim() || '[]');
        parsed = Array.isArray(candidate) ? candidate : [];
      } catch {
        throw new Error('O bundle tem de ser JSON válido no formato de array.');
      }
      if (!parsed.length) {
        throw new Error('O bundle está vazio.');
      }
      const res = await authFetch(`${API_URL}/api/admin/user-stories/knowledge-assets/import-bundle`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ items: parsed }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.error || `Erro ${res.status}`);
      setActionMessage(`Knowledge bundle importado: ${data.created_count || 0} assets.`);
      setRefreshTick(prev => prev + 1);
    } catch (err) {
      setActionMessage(err.message || 'Falha a importar knowledge bundle.');
    } finally {
      setSavingBundleKnowledge(false);
    }
  }

  async function reviewKnowledgeAsset(item, action) {
    if (!item || !item.asset_id || !action) return;
    setReviewingAssetId(item.asset_id);
    setActionMessage('');
    try {
      const res = await authFetch(`${API_URL}/api/admin/user-stories/knowledge-assets/review`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          asset_id: item.asset_id,
          action,
          note: `Ação ${action} executada a partir do painel de knowledge assets.`,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.error || `Erro ${res.status}`);
      setActionMessage(`Knowledge asset atualizado: ${data.entry && data.entry.title ? data.entry.title : item.asset_id} -> ${data.status}`);
      setRefreshTick(prev => prev + 1);
    } catch (err) {
      setActionMessage(err.message || 'Falha a rever knowledge asset.');
    } finally {
      setReviewingAssetId('');
    }
  }

  async function reviewCandidate(item, action) {
    if (!item || !item.draft_id || !action) return;
    setReviewingDraftId(item.draft_id);
    setActionMessage('');
    try {
      const res = await authFetch(`${API_URL}/api/admin/user-stories/review-candidate`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          draft_id: item.draft_id,
          action,
          note: `Ação ${action} executada a partir do painel de curadoria.`,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.error || `Erro ${res.status}`);
      setActionMessage(`Curadoria atualizada: ${data.entry && data.entry.title ? data.entry.title : item.draft_id} -> ${data.status}`);
      setRefreshTick(prev => prev + 1);
    } catch (err) {
      setActionMessage(err.message || 'Falha a rever candidato.');
    } finally {
      setReviewingDraftId('');
    }
  }

  if (!user || user.role !== 'admin') return null;

  const totals = payload && payload.totals ? payload.totals : {};
  const alerts = Array.isArray(payload && payload.alerts) ? payload.alerts : [];
  const gaps = Array.isArray(payload && payload.corpus_gaps) ? payload.corpus_gaps : [];
  const hotspots = Array.isArray(payload && payload.hotspots) ? payload.hotspots : [];
  const mostEdited = Array.isArray(payload && payload.most_edited) ? payload.most_edited : [];
  const recommendations = Array.isArray(payload && payload.recommendations) ? payload.recommendations : [];
  const curationCandidates = Array.isArray(payload && payload.curation_candidates) ? payload.curation_candidates : [];
  const curatedRegistry = payload && payload.curated_registry ? payload.curated_registry : {};
  const reviewQueue = Array.isArray(curatedRegistry.review_queue) ? curatedRegistry.review_queue : [];
  const recentActive = Array.isArray(curatedRegistry.recent_active) ? curatedRegistry.recent_active : [];
  const domains = Array.isArray(payload && payload.domains) ? payload.domains : [];
  const corpus = payload && payload.corpus ? payload.corpus : {};
  const indexItems = Array.isArray(indexStatus && indexStatus.indexes) ? indexStatus.indexes : [];
  const activeKnowledgeAssets = knowledgeAssets.filter(item => String(item.status || '').toLowerCase() === 'active');
  const inactiveKnowledgeAssets = knowledgeAssets.filter(item => String(item.status || '').toLowerCase() === 'inactive');

  const publishTone = metricTone(totals.publish_rate, 'publish');
  const editTone = metricTone(totals.avg_edit_burden, 'edit');
  const qualityTone = metricTone(totals.avg_quality_score, 'quality');

  return (
    <div style={{
      background: 'linear-gradient(135deg, rgba(248,244,236,0.98), rgba(255,255,255,0.98))',
      border: '1px solid rgba(0,0,0,0.08)',
      borderRadius: 22,
      padding: 22,
      marginBottom: 20,
      boxShadow: '0 10px 30px rgba(0,0,0,0.04)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 700, color: '#1a1a1a', marginBottom: 4 }}>Avaliação da lane de User Stories</div>
          <div style={{ fontSize: 12, color: '#6b7280', lineHeight: 1.6 }}>
            Resumo operacional da qualidade dos drafts, esforço de edição e cobertura do corpus curado.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button
            onClick={() => setScope('global')}
            style={toggleStyle(scope === 'global')}
          >
            Global
          </button>
          <button
            onClick={() => setScope('mine')}
            style={toggleStyle(scope === 'mine')}
          >
            Meu utilizador
          </button>
        </div>
      </div>

      {error ? <div style={{ fontSize: 12, fontWeight: 600, color: '#b0103a', marginBottom: 12 }}>{error}</div> : null}
      {actionMessage ? <div style={{ fontSize: 12, fontWeight: 600, color: /^(Promovido|Submetido|Curadoria atualizada|Knowledge asset)/.test(actionMessage) ? '#0f766e' : '#b0103a', marginBottom: 12 }}>{actionMessage}</div> : null}
      {loading ? <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 12 }}>A carregar resumo da lane...</div> : null}

      {payload ? (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 12, marginBottom: 14 }}>
            <MetricCard label="Drafts" value={String(totals.draft_count || 0)} meta={`${totals.domain_count || 0} domínios`} />
            <MetricCard label="Publish rate" value={percent(totals.publish_rate)} tone={publishTone.color} meta={publishTone.label} />
            <MetricCard label="Edit burden" value={percent(totals.avg_edit_burden)} tone={editTone.color} meta={editTone.label} />
            <MetricCard label="Quality média" value={percent(totals.avg_quality_score)} tone={qualityTone.color} meta={qualityTone.label} />
          </div>

          {alerts.length > 0 ? (
            <div style={{ ...cardStyle(), marginBottom: 14, background: 'rgba(255,250,240,0.9)' }}>
              <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5, color: '#8b6a45', marginBottom: 8 }}>
                Alertas operacionais
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12, color: '#5b4633', lineHeight: 1.6 }}>
                {alerts.map(item => (
                  <div key={item.code}>• {item.message}</div>
                ))}
              </div>
            </div>
          ) : null}

          <div style={{ ...cardStyle(), marginBottom: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
              <div style={sectionTitleStyle()}>Estado dos índices dedicados</div>
              <div style={{ fontSize: 11, color: '#6b7280' }}>
                Search service: {(indexStatus && indexStatus.search_service) || 'n/a'}
              </div>
            </div>
            {indexStatus && indexStatus.error ? (
              <div style={{ fontSize: 12, color: '#b0103a', lineHeight: 1.6 }}>{indexStatus.error}</div>
            ) : indexItems.length === 0 ? (
              <div style={emptyStyle()}>Sem dados de status para os índices dedicados.</div>
            ) : (
              <div style={{ display: 'grid', gap: 8 }}>
                {indexItems.map(item => (
                  <div key={item.key} style={{
                    display: 'grid',
                    gridTemplateColumns: '1.2fr 0.7fr 0.8fr 0.9fr auto',
                    gap: 10,
                    alignItems: 'center',
                    padding: '10px 0',
                    borderTop: '1px solid rgba(0,0,0,0.06)',
                  }}>
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 700, color: '#1f2937' }}>{item.label}</div>
                      <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>
                        {item.index_name} · {item.notes}
                      </div>
                    </div>
                    <MiniMetric label="Docs" value={item.document_count || 0} />
                    <MiniMetric label="Modo" value={item.mode || (item.available ? 'ativo' : 'n/a')} />
                    <MiniMetric label="Último sync" value={formatDateLabel(item.last_sync_at)} />
                    <button
                      onClick={() => triggerIndexSync(item.key)}
                      disabled={syncingIndexKey === item.key}
                      style={{
                        borderRadius: 10,
                        border: '1px solid rgba(30,64,175,0.16)',
                        background: 'rgba(30,64,175,0.08)',
                        color: '#1d4ed8',
                        fontSize: 12,
                        fontWeight: 700,
                        padding: '8px 10px',
                        cursor: syncingIndexKey === item.key ? 'default' : 'pointer',
                        opacity: syncingIndexKey === item.key ? 0.6 : 1,
                      }}
                    >
                      {syncingIndexKey === item.key ? 'A sincronizar...' : 'Sync'}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
            <div style={cardStyle()}>
              <div style={sectionTitleStyle()}>Metadata comum para knowledge</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div>
                  <div style={fieldLabelStyle()}>Domínio</div>
                  <input
                    value={knowledgeMeta.domain}
                    onChange={e => setKnowledgeMeta(prev => ({ ...prev, domain: e.target.value }))}
                    placeholder="Ex: Pagamentos"
                    style={fieldInputStyle()}
                  />
                </div>
                <div>
                  <div style={fieldLabelStyle()}>Journey</div>
                  <input
                    value={knowledgeMeta.journey}
                    onChange={e => setKnowledgeMeta(prev => ({ ...prev, journey: e.target.value }))}
                    placeholder="Ex: Transferências recorrentes"
                    style={fieldInputStyle()}
                  />
                </div>
                <div>
                  <div style={fieldLabelStyle()}>Flow</div>
                  <input
                    value={knowledgeMeta.flow}
                    onChange={e => setKnowledgeMeta(prev => ({ ...prev, flow: e.target.value }))}
                    placeholder="Ex: Configurar agendamento"
                    style={fieldInputStyle()}
                  />
                </div>
                <div>
                  <div style={fieldLabelStyle()}>Team scope</div>
                  <input
                    value={knowledgeMeta.team_scope}
                    onChange={e => setKnowledgeMeta(prev => ({ ...prev, team_scope: e.target.value }))}
                    placeholder="Ex: Produto Digital\\...\\ACME"
                    style={fieldInputStyle()}
                  />
                </div>
              </div>
              <div style={{ marginTop: 10 }}>
                <div style={fieldLabelStyle()}>Nota / proveniência</div>
                <textarea
                  value={knowledgeMeta.note}
                  onChange={e => setKnowledgeMeta(prev => ({ ...prev, note: e.target.value }))}
                  rows={3}
                  placeholder="Resumo da utilidade deste asset para a lane, termos do site, regras de negócio ou mapa do fluxo."
                  style={fieldTextareaStyle()}
                />
              </div>
            </div>

            <div style={cardStyle()}>
              <div style={sectionTitleStyle()}>Criar knowledge asset manual</div>
              <div>
                <div style={fieldLabelStyle()}>Título</div>
                <input
                  value={manualKnowledge.title}
                  onChange={e => setManualKnowledge(prev => ({ ...prev, title: e.target.value }))}
                  placeholder="Ex: Sitemap Pagamentos MVP"
                  style={fieldInputStyle()}
                />
              </div>
              <div style={{ marginTop: 10 }}>
                <div style={fieldLabelStyle()}>Conteúdo</div>
                <textarea
                  value={manualKnowledge.content}
                  onChange={e => setManualKnowledge(prev => ({ ...prev, content: e.target.value }))}
                  rows={8}
                  placeholder="Podes colar aqui mapa do site, glossary, critérios de copy, regras de placement, notas de Figma ou do produto."
                  style={fieldTextareaStyle()}
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
                <div style={{ fontSize: 11, color: '#6b7280', lineHeight: 1.6 }}>
                  Este asset entra no índice dedicado de knowledge e pode ser usado no grounding da lane.
                </div>
                <button
                  onClick={createManualKnowledgeAsset}
                  disabled={savingManualKnowledge}
                  style={primaryActionStyle('#1d4ed8', 'rgba(30,64,175,0.08)', savingManualKnowledge)}
                >
                  {savingManualKnowledge ? 'A criar...' : 'Criar asset'}
                </button>
              </div>
            </div>
          </div>

          <div style={{ ...cardStyle(), marginBottom: 14 }}>
            <div style={sectionTitleStyle()}>Importar knowledge bundle</div>
            <div style={{ fontSize: 12, color: '#6b7280', lineHeight: 1.6, marginBottom: 8 }}>
              Cola aqui um array JSON com sitemap, glossário, regras ou flows. O import usa `asset_key` determinístico quando existir, por isso podes reimportar sem duplicar.
            </div>
            <textarea
              value={bundleInput}
              onChange={e => setBundleInput(e.target.value)}
              rows={10}
              spellCheck={false}
              style={{ ...fieldTextareaStyle(), minHeight: 200, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace', fontSize: 11.5 }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
              <div style={{ fontSize: 11, color: '#6b7280', lineHeight: 1.6 }}>
                Ideal para colares um mapa do site, policy pack lexical ou uma lista de flows curados em batch.
              </div>
              <button
                onClick={importKnowledgeBundle}
                disabled={savingBundleKnowledge}
                style={primaryActionStyle('#7c3aed', 'rgba(124,58,237,0.10)', savingBundleKnowledge)}
              >
                {savingBundleKnowledge ? 'A importar...' : 'Importar bundle'}
              </button>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
            <div style={cardStyle()}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
                <div style={sectionTitleStyle()}>Promover uploads da conversa</div>
                <div style={{ fontSize: 11, color: '#6b7280' }}>
                  {conversationId ? `Conversa ${conversationId}` : 'Sem conversa ativa'}
                </div>
              </div>
              {uploadsError ? <div style={{ fontSize: 12, color: '#b0103a', marginBottom: 10 }}>{uploadsError}</div> : null}
              {uploadsLoading ? (
                <div style={emptyStyle()}>A carregar uploads da conversa...</div>
              ) : !conversationId ? (
                <div style={emptyStyle()}>Abre uma conversa com uploads para promover assets diretamente a partir dela.</div>
              ) : conversationUploads.length === 0 ? (
                <div style={emptyStyle()}>Esta conversa ainda não tem uploads indexados.</div>
              ) : (
                <div style={{ display: 'grid', gap: 8 }}>
                  {conversationUploads.map(item => (
                    <div key={item.file_id} style={rowStyle()}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'flex-start' }}>
                        <div>
                          <div style={{ fontSize: 13, fontWeight: 700, color: '#1f2937' }}>{item.filename || item.file_id}</div>
                          <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>
                            {formatDateLabel(item.uploaded_at)} · {item.row_count || 0} rows · {item.has_chunks ? 'com chunks' : 'sem chunks'}
                          </div>
                        </div>
                        <button
                          onClick={() => importConversationUpload(item)}
                          disabled={importingUploadId === item.file_id}
                          style={primaryActionStyle('#0f766e', 'rgba(15,118,110,0.08)', importingUploadId === item.file_id)}
                        >
                          {importingUploadId === item.file_id ? 'A importar...' : 'Promover'}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div style={cardStyle()}>
              <div style={sectionTitleStyle()}>Knowledge ativo</div>
              {knowledgeError ? <div style={{ fontSize: 12, color: '#b0103a', marginBottom: 10 }}>{knowledgeError}</div> : null}
              {knowledgeLoading ? (
                <div style={emptyStyle()}>A carregar assets persistentes...</div>
              ) : activeKnowledgeAssets.length === 0 ? (
                <div style={emptyStyle()}>Ainda não existem knowledge assets ativos.</div>
              ) : (
                <div style={{ display: 'grid', gap: 8 }}>
                  {activeKnowledgeAssets.slice(0, 8).map(item => (
                    <div key={item.asset_id} style={rowStyle()}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'flex-start' }}>
                        <div>
                          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                            <div style={{ fontSize: 13, fontWeight: 700, color: '#1f2937' }}>{item.title || item.asset_id}</div>
                            <span style={statusPillStyle(item.status)}>{item.status}</span>
                          </div>
                          <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>
                            {item.domain || 'domínio n/a'} · {item.source_type || 'source n/a'} · {formatDateLabel(item.updated_at)}
                          </div>
                          <div style={{ fontSize: 11, color: '#8b5e3c', marginTop: 3 }}>
                            {item.flow || item.journey || item.filename || 'sem flow/journey'}
                          </div>
                        </div>
                        <button
                          onClick={() => reviewKnowledgeAsset(item, 'deactivate')}
                          disabled={reviewingAssetId === item.asset_id}
                          style={reviewButtonStyle('#b45309', 'rgba(245,158,11,0.14)')}
                        >
                          {reviewingAssetId === item.asset_id ? 'A atualizar...' : 'Desativar'}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
            <div style={cardStyle()}>
              <div style={sectionTitleStyle()}>Knowledge inativo</div>
              {inactiveKnowledgeAssets.length === 0 ? (
                <div style={emptyStyle()}>Sem assets inativos neste momento.</div>
              ) : (
                <div style={{ display: 'grid', gap: 8 }}>
                  {inactiveKnowledgeAssets.slice(0, 8).map(item => (
                    <div key={item.asset_id} style={rowStyle()}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'flex-start' }}>
                        <div>
                          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                            <div style={{ fontSize: 13, fontWeight: 700, color: '#1f2937' }}>{item.title || item.asset_id}</div>
                            <span style={statusPillStyle(item.status)}>{item.status}</span>
                          </div>
                          <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>
                            {item.domain || 'domínio n/a'} · {item.source_type || 'source n/a'} · {formatDateLabel(item.updated_at)}
                          </div>
                        </div>
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                          <button
                            onClick={() => reviewKnowledgeAsset(item, 'reactivate')}
                            disabled={reviewingAssetId === item.asset_id}
                            style={reviewButtonStyle('#0f766e', 'rgba(15,118,110,0.08)')}
                          >
                            {reviewingAssetId === item.asset_id ? 'A atualizar...' : 'Reativar'}
                          </button>
                          <button
                            onClick={() => reviewKnowledgeAsset(item, 'delete')}
                            disabled={reviewingAssetId === item.asset_id}
                            style={reviewButtonStyle('#b0103a', 'rgba(99, 102, 241,0.08)')}
                          >
                            {reviewingAssetId === item.asset_id ? 'A atualizar...' : 'Apagar'}
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div style={cardStyle()}>
              <div style={sectionTitleStyle()}>Resumo do knowledge persistente</div>
              <div style={{ fontSize: 12, color: '#374151', lineHeight: 1.7 }}>
                <div><strong>{knowledgeAssets.length}</strong> assets registados.</div>
                <div style={{ marginTop: 6 }}>
                  Ativos:
                  {' '}
                  <strong>{activeKnowledgeAssets.length}</strong>
                  {' · '}
                  Inativos:
                  {' '}
                  <strong>{inactiveKnowledgeAssets.length}</strong>
                </div>
                <div style={{ marginTop: 6 }}>
                  Origem dominante:
                  {' '}
                  {topCategoryLabel(knowledgeAssets.map(item => item.source_type || 'n/a'))}
                </div>
                <div style={{ marginTop: 6 }}>
                  Domínios mais presentes:
                  {' '}
                  {topCategoryLabel(knowledgeAssets.map(item => item.domain || 'n/a'), 4)}
                </div>
              </div>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
            <div style={cardStyle()}>
              <div style={sectionTitleStyle()}>Recomendações automáticas</div>
              {recommendations.length === 0 ? (
                <div style={emptyStyle()}>Ainda não há recomendações acionáveis.</div>
              ) : recommendations.map((item, index) => (
                <div key={`${item.domain}-${item.action}-${index}`} style={rowStyle()}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: '#1f2937' }}>{item.title}</div>
                    <span style={pillStyle(item.priority)}>{item.priority}</span>
                  </div>
                  <div style={{ fontSize: 11, color: '#6b7280', marginTop: 4 }}>{item.rationale}</div>
                  <div style={{ fontSize: 11, color: '#8b5e3c', marginTop: 4 }}>{item.suggested_next_step}</div>
                </div>
              ))}
            </div>

            <div style={cardStyle()}>
              <div style={sectionTitleStyle()}>Candidatos a corpus curado</div>
              {curationCandidates.length === 0 ? (
                <div style={emptyStyle()}>Ainda não há drafts publicados fortes o suficiente para promoção.</div>
              ) : curationCandidates.map(item => (
                <div key={item.draft_id} style={rowStyle()}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: '#1f2937' }}>{item.title || item.draft_id}</div>
                  <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>
                    {item.domain} · qualidade {percent(item.quality_score)} · edit {item.edit_burden == null ? 'n/a' : percent(item.edit_burden)}
                  </div>
                  <div style={{ fontSize: 11, color: '#0f766e', marginTop: 4 }}>
                    {(item.reasons || []).join(', ')}
                  </div>
                  <div style={{ marginTop: 8 }}>
                    <button
                      onClick={() => promoteCandidate(item)}
                      disabled={promotingDraftId === item.draft_id}
                      style={{
                        borderRadius: 10,
                        border: '1px solid rgba(15,118,110,0.16)',
                        background: 'rgba(15,118,110,0.08)',
                        color: '#0f766e',
                        fontSize: 12,
                        fontWeight: 700,
                        padding: '8px 10px',
                        cursor: promotingDraftId === item.draft_id ? 'default' : 'pointer',
                        opacity: promotingDraftId === item.draft_id ? 0.6 : 1,
                      }}
                    >
                      {promotingDraftId === item.draft_id ? 'A submeter...' : 'Submeter para curadoria'}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
            <div style={cardStyle()}>
              <div style={sectionTitleStyle()}>Fila de revisão</div>
              {reviewQueue.length === 0 ? (
                <div style={emptyStyle()}>Sem candidatos pendentes de aprovação.</div>
              ) : reviewQueue.map(item => (
                <div key={item.draft_id} style={rowStyle()}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: '#1f2937' }}>{item.title || item.draft_id}</div>
                  <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>
                    {item.domain} · qualidade {percent(item.quality_score)} · source {item.source_user_sub || 'n/a'}
                  </div>
                  <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                    <button
                      onClick={() => reviewCandidate(item, 'approve')}
                      disabled={reviewingDraftId === item.draft_id}
                      style={reviewButtonStyle('#0f766e', 'rgba(15,118,110,0.08)')}
                    >
                      Aprovar
                    </button>
                    <button
                      onClick={() => reviewCandidate(item, 'reject')}
                      disabled={reviewingDraftId === item.draft_id}
                      style={reviewButtonStyle('#b0103a', 'rgba(99, 102, 241,0.08)')}
                    >
                      Rejeitar
                    </button>
                  </div>
                </div>
              ))}
            </div>

            <div style={cardStyle()}>
              <div style={sectionTitleStyle()}>Curadoria ativa recente</div>
              {recentActive.length === 0 ? (
                <div style={emptyStyle()}>Ainda não há exemplos ativos no registry promovido.</div>
              ) : recentActive.map(item => (
                <div key={item.draft_id} style={rowStyle()}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: '#1f2937' }}>{item.title || item.draft_id}</div>
                  <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>
                    {item.domain} · revisto por {item.reviewed_by || 'n/a'}
                  </div>
                  <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                    <button
                      onClick={() => reviewCandidate(item, 'deactivate')}
                      disabled={reviewingDraftId === item.draft_id}
                      style={reviewButtonStyle('#b45309', 'rgba(245,158,11,0.14)')}
                    >
                      Desativar
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 0.9fr', gap: 14, marginBottom: 14 }}>
            <div style={cardStyle()}>
              <div style={sectionTitleStyle()}>Hotspots por domínio</div>
              {hotspots.length === 0 ? (
                <div style={emptyStyle()}>Ainda não há dados suficientes.</div>
              ) : hotspots.map(item => (
                <div key={item.domain} style={rowStyle()}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: '#1f2937' }}>{item.domain}</div>
                    <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>
                      {item.draft_count} drafts · publish {percent(item.publish_rate)} · edit {percent(item.avg_edit_burden)}
                    </div>
                    <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>
                      Corpus curado: {item.curated_example_count || 0} · {Array.isArray(item.alerts) && item.alerts.length > 0 ? item.alerts.join(', ') : 'sem alertas'}
                    </div>
                  </div>
                </div>
              ))}
            </div>

            <div style={cardStyle()}>
              <div style={sectionTitleStyle()}>Gaps de corpus</div>
              {gaps.length === 0 ? (
                <div style={emptyStyle()}>Sem gaps críticos de corpus neste momento.</div>
              ) : gaps.map(item => (
                <div key={item.domain} style={rowStyle()}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: '#1f2937' }}>{item.domain}</div>
                  <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>
                    {item.curated_example_count} exemplos curados para {item.draft_count} drafts recentes.
                  </div>
                  <div style={{ fontSize: 11, color: '#8b5e3c', marginTop: 4 }}>{item.recommended_action}</div>
                </div>
              ))}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
            <div style={cardStyle()}>
              <div style={sectionTitleStyle()}>Drafts mais editados</div>
              {mostEdited.length === 0 ? (
                <div style={emptyStyle()}>Ainda não existem versões finais suficientes para medir edição.</div>
              ) : mostEdited.map(item => (
                <div key={item.draft_id} style={rowStyle()}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: '#1f2937' }}>{item.title || item.draft_id}</div>
                  <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>
                    {item.domain} · {item.feedback_outcome || 'sem feedback'} · edit burden {percent(item.edit_burden)}
                  </div>
                  <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>
                    Campos alterados: {(item.changed_fields || []).join(', ') || 'n/a'}
                  </div>
                </div>
              ))}
            </div>

            <div style={cardStyle()}>
              <div style={sectionTitleStyle()}>Corpus curado disponível</div>
              <div style={{ fontSize: 12, color: '#374151', lineHeight: 1.7 }}>
                <div><strong>{corpus.count || 0}</strong> histórias curadas no total.</div>
                <div style={{ marginTop: 6 }}>
                  Domínios mais fortes:
                  {' '}
                  {(corpus.top_domains || []).slice(0, 4).map(([name, count]) => `${name} (${count})`).join(', ') || 'n/a'}
                </div>
                <div style={{ marginTop: 6 }}>
                  Léxico dominante:
                  {' '}
                  {(corpus.top_ux_terms || []).slice(0, 6).map(([name]) => name).join(', ') || 'n/a'}
                </div>
              </div>
            </div>
          </div>

          <div style={cardStyle()}>
            <div style={sectionTitleStyle()}>Domínios observados</div>
            {domains.length === 0 ? (
              <div style={emptyStyle()}>Ainda não há drafts registados para avaliar.</div>
            ) : (
              <div style={{ display: 'grid', gap: 8 }}>
                {domains.map(item => (
                  <div key={item.domain} style={{
                    display: 'grid',
                    gridTemplateColumns: '1.4fr 0.7fr 0.7fr 0.7fr 1fr',
                    gap: 10,
                    alignItems: 'center',
                    padding: '10px 0',
                    borderTop: '1px solid rgba(0,0,0,0.06)',
                  }}>
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 700, color: '#1f2937' }}>{item.domain}</div>
                      <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>
                        Corpus {item.curated_example_count || 0} · {Array.isArray(item.alerts) && item.alerts.length > 0 ? item.alerts.join(', ') : 'sem alertas'}
                      </div>
                    </div>
                    <MiniMetric label="Drafts" value={item.draft_count} />
                    <MiniMetric label="Publish" value={percent(item.publish_rate)} />
                    <MiniMetric label="Qualidade" value={percent(item.avg_quality_score)} />
                    <MiniMetric label="Edit" value={percent(item.avg_edit_burden)} />
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      ) : null}
    </div>
  );
}

function MetricCard({ label, value, meta, tone }) {
  return (
    <div style={cardStyle()}>
      <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5, color: '#6b7280', marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontSize: 24, fontWeight: 800, color: tone || '#111827', marginBottom: 4 }}>{value}</div>
      <div style={{ fontSize: 11, color: '#6b7280' }}>{meta || ''}</div>
    </div>
  );
}

function MiniMetric({ label, value }) {
  return (
    <div>
      <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.4, color: '#94a3b8', marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 13, fontWeight: 700, color: '#1f2937' }}>{value}</div>
    </div>
  );
}

function sectionTitleStyle() {
  return {
    fontSize: 11,
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    color: '#6b7280',
    marginBottom: 10,
  };
}

function emptyStyle() {
  return { fontSize: 12, color: '#6b7280', lineHeight: 1.6 };
}

function rowStyle() {
  return {
    padding: '10px 0',
    borderTop: '1px solid rgba(0,0,0,0.06)',
  };
}

function toggleStyle(active) {
  return {
    borderRadius: 999,
    border: active ? '1px solid #1a1a1a' : '1px solid rgba(0,0,0,0.08)',
    background: active ? '#1a1a1a' : 'rgba(255,255,255,0.8)',
    color: active ? 'white' : '#334155',
    fontSize: 12,
    fontWeight: 700,
    padding: '8px 12px',
    cursor: 'pointer',
  };
}

function pillStyle(priority) {
  const tone = String(priority || '').toLowerCase();
  if (tone === 'high') {
    return {
      borderRadius: 999,
      background: 'rgba(99, 102, 241,0.12)',
      color: '#b0103a',
      fontSize: 10,
      fontWeight: 800,
      padding: '4px 8px',
      textTransform: 'uppercase',
      letterSpacing: 0.4,
    };
  }
  return {
    borderRadius: 999,
    background: 'rgba(245,158,11,0.14)',
    color: '#b45309',
    fontSize: 10,
    fontWeight: 800,
    padding: '4px 8px',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  };
}

function reviewButtonStyle(color, background) {
  return {
    borderRadius: 10,
    border: `1px solid ${background}`,
    background,
    color,
    fontSize: 12,
    fontWeight: 700,
    padding: '8px 10px',
    cursor: 'pointer',
  };
}

function primaryActionStyle(color, background, disabled = false) {
  return {
    borderRadius: 10,
    border: `1px solid ${background}`,
    background,
    color,
    fontSize: 12,
    fontWeight: 700,
    padding: '8px 10px',
    cursor: disabled ? 'default' : 'pointer',
    opacity: disabled ? 0.6 : 1,
  };
}

function fieldLabelStyle() {
  return {
    fontSize: 10,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
    color: '#6b7280',
    fontWeight: 700,
    marginBottom: 5,
  };
}

function fieldInputStyle() {
  return {
    width: '100%',
    borderRadius: 10,
    border: '1px solid rgba(0,0,0,0.08)',
    background: 'rgba(255,255,255,0.92)',
    color: '#111827',
    fontSize: 12,
    padding: '10px 11px',
    outline: 'none',
    boxSizing: 'border-box',
  };
}

function fieldTextareaStyle() {
  return {
    ...fieldInputStyle(),
    resize: 'vertical',
    lineHeight: 1.6,
    minHeight: 84,
  };
}

function statusPillStyle(status) {
  const tone = String(status || '').toLowerCase();
  if (tone === 'active') {
    return {
      borderRadius: 999,
      background: 'rgba(15,118,110,0.10)',
      color: '#0f766e',
      fontSize: 10,
      fontWeight: 800,
      padding: '4px 8px',
      textTransform: 'uppercase',
      letterSpacing: 0.4,
    };
  }
  if (tone === 'inactive') {
    return {
      borderRadius: 999,
      background: 'rgba(245,158,11,0.14)',
      color: '#b45309',
      fontSize: 10,
      fontWeight: 800,
      padding: '4px 8px',
      textTransform: 'uppercase',
      letterSpacing: 0.4,
    };
  }
  return {
    borderRadius: 999,
    background: 'rgba(99, 102, 241,0.12)',
    color: '#b0103a',
    fontSize: 10,
    fontWeight: 800,
    padding: '4px 8px',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  };
}

function formatDateLabel(value) {
  const text = String(value || '').trim();
  if (!text) return 'n/a';
  return text.replace('T', ' ').replace(/\.\d+Z?$/, 'Z');
}

function topCategoryLabel(values, limit = 3) {
  const counts = new Map();
  (Array.isArray(values) ? values : []).forEach(value => {
    const key = String(value || '').trim();
    if (!key || key === 'n/a') return;
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([name, count]) => `${name} (${count})`)
    .join(', ') || 'n/a';
}
