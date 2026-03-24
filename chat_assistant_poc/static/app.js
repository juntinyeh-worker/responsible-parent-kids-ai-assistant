/**
 * Child Voice Tutor — Frontend SPA
 * State machine, WebRTC connection to OpenAI Realtime API, audio handling.
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
  CONNECTING:  { token_received: 'LISTENING', connection_lost: 'ERROR' },
  LISTENING:   { vad_speech_start: 'SPEAKING', connection_lost: 'ERROR' },
  SPEAKING:    { vad_speech_end: 'PROCESSING', connection_lost: 'ERROR' },
  PROCESSING:  { audio_stream_start: 'RESPONDING', connection_lost: 'ERROR' },
  RESPONDING:  { playback_complete: 'LISTENING', child_interrupts: 'SPEAKING', connection_lost: 'ERROR' },
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

  // Mic mute logic: auto-mute when speech is sent, NEVER auto-unmute (user controls that)
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
  const ua = navigator.userAgent;
  if (!window.RTCPeerConnection || !navigator.mediaDevices?.getUserMedia) {
    return false;
  }
  const chrome = ua.match(/Chrome\/(\d+)/);
  const safari = ua.match(/Version\/(\d+).*Safari/);
  const edge = ua.match(/Edg\/(\d+)/);
  if (chrome && parseInt(chrome[1]) >= 90) return true;
  if (safari && parseInt(safari[1]) >= 16) return true;
  if (edge && parseInt(edge[1]) >= 90) return true;
  // Allow other WebRTC-capable browsers
  return !!window.RTCPeerConnection;
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

let sessionToken = null;
let sessionId = null;
let tokenExpiresAt = 0;
let sessionModel = '';
let sessionIsGA = true;
let pc = null; // RTCPeerConnection
let dataChannel = null;
let audioEl = null;
let micStream = null; // microphone MediaStream
let heartbeatInterval = null;
let reconnectAttempts = 0;
const MAX_RECONNECT = 3;
let conversationHistory = [];
let currentTurn = { input: '', output: '', turnNumber: 0 };
let micUserMuted = true; // Start muted, user must toggle ON

// --- Mic Mute Control ---
function setMicMuted(muted) {
  if (micStream) {
    micStream.getAudioTracks().forEach(track => { track.enabled = !muted; });
  }
  // Update visual indicator
  if (micIcon && micLabel) {
    micIcon.textContent = muted ? '🔇' : '🎙️';
    micLabel.textContent = muted ? '麥克風靜音' : '麥克風開啟';
    micIndicator.style.opacity = muted ? '0.5' : '1';
  }
}

// --- Status Messages ---
const STATUS_MESSAGES = {
  IDLE: '點擊下方按鈕開始對話',
  CONNECTING: '正在連線中...',
  LISTENING: '🎧 我在聽，請說話...',
  SPEAKING: '🗣️ 我聽到你了...',
  PROCESSING: '🤔 讓我想想...',
  RESPONDING: '💬 正在回答...',
  ERROR: '� 連線出了問題',
};

const AVATAR_EMOJIS = {
  IDLE: '🤖', CONNECTING: '⏳', LISTENING: '👂',
  SPEAKING: '🗣️', PROCESSING: '🤔', RESPONDING: '💬', ERROR: '😔',
};

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

function updateUI() {
  avatarEl.className = `avatar ${currentState.toLowerCase()}`;
  avatarEl.textContent = AVATAR_EMOJIS[currentState] || '🤖';
  statusEl.textContent = STATUS_MESSAGES[currentState] || '';
  startBtn.style.display = currentState === STATES.IDLE ? '' : 'none';
  retryBtn.style.display = currentState === STATES.ERROR ? '' : 'none';
  // Show mic toggle and stop button during active conversation
  const activeStates = [STATES.LISTENING, STATES.SPEAKING, STATES.PROCESSING, STATES.RESPONDING, STATES.CONNECTING];
  const isActive = activeStates.includes(currentState);
  micToggleBtn.style.display = isActive ? '' : 'none';
  stopBtn.style.display = isActive ? '' : 'none';
  micIndicator.style.display = isActive ? '' : 'none';
  updateMicToggleBtn();
}

// --- Token Management ---
async function fetchSessionToken() {
  const resp = await fetch('/api/session/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clientId }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || err.error || `HTTP ${resp.status}`);
  }
  const data = await resp.json();
  sessionToken = data.sessionToken;
  sessionId = data.sessionId;
  tokenExpiresAt = data.expiresAt;
  sessionModel = data.model || '';
  sessionIsGA = data.isGA !== false;
  return data;
}

function isTokenExpired() {
  return Date.now() / 1000 >= tokenExpiresAt - 5; // 5s buffer
}

async function refreshTokenIfNeeded() {
  if (isTokenExpired()) {
    console.log('Token expired, refreshing...');
    await fetchSessionToken();
  }
}

// --- WebRTC Connection ---
async function connectWebRTC() {
  pc = new RTCPeerConnection();

  // Get microphone
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  micStream = stream;
  stream.getTracks().forEach(track => pc.addTrack(track, stream));

  // Handle incoming audio from OpenAI
  pc.ontrack = (event) => {
    if (audioEl) { audioEl.pause(); audioEl.srcObject = null; }
    audioEl = new Audio();
    audioEl.playbackRate = 1.25; // Speed up Mandarin speech
    audioEl.srcObject = event.streams[0];
    audioEl.play().catch(e => console.warn('Audio play failed:', e));
  };

  // Data channel for events
  dataChannel = pc.createDataChannel('oai-events');
  dataChannel.onmessage = handleDataChannelMessage;
  dataChannel.onopen = () => {
    // Increase VAD silence threshold to avoid premature responses
    dataChannel.send(JSON.stringify({
      type: 'session.update',
      session: {
        turn_detection: {
          type: 'server_vad',
          threshold: 0.6,              // Higher = less sensitive (default ~0.5)
          prefix_padding_ms: 500,      // Keep 500ms audio before speech
          silence_duration_ms: 1000,   // Wait 1s of silence before triggering (default ~500ms)
        }
      }
    }));
    console.log('VAD config sent: threshold=0.6, silence=1000ms');
  };

  // Create and send SDP offer
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  const sdpUrl = sessionIsGA
    ? 'https://api.openai.com/v1/realtime/calls'
    : `https://api.openai.com/v1/realtime?model=${encodeURIComponent(sessionModel)}`;

  const sdpResp = await fetch(sdpUrl, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${sessionToken}`,
      'Content-Type': 'application/sdp',
    },
    body: offer.sdp,
  });

  if (!sdpResp.ok) {
    throw new Error(`WebRTC SDP exchange failed: ${sdpResp.status}`);
  }

  const answerSdp = await sdpResp.text();
  await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });

  // Monitor connection state
  pc.onconnectionstatechange = () => {
    console.log('WebRTC state:', pc.connectionState);
    if (pc.connectionState === 'disconnected' || pc.connectionState === 'failed') {
      handleConnectionLost();
    }
  };

  // Start heartbeat
  startHeartbeat();
  reconnectAttempts = 0;
  transition('token_received');
}

// --- Data Channel Event Handling ---
function handleDataChannelMessage(event) {
  try {
    const msg = JSON.parse(event.data);
    const type = msg.type;
    // Debug: log all events with current state
    if (!type.includes('.delta')) {
      console.log(`[Event] ${type} (state: ${currentState})`);
    }

    switch (type) {
      case 'input_audio_buffer.speech_started':
        if (currentState === STATES.LISTENING) {
          transition('vad_speech_start');
        }
        // Ignore speech during RESPONDING — mic is muted, no interruption
        break;

      case 'input_audio_buffer.speech_stopped':
        if (currentState === STATES.SPEAKING) {
          transition('vad_speech_end');
        }
        break;

      case 'response.output_audio.delta':
        if (currentState === STATES.PROCESSING) {
          transition('audio_stream_start');
        }
        break;

      case 'response.output_text.delta':
        if (msg.delta) {
          currentTurn.output += msg.delta;
        }
        break;

      // Beta API: audio transcript of AI response
      case 'response.audio_transcript.delta':
        if (msg.delta) {
          currentTurn.output += msg.delta;
        }
        break;

      // GA API: audio transcript of AI response
      case 'response.output_audio_transcript.delta':
        if (msg.delta) {
          currentTurn.output += msg.delta;
        }
        break;

      case 'response.audio.delta':
        // Beta event name for audio stream start
        if (currentState === STATES.PROCESSING) {
          transition('audio_stream_start');
        }
        break;

      case 'conversation.item.input_audio_transcription.completed':
        if (msg.transcript) {
          currentTurn.input = msg.transcript;
        }
        break;

      case 'response.done':
        // Turn complete — go to LISTENING but keep mic muted
        finalizeTurn();
        if (currentState === STATES.RESPONDING || currentState === STATES.PROCESSING) {
          currentState = STATES.LISTENING;
          micUserMuted = true;
          setMicMuted(true);
          updateUI();
          console.log('State: → LISTENING (response.done, mic auto-muted)');
        }
        break;

      default:
        // Ignore other events
        break;
    }
  } catch (e) {
    console.warn('Data channel parse error:', e);
  }
}

// --- Conversation Logging ---
function finalizeTurn() {
  currentTurn.turnNumber = conversationHistory.length + 1;
  conversationHistory.push({ ...currentTurn });

  // Show in transcript
  transcriptEl.style.display = '';
  const entry = document.createElement('div');
  entry.style.cssText = 'margin:0.5rem 0; font-size:0.85rem;';
  entry.innerHTML = `<b>👧:</b> ${currentTurn.input || '(audio)'}<br><b>🤖:</b> ${currentTurn.output || '(audio)'}`;
  transcriptContent.appendChild(entry);
  // Auto-scroll the transcript container to the bottom
  transcriptEl.scrollTop = transcriptEl.scrollHeight;

  // Fire-and-forget log to backend
  const logPayload = {
    clientId,
    sessionId: sessionId || '',
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

  // Reset for next turn
  currentTurn = { input: '', output: '', turnNumber: 0 };
}

// --- Reconnection ---
function handleConnectionLost() {
  stopHeartbeat();
  if (reconnectAttempts < MAX_RECONNECT) {
    reconnectAttempts++;
    const delay = Math.pow(2, reconnectAttempts) * 1000; // exponential backoff
    console.log(`Reconnecting (attempt ${reconnectAttempts}/${MAX_RECONNECT}) in ${delay}ms...`);
    statusEl.textContent = `正在重新連線... (${reconnectAttempts}/${MAX_RECONNECT})`;
    setTimeout(async () => {
      try {
        cleanup();
        await refreshTokenIfNeeded();
        await connectWebRTC();
      } catch (e) {
        console.error('Reconnection failed:', e);
        handleConnectionLost();
      }
    }, delay);
  } else {
    transition('connection_lost');
  }
}

function cleanup() {
  stopHeartbeat();
  if (pc) { pc.close(); pc = null; }
  if (audioEl) { audioEl.pause(); audioEl.srcObject = null; audioEl = null; }
  dataChannel = null;
}

// --- Heartbeat ---
function startHeartbeat() {
  stopHeartbeat();
  heartbeatInterval = setInterval(() => {
    if (pc && pc.connectionState !== 'connected') {
      console.warn('Heartbeat: connection not healthy');
      handleConnectionLost();
    }
    // Refresh token if approaching expiry
    refreshTokenIfNeeded().catch(e => console.warn('Token refresh failed:', e));
  }, 30000);
}

function stopHeartbeat() {
  if (heartbeatInterval) { clearInterval(heartbeatInterval); heartbeatInterval = null; }
}

// --- Start Flow ---
async function startConversation() {
  try {
    transition('mic_granted');
    await fetchSessionToken();
    await connectWebRTC();
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
  reconnectAttempts = 0;
  startConversation();
});
stopBtn.addEventListener('click', () => {
  cleanup();
  currentState = STATES.IDLE;
  updateUI();
  console.log('Conversation ended by user.');
});
micToggleBtn.addEventListener('click', () => {
  micUserMuted = !micUserMuted;
  setMicMuted(micUserMuted);
  updateMicToggleBtn();
  console.log('Mic toggled:', micUserMuted ? 'OFF' : 'ON');
});

// --- Init ---
if (!checkBrowser()) {
  unsupportedEl.style.display = '';
  startBtn.style.display = 'none';
}

updateUI();
console.log('Child Voice Tutor initialized. clientId:', clientId);
