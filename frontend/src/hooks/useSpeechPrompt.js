import { useEffect, useRef, useState } from 'react';
import {
  createAzureSpeechRecognition,
  createSpeechRecognition,
  isAzureSpeechBrowserSupported,
  isSpeechRecognitionSupported,
  normalizeSpeechPrompt,
} from '../utils/speech.js';

const STORAGE_KEY = 'app:speech-submit-mode';

function loadStoredSubmitMode() {
  if (typeof window === 'undefined') return 'auto';
  const stored = String(window.localStorage.getItem(STORAGE_KEY) || '').trim().toLowerCase();
  return stored === 'text' ? 'text' : 'auto';
}

export default function useSpeechPrompt({
  authFetchFn,
  apiUrl,
  agentMode,
  conversationId,
  inputRef,
  onApplyPrompt,
  onAutoSendPrompt,
  hasPendingInput,
}) {
  const [speechSupported, setSpeechSupported] = useState(false);
  const [speechListening, setSpeechListening] = useState(false);
  const [speechStopping, setSpeechStopping] = useState(false);
  const [speechProcessing, setSpeechProcessing] = useState(false);
  const [speechInterimText, setSpeechInterimText] = useState('');
  const [speechNotice, setSpeechNotice] = useState('');
  const [speechSubmitMode, setSpeechSubmitMode] = useState(loadStoredSubmitMode);
  const [speechProvider, setSpeechProvider] = useState('browser_fallback');

  const speechRecognitionRef = useRef(null);
  const speechTranscriptRef = useRef('');
  const speechStoppingRef = useRef(false);

  useEffect(() => {
    setSpeechSupported(isSpeechRecognitionSupported() || isAzureSpeechBrowserSupported());
    return () => {
      if (speechRecognitionRef.current) {
        Promise.resolve(speechRecognitionRef.current.abort?.()).catch(() => {});
        speechRecognitionRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(STORAGE_KEY, speechSubmitMode);
  }, [speechSubmitMode]);

  useEffect(() => {
    let cancelled = false;
    async function loadSpeechInfo() {
      try {
        const response = await fetch(apiUrl + '/api/info');
        if (!response.ok) return;
        const payload = await response.json();
        const provider = String(payload?.features?.speech_provider || 'browser_fallback').trim() || 'browser_fallback';
        if (!cancelled) {
          setSpeechProvider(provider);
        }
      } catch (_) {
        // ignore info fetch failures; browser fallback remains available
      }
    }
    loadSpeechInfo();
    return () => {
      cancelled = true;
    };
  }, [apiUrl]);

  function resizeComposerInput() {
    if (!inputRef?.current) return;
    inputRef.current.style.height = 'auto';
    inputRef.current.style.height = `${Math.min(inputRef.current.scrollHeight, 120)}px`;
  }

  function clearSpeechNotice() {
    setSpeechNotice('');
  }

  function resetSpeechPrompt() {
    if (speechRecognitionRef.current) {
      Promise.resolve(speechRecognitionRef.current.abort?.()).catch(() => {});
      speechRecognitionRef.current = null;
    }
    speechTranscriptRef.current = '';
    speechStoppingRef.current = false;
    setSpeechStopping(false);
    setSpeechListening(false);
    setSpeechProcessing(false);
    setSpeechInterimText('');
    setSpeechNotice('');
  }

  function toggleSpeechSubmitMode() {
    setSpeechSubmitMode((prev) => (prev === 'auto' ? 'text' : 'auto'));
  }

  function applyNormalizedSpeechPrompt(nextPrompt, notice = '') {
    onApplyPrompt(nextPrompt);
    setSpeechInterimText('');
    setSpeechNotice(notice);
    window.setTimeout(() => {
      if (inputRef?.current) {
        inputRef.current.focus();
        resizeComposerInput();
      }
    }, 0);
  }

  async function finalizeSpeechPrompt(rawTranscript) {
    const transcript = String(rawTranscript || '').trim();
    if (!transcript) {
      setSpeechProcessing(false);
      setSpeechInterimText('');
      setSpeechNotice('Não apanhei texto suficiente. Tenta novamente.');
      return;
    }

    setSpeechProcessing(true);
    setSpeechInterimText(transcript);
    setSpeechNotice('');

    try {
      const normalized = await normalizeSpeechPrompt(authFetchFn, apiUrl, {
        transcript,
        mode: agentMode,
        conversation_id: conversationId || null,
        language: 'pt-PT',
      });

      const finalPrompt = String(normalized?.normalized_prompt || transcript).trim() || transcript;
      const inferredMode = String(normalized?.inferred_mode || agentMode).trim().toLowerCase();
      const confidence = String(normalized?.confidence || 'medium').trim().toLowerCase();
      const notes = Array.isArray(normalized?.notes)
        ? normalized.notes.filter(Boolean).map(item => String(item).trim()).filter(Boolean)
        : [];
      const providerPolicyNote = String(normalized?.provider_policy_note || '').trim();
      const externalProvider = Boolean(normalized?.external_provider);
      const autoSendAllowed = Boolean(normalized?.auto_send_allowed);
      const hasDraftText = Boolean(hasPendingInput?.());
      const shouldAutoSend = speechSubmitMode === 'auto' && autoSendAllowed && !hasDraftText;

      const noticeParts = [];
      if (shouldAutoSend) {
        noticeParts.push('Pedido por voz enviado automaticamente.');
      } else if (speechSubmitMode === 'auto' && hasDraftText) {
        noticeParts.push('Mantive o texto no composer porque já tinhas conteúdo por rever.');
      } else if (confidence === 'high') {
        noticeParts.push('Pedido por voz pronto a rever.');
      } else if (confidence === 'low') {
        noticeParts.push('Interpretação com baixa confiança. Revê o texto antes de enviar.');
      } else {
        noticeParts.push('Pedido por voz interpretado. Revê antes de enviar.');
      }
      if (inferredMode && inferredMode !== agentMode) {
        noticeParts.push(inferredMode === 'userstory' ? 'Parece um pedido de User Stories.' : 'Parece um pedido do modo geral.');
      }
      if (notes.length > 0) {
        noticeParts.push(notes[0]);
      }
      if (externalProvider && providerPolicyNote) {
        noticeParts.push(providerPolicyNote);
      }
      const finalNotice = noticeParts.join(' ');

      if (shouldAutoSend) {
        await onAutoSendPrompt?.(finalPrompt, finalNotice);
        setSpeechNotice(finalNotice);
        return;
      }

      applyNormalizedSpeechPrompt(finalPrompt, finalNotice);
    } catch (error) {
      setSpeechNotice(error?.message || 'Falha ao interpretar o pedido por voz.');
    } finally {
      setSpeechProcessing(false);
      setSpeechInterimText('');
    }
  }

  async function createRecognitionSession() {
    if (speechProvider === 'azure_speech') {
      try {
        return await createAzureSpeechRecognition({
          authFetchFn,
          apiUrl,
          language: 'pt-PT',
          mode: agentMode,
          onResult: ({ finalTranscript, combinedTranscript }) => {
            const transcript = String(finalTranscript || combinedTranscript || '').trim();
            speechTranscriptRef.current = transcript;
            setSpeechInterimText(String(combinedTranscript || transcript || '').trim());
          },
          onError: (message) => {
            speechRecognitionRef.current = null;
            setSpeechStopping(false);
            setSpeechListening(false);
            setSpeechProcessing(false);
            setSpeechInterimText('');
            if (!speechTranscriptRef.current.trim()) {
              setSpeechNotice(message || 'Azure Speech falhou. Tenta novamente.');
            }
          },
          onEnd: () => {
            const transcript = speechTranscriptRef.current;
            speechRecognitionRef.current = null;
            setSpeechStopping(false);
            setSpeechListening(false);
            const wasStopping = speechStoppingRef.current;
            speechStoppingRef.current = false;
            if (transcript.trim()) {
              finalizeSpeechPrompt(transcript);
              return;
            }
            if (wasStopping) {
              setSpeechNotice('Captação de voz terminada sem texto suficiente.');
            }
          },
        });
      } catch (error) {
        if (isSpeechRecognitionSupported()) {
          setSpeechNotice('Azure Speech indisponível. A usar reconhecimento de voz do browser.');
        } else {
          throw error;
        }
      }
    }

    return createSpeechRecognition({
      language: 'pt-PT',
      onResult: ({ finalTranscript, combinedTranscript }) => {
        const transcript = String(finalTranscript || combinedTranscript || '').trim();
        speechTranscriptRef.current = transcript;
        setSpeechInterimText(String(combinedTranscript || transcript || '').trim());
      },
      onError: (errorCode) => {
        const code = String(errorCode || '').trim();
        const friendlyMessage =
          code === 'not-allowed' || code === 'service-not-allowed'
            ? 'A permissão do microfone foi negada neste browser.'
            : code === 'no-speech'
              ? 'Não apanhei fala suficiente. Tenta novamente.'
              : 'O reconhecimento de voz falhou. Tenta novamente.';
        speechRecognitionRef.current = null;
        setSpeechStopping(false);
        setSpeechListening(false);
        setSpeechProcessing(false);
        setSpeechInterimText('');
        if (!speechTranscriptRef.current.trim()) {
          setSpeechNotice(friendlyMessage);
        }
      },
      onEnd: () => {
        const transcript = speechTranscriptRef.current;
        speechRecognitionRef.current = null;
        setSpeechStopping(false);
        setSpeechListening(false);
        const wasStopping = speechStoppingRef.current;
        speechStoppingRef.current = false;
        if (transcript.trim()) {
          finalizeSpeechPrompt(transcript);
          return;
        }
        if (wasStopping) {
          setSpeechNotice('Captação de voz terminada sem texto suficiente.');
        }
      },
    });
  }

  function createBrowserFallbackSession(withNotice = false) {
    if (withNotice) {
      setSpeechNotice('Azure Speech indisponível neste browser. A usar reconhecimento de voz do browser.');
    }
    return createSpeechRecognition({
      language: 'pt-PT',
      onResult: ({ finalTranscript, combinedTranscript }) => {
        const transcript = String(finalTranscript || combinedTranscript || '').trim();
        speechTranscriptRef.current = transcript;
        setSpeechInterimText(String(combinedTranscript || transcript || '').trim());
      },
      onError: (errorCode) => {
        const code = String(errorCode || '').trim();
        const friendlyMessage =
          code === 'not-allowed' || code === 'service-not-allowed'
            ? 'A permissão do microfone foi negada neste browser.'
            : code === 'no-speech'
              ? 'Não apanhei fala suficiente. Tenta novamente.'
              : 'O reconhecimento de voz falhou. Tenta novamente.';
        speechRecognitionRef.current = null;
        setSpeechListening(false);
        setSpeechProcessing(false);
        setSpeechInterimText('');
        if (!speechTranscriptRef.current.trim()) {
          setSpeechNotice(friendlyMessage);
        }
      },
      onEnd: () => {
        const transcript = speechTranscriptRef.current;
        speechRecognitionRef.current = null;
        setSpeechListening(false);
        const wasStopping = speechStoppingRef.current;
        speechStoppingRef.current = false;
        if (transcript.trim()) {
          finalizeSpeechPrompt(transcript);
          return;
        }
        if (wasStopping) {
          setSpeechNotice('Captação de voz terminada sem texto suficiente.');
        }
      },
    });
  }

  async function toggleSpeech() {
    if (!speechSupported) {
      setSpeechNotice('O browser atual não suporta captação de voz.');
      return;
    }

    if (speechListening && speechRecognitionRef.current) {
      const activeRecognition = speechRecognitionRef.current;
      speechStoppingRef.current = true;
      setSpeechStopping(true);
      setSpeechListening(false);
      setSpeechNotice('A terminar a captação de voz...');
      try {
        await Promise.race([
          Promise.resolve(activeRecognition.stop?.()),
          new Promise((_, reject) => window.setTimeout(() => reject(new Error('speech_stop_timeout')), 1500)),
        ]);
      } catch (_) {
        try {
          await activeRecognition.abort?.();
        } catch (_) {
          // ignore stop/abort race
        }
      }
      return;
    }

    speechTranscriptRef.current = '';
    speechStoppingRef.current = false;
    setSpeechStopping(false);
    setSpeechNotice('');
    setSpeechInterimText('');
    setSpeechProcessing(false);

    try {
      let recognition = await createRecognitionSession();
      speechRecognitionRef.current = recognition;
      try {
        await recognition.start();
      } catch (startError) {
        const message = String(startError?.message || '');
        const canFallbackToBrowser =
          speechProvider === 'azure_speech' &&
          isSpeechRecognitionSupported() &&
          !message.toLowerCase().includes('permissão do microfone foi negada');
        if (!canFallbackToBrowser) {
          throw startError;
        }
        recognition = createBrowserFallbackSession(true);
        speechRecognitionRef.current = recognition;
        await recognition.start();
      }
      setSpeechListening(true);
      setSpeechStopping(false);
      setSpeechNotice('');
    } catch (error) {
      speechRecognitionRef.current = null;
      setSpeechStopping(false);
      setSpeechListening(false);
      setSpeechProcessing(false);
      setSpeechInterimText('');
      setSpeechNotice(error?.message || 'Não foi possível iniciar a captação de voz.');
    }
  }

  return {
    speechSupported,
    speechListening,
    speechStopping,
    speechProcessing,
    speechInterimText,
    speechNotice,
    speechSubmitMode,
    speechProvider,
    clearSpeechNotice,
    resetSpeechPrompt,
    toggleSpeech,
    toggleSpeechSubmitMode,
  };
}
