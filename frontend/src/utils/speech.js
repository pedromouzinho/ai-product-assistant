const GENERAL_PHRASES = [
  "Acme",
  "Produto Digital",
  "ACME",
  "BackOffice",
  "Via Verde",
  "CTA",
  "stepper",
  "Payments",
  "epic",
  "feature",
  "user story",
  "acceptance criteria",
  "KPI",
  "DevOps",
];

const USERSTORY_PHRASES = [
  ...GENERAL_PHRASES,
  "critérios de aceitação",
  "proveniência",
  "composição",
  "comportamento",
];

export function isSpeechRecognitionSupported() {
  if (typeof window === "undefined") return false;
  return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
}

export function isAzureSpeechBrowserSupported() {
  if (typeof window === "undefined") return false;
  return typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;
}

function getPhraseList(mode = "general") {
  return mode === "userstory" ? USERSTORY_PHRASES : GENERAL_PHRASES;
}

function mapAzureSpeechCancel(errorCode = "") {
  const code = String(errorCode || "").toLowerCase();
  if (code.includes("notallowed") || code.includes("permission")) {
    return "A permissão do microfone foi negada. Verifica as permissões do browser e também as permissões de microfone do sistema operativo.";
  }
  if (code.includes("nomatch") || code.includes("no_match")) {
    return "Não apanhei fala suficiente. Tenta novamente.";
  }
  if (code.includes("notreadable") || code.includes("device")) {
    return "O microfone não ficou disponível. Fecha outras apps a usar o microfone e tenta novamente.";
  }
  if (code.includes("connection")) {
    return "A ligação ao Azure Speech falhou. Tenta novamente.";
  }
  return "A captação de voz falhou. Tenta novamente.";
}

async function requestMicrophonePreflight() {
  if (!navigator?.mediaDevices?.getUserMedia) {
    throw new Error("O browser atual não suporta acesso ao microfone.");
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    const rawMessage = String(error?.message || error?.name || "microphone_error");
    throw new Error(mapAzureSpeechCancel(rawMessage));
  } finally {
    if (stream) {
      for (const track of stream.getTracks?.() || []) {
        try {
          track.stop();
        } catch (_) {
          // ignore
        }
      }
    }
  }
}

export function createSpeechRecognition({ language = "pt-PT", onResult, onError, onEnd } = {}) {
  if (!isSpeechRecognitionSupported()) {
    throw new Error("O browser não suporta reconhecimento de voz.");
  }

  const RecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
  const recognition = new RecognitionCtor();
  recognition.lang = language;
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;

  recognition.onresult = (event) => {
    let finalTranscript = "";
    let interimTranscript = "";
    for (let i = 0; i < event.results.length; i += 1) {
      const text = String(event.results[i][0]?.transcript || "");
      if (event.results[i].isFinal) {
        finalTranscript += `${text} `;
      } else {
        interimTranscript += `${text} `;
      }
    }
    onResult?.({
      finalTranscript: finalTranscript.trim(),
      interimTranscript: interimTranscript.trim(),
      combinedTranscript: `${finalTranscript} ${interimTranscript}`.trim(),
    });
  };

  recognition.onerror = (event) => {
    onError?.(String(event?.error || "Erro de reconhecimento de voz"));
  };

  recognition.onend = () => {
    onEnd?.();
  };

  return recognition;
}

export async function fetchSpeechToken(authFetchFn, apiUrl) {
  const response = await authFetchFn(apiUrl + "/api/speech/token", {
    method: "POST",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(String(data?.detail || "Falha ao obter token de voz."));
  }
  return data;
}

export async function createAzureSpeechRecognition({
  authFetchFn,
  apiUrl,
  language = "pt-PT",
  mode = "general",
  onResult,
  onError,
  onEnd,
} = {}) {
  if (!isAzureSpeechBrowserSupported()) {
    throw new Error("O browser atual não suporta captação de microfone para Azure Speech.");
  }

  await requestMicrophonePreflight();

  const [{ default: speechsdk }, tokenPayload] = await Promise.all([
    import("microsoft-cognitiveservices-speech-sdk"),
    fetchSpeechToken(authFetchFn, apiUrl),
  ]);

  const speechConfig = speechsdk.SpeechConfig.fromAuthorizationToken(
    tokenPayload.token,
    tokenPayload.region,
  );
  speechConfig.speechRecognitionLanguage = tokenPayload.language || language;
  speechConfig.enableDictation();

  const audioConfig = speechsdk.AudioConfig.fromDefaultMicrophoneInput();
  const recognizer = new speechsdk.SpeechRecognizer(speechConfig, audioConfig);
  const phraseList = speechsdk.PhraseListGrammar.fromRecognizer(recognizer);
  for (const phrase of getPhraseList(mode)) {
    phraseList.addPhrase(phrase);
  }

  let finalTranscript = "";
  let ended = false;

  function finish() {
    if (ended) return;
    ended = true;
    try {
      recognizer.close();
    } catch (_) {
      // ignore close failures
    }
    try {
      audioConfig.close?.();
    } catch (_) {
      // ignore close failures
    }
    onEnd?.();
  }

  recognizer.recognizing = (_, event) => {
    const interimText = String(event?.result?.text || "").trim();
    onResult?.({
      finalTranscript,
      interimTranscript: interimText,
      combinedTranscript: `${finalTranscript} ${interimText}`.trim(),
    });
  };

  recognizer.recognized = (_, event) => {
    if (event?.result?.reason === speechsdk.ResultReason.RecognizedSpeech) {
      const text = String(event.result.text || "").trim();
      finalTranscript = `${finalTranscript} ${text}`.trim();
      onResult?.({
        finalTranscript,
        interimTranscript: "",
        combinedTranscript: finalTranscript,
      });
      return;
    }
    if (event?.result?.reason === speechsdk.ResultReason.NoMatch) {
      onError?.("nomatch");
    }
  };

  recognizer.canceled = (_, event) => {
    onError?.(mapAzureSpeechCancel(event?.errorDetails || event?.reason || ""));
    finish();
  };

  recognizer.sessionStopped = () => {
    finish();
  };

  return {
    start: () =>
      new Promise((resolve, reject) => {
        recognizer.startContinuousRecognitionAsync(resolve, (error) => reject(new Error(String(error || "Falha ao iniciar Azure Speech."))));
      }),
    stop: () =>
      new Promise((resolve, reject) => {
        recognizer.stopContinuousRecognitionAsync(
          () => {
            finish();
            resolve();
          },
          (error) => {
            finish();
            reject(new Error(String(error || "Falha ao parar Azure Speech.")));
          },
        );
      }),
    abort: () => {
      finish();
      return Promise.resolve();
    },
  };
}

export async function normalizeSpeechPrompt(authFetchFn, apiUrl, payload) {
  const response = await authFetchFn(apiUrl + "/api/speech/prompt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(String(data?.detail || "Falha ao interpretar o pedido por voz."));
  }
  return data;
}
