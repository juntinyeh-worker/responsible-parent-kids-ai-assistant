/**
 * Child Voice Tutor — Frontend SPA (Nova Sonic via WebSocket)
 * State machine, WebSocket audio streaming, mic toggle.
 */

// --- State Machine ---
const STATES = {
  IDLE: 'IDLE',
  CONNECTING: 'CONNECTING',
  LISTENING: 'LISTENING',
  SPEAKING: 'SPEAKING',
  PROCESSING: 'PROCESSING',
  RESPONDING: 'RESPONDING',
  ERROR: 'ERROR',
};

const TRANSITIONS = {
  IDLE:        { mic_granted: 'CONNECTING' },
  CONNECTING:  { connected: 'LISTENING', connection_lost: 'ERROR' },
  LISTENING:   { vad_speech_start: 'SPEAKING', connection_lost: 'ERROR' },
  SPEAKING:    { vad_speech_end: 'PROCESSING', connection_lost: 'ERROR' },
  PROCESSING:  { audio_stream_start: 'RESPONDING', connection_lost: 'ERROR' },
  RESPONDING:  { playback_complete: 'LISTENING', connection_lost: 'ERROR' },
  ERROR:       { manual_retry: 'CONNECTING' },
};

let currentState = STATES.IDLE;

function transition(event) {
  const allowed = TRANSITIONS[currentState];
  if (!allowed || !allowed[event]) {
    console.warn(`Invalid transition: ${currentState} + ${event}`);
    return false;
  }
  const newState = allowed[event];
  console.log(`State: ${currentState} → ${newState} (${event})`);
  currentState = newState;

  // Auto-mute when processing/responding
  const autoMuteStates = [STATES.PROCESSING, STATES.RESPONDING, STATES.CONNECTING];
  if (autoMuteStates.includes(currentState)) {
    micUserMuted = true;
    setMicMuted(true);
    updateMicToggleBtn();
  }

  updateUI();
  return true;
}

// --- Browser Compatibility ---
function checkBrowser() {
  return !!(navigator.mediaDevices?.getUserMedia);
}

// --- DOM Elements ---
const avatarEl = document.getElementById('avatar');
const statusEl = document.getElementById('status-text');
const startBtn = document.getElementById('start-btn');
const retryBtn = document.getElementById('retry-btn');
const stopBtn = document.getElementById('stop-btn');
const micToggleBtn = document.getElementById('mic-toggle-btn');
const unsupportedEl = document.getElementById('unsupported');
const transcriptEl = document.getElementById('transcript');
const transcriptContent = document.getElementById('transcript-content');
const micIndicator = document.getElementById('mic-indicator');
const micIcon = document.getElementById('mic-icon');
const micLabel = document.getElementById('mic-label');

// --- Session State ---
let clientId = localStorage.getItem('clientId') || crypto.randomUUID();
localStorage.setItem('clientId', clientId);

let ws = null;
let micStream = null;
let audioContext = null;
let scriptProcessor = null;
let playbackContext = null;
let micUserMuted = true;
let conversationHistory = [];
let currentTurn = { input: '', output: '', turnNumber: 0 };
let currentRole = '';
let isSpeculative = false;

// Track last 5 inputs/outputs for display, and last text to deduplicate
let inputHistory = [];   // newest first
let outputHistory = [];  // newest first
let lastUserText = '';
let lastAssistantText = '';
const MAX_HISTORY = 5;

// --- Audio Config ---
const INPUT_SAMPLE_RATE = 16000;
const OUTPUT_SAMPLE_RATE = 24000;
const BUFFER_SIZE = 4096;

// --- Mic Mute Control ---
function setMicMuted(muted) {
  if (micStream) {
    micStream.getAudioTracks().forEach(track => { track.enabled = !muted; });
  }
  if (micIcon && micLabel) {
    micIcon.textContent = muted ? '🔇' : '🎙️';
    micLabel.textContent = muted ? 'Mic Off' : 'Mic On';
    micIndicator.style.opacity = muted ? '0.5' : '1';
  }
}

