// Lumi — PWA client. Captures a turn (mic or text), sends to /api/turn,
// renders the emotion + reply, and speaks the reply (ElevenLabs audio, or
// browser speechSynthesis as a local fallback).

const COLORS = {
  calm: '#1a9d5b', happy: '#1a9d5b', relieved: '#1a9d5b', eager: '#c07c12',
  neutral: '#5b6070', expectant: '#0a84ff',
  anxious: '#b8860b', frustrated: '#d2691e', angry: '#cc3a1f', distress: '#ff3b30',
};

const chat = document.getElementById('chat');
const empty = document.getElementById('empty');
const emotionChip = document.getElementById('emotionChip');
const srcNote = document.getElementById('srcNote');
const statusEl = document.getElementById('status');
const textIn = document.getElementById('textIn');
const sendBtn = document.getElementById('sendBtn');
const ptt = document.getElementById('ptt');

let messages = [];
let forcedEmotion = 'auto';
let busy = false;

// device-token identity (persists across refreshes) + a per-visit session id
let guestToken = localStorage.getItem('lumi_guest_token') || '';
const sessionId = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : ('s-' + Date.now());
async function ensureGuest() {
  if (guestToken) return;
  try {
    const d = await (await fetch('/api/guest', { method: 'POST' })).json();
    if (d.guest_token) { guestToken = d.guest_token; localStorage.setItem('lumi_guest_token', guestToken); }
  } catch (e) { /* no server -> no memory, still works */ }
}
ensureGuest();

// ---------- rendering ----------
function bubble(role, text, opts = {}) {
  if (empty) empty.style.display = 'none';
  const wrap = document.createElement('div');
  wrap.className = 'msg ' + (role === 'user' ? 'user' : 'agent') + (opts.red ? ' red' : '');
  const who = document.createElement('span');
  who.className = 'who';
  who.innerHTML = role === 'user'
    ? `guest${opts.emotion ? ' · <b style="color:' + (COLORS[opts.emotion] || '#fff') + '">' + opts.emotion.toUpperCase() + '</b>' : ''}`
    : `Lumi${opts.tone ? ' · ' + opts.tone : ''}`;
  const b = document.createElement('div');
  b.className = 'bubble';
  b.textContent = text;
  if (opts.media && opts.media.length) {
    const grid = document.createElement('div');
    grid.className = 'media-grid';
    grid.innerHTML = opts.media.map((m) =>
      `<div class="media-tile"><div class="pic" style="background-image:url('${m.url}');background-size:cover;background-position:center"></div>` +
      `<div class="cap"><div class="t">${m.title}</div>${m.caption ? `<div class="s">${m.caption}</div>` : ''}</div></div>`
    ).join('');
    b.appendChild(grid);
  }
  wrap.appendChild(who);
  wrap.appendChild(b);
  chat.appendChild(wrap);
  chat.scrollTop = chat.scrollHeight;
  return b;
}

function setEmotion(emotion, source) {
  const col = COLORS[emotion] || '#f4f6ef';
  emotionChip.textContent = 'emotion: ' + emotion;
  emotionChip.style.color = col;
  emotionChip.style.borderColor = col;
  srcNote.textContent = source === 'local-fallback' ? 'local fallback (no GPU server)' : 'live · acoustic read';
  document.body.classList.toggle('redmode', emotion === 'distress');
}

function speak(audio_b64, text) {
  if (audio_b64) {
    const a = new Audio('data:audio/mpeg;base64,' + audio_b64);
    a.play().catch(() => browserSpeak(text));
  } else {
    browserSpeak(text);
  }
}
function browserSpeak(text) {
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.0; u.pitch = 1.0;
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  } catch (e) { /* no TTS available */ }
}

// ---------- a turn ----------
async function sendTurn({ blob = null, typed = '' } = {}) {
  if (busy) return;
  busy = true;
  sendBtn.disabled = true;
  await ensureGuest();
  const thinking = document.createElement('div');
  thinking.className = 'thinking';
  thinking.textContent = 'Lumi is listening…';
  chat.appendChild(thinking);
  chat.scrollTop = chat.scrollHeight;

  const fd = new FormData();
  if (blob) fd.append('audio', blob, 'turn.webm');
  if (typed) fd.append('user_text', typed);
  if (forcedEmotion !== 'auto') fd.append('forced_emotion', forcedEmotion);
  fd.append('messages_json', JSON.stringify(messages));
  if (guestToken) fd.append('guest_token', guestToken);
  fd.append('session_id', sessionId);

  try {
    const res = await fetch('/api/turn', { method: 'POST', body: fd });
    const data = await res.json();
    thinking.remove();

    const userText = data.transcript || typed || 'Voice message';
    bubble('user', userText, { emotion: data.emotion });
    bubble('agent', data.reply, { red: data.emotion === 'distress', media: data.media });
    setEmotion(data.emotion, data.source);
    if (data.returning) statusEl.innerHTML = '<span class="dot live"></span> returning guest · memory on';
    speak(data.audio_b64, data.reply);
    if (Array.isArray(data.messages)) messages = data.messages;
  } catch (e) {
    thinking.remove();
    bubble('agent', '(connection problem — is the app server running?)');
  } finally {
    busy = false;
    sendBtn.disabled = false;
  }
}

