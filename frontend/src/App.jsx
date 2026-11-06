import React, { useState, useEffect, useRef } from 'react';
import {
  API_URL,
  APP_VERSION,
  DEFAULT_MAX_IMAGES_PER_MESSAGE,
  DEFAULT_MAX_FILES_PER_CONVERSATION,
  DEFAULT_MAX_BATCH_TOTAL_BYTES,
  UPLOAD_POLL_INTERVAL_MS,
  EMPTY_CONVERSATION,
  BRAND_SYMBOL_DATA_URI,
} from './utils/constants.js';
import { renderMarkdown } from './utils/markdown.js';
import {
  getPreferredExportableData,
  getPreferredToolResult,
  getPreferredAutoCsvDownload,
} from './utils/toolResults.js';
import {
    createConversation,
    formatRelativeTimestamp,
    getConversationKey,
    getConversationMetaLabel,
    sanitizeConversationTitle,
} from './utils/conversations.js';
import { getAuthHeaders, authFetch, startTokenAutoRefresh, stopTokenAutoRefresh } from './utils/auth.js';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import MessageBubble from './components/MessageBubble.jsx'; // MessageBubble composes ChartBlock and FeedbackWidget.
import TypingIndicator from './components/TypingIndicator.jsx';
import LoginScreen from './components/LoginScreen.jsx';
import UserMenu from './components/UserMenu.jsx';
import UserStoryWorkspace from './components/UserStoryWorkspace.jsx';
import ChatEmptyState from './components/ChatEmptyState.jsx';
import ConversationListItem from './components/ConversationListItem.jsx';
import ModalDialog from './components/ModalDialog.jsx';
import ChatComposer from './components/ChatComposer.jsx';
import StreamingActivityPanel from './components/StreamingActivityPanel.jsx';
import FeedbackDashboard from './components/FeedbackDashboard.jsx';
import ToolsManual from './components/ToolsManual.jsx';
import DigestSidePanel from './components/DigestSidePanel.jsx';
import {
    AttachmentIcon,
    BookIcon,
    ChevronDownIcon,
    ChevronLeftIcon,
    ConversationIcon,
    EditIcon,
    ExportIcon,
    FastIcon,
    MenuIcon,
    PlusIcon,
    ProIcon,
    StoryIcon,
    ThinkingIcon,
} from './components/AppIcons.jsx';
import {
    applyStreamingTraceEvent,
    EMPTY_STREAMING_TRACE,
    formatStreamingToolLabel,
} from './utils/streaming.js';
import useSpeechPrompt from './hooks/useSpeechPrompt.js';
import {
    getMaxBytesForFile,
    fetchUploadStatusBatch,
    isTabularFile,
    uploadSingleFileSync,
    queueUploadJob,
    queueUploadJobStream,
    queueUploadJobsBatch,
} from './utils/uploads.js';
import { useToast } from './components/ToastProvider.jsx';

/**
 * Detect clarification question pattern in agent response.
 * Pattern: question text followed by 2-4 numbered options at the end.
 * Returns { options: string[] } or null.
 */
function parseClarificationOptions(text) {
    const trimmed = (text || '').trim();
    if (!trimmed) return null;
    const lines = trimmed.split('\n').filter(l => l.trim());
    if (lines.length < 3) return null;
    // Find numbered options at the end (must be 2-4 consecutive numbered lines)
    const numbered = [];
    for (let i = lines.length - 1; i >= 0; i--) {
        if (/^\d+\.\s+\S/.test(lines[i].trim())) {
            numbered.unshift(lines[i].trim());
        } else {
            break;
        }
    }
    if (numbered.length < 2 || numbered.length > 4) return null;
    // Must have at least one non-numbered line before (the question)
    if (numbered.length >= lines.length) return null;
    // The question line(s) before the options must end with '?' to avoid
    // false positives on normal numbered lists in informational answers
    const questionLines = lines.slice(0, lines.length - numbered.length);
    const lastQuestionLine = (questionLines[questionLines.length - 1] || '').trim();
    if (!lastQuestionLine.endsWith('?')) return null;
    const options = numbered.map(l => l.replace(/^\d+\.\s+/, '').trim());
    return { options };
}

function getFirstName(user) {
    const raw = String(user?.display_name || user?.username || "").trim();
    if (!raw) return "";
    return raw.split(/\s+/)[0];
}

function buildFrontendToolResults(toolDetails = [], sourceLabel = "tool") {
    const results = [];
    if (!Array.isArray(toolDetails)) return results;
    for (const td of toolDetails) {
        if (!td?.result_json) continue;
        try {
            const summary = td?.result_summary || {};
            results.push({
                tool: td.tool,
                result: td.result_json,
                result_blob_ref: td.result_blob_ref || "",
                result_status: summary.result_status || "ok",
                error_kind: summary.error_kind || "",
                error_detail: summary.error_detail || "",
            });
        } catch (e) {
            console.warn(`Tool result parse failed (${sourceLabel}):`, e);
        }
    }
    return results;
}

function updateClientRuntimeContext({ conversationId, agentMode, requestId }) {
    try {
        window.__APP_RUNTIME_CONTEXT__ = {
            conversation_id: String(conversationId || '').slice(0, 100),
            agent_mode: String(agentMode || '').slice(0, 50),
            request_id: String(requestId || '').slice(0, 100),
        };
    } catch (_) {}
}