function updateMicToggleBtn() {
  if (!micToggleBtn) return;
  if (micUserMuted) {
    micToggleBtn.textContent = '🔇 Mic OFF';
    micToggleBtn.style.background = '#2196F3';
  } else {
    micToggleBtn.textContent = '🎤 Mic ON';
    micToggleBtn.style.background = '#4CAF50';
  }
}

// --- Status Messages ---
const STATUS_MESSAGES = {
  IDLE: 'Tap the button below to start',
  CONNECTING: 'Connecting...',
  LISTENING: '🎧 Listening, speak now...',
  SPEAKING: '🗣️ I hear you...',
  PROCESSING: '🤔 Let me think...',
  RESPONDING: '💬 Responding...',
  ERROR: '😔 Connection error',
};

const AVATAR_EMOJIS = {
  IDLE: '🤖', CONNECTING: '⏳', LISTENING: '👂',
  SPEAKING: '🗣️', PROCESSING: '🤔', RESPONDING: '💬', ERROR: '😔',
};

function updateUI() {
  avatarEl.className = `avatar ${currentState.toLowerCase()}`;
  avatarEl.textContent = AVATAR_EMOJIS[currentState] || '🤖';
  statusEl.textContent = STATUS_MESSAGES[currentState] || '';
  startBtn.style.display = currentState === STATES.IDLE ? '' : 'none';
  retryBtn.style.display = currentState === STATES.ERROR ? '' : 'none';
  const activeStates = [STATES.LISTENING, STATES.SPEAKING, STATES.PROCESSING, STATES.RESPONDING, STATES.CONNECTING];
  const isActive = activeStates.includes(currentState);
  micToggleBtn.style.display = isActive ? '' : 'none';
  stopBtn.style.display = isActive ? '' : 'none';
  micIndicator.style.display = isActive ? '' : 'none';
  updateMicToggleBtn();
}

// --- Audio Helpers ---

function float32ToInt16(float32Array) {
  const int16 = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }
  return int16;
}

function resample(audioData, fromRate, toRate) {
  if (fromRate === toRate) return audioData;
  const ratio = fromRate / toRate;
  const newLength = Math.round(audioData.length / ratio);
  const result = new Float32Array(newLength);
  for (let i = 0; i < newLength; i++) {
    const idx = i * ratio;
    const low = Math.floor(idx);
    const high = Math.min(low + 1, audioData.length - 1);
    const frac = idx - low;
    result[i] = audioData[low] * (1 - frac) + audioData[high] * frac;
  }
  return result;
}

function int16ToFloat32(int16Array) {
  const float32 = new Float32Array(int16Array.length);
  for (let i = 0; i < int16Array.length; i++) {
    float32[i] = int16Array[i] / 0x8000;
  }
  return float32;
}

// --- Audio Playback Queue ---
let playbackNextTime = 0;

function playAudioChunk(b64Audio) {
  const raw = atob(b64Audio);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  const int16 = new Int16Array(bytes.buffer);
  const float32 = int16ToFloat32(int16);

  if (!playbackContext) {
    playbackContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: OUTPUT_SAMPLE_RATE });
    playbackNextTime = 0;
  }

  const buffer = playbackContext.createBuffer(1, float32.length, OUTPUT_SAMPLE_RATE);
  buffer.getChannelData(0).set(float32);
  const source = playbackContext.createBufferSource();
  source.buffer = buffer;
  source.connect(playbackContext.destination);

  // Schedule chunks back-to-back with no gaps
  const now = playbackContext.currentTime;
  if (playbackNextTime < now) {
    playbackNextTime = now + 0.01; // small buffer to avoid underrun
  }
  source.start(playbackNextTime);
  playbackNextTime += buffer.duration;
}

// --- WebSocket Connection ---

async function connectWebSocket() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${proto}//${location.host}/api/ws/audio`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log('WebSocket connected');
    transition('connected');
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    handleServerMessage(msg);
  };

  ws.onerror = (err) => {
    console.error('WebSocket error:', err);
    transition('connection_lost');
  };

  ws.onclose = () => {
    console.log('WebSocket closed');
    if (currentState !== STATES.IDLE && currentState !== STATES.ERROR) {
      transition('connection_lost');
    }
  };
}