// ---------- text input ----------
function sendText() {
  const t = textIn.value.trim();
  if (!t) return;
  textIn.value = '';
  sendTurn({ typed: t });
}
sendBtn.addEventListener('click', sendText);
textIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendText(); });

// ---------- operator override (hidden by default; auto-detect is primary) ----------
const opToggle = document.getElementById('opToggle');
if (opToggle) {
  opToggle.addEventListener('click', () => {
    const t = document.getElementById('emoToggle');
    const showing = t.style.display !== 'none' && t.style.display !== '';
    t.style.display = showing ? 'none' : 'flex';
    opToggle.style.color = showing ? 'var(--muted-2)' : 'var(--lime)';
  });
}

// ---------- emotion toggle ----------
document.querySelectorAll('#emoToggle .seg').forEach((seg) => {
  seg.addEventListener('click', () => {
    document.querySelectorAll('#emoToggle .seg').forEach((s) => s.classList.remove('active'));
    seg.classList.add('active');
    forcedEmotion = seg.dataset.e;
  });
});

// ---------- push to talk ----------
let mediaRecorder = null, chunks = [], micStream = null;
let hasServer = false;

// Browser speech-to-text → instant transcript when there's no GPU server yet.
// IMPORTANT: run it ALONE (never alongside MediaRecorder) — two things can't hold the
// mic at once. Phase 2's server-side faster-whisper is the robust version (phones too).
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null, recognizedText = '', sttMode = false;
if (SR) {
  recognition = new SR();
  recognition.lang = 'en-US';
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.onresult = (e) => {
    let t = '';
    for (let i = 0; i < e.results.length; i++) t += e.results[i][0].transcript;
    recognizedText = t.trim();
  };
  recognition.onerror = () => {};
  recognition.onend = () => {
    if (!sttMode) return;
    sttMode = false;
    ptt.classList.remove('listening');
    statusEl.innerHTML = '<span class="dot live"></span> ready';
    if (recognizedText) sendTurn({ typed: recognizedText });
    else bubble('agent', "(I didn't catch that — try again, or type below.)");
  };
}

async function ensureMic() {
  if (micStream) return micStream;
  micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  return micStream;
}

async function startRec() {
  if (busy) return;
  // No GPU server → transcribe in the browser, on its own (avoids mic contention).
  if (!hasServer && recognition) {
    recognizedText = '';
    sttMode = true;
    try { recognition.start(); } catch (e) { /* already running */ }
    ptt.classList.add('listening');
    statusEl.innerHTML = '<span class="dot live"></span> listening…';
    return;
  }
  // Server present (or no SR available) → record audio for server-side STT + emotion.
  try {
    const stream = await ensureMic();
    chunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    mediaRecorder.onstop = () => {
      const blob = new Blob(chunks, { type: 'audio/webm' });
      if (blob.size > 0) sendTurn({ blob });
    };
    mediaRecorder.start();
    ptt.classList.add('listening');
    statusEl.innerHTML = '<span class="dot live"></span> listening…';
  } catch (e) {
    statusEl.innerHTML = '<span class="dot warn"></span> mic blocked';
    bubble('agent', '(mic needs permission + HTTPS. On the laptop use http://localhost; on a phone use the tunnel URL. You can also type below.)');
  }
}

function stopRec() {
  if (sttMode) { if (recognition) { try { recognition.stop(); } catch (e) {} } return; } // onend sends
  if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
  ptt.classList.remove('listening');
  statusEl.innerHTML = '<span class="dot live"></span> ready';
}

ptt.addEventListener('mousedown', startRec);
ptt.addEventListener('mouseup', stopRec);
ptt.addEventListener('mouseleave', stopRec);
ptt.addEventListener('touchstart', (e) => { e.preventDefault(); startRec(); }, { passive: false });
ptt.addEventListener('touchend', (e) => { e.preventDefault(); stopRec(); }, { passive: false });

// ---------- service worker (PWA install) ----------
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

// ---------- health ----------
fetch('/healthz').then((r) => r.json()).then((h) => {
  hasServer = h.emotion_server;
  const bits = [];
  if (!h.elevenlabs) bits.push('no ElevenLabs key (browser voice)');
  if (!h.emotion_server) bits.push('no GPU server (browser STT + manual emotion)');
  if (bits.length) statusEl.innerHTML = '<span class="dot warn"></span> ' + bits.join(' · ');
}).catch(() => {});