function App() {
    const [conversations, setConversations] = useState([createConversation("general")]);
    const [activeIdx, setActiveIdx] = useState(0);
    const [input, setInput] = useState("");
    const [loading, setLoading] = useState(false);
    const [streamingText, setStreamingText] = useState("");
    const [streamingRenderedBlocks, setStreamingRenderedBlocks] = useState([]);
    const [streamingActiveBlock, setStreamingActiveBlock] = useState("");
    const [streamingStatus, setStreamingStatus] = useState("");
    const [streamingTrace, setStreamingTrace] = useState(EMPTY_STREAMING_TRACE);
    const [sidebarOpen, setSidebarOpen] = useState(window.innerWidth > 768);
    const [agentMode, setAgentMode] = useState("general");
    const [modelTier, setModelTier] = useState("standard");
    const [tierRoutingNotice, setTierRoutingNotice] = useState("");
    const [uploadedFiles, setUploadedFiles] = useState([]);
    const [imagePreviews, setImagePreviews] = useState([]);
    const [uploadingFiles, setUploadingFiles] = useState(false);
    const [uploadProgressText, setUploadProgressText] = useState("");
    const [maxImagesPerMessage, setMaxImagesPerMessage] = useState(DEFAULT_MAX_IMAGES_PER_MESSAGE);
    const [maxFilesPerConversation, setMaxFilesPerConversation] = useState(DEFAULT_MAX_FILES_PER_CONVERSATION);
    const [maxBatchTotalBytes, setMaxBatchTotalBytes] = useState(DEFAULT_MAX_BATCH_TOTAL_BYTES);
    const [maxUploadFileBytes, setMaxUploadFileBytes] = useState(10 * 1024 * 1024);
    const [maxUploadFileBytesByExtension, setMaxUploadFileBytesByExtension] = useState({});
    const [uploadFrontendAsyncThresholdBytes, setUploadFrontendAsyncThresholdBytes] = useState(2 * 1024 * 1024);
    const [dragOver, setDragOver] = useState(false);
    const [pendingFiles, setPendingFiles] = useState([]); // S5-03: files selected but not yet uploaded
    const [auth, setAuth] = useState(null);
    const [authInitializing, setAuthInitializing] = useState(true);
    const [showExportDropdown, setShowExportDropdown] = useState(false);
    const [selectorOpen, setSelectorOpen] = useState(false);
    const [suggestionSeed, setSuggestionSeed] = useState(0);
    const [renameTarget, setRenameTarget] = useState(null);
    const [renameValue, setRenameValue] = useState("");
    const [deleteTarget, setDeleteTarget] = useState(null);
    const [digest, setDigest] = useState(null);
    const [digestLoading, setDigestLoading] = useState(false);
    const [digestError, setDigestError] = useState(null);
    const [showFeedbackDash, setShowFeedbackDash] = useState(false);
    const [showToolsManual, setShowToolsManual] = useState(false);
    const [digestPanelOpen, setDigestPanelOpen] = useState(false);
    const digestFetchedRef = useRef(null); // data string "YYYY-MM-DD" do último fetch

    const toast = useToast();

    const active = conversations[activeIdx] || createConversation(agentMode);
    const activeMessages = Array.isArray(active.messages) ? active.messages : [];
    const activeUploadedFiles = Array.isArray(active.uploadedFiles) ? active.uploadedFiles : uploadedFiles;
    const activePendingUploadJobs = Array.isArray(active.pendingUploadJobs) ? active.pendingUploadJobs : [];
    const authUser = auth ? auth.user : {};
    const firstName = getFirstName(authUser);
    const userId = authUser.username || "";
    const uploadBusy = uploadingFiles || activePendingUploadJobs.length > 0;
    const currentUploadProgressText = uploadingFiles
        ? uploadProgressText
        : (
            activePendingUploadJobs.length > 0
                ? `A processar ${activePendingUploadJobs.length} anexo(s) em background...`
                : ""
        );
    const chatEndRef = useRef(null);
    const inputRef = useRef(null);
    const fileInputRef = useRef(null);
    const imageInputRef = useRef(null);
    const selectorRef = useRef(null);
    const lastTtsAudioRef = useRef(null);
    const suggestionsRef = useRef({ seed: -1, mode: '', convIdx: -1, files: -1, result: [] });

    useEffect(() => { chatEndRef.current && chatEndRef.current.scrollIntoView({ behavior: "smooth" }); }, [conversations, activeIdx, loading, streamingText]);
    useEffect(() => {
        if (!Array.isArray(conversations) || conversations.length === 0) {
            setConversations([createConversation(agentMode)]);
            if (activeIdx !== 0) setActiveIdx(0);
            return;
        }
        if (activeIdx < 0 || activeIdx >= conversations.length) {
            setActiveIdx(0);
        }
    }, [conversations, activeIdx]);
    useEffect(() => {
        function handleClickOutside(e) {
            if (selectorRef.current && !selectorRef.current.contains(e.target)) {
                setSelectorOpen(false);
            }
        }
        document.addEventListener("mousedown", handleClickOutside);
        return () => document.removeEventListener("mousedown", handleClickOutside);
    }, []);

    useEffect(() => {
        function handleResize() {
            if (window.innerWidth <= 900) {
                setSidebarOpen(false);
            }
        }
        window.addEventListener("resize", handleResize);
        return () => window.removeEventListener("resize", handleResize);
    }, []);
    useEffect(() => {
        const latestAssistantWithRequest = [...activeMessages].reverse().find(
            (msg) => msg && msg.role === "assistant" && String(msg.request_id || "").trim()
        );
        updateClientRuntimeContext({
            conversationId: active?.id || "",
            agentMode,
            requestId: latestAssistantWithRequest?.request_id || "",
        });
    }, [active?.id, activeMessages, agentMode]);
    const { speechSupported, speechListening, speechStopping, speechProcessing, speechInterimText, speechNotice, speechSubmitMode, speechProvider, clearSpeechNotice, resetSpeechPrompt, toggleSpeech, toggleSpeechSubmitMode } = useSpeechPrompt({
        authFetchFn: authFetch,
        apiUrl: API_URL,
        agentMode,
        conversationId: active && active.id ? active.id : null,
        inputRef,
        onApplyPrompt: setInput,
        onAutoSendPrompt: async (prompt) => { await send(prompt); },
        hasPendingInput: () => Boolean(String(input || "").trim()),
    });
    useEffect(() => {
        if (!auth) return;
        let cancelled = false;
        let timerId = null;

        async function pollPendingUploads() {
            const pendingJobs = conversations.flatMap((conv, idx) =>
                (Array.isArray(conv?.pendingUploadJobs) ? conv.pendingUploadJobs : []).map(job => ({
                    ...job,
                    conversationIndex: idx,
                    conversationId: conv?.id || job?.conversation_id || "",
                }))
            );
            const pendingIds = Array.from(new Set(
                pendingJobs
                    .map(job => String(job.job_id || "").trim())
                    .filter(Boolean)
            ));

            if (!pendingIds.length) {
                return;
            }

            try {
                const batch = await fetchUploadStatusBatch(authFetch, API_URL, authHeaders, pendingIds);
                if (cancelled || !batch || !Array.isArray(batch.items)) {
                    return;
                }

                const itemsById = new Map(
                    batch.items.map(item => [String(item.job_id || "").trim(), item])
                );

                setConversations(prev => {
                    let changed = false;
                    const next = prev.map((conv, idx) => {
                        const pending = Array.isArray(conv?.pendingUploadJobs) ? conv.pendingUploadJobs : [];
                        if (!pending.length) return conv;

                        const remaining = [];
                        const newMessages = [];
                        let nextUploadedFiles = Array.isArray(conv.uploadedFiles) ? conv.uploadedFiles : [];
                        let jobStateChanged = false;

                        const UPLOAD_JOB_ZOMBIE_MS = 15 * 60 * 1000; // 15 min — alinhado com UPLOAD_JOB_STALE_SECONDS do backend
                        for (const job of pending) {
                            const item = itemsById.get(String(job.job_id || "").trim());
                            if (!item) {
                                // If job is not found in batch AND older than timeout, drop it
                                const queuedAt = job.queued_at ? new Date(job.queued_at).getTime() : 0;
                                if (queuedAt > 0 && (Date.now() - queuedAt) > UPLOAD_JOB_ZOMBIE_MS) {
                                    newMessages.push({
                                        role: "assistant",
                                        content: `Upload de **${job.filename || "ficheiro"}** expirou sem resposta do servidor.`,
                                        tools_used: ["upload_file"],
                                    });
                                    jobStateChanged = true;
                                    continue;
                                }
                                remaining.push(job);
                                continue;
                            }

                            const status = String(item.status || "").toLowerCase();
                            if (status === "completed" && item.result) {
                                const result = item.result;
                                const rows = Number(result.rows || 0);
                                const columns = Array.isArray(result.columns) ? result.columns.length : 0;
                                if (Array.isArray(result.all_files)) {
                                    nextUploadedFiles = result.all_files;
                                }
                                // A4: Use auto_summary from backend if available
                                const summaryText = result.auto_summary
                                    ? String(result.auto_summary)
                                    : `Anexo processado com sucesso: **${result.filename || job.filename || "ficheiro"}**${rows > 0 ? ` (${rows.toLocaleString("pt-PT")} linhas` : ""}${columns > 0 ? ` · ${columns} colunas` : ""}).`;
                                newMessages.push({
                                    role: "assistant",
                                    content: summaryText,
                                    tools_used: ["upload_file"],
                                });
                                jobStateChanged = true;
                                continue;
                            }

                            if (status === "failed" || status === "forbidden" || status === "not_found") {
                                const reason = String(item.error || "Falha no processamento do ficheiro").trim();
                                newMessages.push({
                                    role: "assistant",
                                    content: `Falha ao processar **${job.filename || "ficheiro"}**: ${reason}`,
                                    tools_used: ["upload_file"],
                                });
                                jobStateChanged = true;
                                continue;
                            }

                            const nextJob = {
                                ...job,
                                status: status || job.status || "queued",
                                updated_at: item.updated_at || job.updated_at || "",
                            };
                            if (
                                nextJob.status !== job.status
                                || nextJob.updated_at !== job.updated_at
                            ) {
                                jobStateChanged = true;
                            }
                            remaining.push(nextJob);
                        }

                        if (!jobStateChanged && remaining.length === pending.length && newMessages.length === 0) {
                            return conv;
                        }

                        changed = true;
                        return {
                            ...conv,
                            fileMode: conv.fileMode || nextUploadedFiles.length > 0 || remaining.length > 0,
                            uploadedFiles: nextUploadedFiles,
                            pendingUploadJobs: remaining,
                            updatedAt: new Date().toISOString(),
                            messages: newMessages.length > 0 ? [...conv.messages, ...newMessages] : conv.messages,
                        };
                    });

                    return changed ? next : prev;
                });
            } catch (err) {
                console.warn("Pending upload polling failed:", err);
            } finally {
                if (!cancelled) {
                    timerId = window.setTimeout(pollPendingUploads, UPLOAD_POLL_INTERVAL_MS);
                }
            }
        }

        pollPendingUploads();
        return () => {
            cancelled = true;
            if (timerId) window.clearTimeout(timerId);
        };
    }, [auth, conversations]);

    function handleLogin(user) {
        const a = { user };
        setAuth(a);
    }

    async function handleLogout() {
        resetSpeechPrompt();
        try {
            await authFetch(API_URL + "/api/auth/logout", { method: "POST" });
        } catch (e) {
            console.warn("Logout request failed:", e);
        }
        setAuth(null);
        setConversations([createConversation("general")]);
        setActiveIdx(0);
        setUploadedFiles([]);
        setImagePreviews([]);
        setRenameTarget(null);
        setDeleteTarget(null);
    }

    function authHeaders() { return getAuthHeaders(); }
    const saveTimerRef = useRef(null);

    useEffect(() => {
        if (userId && auth) loadChats(userId);
    }, [userId, auth]);

    // Auto-refresh do JWT — inicia quando autenticado, para no logout
    useEffect(() => {
        if (auth) {
            startTokenAutoRefresh(API_URL, () => {
                toast.warning("Sessão expirada. Faz login novamente.");
                setAuth(null);
            });
        } else {
            stopTokenAutoRefresh();
        }
        return () => stopTokenAutoRefresh();
    }, [auth]);

    // Fetch digest diário — uma vez por dia, após autenticação
    useEffect(() => {
        if (!auth) return;
        const today = new Date().toISOString().slice(0, 10);
        if (digestFetchedRef.current === today) return; // já buscou hoje
        let cancelled = false;
        async function fetchDigest() {
            setDigestLoading(true);
            setDigestError(null);
            try {
                const res = await authFetch(API_URL + "/api/digest");
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                if (!cancelled) {
                    setDigest(data);
                    digestFetchedRef.current = today;
                }
            } catch (e) {
                if (!cancelled) setDigestError("Não foi possível carregar o resumo diário.");
            } finally {
                if (!cancelled) setDigestLoading(false);
            }
        }
        fetchDigest();
        return () => { cancelled = true; };
    }, [auth]);

    useEffect(() => {
        let cancelled = false;
        async function bootstrapSession() {
            try {
                const res = await authFetch(API_URL + "/api/auth/me");
                if (!res.ok) {
                    if (!cancelled) setAuth(null);
                    return;
                }
                const me = await res.json();
                if (!cancelled) {
                    setAuth({
                        user: {
                            username: me.username,
                            role: me.role,
                            display_name: me.name || me.username,
                        },
                    });
                }
            } catch (e) {
                if (!cancelled) setAuth(null);
            } finally {
                if (!cancelled) setAuthInitializing(false);
            }
        }
        bootstrapSession();
        return () => { cancelled = true; };
    }, []);
    useEffect(() => {
        let cancelled = false;
        async function loadRuntimeLimits() {
            try {
                const res = await fetch(API_URL + "/api/info");
                if (!res.ok) return;
                const info = await res.json();
                const limits = info && info.upload_limits ? info.upload_limits : {};
                const maxImages = Number(limits.max_images_per_message);
                const maxFiles = Number(limits.max_files_per_conversation);
                const maxBatchBytes = Number(limits.max_batch_total_bytes);
                const maxFileBytes = Number(limits.max_file_bytes);
                const maxConcurrency = Number(limits.max_concurrent_jobs);
                const asyncThreshold = Number(limits.frontend_async_threshold_bytes);
                const byExtension = limits.max_file_bytes_by_extension && typeof limits.max_file_bytes_by_extension === "object"
                    ? limits.max_file_bytes_by_extension
                    : {};
                if (!cancelled && Number.isFinite(maxImages) && maxImages > 0) {
                    setMaxImagesPerMessage(Math.floor(maxImages));
                }
                if (!cancelled && Number.isFinite(maxFiles) && maxFiles > 0) {
                    setMaxFilesPerConversation(Math.floor(maxFiles));
                }
                if (!cancelled && Number.isFinite(maxBatchBytes) && maxBatchBytes > 0) {
                    setMaxBatchTotalBytes(Math.floor(maxBatchBytes));
                }
                if (!cancelled && Number.isFinite(maxFileBytes) && maxFileBytes > 0) {
                    setMaxUploadFileBytes(Math.floor(maxFileBytes));
                }
                if (!cancelled) {
                    setMaxUploadFileBytesByExtension(byExtension);
                }
                if (!cancelled && Number.isFinite(asyncThreshold) && asyncThreshold > 0) {
                    setUploadFrontendAsyncThresholdBytes(Math.floor(asyncThreshold));
                }
                if (!cancelled && Number.isFinite(maxConcurrency) && maxConcurrency > 0) {
                    setUploadWorkerConcurrency(Math.max(1, Math.min(4, Math.floor(maxConcurrency))));
                }
            } catch (e) {
                // Fallback silencioso para defaults locais
            }
        }
        loadRuntimeLimits();
        return () => { cancelled = true; };
    }, []);

    if (authInitializing) return React.createElement("div", { style: { minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", color: "#777", fontSize: 14 } }, "A iniciar sessão...");
    if (!auth) return React.createElement(LoginScreen, { onLogin: handleLogin });

    async function loadChats(uid) {
        try {
            const res = await authFetch(API_URL + "/api/chats/" + uid, { headers: authHeaders() });
            if (res.status === 401) { handleLogout(); return; }
            if (res.ok) {
                const data = await res.json();
                if (data.chats && data.chats.length > 0) {
                    const loaded = data.chats.map(c => ({
                        id: c.conversation_id, title: c.title || "Conversa",
                        messages: [], savedOnServer: true, uploadedFiles: [], pendingUploadJobs: [],
                        message_count: c.message_count,
                        updatedAt: c.updated_at || "",
                        titleManuallyEdited: true,
                    }));
                    setConversations([createConversation(agentMode), ...loaded]);
                }
            }
        } catch (e) { console.error("Load chats error:", e); }
    }

    async function loadChatMessages(uid, convId, idx) {
        try {
            const res = await authFetch(API_URL + "/api/chats/" + uid + "/" + convId, { headers: authHeaders() });
            if (res.ok) {
                const data = await res.json();
                setConversations(prev => {
                    const u = [...prev];
                    u[idx] = { ...u[idx], messages: data.messages || [], savedOnServer: true, updatedAt: data.updated_at || u[idx].updatedAt };
                    return u;
                });
            }
        } catch (e) { console.error("Load msgs error:", e); }
    }

    function scheduleSave(conv) {
        if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
        saveTimerRef.current = setTimeout(() => saveChat(conv), 2000);
    }

    async function saveChat(conv) {
        if (!conv.id || conv.messages.length === 0) return;
        try {
            await authFetch(API_URL + "/api/chats/save", {
                method: "POST", headers: authHeaders(),
                body: JSON.stringify({ user_id: userId, conversation_id: conv.id, title: conv.title, messages: conv.messages }),
            });
        } catch (e) { console.error("Save error:", e); }
    }

    function startNew() {
        setConversations(prev => {
            if (prev[0] && prev[0].messages.length === 0) {
                const updated = [...prev];
                updated[0] = { ...updated[0], mode: agentMode };
                return updated;
            }
            return [createConversation(agentMode), ...prev];
        });
        setActiveIdx(0);
        setUploadedFiles([]);
        setImagePreviews([]);
        setRenameTarget(null);
        setDeleteTarget(null);
        setTimeout(() => inputRef.current && inputRef.current.focus(), 100);
    }

    function deleteConv(idx) {
        const conv = conversations[idx];
        let nextUploadedFiles = [];
        setConversations(prev => {
            const u = prev.filter((_, i) => i !== idx);
            if (u.length === 0) {
                nextUploadedFiles = [];
                return [createConversation("general")];
            }
            const nextIdx = idx <= activeIdx ? Math.max(0, activeIdx - 1) : activeIdx;
            const nextConv = u[nextIdx] || createConversation(agentMode);
            nextUploadedFiles = Array.isArray(nextConv.uploadedFiles) ? nextConv.uploadedFiles : [];
            return u;
        });
        if (idx <= activeIdx) setActiveIdx(Math.max(0, activeIdx - 1));
        if (idx === activeIdx) {
            setUploadedFiles(nextUploadedFiles);
            setImagePreviews([]);
        }
        // Delete from server
        if (conv.id && userId) {
            authFetch(API_URL + "/api/chats/" + userId + "/" + conv.id, { method: "DELETE", headers: authHeaders() }).catch(() => {});
        }
    }

    function openRenameDialog(idx) {
        const conv = conversations[idx];
        if (!conv) return;
        setRenameTarget({ idx, key: getConversationKey(conv, idx) });
        setRenameValue(conv.title || "Nova conversa");
    }

    function closeRenameDialog() {
        setRenameTarget(null);
        setRenameValue("");
    }

    async function submitRenameDialog() {
        if (!renameTarget) return;
        const conv = conversations[renameTarget.idx];
        if (!conv) {
            closeRenameDialog();
            return;
        }
        const nextTitle = sanitizeConversationTitle(renameValue);
        if (nextTitle === conv.title) {
            closeRenameDialog();
            return;
        }
        const previousTitle = conv.title;
        const nextUpdatedAt = new Date().toISOString();

        setConversations(prev => {
            const updated = [...prev];
            if (!updated[renameTarget.idx]) return prev;
            updated[renameTarget.idx] = {
                ...updated[renameTarget.idx],
                title: nextTitle,
                updatedAt: nextUpdatedAt,
                titleManuallyEdited: true,
            };
            return updated;
        });

        closeRenameDialog();

        if (!conv.id || !userId) {
            return;
        }

        try {
            const res = await authFetch(API_URL + "/api/chats/" + userId + "/" + conv.id + "/title", {
                method: "POST",
                headers: authHeaders(),
                body: JSON.stringify({ title: nextTitle }),
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || "Não foi possível guardar o novo título");
            }
        } catch (e) {
            setConversations(prev => {
                const updated = [...prev];
                const idx = updated.findIndex(item => item && item.id === conv.id);
                if (idx === -1) return prev;
                updated[idx] = {
                    ...updated[idx],
                    title: previousTitle,
                };
                return updated;
            });
            toast.error("Erro ao renomear conversa: " + e.message);
        }
    }

    function openDeleteDialog(idx) {
        const conv = conversations[idx];
        if (!conv) return;
        setDeleteTarget({ idx, title: conv.title || "Nova conversa" });
    }

    function closeDeleteDialog() {
        setDeleteTarget(null);
    }

    function confirmDeleteDialog() {
        if (deleteTarget) {
            deleteConv(deleteTarget.idx);
        }
        closeDeleteDialog();
    }

    // S5-03: File selection → preview (pendingFiles) → confirm → upload
    function handleFileSelect(e) {
        const selectedFiles = Array.from((e.target && e.target.files) || []);
        if (e.target) e.target.value = "";
        if (selectedFiles.length === 0) return;

        const currentCount = (Array.isArray(activeUploadedFiles) ? activeUploadedFiles.length : 0) + activePendingUploadJobs.length + pendingFiles.length;
        const availableSlots = Math.max(0, maxFilesPerConversation - currentCount);
        const filesWithinSlot = selectedFiles.slice(0, availableSlots);
        const validated = [];
        const skippedByBatchLimit = [];
        const skippedByFileLimit = [];
        let batchBytes = 0;
        for (const file of filesWithinSlot) {
            const fsize = Number(file.size || 0);
            const maxBytesForFile = getMaxBytesForFile(file, maxUploadFileBytes, maxUploadFileBytesByExtension);
            if (fsize > maxBytesForFile) {
                skippedByFileLimit.push({ filename: file.name || "ficheiro", limitBytes: maxBytesForFile });
                continue;
            }
            if (batchBytes + fsize > maxBatchTotalBytes) {
                skippedByBatchLimit.push(file.name || "ficheiro");
                continue;
            }
            validated.push(file);
            batchBytes += fsize;
        }
        if (validated.length === 0) {
            if (skippedByFileLimit.length > 0) {
                const first = skippedByFileLimit[0];
                toast.warning(`${first.filename} excede o limite máximo permitido (${(first.limitBytes / (1024 * 1024)).toFixed(0)}MB).`);
            } else if (skippedByBatchLimit.length > 0) {
                toast.warning(`Lote excede ${(maxBatchTotalBytes / (1024 * 1024)).toFixed(0)}MB. Seleciona menos ficheiros ou ficheiros mais pequenos.`);
            } else {
                toast.warning(`Limite de ${maxFilesPerConversation} ficheiros por conversa atingido.`);
            }
            return;
        }
        if (selectedFiles.length > validated.length || skippedByBatchLimit.length > 0 || skippedByFileLimit.length > 0) {
            toast.info(`Só foram aceites ${validated.length} ficheiros (máximo por conversa: ${maxFilesPerConversation}).`);
        }

        // Build preview entries with optional row estimates for CSV
        const entries = validated.map(file => {
            const entry = { file, rowEstimate: null, thumbnailUrl: null };
            const ext = (file.name || '').toLowerCase();
            if (ext.endsWith('.csv') || ext.endsWith('.tsv')) {
                // Quick row count by reading first 512KB of text
                const slice = file.slice(0, 512 * 1024);
                const reader = new FileReader();
                reader.onload = (ev) => {
                    const text = ev.target.result || '';
                    const lines = text.split('\n').filter(l => l.trim().length > 0);
                    const headerLines = lines.length > 0 ? 1 : 0;
                    const dataLines = Math.max(0, lines.length - headerLines);
                    // If file was truncated, estimate total from ratio
                    const ratio = file.size > 0 ? file.size / slice.size : 1;
                    const estimate = ratio > 1.05 ? Math.round(dataLines * ratio) : dataLines;
                    setPendingFiles(prev => prev.map((p, i) =>
                        p.file === file ? { ...p, rowEstimate: estimate } : p
                    ));
                };
                reader.readAsText(slice);
            } else if (file.type && file.type.startsWith('image/')) {
                // Generate thumbnail for image files
                const reader = new FileReader();
                reader.onload = (ev) => {
                    setPendingFiles(prev => prev.map(p =>
                        p.file === file ? { ...p, thumbnailUrl: ev.target.result } : p
                    ));
                };
                reader.readAsDataURL(file);
            }
            return entry;
        });

        setPendingFiles(prev => [...prev, ...entries]);
    }

    function handlePendingFileRemove(idx) {
        setPendingFiles(prev => prev.filter((_, i) => i !== idx));
    }

    function handlePendingFilesCancel() {
        setPendingFiles([]);
    }

    function handlePendingFilesConfirm() {
        const files = pendingFiles.map(e => e.file);
        setPendingFiles([]);
        if (files.length === 0) return;
        _doFileUpload(files);
    }

    async function _doFileUpload(filesToUpload) {
        if (filesToUpload.length === 0) return;

        try {
            setUploadingFiles(true);
            setUploadProgressText(`A preparar upload de ${filesToUpload.length} ficheiro(s)...`);
            let convId = active && active.id ? active.id : null;
            let lastData = null;
            let useAsyncJobs = true;
            const queuedJobs = [];
            const preSkipped = []; // validation already done in handleFileSelect
            const hasLargeFiles = filesToUpload.some(file => Number(file.size || 0) > uploadFrontendAsyncThresholdBytes);
            const syncCandidates = hasLargeFiles ? [] : filesToUpload;
            const asyncCandidates = hasLargeFiles ? filesToUpload : [];
            const streamFiles = asyncCandidates.filter(file => isTabularFile(file));
            const regularFiles = asyncCandidates.filter(file => !isTabularFile(file));

            if (!hasLargeFiles) {
                useAsyncJobs = false;
            }

            if (useAsyncJobs && regularFiles.length > 0) {
                setUploadProgressText(`A enfileirar ${filesToUpload.length} ficheiro(s)...`);
                const batch = await queueUploadJobsBatch(authFetch, API_URL, regularFiles, convId);
                if (batch && Array.isArray(batch.queued_jobs)) {
                    convId = batch.conversation_id || convId;
                    batch.queued_jobs.forEach(j => {
                        queuedJobs.push({
                            job_id: j.job_id,
                            filename: j.filename,
                        });
                    });
                    if (Array.isArray(batch.skipped)) {
                        batch.skipped.forEach(s => {
                            preSkipped.push({
                                filename: s.filename || "ficheiro",
                                error: s.reason || "Não enfileirado",
                            });
                        });
                    }
                } else {
                    for (const file of regularFiles) {
                        if (!useAsyncJobs) break;
                        setUploadProgressText(`A enfileirar ${file.name}...`);
                        let queued = null;
                        try {
                            queued = await queueUploadJob(authFetch, API_URL, file, convId);
                        } catch (err) {
                            preSkipped.push({
                                filename: file.name || "ficheiro",
                                error: (err && err.message) ? err.message : "Não foi possível enfileirar",
                            });
                            continue;
                        }
                        if (!queued) {
                            useAsyncJobs = false;
                            queuedJobs.length = 0;
                            break;
                        }
                        convId = queued.conversation_id || convId;
                        queuedJobs.push({
                            job_id: queued.job_id,
                            filename: file.name,
                        });
                    }
                }
            }

            if (useAsyncJobs && streamFiles.length > 0) {
                for (const file of streamFiles) {
                    setUploadProgressText(`A enviar ${file.name} em streaming...`);
                    let queued = null;
                    try {
                        queued = await queueUploadJobStream(authFetch, API_URL, file, convId);
                    } catch (err) {
                        preSkipped.push({
                            filename: file.name || "ficheiro",
                            error: (err && err.message) ? err.message : "Não foi possível enfileirar",
                        });
                        continue;
                    }
                    if (!queued) {
                        try {
                            queued = await queueUploadJob(authFetch, API_URL, file, convId);
                        } catch (err) {
                            preSkipped.push({
                                filename: file.name || "ficheiro",
                                error: (err && err.message) ? err.message : "Não foi possível enfileirar",
                            });
                            continue;
                        }
                    }
                    convId = queued.conversation_id || convId;
                    queuedJobs.push({
                        job_id: queued.job_id,
                        filename: file.name,
                    });
                }
            }

            if (useAsyncJobs) {
                if (queuedJobs.length === 0) {
                    const failedSummary = preSkipped.slice(0, 3).map(f => `${f.filename}: ${f.error}`).join(" | ");
                    throw new Error(failedSummary ? `Nenhum ficheiro foi enfileirado. ${failedSummary}` : "Nenhum ficheiro foi enfileirado.");
                }
                const queuedSummary = queuedJobs.map(job => `• ${job.filename}`).join("\n");
                const failureDetails = preSkipped.length > 0
                    ? `\n\nIgnorados nesta tentativa:\n${preSkipped.slice(0, 5).map(f => `• ${f.filename}: ${f.error}`).join("\n")}`
                    : "";
                setConversations(prev => {
                    const u = [...prev];
                    const currentConv = u[activeIdx] || createConversation(agentMode);
                    const existingJobs = Array.isArray(currentConv.pendingUploadJobs) ? currentConv.pendingUploadJobs : [];
                    const existingIds = new Set(existingJobs.map(job => String(job.job_id || "").trim()));
                    const mergedJobs = existingJobs.slice();
                    for (const job of queuedJobs) {
                        const safeJobId = String(job.job_id || "").trim();
                        if (!safeJobId || existingIds.has(safeJobId)) continue;
                        mergedJobs.push({
                            job_id: safeJobId,
                            filename: job.filename || "ficheiro",
                            status: "queued",
                            conversation_id: convId || currentConv.id || "",
                            queued_at: new Date().toISOString(),
                        });
                    }
                    u[activeIdx] = {
                        ...currentConv,
                        id: convId || currentConv.id,
                        fileMode: true,
                        pendingUploadJobs: mergedJobs,
                        updatedAt: new Date().toISOString(),
                        messages: [...currentConv.messages, {
                            role: "assistant",
                            content: `Upload iniciado para ${queuedJobs.length} ficheiro(s).\n\n${queuedSummary}${failureDetails}\n\nVou continuar a processar em background e aviso aqui na conversa quando cada anexo ficar pronto.`,
                            tools_used: ["upload_file"],
                        }],
                    };
                    return u;
                });
            } else {
                const uploadedNow = [];
                const failedNow = [];
                for (const file of syncCandidates) {
                    setUploadProgressText(`A processar ${file.name}...`);
                    try {
                        const data = await uploadSingleFileSync(authFetch, API_URL, file, convId);
                        convId = data.conversation_id || convId;
                        lastData = data;
                        uploadedNow.push(data);
                    } catch (err) {
                        failedNow.push({
                            filename: file.name || "ficheiro",
                            error: (err && err.message) ? err.message : "Falha no processamento",
                        });
                    }
                }
                failedNow.push(...preSkipped);

                if (uploadedNow.length === 0) {
                    const failedSummary = failedNow.slice(0, 3).map(f => `${f.filename}: ${f.error}`).join(" | ");
                    throw new Error(failedSummary ? `Nenhum ficheiro processado com sucesso. ${failedSummary}` : "Nenhum ficheiro processado com sucesso.");
                }

                const allFiles = (lastData && Array.isArray(lastData.all_files)) ? lastData.all_files : [];
                setUploadedFiles(allFiles);
                setConversations(prev => {
                    const u = [...prev];
                    const successDetails = uploadedNow.map(d => `• ${d.filename} (${d.rows} linhas)`).join("\n");
                    const failureDetails = failedNow.length > 0
                        ? `\n\n${failedNow.length} ficheiro(s) com falha:\n` + failedNow.slice(0, 5).map(f => `• ${f.filename}: ${f.error}`).join("\n")
                        : "";
                    // Detect tabular files for proactive suggestions
                    const tabularExts = ['.csv', '.xlsx', '.xls', '.tsv'];
                    const hasTabular = uploadedNow.some(d =>
                        tabularExts.some(ext => (d.filename || '').toLowerCase().endsWith(ext))
                    );
                    const uploadMsg = {
                        role: "assistant",
                        content: `${uploadedNow.length} ficheiro(s) carregado(s) com sucesso.\n\n${successDetails}${failureDetails}\n\nTotal anexado nesta conversa: ${allFiles.length}/${maxFilesPerConversation}.`,
                        tools_used: ["upload_file"],
                    };
                    if (hasTabular) {
                        uploadMsg.quick_replies = [
                            "Analisa este ficheiro",
                            "Mostra as primeiras linhas",
                            "Faz um resumo estatístico",
                        ];
                    }
                    u[activeIdx] = {
                        ...u[activeIdx],
                        id: (lastData && lastData.conversation_id) || u[activeIdx].id,
                        fileMode: true,
                        uploadedFiles: allFiles,
                        updatedAt: new Date().toISOString(),
                        messages: [...u[activeIdx].messages, uploadMsg],
                    };
                    return u;
                });
            }
        } catch (e) {
            toast.error("Erro no upload: " + e.message);
        } finally {
            setUploadingFiles(false);
            setUploadProgressText("");
            e.target.value = "";
        }
    }

    async function getPendingUploads(conversationId) {
        if (!conversationId) return 0;
        try {
            const res = await authFetch(API_URL + "/api/upload/pending/" + encodeURIComponent(conversationId), { method: "GET" });
            if (!res.ok) return 0;
            const data = await res.json();
            return Number(data.pending_jobs || 0);
        } catch (e) {
            return 0;
        }
    }

    async function handleRetryUploadJob(job) {
        if (!job || !job.job_id || !active) return;
        try {
            const res = await authFetch(
                API_URL + "/api/upload/retry/" + encodeURIComponent(job.job_id),
                { method: "POST" },
            );
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || "Erro no retry");
            }
            const data = await res.json();
            // Substituir o job antigo pelo novo na lista de pendingUploadJobs
            setConversations(prev => {
                const u = [...prev];
                const conv = u[activeIdx];
                if (!conv) return prev;
                const pending = Array.isArray(conv.pendingUploadJobs) ? conv.pendingUploadJobs : [];
                const updated = pending.map(j => {
                    if (j.job_id === job.job_id) {
                        return {
                            ...j,
                            job_id: data.new_job_id,
                            status: "queued",
                            queued_at: new Date().toISOString(),
                        };
                    }
                    return j;
                });
                u[activeIdx] = { ...conv, pendingUploadJobs: updated };
                return u;
            });
            toast.info(`Retry iniciado para ${job.filename || 'ficheiro'}.`);
        } catch (err) {
            toast.error(`Erro ao re-tentar upload: ${err.message}`);
        }
    }

    function addImageFiles(files) {
        const candidates = Array.from(files).filter(f => f.type.startsWith("image/"));
        const allowedSlots = Math.max(0, maxImagesPerMessage - imagePreviews.length);
        const accepted = candidates.slice(0, allowedSlots);
        if (candidates.length > accepted.length) {
            toast.warning(`Máximo de ${maxImagesPerMessage} imagens por pedido.`);
        }
        accepted.forEach(f => {
            const reader = new FileReader();
            reader.onload = (ev) => {
                const dataUrl = ev.target.result;
                const base64 = dataUrl.split(",")[1];
                const contentType = f.type;
                setImagePreviews(prev => [...prev, { dataUrl, base64, contentType, filename: f.name, size: (f.size / 1024).toFixed(0) + "KB" }]);
            };
            reader.readAsDataURL(f);
        });
    }

    function handleImageUpload(e) { if (e.target.files) addImageFiles(e.target.files); e.target.value = ""; }

    function handlePaste(e) {
        const items = e.clipboardData && e.clipboardData.items;
        if (!items) return;
        const imageFiles = [];
        for (let i = 0; i < items.length; i++) {
            if (items[i].type.startsWith("image/")) { const f = items[i].getAsFile(); if (f) imageFiles.push(f); }
        }
        if (imageFiles.length > 0) { e.preventDefault(); addImageFiles(imageFiles); }
    }

    function removeImage(idx) { setImagePreviews(prev => prev.filter((_, i) => i !== idx)); }

    function patchActiveConversation(patch) {
        if (!patch) return;
        setConversations(prev => {
            const u = [...prev];
            const current = u[activeIdx] || createConversation(agentMode);
            u[activeIdx] = { ...current, ...patch };
            return u;
        });
    }

    function normalizeRoutingText(value) {
        return String(value || "")
            .toLowerCase()
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "");
    }

    function shouldEscalateFastPrompt(question, filesCount = 0, imagesCount = 0) {
        if (filesCount > 0 || imagesCount > 0) return true;
        const q = normalizeRoutingText(question);
        if (!q) return false;
        const analyticKeywords = [
            "analisa", "analisar", "analise", "resumo estatistico", "estatistica",
            "minimo", "maximo", "media", "mediana", "percentil", "desvio padrao",
            "variancia", "correlacao", "regressao", "comparar", "comparacao",
            "tendencia", "padrao", "serie temporal", "volatilidade", "agregacao",
            "por ano", "por mes", "por semana", "por dia", "grafico", "chart",
            "plot", "scatter", "histograma", "csv", "excel", "xlsx",
            "dataset", "tabela", "ficheiro", "upload", "anexo",
        ];
        if (analyticKeywords.some((kw) => q.includes(kw))) return true;
        return /\b(min|max|avg|mean|std|p\d{2})\b/.test(q);
    }

    // S5-02: Suggest Pro tier for complex prompts (non-blocking)
    function shouldSuggestProTier(question, filesCount = 0) {
        const q = normalizeRoutingText(question);
        if (!q || q.length < 30) return false;
        // Multi-step / complex reasoning indicators
        const complexIndicators = [
            "compara", "comparar", "comparacao", "cross", "cruzar", "cruzamento",
            "correlacao", "regressao", "causal", "impacto", "root cause",
            "explica porque", "explica por que", "analise profunda",
            "estrategia", "recomendacao", "plano de acao", "roadmap",
            "apresentacao", "powerpoint", "pptx", "relatorio completo",
            "multi", "varias", "todos os", "todas as", "de ponta a ponta",
            "arquitetura", "redesenhar", "migrar", "migracao",
        ];
        let score = 0;
        if (complexIndicators.some(kw => q.includes(kw))) score += 2;
        if (filesCount >= 2) score += 1; // multi-file analysis
        if (q.length > 120) score += 1; // long prompt = likely complex
        if ((q.match(/\be\b/g) || []).length >= 3) score += 1; // many conjunctions = multi-part
        return score >= 2;
    }

    function handleDrop(e) {
        e.preventDefault(); setDragOver(false);
        const files = e.dataTransfer && e.dataTransfer.files;
        if (!files || files.length === 0) return;
        const imageFiles = Array.from(files).filter(f => f.type.startsWith("image/"));
        const dataFiles = Array.from(files).filter(f => !f.type.startsWith("image/"));
        if (imageFiles.length > 0) addImageFiles(imageFiles);
        if (dataFiles.length > 0) {
            const dt = new DataTransfer();
            dataFiles.forEach(f => dt.items.add(f));
            if (fileInputRef.current) { fileInputRef.current.files = dt.files; handleFileSelect({ target: fileInputRef.current }); }
        }
    }

    async function switchMode(newMode) {
        setAgentMode(newMode);
        if (active && active.id) {
            try { await authFetch(API_URL + "/api/mode/switch", { method: "POST", headers: authHeaders(), body: JSON.stringify({ conversation_id: active.id, mode: newMode }) }); } catch (e) { console.warn("Mode switch sync failed:", e); }
        }
        if (active) {
            const label = newMode === "userstory" ? "User Story Writer" : "Assistente Geral";
            setConversations(prev => {
                const u = [...prev];
                u[activeIdx] = {
                    ...u[activeIdx],
                    mode: newMode,
                    updatedAt: new Date().toISOString(),
                    messages: [...u[activeIdx].messages, {
                        role: "assistant",
                        content: `Modo alterado para **${label}**. ${newMode === "userstory" ? "Estou pronto para gerar user stories." : "Modo geral ativo."}`,
                        tools: [],
                    }],
                };
                return u;
            });
        }
    }

    async function send(promptOverride = null) {
        const nextPrompt = typeof promptOverride === "string" ? promptOverride : input;
        if (!nextPrompt.trim() || loading || uploadBusy || !active) return;
        if (activePendingUploadJobs.length > 0 && !active.id) {
            toast.warning("Ainda existem anexos a processar. Aguarda conclusão antes de enviar a pergunta.");
            return;
        }
        if (active.id) {
            const pendingUploads = await getPendingUploads(active.id);
            if (pendingUploads > 0) {
                toast.warning(`Ainda existem ${pendingUploads} upload(s) a processar. Aguarda conclusão antes de enviar a pergunta.`);
                return;
            }
        }
        const q = nextPrompt.trim();
        const currentImages = typeof promptOverride === "string" ? [] : [...imagePreviews];
        const fastEscalatedToThinking = modelTier === "fast" && shouldEscalateFastPrompt(
            q,
            Array.isArray(activeUploadedFiles) ? activeUploadedFiles.length : 0,
            currentImages.length
        );
        const requestTier = fastEscalatedToThinking ? "standard" : modelTier;
        if (fastEscalatedToThinking) {
            setTierRoutingNotice("Pedido analítico detetado: enviado automaticamente em Thinking para melhor qualidade.");
        } else {
            setTierRoutingNotice("");
        }
        if (typeof promptOverride !== "string") {
            setInput(""); setImagePreviews([]);
            if (inputRef.current) inputRef.current.style.height = "auto";
        }

        // Add user message
        setConversations(prev => {
            const u = [...prev];
            u[activeIdx] = {
                ...u[activeIdx],
                messages: [...u[activeIdx].messages, {
                    role: "user", content: q,
                    images: currentImages.length > 0 ? currentImages.map(img => ({ url: img.dataUrl, name: img.filename })) : null,
                }],
                title: u[activeIdx].messages.length === 0 && !u[activeIdx].titleManuallyEdited
                    ? q.slice(0, 42) + (q.length > 42 ? "..." : "")
                    : u[activeIdx].title,
                updatedAt: new Date().toISOString(),
            };
            return u;
        });

        setLoading(true);
        setStreamingText("");
        setStreamingRenderedBlocks([]);
        setStreamingActiveBlock("");
        setStreamingStatus("");
        setStreamingTrace(EMPTY_STREAMING_TRACE);

        try {
            const imagesPayload = currentImages.slice(0, maxImagesPerMessage).map(img => ({
                base64: img.base64,
                content_type: img.contentType,
                filename: img.filename,
            }));
            const firstImage = imagesPayload.length > 0 ? imagesPayload[0] : null;
            const reqBody = {
                question: q,
                conversation_id: active.id || null,
                image_base64: firstImage ? firstImage.base64 : null,
                image_content_type: firstImage ? firstImage.content_type : null,
                images: imagesPayload.length > 0 ? imagesPayload : null,
                mode: agentMode,
                model_tier: requestTier,
            };

            // Try SSE streaming first
            let useStreaming = true;
            let streamCompleted = false;
            let streamedText = "";
            let streamErrorMessage = "";

            if (useStreaming) {
                try {
                    const res = await authFetch(API_URL + "/chat/agent/stream", {
                        method: "POST", headers: authHeaders(), body: JSON.stringify(reqBody),
                    });

                    if (!res.ok || !res.body) {
                        // Fallback to non-streaming
                        useStreaming = false;
                    } else {
                        const reader = res.body.getReader();
                        const decoder = new TextDecoder();
                        let buffer = "";
                        let fullText = "";
                        let convId = active.id;
                        let toolsUsed = [];
                        let toolDetails = [];
                        let modelUsed = "";
                        let backendRequestId = "";
                        let backendRequestStatus = "ok";
                        let backendRequestErrorKind = "";
                        let totalTime = 0;
                        let tokensUsed = null;
                        let streamHasExportable = false;
                        let streamExportIndex = null;
                        let committedUntil = 0;

                        while (true) {
                            const { done, value } = await reader.read();
                            if (done) break;

                            buffer += decoder.decode(value, { stream: true });
                            const lines = buffer.split("\n");
                            buffer = lines.pop() || "";

                            for (const line of lines) {
                                if (!line.startsWith("data: ")) continue;
                                try {
                                    const evt = JSON.parse(line.slice(6));
                                    switch (evt.type) {
                                        case "init":
                                            convId = evt.conversation_id || convId;
                                            backendRequestId = evt.request_id || backendRequestId;
                                            break;
                                        case "thinking":
                                            setStreamingStatus(evt.text || evt.tool || "A pensar...");
                                            setStreamingTrace(prev => applyStreamingTraceEvent(prev, "thinking", evt));
                                            break;
                                        case "tool_start":
                                            setStreamingStatus(`A executar ${formatStreamingToolLabel(evt.tool)}...`);
                                            if (evt.tool) toolsUsed.push(evt.tool);
                                            setStreamingTrace(prev => applyStreamingTraceEvent(prev, "tool_start", evt));
                                            break;
                                        case "tool_result":
                                            setStreamingStatus(`${formatStreamingToolLabel(evt.tool)} concluido`);
                                            setStreamingTrace(prev => applyStreamingTraceEvent(prev, "tool_result", evt));
                                            break;
                                        case "token":
                                            fullText += (evt.text || "");
                                            streamedText = fullText;
                                            setStreamingText(fullText);
                                            setStreamingTrace(prev => applyStreamingTraceEvent(prev, "token", evt));
                                            // Render all committed text as single unit to preserve
                                            // cross-block markdown (tables, headings adjacent to hr, etc.)
                                            const lastBlockBoundary = fullText.lastIndexOf("\n\n");
                                            if (lastBlockBoundary >= committedUntil) {
                                                committedUntil = lastBlockBoundary + 2;
                                                const committedText = fullText.slice(0, committedUntil);
                                                setStreamingRenderedBlocks([renderMarkdown(committedText)]);
                                            }
                                            setStreamingActiveBlock(fullText.slice(committedUntil));
                                            setStreamingStatus("");
                                            break;
                                        case "done":
                                            backendRequestId = evt.request_id || backendRequestId;
                                            backendRequestStatus = evt.request_status || backendRequestStatus;
                                            backendRequestErrorKind = evt.request_error_kind || backendRequestErrorKind;
                                            modelUsed = evt.model_used || "";
                                            var isFallbackFromBackend = !!evt.is_fallback;
                                            var suggestLearnStyle = !!evt.suggest_learn_style;
                                            totalTime = evt.total_time_ms || 0;
                                            tokensUsed = evt.tokens_used || null;
                                            streamHasExportable = !!evt.has_exportable_data;
                                            streamExportIndex = (evt.export_index !== undefined) ? evt.export_index : null;
                                            if (Array.isArray(evt.tools_used) && evt.tools_used.length > 0) toolsUsed = evt.tools_used;
                                            if (Array.isArray(evt.tool_details) && evt.tool_details.length > 0) toolDetails = evt.tool_details;
                                            setStreamingTrace(prev => applyStreamingTraceEvent(prev, "done", evt));
                                            streamCompleted = true;
                                            break;
                                        case "error":
                                            setStreamingTrace(prev => applyStreamingTraceEvent(prev, "error", evt));
                                            streamErrorMessage = evt.text || evt.message || "Erro de streaming";
                                            throw new Error(streamErrorMessage);
                                    }
                                } catch (parseErr) {
                                    if (!(parseErr instanceof SyntaxError)) throw parseErr;
                                }
                            }
                        }

                        // Add final message
                        if (fullText || streamCompleted) {
                            const toolResults = buildFrontendToolResults(toolDetails, "stream");
                            // Detect clarification pattern in response
                            const clarParsed = parseClarificationOptions(fullText);
                            const clarificationOptions = clarParsed ? clarParsed.options : undefined;

                            setConversations(prev => {
                                const u = [...prev];
                                const finalContent = fullText || streamErrorMessage || "A resposta terminou sem conteúdo.";
                                u[activeIdx] = {
                                    ...u[activeIdx],
                                    id: convId,
                                    updatedAt: new Date().toISOString(),
                                    messages: [...u[activeIdx].messages, {
                                        role: "assistant", content: finalContent,
                                        request_id: backendRequestId || "",
                                        request_status: backendRequestStatus || "ok",
                                        request_error_kind: backendRequestErrorKind || "",
                                        tools_used: toolsUsed.length > 0 ? [...new Set(toolsUsed)] : undefined,
                                        tool_details: toolDetails.length > 0 ? toolDetails : undefined,
                                        tool_results: toolResults.length > 0 ? toolResults : undefined,
                                        has_exportable: streamHasExportable,
                                        export_index: streamExportIndex,
                                        model_tier: requestTier,
                                        model_used: modelUsed, is_fallback: isFallbackFromBackend,
                                        total_time_ms: totalTime, tokens_used: tokensUsed,
                                        clarification_options: clarificationOptions,
                                        suggest_learn_style: suggestLearnStyle || false,
                                    }],
                                };
                                scheduleSave(u[activeIdx]);
                                return u;
                            });
                            // TTS for clarification when in speech mode
                            if (clarificationOptions && (speechSubmitMode === 'auto' || speechListening)) {
                                // Speak the question part (everything before the numbered options)
                                const lines = (fullText || '').trim().split('\n').filter(l => l.trim());
                                const questionLines = lines.filter(l => !/^\d+\.\s+\S/.test(l.trim()));
                                if (questionLines.length > 0) {
                                    speakText(questionLines.join(' '));
                                }
                            }
                            streamCompleted = true;
                        }
                    }
                } catch (streamErr) {
                    console.warn("Stream error, falling back:", streamErr);
                    streamErrorMessage = streamErr.message || streamErrorMessage || "Erro de streaming";
                    setStreamingTrace(prev => applyStreamingTraceEvent(prev, "error", { message: streamErrorMessage }));
                    if (streamedText) {
                        streamedText += `\n\nErro real detetado durante o streaming: ${streamErrorMessage}`;
                        setStreamingText(streamedText);
                        setStreamingActiveBlock(streamedText);
                        streamCompleted = true;
                    } else {
                        useStreaming = false;
                    }
                }
            }

            // Fallback: non-streaming
            if (!useStreaming && !streamCompleted) {
                setStreamingStatus("A processar...");
                setStreamingTrace(prev => applyStreamingTraceEvent(prev, "thinking", { text: "A processar..." }));
                let res, lastErr;
                for (let attempt = 0; attempt < 3; attempt++) {
                    try {
                        res = await authFetch(API_URL + "/chat/agent", { method: "POST", headers: authHeaders(), body: JSON.stringify(reqBody) });
                        if (res.status === 429 || res.status === 502) {
                            setStreamingStatus(`Serviço ocupado, tentativa ${attempt + 2}/3...`);
                            await new Promise(r => setTimeout(r, 5000 * (attempt + 1)));
                            continue;
                        }
                        break;
                    } catch (e) { lastErr = e; if (attempt < 2) await new Promise(r => setTimeout(r, 3000)); }
                }
                if (!res) throw lastErr || new Error("Falha após 3 tentativas");
                if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(err.detail || "Erro " + res.status); }
                const data = await res.json();
                setConversations(prev => {
                    const u = [...prev];
                    const toolResults = buildFrontendToolResults(data.tool_details, "sync");
                    const syncClarParsed = parseClarificationOptions(data.answer);
                    u[activeIdx] = {
                        ...u[activeIdx],
                        id: data.conversation_id,
                        updatedAt: new Date().toISOString(),
                        messages: [...u[activeIdx].messages, {
                            role: "assistant", content: data.answer,
                            request_id: data.request_id || "",
                            request_status: data.request_status || "ok",
                            request_error_kind: data.request_error_kind || "",
                            tools_used: data.tools_used, tool_details: data.tool_details,
                            tool_results: toolResults.length > 0 ? toolResults : undefined,
                            model_tier: requestTier,
                            model_used: data.model_used, is_fallback: !!data.is_fallback,
                            tokens_used: data.tokens_used, total_time_ms: data.total_time_ms,
                            has_exportable: data.has_exportable_data || false,
                            export_index: data.export_index,
                            clarification_options: syncClarParsed ? syncClarParsed.options : undefined,
                        }],
                    };
                    scheduleSave(u[activeIdx]);
                    return u;
                });
                setStreamingTrace(prev => applyStreamingTraceEvent(prev, "done", {
                    request_status: data.request_status || "ok",
                    request_error_kind: data.request_error_kind || "",
                }));
            }
        } catch (err) {
            setStreamingTrace(prev => applyStreamingTraceEvent(prev, "error", { message: err.message || "Erro na resposta" }));
            setConversations(prev => {
                const u = [...prev];
                u[activeIdx] = {
                    ...u[activeIdx],
                    updatedAt: new Date().toISOString(),
                    messages: [...u[activeIdx].messages, { role: "assistant", content: "Erro real detetado: " + (err.message || "erro sem detalhe") }],
                };
                return u;
            });
        } finally {
            setLoading(false);
            setStreamingText("");
            setStreamingRenderedBlocks([]);
            setStreamingActiveBlock("");
            setStreamingStatus("");
            setTimeout(() => inputRef.current && inputRef.current.focus(), 100);
        }
    }

    function getAllChatMessages() {
        if (!active || !Array.isArray(active.messages)) return [];
        return active.messages
            .filter(m => m && (m.role === "user" || m.role === "assistant"))
            .map(m => ({
                role: m.role,
                content: (typeof m.content === "string" || Array.isArray(m.content)) ? m.content : (m.text || ""),
                timestamp: m.timestamp || m.created_at || "",
            }));
    }

    async function exportChat(format = "html") {
        if (!active) return;
        try {
            const messages = getAllChatMessages();
            if (!messages.length) {
                toast.info("Sem mensagens para exportar.");
                return;
            }
            const res = await authFetch(API_URL + "/api/export-chat", {
                method: "POST",
                headers: authHeaders(),
                body: JSON.stringify({
                    messages,
                    format,
                    title: active.title || "Chat Export",
                }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || "Erro export chat");
            if (data.url) {
                const target = String(data.url || "");
                const finalUrl = target.startsWith("http") ? target : (API_URL + target);
                try {
                    const parsed = new URL(finalUrl, window.location.origin);
                    const apiOrigin = new URL(API_URL || window.location.origin, window.location.origin).origin;
                    const allowedOrigins = new Set([window.location.origin, apiOrigin]);
                    if ((parsed.protocol === "http:" || parsed.protocol === "https:") && allowedOrigins.has(parsed.origin)) {
                        window.open(parsed.href, "_blank", "noopener,noreferrer");
                    } else {
                        throw new Error("Origem de export não permitida");
                    }
                } catch (err) {
                    throw new Error("URL de export inválida");
                }
            }
            if (data.format_served && data.format_requested && data.format_served !== data.format_requested) {
                toast.info("Exportado como " + data.format_served.toUpperCase() + " em vez de " + data.format_requested.toUpperCase() + (data.fallback_reason ? " (" + data.fallback_reason + ")" : ""));
            } else if (data.note) {
                toast.info(data.note);
            }
        } catch (e) {
            toast.error("Erro ao exportar conversa: " + e.message);
        } finally {
            setShowExportDropdown(false);
        }
    }

    function _messageTextContent(msg) {
        if (!msg) return "";
        if (typeof msg.content === "string") return msg.content;
        if (Array.isArray(msg.content)) {
            return msg.content
                .filter(p => p && typeof p === "object" && p.type === "text")
                .map(p => String(p.text || ""))
                .join("\n")
                .trim();
        }
        return "";
    }

    function _promptForAssistantMessage(messages, assistantIndex) {
        if (!Array.isArray(messages) || assistantIndex < 0) return "";
        for (let i = assistantIndex - 1; i >= 0; i--) {
            const msg = messages[i];
            if (msg && msg.role === "user") return _messageTextContent(msg);
        }
        return "";
    }

    async function exportData(format) {
        if (!active) return;
        try {
            // Find the last tool result data in the conversation messages
            let selectedToolResult = null;
            let toolData = null;
            let promptSummary = "";
            for (let i = active.messages.length - 1; i >= 0; i--) {
                const msg = active.messages[i];
                selectedToolResult = getPreferredToolResult(msg.tool_results, msg.export_index);
                toolData = getPreferredExportableData(msg.tool_results, msg.export_index);
                if (selectedToolResult || toolData) {
                    promptSummary = _promptForAssistantMessage(active.messages, i);
                    break;
                }
            }
            if (!selectedToolResult && !toolData) { toast.info("Sem dados exportáveis nesta conversa. Executa uma query primeiro."); return; }

            const res = await authFetch(API_URL + "/api/export", {
                method: "POST", headers: authHeaders(),
                body: JSON.stringify({
                    conversation_id: active.id || "",
                    format,
                    title: active.title || "Export",
                    data: toolData || undefined,
                    result_blob_ref: selectedToolResult?.result_blob_ref || undefined,
                    summary: promptSummary || undefined,
                }),
            });
            if (res.status === 202) {
                const queued = await res.json();
                const statusEndpoint = queued.status_endpoint || (`/api/export/status/${queued.job_id}`);
                const result = await waitForExportJob(statusEndpoint);
                if (!result || !result.endpoint) throw new Error("Export concluído sem ficheiro disponível");
                await downloadGeneratedFile(result);
                return;
            }
            if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || "Erro export"); }
            const blob = await res.blob();
            const ext = format === "xlsx" ? "xlsx" : format === "pdf" ? "pdf" : format === "svg" ? "svg" : format === "html" ? "html" : format === "zip" ? "zip" : "csv";
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = `${(active.title || "export").replace(/[^a-zA-Z0-9]/g, "_").slice(0, 30)}.${ext}`;
            a.click();
            URL.revokeObjectURL(a.href);
        } catch (e) { toast.error("Erro ao exportar: " + e.message); }
    }

    async function exportMessageData(format, toolResults, exportIndex = null, messageIndex = null, withCompanion = true) {
        if (!active || !toolResults || toolResults.length === 0) return;
        try {
            const selectedToolResult = getPreferredToolResult(toolResults, exportIndex);
            const toolData = getPreferredExportableData(toolResults, exportIndex);
            if (!selectedToolResult && !toolData) { toast.info("Sem dados exportáveis nesta mensagem."); return; }
            const promptSummary = Number.isInteger(messageIndex) ? _promptForAssistantMessage(active.messages, messageIndex) : "";

            const res = await authFetch(API_URL + "/api/export", {
                method: "POST", headers: authHeaders(),
                body: JSON.stringify({
                    conversation_id: active.id || "",
                    format,
                    title: active.title || "Export",
                    data: toolData || undefined,
                    result_blob_ref: selectedToolResult?.result_blob_ref || undefined,
                    summary: promptSummary || undefined,
                }),
            });
            if (res.status === 202) {
                const queued = await res.json();
                const statusEndpoint = queued.status_endpoint || (`/api/export/status/${queued.job_id}`);
                const result = await waitForExportJob(statusEndpoint);
                if (!result || !result.endpoint) throw new Error("Export concluído sem ficheiro disponível");
                await downloadGeneratedFile(result);
            } else {
                if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || "Erro export"); }
                const blob = await res.blob();
                const ext = format === "xlsx" ? "xlsx" : format === "pdf" ? "pdf" : format === "svg" ? "svg" : format === "html" ? "html" : format === "zip" ? "zip" : "csv";
                const a = document.createElement("a");
                a.href = URL.createObjectURL(blob);
                a.download = `${(active.title || "export").replace(/[^a-zA-Z0-9]/g, "_").slice(0, 30)}.${ext}`;
                a.click();
                URL.revokeObjectURL(a.href);
            }
            const fullCsv = getPreferredAutoCsvDownload(toolResults, exportIndex);
            if (withCompanion && fullCsv && format !== "csv") {
                await downloadGeneratedFile(fullCsv);
            }
        } catch (e) { toast.error("Erro ao exportar: " + e.message); }
    }

    async function exportMessageBundle(toolResults, exportIndex = null, messageIndex = null) {
        await exportMessageData("zip", toolResults, exportIndex, messageIndex, false);
    }

    async function waitForExportJob(statusEndpoint, timeoutMs = 180000) {
        const started = Date.now();
        const endpoint = String(statusEndpoint || "").startsWith("http")
            ? String(statusEndpoint)
            : (API_URL + String(statusEndpoint || ""));
        while (Date.now() - started < timeoutMs) {
            const res = await authFetch(endpoint, { method: "GET", headers: authHeaders() });
            if (!res.ok) {
                const e = await res.json().catch(() => ({}));
                throw new Error(e.detail || "Falha ao obter estado do export");
            }
            const job = await res.json();
            const status = String(job.status || "").toLowerCase();
            if (status === "completed") return job.result || {};
            if (status === "failed") throw new Error(job.error || "Export falhou");
            await new Promise(resolve => setTimeout(resolve, 1800));
        }
        throw new Error("Timeout no processamento do export");
    }

    async function downloadGeneratedFile(fileMeta) {
        if (!fileMeta) return;
        try {
            if (!fileMeta.endpoint) throw new Error("Endpoint de download em falta");
            const url = String(fileMeta.endpoint).startsWith("http") ? fileMeta.endpoint : (API_URL + fileMeta.endpoint);
            const res = await authFetch(url, { method: "GET", headers: authHeaders() });
            if (!res.ok) {
                const e = await res.json().catch(() => ({}));
                throw new Error(e.detail || "Erro download");
            }
            const blob = await res.blob();
            const fallbackName = `download_${Date.now()}.${String(fileMeta.format || "bin").toLowerCase()}`;
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = fileMeta.filename || fallbackName;
            a.click();
            URL.revokeObjectURL(a.href);
        } catch (e) {
            toast.error("Erro ao descarregar: " + e.message);
        }
    }

    async function submitFeedback(convId, msgIdx, rating, note) {
        try {
            await authFetch(API_URL + "/feedback", { method: "POST", headers: authHeaders(), body: JSON.stringify({ conversation_id: convId, message_index: msgIdx, rating, note: note || "" }) });
        } catch (e) { console.warn("Submit feedback failed:", e); }
    }

    async function speakText(text) {
        if (!text) return;
        try {
            if (lastTtsAudioRef.current) {
                lastTtsAudioRef.current.pause();
                lastTtsAudioRef.current = null;
            }
            const res = await authFetch(API_URL + '/api/speech/synthesize', {
                method: 'POST',
                headers: authHeaders(),
                body: JSON.stringify({ text, voice: 'pt-PT-RaquelNeural', language: 'pt-PT' }),
            });
            if (!res.ok) return;
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            lastTtsAudioRef.current = audio;
            audio.onended = () => { URL.revokeObjectURL(url); lastTtsAudioRef.current = null; };
            await audio.play();
        } catch (e) { console.warn('TTS failed:', e); }
    }

    function handleKeyDown(e) {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    }

    const suggestionPools = {
        general: {
            rag: [
                "Quantas user stories existem na área Payments?",
                "Quais bugs estão ativos no BackOffice?",
                "Mostra os work items criados esta semana",
                "Lista as user stories em estado Active",
                "Quais áreas têm mais bugs abertos?",
            ],
            knowledge: [
                "Como fazer uma transferência SEPA no portal?",
                "O que é o processo de KYC?",
                "Explica o fluxo de abertura de conta digital",
                "Quais são as regras de compliance para SEPA?",
            ],
            analytics: [
                "Quem criou mais user stories este mês?",
                "Mostra KPIs da equipa BackOffice no último sprint",
                "Qual a velocity média da área Payments?",
                "Gera um gráfico de bugs por prioridade",
            ],
            files: [
                "Analisa o ficheiro Excel que vou anexar",
                "Compara os dados dos 2 ficheiros CSV",
                "Resume o conteúdo do PDF anexado",
            ],
        },
        userstory: {
            creation: [
                "Gera 3 user stories sobre pagamento de serviços",
                "Cria uma US para exportar PDF nas consultas",
                "Gera user stories para abertura de conta online",
                "Cria user stories para o módulo de notificações push",
                "Gera uma US para autenticação biométrica",
            ],
            modification: [
                "Quero adicionar um campo de IBAN no formulário",
                "Preciso de uma US para alterar o layout do dashboard",
                "Cria uma US para adicionar filtros na pesquisa",
            ],
            integration: [
                "Gera US para integração com sistema de pagamentos",
                "Cria user stories para a API de consulta de saldos",
                "US para integrar notificações por email",
            ],
        },
    };
    // S4-06: Build history-based contextual suggestions from recent conversations
    function _buildHistorySuggestions(convList) {
        const history = [];
        const recentUserMsgs = convList
            .flatMap(c => (c.messages || []).map(m => ({ ...m, convTitle: c.title || '' })))
            .filter(m => m.role === "user")
            .slice(-30);
        if (recentUserMsgs.length === 0) return history;

        // Detect frequently used tools
        const toolCounts = {};
        for (const c of convList) {
            for (const m of (c.messages || [])) {
                if (m.role === 'assistant' && Array.isArray(m.tools_used)) {
                    for (const t of m.tools_used) {
                        toolCounts[t] = (toolCounts[t] || 0) + 1;
                    }
                }
            }
        }

        // Detect topics from recent user messages
        const recentTexts = recentUserMsgs.slice(-10).map(m => (m.content || m.text || '').toLowerCase());
        const topicKeywords = {
            csv: /\b(csv|excel|xlsx|dados|tabela|ficheiro)\b/,
            stories: /\b(user stor|us |gera.*stor|cria.*stor)\b/,
            bugs: /\b(bug|defeito|erro|issue)\b/,
            kpi: /\b(kpi|metric|velocity|sprint|ranking)\b/,
            email: /\b(email|correio|classificar|triagem)\b/,
        };
        const detectedTopics = new Set();
        for (const text of recentTexts) {
            for (const [topic, rx] of Object.entries(topicKeywords)) {
                if (rx.test(text)) detectedTopics.add(topic);
            }
        }

        // S4-06 fix: generate concrete, actionable suggestions with real context
        // Extract last mentioned filenames from messages
        const lastFile = recentTexts.reduce((found, t) => {
            const m = t.match(/[\w.-]+\.(csv|xlsx|xls|pdf)/i);
            return m ? m[0] : found;
        }, '');
        // Extract last mentioned project/area
        const lastProject = recentTexts.reduce((found, t) => {
            const m = t.match(/\b(BackOffice|ACME|APP|Portal|CRM|ERP|SPIN|SEPA|SDD)\b/i);
            return m ? m[1].toUpperCase() : found;
        }, '');

        if (detectedTopics.has('csv') && lastFile) {
            history.push(`Analisa "${lastFile}" — mostra resumo e gráfico`);
        } else if (detectedTopics.has('csv')) {
            history.push("Carrega um CSV e pede análise com gráfico");
        }
        if (detectedTopics.has('stories') && (toolCounts['generate_user_stories'] || 0) >= 2) {
            const ctx = lastProject ? ` para o ${lastProject}` : '';
            history.push(`Gera mais user stories${ctx}`);
        }
        if (detectedTopics.has('bugs') && lastProject) {
            history.push(`Mostra bugs activos no ${lastProject}`);
        } else if (detectedTopics.has('bugs')) {
            history.push("Lista os bugs activos por prioridade");
        }
        if (detectedTopics.has('kpi')) {
            history.push("Mostra KPIs e velocity do sprint actual");
        }
        if ((toolCounts['run_code'] || 0) >= 3) {
            history.push("Executa análise Python sobre os dados carregados");
        }

        return history.slice(0, 2); // max 2 history-based suggestions
    }

    function getDynamicSuggestions(mode, convList, filesList, seed) {
        const pool = suggestionPools[mode] || suggestionPools.general;
        const allSuggestions = Object.values(pool).flat();

        // Simple seeded pseudo-random to guarantee different picks on refresh
        let _seed = ((seed || 0) * 2654435761) >>> 0; // Knuth multiplicative hash
        function seededRandom() {
            _seed = (_seed * 1664525 + 1013904223) >>> 0;
            return (_seed >>> 0) / 4294967296;
        }

        const recentMessages = convList
            .flatMap(c => c.messages || [])
            .filter(m => m.role === "user")
            .slice(-20)
            .map(m => (m.content || m.text || "").toLowerCase());

        const usedCategories = new Set();
        for (const msg of recentMessages) {
            for (const [cat, items] of Object.entries(pool)) {
                if (items.some(s => msg.includes(s.slice(0, 20).toLowerCase()))) {
                    usedCategories.add(cat);
                }
            }
        }

        const hasFiles = filesList && filesList.length > 0;
        const selected = [];
        const categories = Object.keys(pool);

        // S4-06: Inject history-based suggestions first
        const historySuggestions = _buildHistorySuggestions(convList);
        for (const hs of historySuggestions) {
            if (selected.length < 4) selected.push(hs);
        }

        if (hasFiles && pool.files) {
            const fileSugg = pool.files[Math.floor(seededRandom() * pool.files.length)];
            if (!selected.includes(fileSugg)) selected.push(fileSugg);
        }

        const shuffledCats = [...categories].sort(() => seededRandom() - 0.5);
        for (const cat of shuffledCats) {
            if (selected.length >= 4) break;
            if (hasFiles && cat === "files") continue;
            const catItems = pool[cat].filter(s => !selected.includes(s));
            if (catItems.length > 0) {
                selected.push(catItems[Math.floor(seededRandom() * catItems.length)]);
            }
        }

        while (selected.length < 4 && allSuggestions.length > 0) {
            const remaining = allSuggestions.filter(s => !selected.includes(s));
            if (remaining.length === 0) break;
            selected.push(remaining[Math.floor(seededRandom() * remaining.length)]);
        }

        return selected.slice(0, 4);
    }
    const tierLabels = { fast: "Fast", standard: "Thinking", pro: "Pro" };
    const tierIcons = { fast: FastIcon, standard: ThinkingIcon, pro: ProIcon };
    const modeLabels = { general: "Geral", userstory: "User Stories" };
    const modeIcons = { general: ConversationIcon, userstory: StoryIcon };
    const ActiveTierIcon = tierIcons[modelTier] || ThinkingIcon;
    const ActiveModeIcon = modeIcons[agentMode] || ConversationIcon;
    const selectorLabel = `${tierLabels[modelTier]} · ${modeLabels[agentMode]}`;
    // Stable suggestions: recalculate on meaningful context changes, not on every render
    const fileCount = Array.isArray(activeUploadedFiles) ? activeUploadedFiles.length : 0;
    if (
        suggestionsRef.current.seed !== suggestionSeed ||
        suggestionsRef.current.mode !== agentMode ||
        suggestionsRef.current.convIdx !== activeIdx ||
        suggestionsRef.current.files !== fileCount
    ) {
        suggestionsRef.current = {
            seed: suggestionSeed,
            mode: agentMode,
            convIdx: activeIdx,
            files: fileCount,
            result: getDynamicSuggestions(agentMode, conversations, activeUploadedFiles, suggestionSeed),
        };
    }
    const suggestions = suggestionsRef.current.result;
    const showStreamingPanel = loading && Boolean(streamingStatus || streamingTrace.label || streamingTrace.events.length > 0);
    const showTypingIndicator = loading && !showStreamingPanel && !streamingText;
    const showFastAnalyticHint = modelTier === "fast" && shouldEscalateFastPrompt(
        input,
        Array.isArray(activeUploadedFiles) ? activeUploadedFiles.length : 0,
        imagePreviews.length
    );
    const showEmptyState = (!active || activeMessages.length === 0) && !loading && agentMode !== "userstory";
    // S5-02: Suggest Pro for complex prompts when on Standard tier
    const showProSuggestion = modelTier === "standard" && shouldSuggestProTier(
        input,
        Array.isArray(activeUploadedFiles) ? activeUploadedFiles.length : 0
    );

    // ─── Render ──────────────────────────────────────────────────────────

    return (
        <div className="app-shell">
            {/* Mobile sidebar backdrop */}
            {sidebarOpen && (
                <div className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} />
            )}
            <div
                className="app-sidebar"
                style={{ width: sidebarOpen ? 318 : 0, minWidth: sidebarOpen ? 318 : 0 }}
            >
                <div className="app-sidebar-header">
                    <button
                        type="button"
                        className="sidebar-close-btn"
                        onClick={() => setSidebarOpen(false)}
                        title="Fechar menu"
                    >
                        &times;
                    </button>
                    <div className="app-brand-lockup">
                        <img
                            src={BRAND_SYMBOL_DATA_URI}
                            alt="AI Product Assistant"
                            style={{ width: 52, height: 52, flexShrink: 0 }}
                        />
                        <div>
                            <div className="app-brand-heading">AI Product Assistant</div>
                            <div className="app-brand-subtitle">Portfolio edition · {APP_VERSION}</div>
                        </div>
                    </div>

                    <button type="button" className="app-primary-btn" style={{ width: "100%" }} onClick={startNew}>
                        <PlusIcon size={16} />
                        Nova conversa
                    </button>
                </div>

                <div style={{ flex: 1, overflowY: "auto", padding: "12px 12px 10px" }}>
                    {conversations.map((conv, i) => (
                        <ConversationListItem
                            key={getConversationKey(conv, i)}
                            title={conv.title || "Nova conversa"}
                            meta={getConversationMetaLabel(conv)}
                            active={i === activeIdx}
                            canDelete={conversations.length > 1}
                            onSelect={() => {
                                setActiveIdx(i);
                                setUploadedFiles(Array.isArray(conv.uploadedFiles) ? conv.uploadedFiles : []);
                                if (conv.savedOnServer && (!Array.isArray(conv.messages) || conv.messages.length === 0) && conv.id && userId) {
                                    loadChatMessages(userId, conv.id, i);
                                }
                                if (window.innerWidth <= 900) setSidebarOpen(false);
                                inputRef.current && inputRef.current.focus();
                            }}
                            onRename={() => { if (window.innerWidth <= 900) setSidebarOpen(false); openRenameDialog(i); }}
                            onDelete={() => { if (window.innerWidth <= 900) setSidebarOpen(false); openDeleteDialog(i); }}
                        />
                    ))}
                </div>

                <div className="app-sidebar-footer">
                    <div className="app-sidebar-user">{userId}</div>
                    <button type="button" className="app-ghost-btn" onClick={handleLogout}>
                        Terminar sessão
                    </button>
                </div>
            </div>

            <div
                className="app-main"
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={(e) => { if (e.currentTarget === e.target || !e.currentTarget.contains(e.relatedTarget)) setDragOver(false); }}
                onDrop={handleDrop}
            >
                {dragOver ? (
                    <div className="drag-drop-overlay">
                        <div className="drag-drop-card">
                            <AttachmentIcon size={36} style={{ color: "var(--brand-accent)", marginBottom: 10 }} />
                            <div className="drag-drop-title">Larga aqui para anexar</div>
                            <div className="drag-drop-subtitle">Imagens, Excel, CSV, PDF</div>
                        </div>
                    </div>
                ) : null}

                <div className="app-header">
                    <div className="app-header-title">
                        <button
                            type="button"
                            className="header-icon-btn"
                            onClick={() => setSidebarOpen(!sidebarOpen)}
                            title={sidebarOpen ? "Ocultar conversas" : "Mostrar conversas"}
                        >
                            {sidebarOpen ? <ChevronLeftIcon size={18} /> : <MenuIcon size={18} />}
                        </button>
                        <div className="app-header-copy">
                            <div className="app-title-row">
                                <div className="app-title-text">{active ? active.title : "AI Product Assistant"}</div>
                                {active ? (
                                    <button
                                        type="button"
                                        className="header-icon-btn"
                                        title="Renomear conversa"
                                        onClick={() => openRenameDialog(activeIdx)}
                                    >
                                        <EditIcon size={15} />
                                    </button>
                                ) : null}
                            </div>
                            <div className="app-subtitle-row">
                                <span className="app-subtle-pill">
                                    <ActiveModeIcon size={14} />
                                    {modeLabels[agentMode]}
                                </span>
                                <span>{activeMessages.length} msgs</span>
                                {active && active.updatedAt ? <span>{formatRelativeTimestamp(active.updatedAt)}</span> : null}
                            </div>
                        </div>
                    </div>

                    <div className="app-header-actions">
                        <button type="button" className="app-primary-btn header-new-chat-btn" onClick={startNew} title="Nova conversa">
                            <PlusIcon size={16} />
                            <span className="header-action-text">Nova conversa</span>
                        </button>

                        <div ref={selectorRef} style={{ position: "relative" }}>
                            <button
                                type="button"
                                className="app-secondary-btn selector-trigger header-mode-btn"
                                onClick={() => setSelectorOpen(!selectorOpen)}
                                title={selectorLabel}
                            >
                                <span className="selector-trigger-tier-icon"><ActiveTierIcon size={15} /></span>
                                <span className="selector-trigger-mode-icon"><ActiveModeIcon size={15} /></span>
                                <span className="selector-trigger-text">{selectorLabel}</span>
                                <ChevronDownIcon
                                    className="selector-trigger-chevron"
                                    size={14}
                                    style={{ transform: selectorOpen ? "rotate(180deg)" : "rotate(0deg)", transition: "transform 0.2s ease" }}
                                />
                            </button>
                            {selectorOpen ? (
                                <div
                                    style={{
                                        position: "absolute",
                                        right: 0,
                                        top: "calc(100% + 8px)",
                                        background: "white",
                                        borderRadius: 18,
                                        boxShadow: "0 18px 42px rgba(0,0,0,0.14)",
                                        padding: 14,
                                        minWidth: 240,
                                        zIndex: 1000,
                                        border: "1px solid rgba(16,18,23,0.08)",
                                        animation: "fadeUp 0.15s ease",
                                    }}
                                >
                                    <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text-soft)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6, padding: "0 4px" }}>
                                        Modelo
                                    </div>
                                    {["fast", "standard", "pro"].map((t) => {
                                        const TierIcon = tierIcons[t];
                                        return (
                                            <button
                                                key={t}
                                                type="button"
                                                onClick={() => { setTierRoutingNotice(""); setModelTier(t); }}
                                                style={{
                                                    display: "flex",
                                                    alignItems: "center",
                                                    gap: 8,
                                                    width: "100%",
                                                    padding: "10px 12px",
                                                    border: "none",
                                                    borderRadius: 12,
                                                    cursor: "pointer",
                                                    fontSize: 12,
                                                    fontWeight: modelTier === t ? 700 : 500,
                                                    background: modelTier === t ? "rgba(var(--brand-accent-rgb), 0.08)" : "transparent",
                                                    color: modelTier === t ? "var(--brand-accent)" : "var(--text-body)",
                                                }}
                                            >
                                                <TierIcon size={15} />
                                                <span>{tierLabels[t]}</span>
                                                {modelTier === t ? <span style={{ marginLeft: "auto", fontSize: 11 }}>✓</span> : null}
                                            </button>
                                        );
                                    })}
                                    <div style={{ borderTop: "1px solid rgba(16,18,23,0.06)", margin: "10px 0" }} />
                                    <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text-soft)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6, padding: "0 4px" }}>
                                        Modo
                                    </div>
                                    {["general", "userstory"].map((m) => {
                                        const ModeIcon = modeIcons[m];
                                        return (
                                            <button
                                                key={m}
                                                type="button"
                                                onClick={() => { switchMode(m); setSelectorOpen(false); }}
                                                style={{
                                                    display: "flex",
                                                    alignItems: "center",
                                                    gap: 8,
                                                    width: "100%",
                                                    padding: "10px 12px",
                                                    border: "none",
                                                    borderRadius: 12,
                                                    cursor: "pointer",
                                                    fontSize: 12,
                                                    fontWeight: agentMode === m ? 700 : 500,
                                                    background: agentMode === m ? "rgba(var(--brand-accent-rgb), 0.08)" : "transparent",
                                                    color: agentMode === m ? "var(--brand-accent)" : "var(--text-body)",
                                                }}
                                            >
                                                <ModeIcon size={15} />
                                                <span>{modeLabels[m]}</span>
                                                {agentMode === m ? <span style={{ marginLeft: "auto", fontSize: 11 }}>✓</span> : null}
                                            </button>
                                        );
                                    })}
                                </div>
                            ) : null}
                        </div>

                        <div style={{ position: "relative" }}>
                            <button
                                type="button"
                                className="app-secondary-btn header-export-btn"
                                onClick={() => setShowExportDropdown(!showExportDropdown)}
                                title="Exportar conversa"
                            >
                                <ExportIcon size={15} />
                                <span className="header-action-text">Exportar</span>
                            </button>
                            {showExportDropdown ? (
                                <div
                                    style={{
                                        position: "absolute",
                                        right: 0,
                                        top: "calc(100% + 8px)",
                                        background: "white",
                                        border: "1px solid rgba(16,18,23,0.08)",
                                        borderRadius: 14,
                                        boxShadow: "0 16px 32px rgba(0,0,0,0.12)",
                                        zIndex: 60,
                                        minWidth: 190,
                                        overflow: "hidden",
                                    }}
                                >
                                    <button type="button" className="user-menu-item" onClick={() => exportChat("html")}>Exportar como HTML</button>
                                    <button type="button" className="user-menu-item" onClick={() => exportChat("pdf")}>Exportar como PDF</button>
                                </div>
                            ) : null}
                        </div>

                        <button
                            type="button"
                            className="app-secondary-btn header-manual-btn"
                            onClick={() => setShowToolsManual(true)}
                            title="Manual de funcionalidades"
                        >
                            <BookIcon size={15} />
                            <span className="header-action-text">Manual</span>
                        </button>

                        <UserMenu user={authUser} onLogout={handleLogout} onShowFeedbackDashboard={authUser?.role === 'admin' ? () => setShowFeedbackDash(true) : undefined} />
                    </div>
                </div>

                <div className="app-pane">
                    <div className={`app-pane-inner${showEmptyState ? " is-empty-state" : ""}`}>
                        {agentMode === "userstory" ? (
                            <UserStoryWorkspace
                                conversation={active}
                                uploadedFiles={activeUploadedFiles}
                                onConversationUpdate={patchActiveConversation}
                                user={authUser}
                            />
                        ) : null}

                        {showEmptyState ? (
                            <div className="app-empty-shell">
                                <ChatEmptyState
                                    firstName={firstName}
                                    suggestions={suggestions}
                                    onSuggestionClick={(question) => {
                                        setInput(question);
                                        setTimeout(() => inputRef.current && inputRef.current.focus(), 50);
                                    }}
                                    onRefreshSuggestions={() => setSuggestionSeed((s) => s + 1)}
                                />
                            </div>
                        ) : null}

                        {active && activeMessages.map((msg, i) => (
                            <ErrorBoundary key={`message-boundary-${i}`} name="MessageBubble">
                                <MessageBubble
                                    message={msg}
                                    isLastAssistant={msg.role === "assistant" && i === activeMessages.length - 1 && !loading}
                                    conversationId={active.id}
                                    messageIndex={i}
                                    onFeedback={submitFeedback}
                                    onExport={exportMessageData}
                                    onExportBundle={exportMessageBundle}
                                    onFileDownload={downloadGeneratedFile}
                                    onQuickReply={(opt) => { if (!loading) send(opt); }}
                                    onLearnStyleAccept={() => { if (!loading) send("Sim, aprende o meu estilo de escrita de user stories com base nas que já gerei."); }}
                                    onLearnStyleDismiss={() => {}}
                                />
                            </ErrorBoundary>
                        ))}

                        {showStreamingPanel ? (
                            <StreamingActivityPanel trace={streamingTrace} statusText={streamingStatus} />
                        ) : null}

                        {loading && (streamingText || streamingRenderedBlocks.length > 0 || streamingActiveBlock) ? (
                            <div style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: 16, animation: "fadeUp 0.3s ease" }}>
                                <img src={BRAND_SYMBOL_DATA_URI} alt="AI Product Assistant" style={{ width: 32, height: 32, flexShrink: 0 }} />
                                <div
                                    className="msg-content"
                                    style={{
                                        background: "white",
                                        borderRadius: "4px 16px 16px 16px",
                                        padding: "14px 20px",
                                        boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
                                        fontSize: 14,
                                        lineHeight: 1.7,
                                        color: "#1a1a1a",
                                        maxWidth: "min(900px, 100%)",
                                    }}
                                >
                                    {streamingRenderedBlocks.map((blockHtml, bi) => (
                                        <div key={`stream-block-${bi}`} dangerouslySetInnerHTML={{ __html: blockHtml }} />
                                    ))}
                                    {streamingActiveBlock ? <div dangerouslySetInnerHTML={{ __html: renderMarkdown(streamingActiveBlock) }} /> : null}
                                </div>
                            </div>
                        ) : null}

                        {showTypingIndicator ? <TypingIndicator text={streamingStatus || streamingTrace.label} /> : null}
                        <div ref={chatEndRef} />
                    </div>
                </div>

                <ChatComposer
                    uploadingFiles={uploadBusy}
                    uploadProgressText={currentUploadProgressText}
                    pendingUploadJobs={activePendingUploadJobs}
                    onRetryUploadJob={handleRetryUploadJob}
                    activeUploadedFiles={activeUploadedFiles}
                    activeFileMode={!!(active && active.fileMode)}
                    maxFilesPerConversation={maxFilesPerConversation}
                    imagePreviews={imagePreviews}
                    maxImagesPerMessage={maxImagesPerMessage}
                    onRemoveImage={removeImage}
                    onClearImages={() => setImagePreviews([])}
                    modelTier={modelTier}
                    showFastAnalyticHint={showFastAnalyticHint}
                    showProSuggestion={showProSuggestion}
                    onSwitchToPro={() => { setModelTier("pro"); setTierRoutingNotice(""); }}
                    tierRoutingNotice={tierRoutingNotice}
                    fileInputRef={fileInputRef}
                    imageInputRef={imageInputRef}
                    loading={loading}
                    onFilePick={handleFileSelect}
                    onImagePick={handleImageUpload}
                    inputRef={inputRef}
                    input={input}
                    onInputChange={(e) => { setInput(e.target.value); if (speechNotice) clearSpeechNotice(); }}
                    onInputKeyDown={handleKeyDown}
                    onInputPaste={handlePaste}
                    inputPlaceholder={
                        activeUploadedFiles.length > 0 && active && active.fileMode
                            ? `Pergunta sobre os ${activeUploadedFiles.length} ficheiros anexados...`
                            : "Escreve a tua mensagem..."
                    }
                    onSend={send}
                    maxBatchTotalBytes={maxBatchTotalBytes}
                    speechSupported={speechSupported}
                    speechListening={speechListening}
                    speechStopping={speechStopping}
                    speechProcessing={speechProcessing}
                    speechInterimText={speechInterimText}
                    speechNotice={speechNotice}
                    speechSubmitMode={speechSubmitMode}
                    speechProvider={speechProvider}
                    onToggleSpeech={toggleSpeech}
                    onToggleSpeechSubmitMode={toggleSpeechSubmitMode}
                    clarificationOptions={(() => {
                        if (loading) return null;
                        const last = activeMessages[activeMessages.length - 1];
                        return last && last.role === 'assistant' && last.clarification_options ? last.clarification_options : null;
                    })()}
                    onClarificationSelect={(opt) => { if (!loading) send(opt); }}
                    pendingFiles={pendingFiles}
                    onRemovePendingFile={handlePendingFileRemove}
                    onConfirmPendingFiles={handlePendingFilesConfirm}
                    onCancelPendingFiles={handlePendingFilesCancel}
                />

                {renameTarget ? (
                    <ModalDialog
                        title="Renomear conversa"
                        description="Escolhe um título curto e claro para encontrares esta conversa mais depressa."
                        primaryAction={{ label: "Guardar título", onClick: submitRenameDialog, disabled: !renameValue.trim() }}
                        secondaryAction={{ label: "Cancelar", onClick: closeRenameDialog }}
                    >
                        <input
                            className="login-input"
                            value={renameValue}
                            onChange={(event) => setRenameValue(event.target.value)}
                            placeholder="Ex.: Via Verde · Step 2"
                            maxLength={100}
                            autoFocus
                            onKeyDown={(event) => {
                                if (event.key === "Enter") {
                                    event.preventDefault();
                                    submitRenameDialog();
                                }
                                if (event.key === "Escape") {
                                    event.preventDefault();
                                    closeRenameDialog();
                                }
                            }}
                        />
                    </ModalDialog>
                ) : null}

                {deleteTarget ? (
                    <ModalDialog
                        title="Apagar conversa"
                        description={`Vais remover "${deleteTarget.title}" do histórico. Esta ação não pode ser desfeita.`}
                        danger
                        primaryAction={{ label: "Apagar conversa", onClick: confirmDeleteDialog }}
                        secondaryAction={{ label: "Cancelar", onClick: closeDeleteDialog }}
                    />
                ) : null}

                {showFeedbackDash ? (
                    <div className="fb-dash-overlay" onClick={(ev) => { if (ev.target === ev.currentTarget) setShowFeedbackDash(false); }}>
                        <div className="fb-dash-modal">
                            <button className="fb-dash-close" onClick={() => setShowFeedbackDash(false)}>✕</button>
                            <FeedbackDashboard apiUrl={API_URL} authFetch={authFetch} />
                        </div>
                    </div>
                ) : null}

                {showToolsManual ? (
                    <ToolsManual
                        onTryPrompt={(prompt) => {
                            setInput(prompt);
                            setTimeout(() => inputRef.current && inputRef.current.focus(), 50);
                        }}
                        onClose={() => setShowToolsManual(false)}
                    />
                ) : null}

                <DigestSidePanel
                    open={digestPanelOpen}
                    onToggle={() => setDigestPanelOpen(prev => !prev)}
                    onClose={() => setDigestPanelOpen(false)}
                    digest={digest}
                    digestLoading={digestLoading}
                    digestError={digestError}
                    onDigestAction={(text) => {
                        setInput(text);
                        setDigestPanelOpen(false);
                        setTimeout(() => inputRef.current && inputRef.current.focus(), 50);
                    }}
                />
            </div>
        </div>
    );
}

export default App;