function handleServerMessage(msg) {
  switch (msg.type) {
    case 'audio':
      if (currentState === STATES.PROCESSING) {
        transition('audio_stream_start');
      }
      playAudioChunk(msg.data);
      break;

    case 'text':
      // Skip system/internal messages
      if (msg.content && (msg.content.includes('"interrupted"') || msg.content.startsWith('{'))) {
        break;
      }
      if (isSpeculative) break;

      if (msg.role === 'USER') {
        // Deduplicate: skip if same as last
        if (msg.content && msg.content !== lastUserText) {
          lastUserText = msg.content;
          currentTurn.input = msg.content;
          inputHistory.unshift(msg.content);
          if (inputHistory.length > MAX_HISTORY) inputHistory.pop();
          renderHistory();
        }
      } else if (msg.role === 'ASSISTANT') {
        if (msg.content && msg.content !== lastAssistantText) {
          lastAssistantText = msg.content;
          currentTurn.output = msg.content;
          outputHistory.unshift(msg.content);
          if (outputHistory.length > MAX_HISTORY) outputHistory.pop();
          renderHistory();
        }
      }
      break;

    case 'event':
      if (msg.speculative !== undefined) {
        isSpeculative = msg.speculative;
      }
      // contentEnd from non-speculative assistant = response done
      if (msg.role === 'ASSISTANT' && !isSpeculative) {
        if (currentState === STATES.RESPONDING || currentState === STATES.PROCESSING) {
          finalizeTurn();
          currentState = STATES.LISTENING;
          micUserMuted = true;
          setMicMuted(true);
          updateUI();
          console.log('State: → LISTENING (response done, mic auto-muted)');
        }
      }
      break;
  }
}

function renderHistory() {
  let container = document.getElementById('live-text-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'live-text-container';
    container.style.cssText = 'margin:1rem 0; width:100%; display:grid; grid-template-columns:1fr 1fr; gap:8px;';
    const assistantCol = document.createElement('div');
    assistantCol.id = 'live-assistant';
    assistantCol.style.cssText = 'padding:8px; background:rgba(33,150,243,0.15); border-radius:12px; font-size:0.85rem; text-align:left; word-wrap:break-word; max-height:300px; overflow-y:auto;';
    const userCol = document.createElement('div');
    userCol.id = 'live-user';
    userCol.style.cssText = 'padding:8px; background:rgba(76,175,80,0.15); border-radius:12px; font-size:0.85rem; text-align:left; word-wrap:break-word; max-height:300px; overflow-y:auto;';
    container.appendChild(assistantCol);
    container.appendChild(userCol);
    const parent = document.querySelector('.container');
    const transcript = document.getElementById('transcript');
    parent.insertBefore(container, transcript);
  }
  container.style.display = '';

  const assistantCol = document.getElementById('live-assistant');
  const userCol = document.getElementById('live-user');

  // Render output history (newest on top)
  assistantCol.innerHTML = '<b style="opacity:0.6">🤖 Response</b>' +
    outputHistory.map((t, i) => {
      const lines = t.split(/(?<=[。！？!?.])\s*/).filter(Boolean).join('<br>');
      const opacity = i === 0 ? '1' : '0.6';
      return `<div style="margin-top:6px; padding:4px 0; border-top:${i > 0 ? '1px solid rgba(255,255,255,0.1)' : 'none'}; opacity:${opacity}">${lines}</div>`;
    }).join('');

  // Render input history (newest on top)
  userCol.innerHTML = '<b style="opacity:0.6">👧 You said</b>' +
    inputHistory.map((t, i) => {
      const lines = t.split(/(?<=[。！？!?.])\s*/).filter(Boolean).join('<br>');
      const opacity = i === 0 ? '1' : '0.6';
      return `<div style="margin-top:6px; padding:4px 0; border-top:${i > 0 ? '1px solid rgba(255,255,255,0.1)' : 'none'}; opacity:${opacity}">${lines}</div>`;
    }).join('');
}

function clearLiveText() {
  // Don't clear — we keep history across turns
}

// --- Mic Capture & Streaming ---

async function startMicCapture() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  micStream = stream;

  audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 });
  const source = audioContext.createMediaStreamSource(stream);
  scriptProcessor = audioContext.createScriptProcessor(BUFFER_SIZE, 1, 1);

  scriptProcessor.onaudioprocess = (e) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (micUserMuted) return;

    const inputData = e.inputBuffer.getChannelData(0);
    const resampled = resample(inputData, audioContext.sampleRate, INPUT_SAMPLE_RATE);
    const int16 = float32ToInt16(resampled);
    const bytes = new Uint8Array(int16.buffer);

    // Base64 encode
    let binary = '';
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    const b64 = btoa(binary);

    ws.send(JSON.stringify({ type: 'audio', data: b64 }));
  };

  source.connect(scriptProcessor);
  scriptProcessor.connect(audioContext.destination);

  // Start muted
  setMicMuted(true);
}

// --- Conversation Logging ---

function formatLines(text) {
  return text
    .replace(/([。！？!?.])(\s*)/g, '$1<br>')
    .replace(/(<br>)+/g, '<br>')
    .trim();
}

function finalizeTurn() {
  currentTurn.turnNumber = conversationHistory.length + 1;
  conversationHistory.push({ ...currentTurn });

  transcriptEl.style.display = '';
  const entry = document.createElement('div');
  entry.style.cssText = 'margin:0.5rem 0; font-size:0.85rem;';
  entry.innerHTML = `<b>👧:</b> ${formatLines(currentTurn.input || '(audio)')}<br><b>🤖:</b> ${formatLines(currentTurn.output || '(audio)')}`;
  transcriptContent.appendChild(entry);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;

  const logPayload = {
    clientId,
    sessionId: 'nova-sonic',
    turnNumber: currentTurn.turnNumber,
    timestamp: new Date().toISOString(),
    requestSummary: currentTurn.input || '(audio input)',
    responseSummary: currentTurn.output || '(audio response)',
  };

  navigator.sendBeacon?.('/api/session/log',
    new Blob([JSON.stringify(logPayload)], { type: 'application/json' })
  ) || fetch('/api/session/log', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(logPayload),
  }).catch(() => {});

  currentTurn = { input: '', output: '', turnNumber: 0 };
  clearLiveText();
}

// --- Cleanup ---

function cleanup() {
  if (ws) {
    try { ws.send(JSON.stringify({ type: 'stop' })); } catch (e) {}
    ws.close();
    ws = null;
  }
  if (scriptProcessor) { scriptProcessor.disconnect(); scriptProcessor = null; }
  if (audioContext) { audioContext.close(); audioContext = null; }
  if (playbackContext) { playbackContext.close(); playbackContext = null; playbackNextTime = 0; }
  if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
}

// --- Start Flow ---

async function startConversation() {
  try {
    transition('mic_granted');
    await startMicCapture();
    await connectWebSocket();
  } catch (e) {
    console.error('Failed to start:', e);
    currentState = STATES.ERROR;
    updateUI();
    statusEl.textContent = `連線失敗: ${e.message}`;
  }
}

// --- Event Listeners ---

startBtn.addEventListener('click', startConversation);
retryBtn.addEventListener('click', () => {
  transition('manual_retry');
  startConversation();
});
stopBtn.addEventListener('click', () => {
  cleanup();
  currentState = STATES.IDLE;
  updateUI();
});
micToggleBtn.addEventListener('click', () => {
  micUserMuted = !micUserMuted;
  setMicMuted(micUserMuted);
  updateMicToggleBtn();
});

// --- Init ---
if (!checkBrowser()) {
  unsupportedEl.style.display = '';
  startBtn.style.display = 'none';
}

updateUI();
console.log('Child Voice Tutor (Nova Sonic) initialized. clientId:', clientId);
