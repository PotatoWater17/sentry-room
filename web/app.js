const loginView = document.querySelector("#login");
const setupView = document.querySelector("#setup");
const roomView = document.querySelector("#room");
const loginForm = document.querySelector("#login-form");
const pinInput = document.querySelector("#pin");
const loginError = document.querySelector("#login-error");
const loginHost = document.querySelector("#login-host");
const video = document.querySelector("#video");
const connecting = document.querySelector("#connecting");
const cameraNote = document.querySelector("#camera-note");
const cameraSelect = document.querySelector("#camera-select");
const speakerSelect = document.querySelector("#speaker-select");
const micSelect = document.querySelector("#mic-select");
const soundsEl = document.querySelector("#sounds");
const toast = document.querySelector("#toast");
const talkBtn = document.querySelector("#talk");
const talkError = document.querySelector("#talk-error");
const listenBtn = document.querySelector("#listen");
const armBtn = document.querySelector("#arm");
const armState = document.querySelector("#arm-state");
const eventsEl = document.querySelector("#events");
const clipLimit = document.querySelector("#clip-limit");
const motionSensitivity = document.querySelector("#motion-sensitivity");
const soundSensitivity = document.querySelector("#sound-sensitivity");
const cooldownInput = document.querySelector("#cooldown");
const listenError = document.querySelector("#listen-error");
const banner = document.querySelector("#secure-banner");
const machine = document.querySelector("#machine");
const clock = document.querySelector("#clock");

const roomVolume = document.querySelector("#room-volume");
const voiceVolume = document.querySelector("#voice-volume");
const soundVolume = document.querySelector("#sound-volume");

let info = null;
let signedIn = false;
let statusTimer = 0;
let toastTimer = 0;
let holding = false;
let generation = 0;
let listenCtrl = null;
let listenPlayer = null;
let wakeLock = null;
let fillingDevices = false;
let deviceSig = "";
let eventSig = null;

const talk = {
  stream: null,
  ctx: null,
  proc: null,
  ws: null,
  timer: null,
  pending: false,
  chunks: [],
  mode: "ws",
  rate: 48000,
};

let cid = sessionStorage.getItem("sentry-cid");
if (!cid) {
  cid = Math.random().toString(36).slice(2) + Date.now().toString(36);
  sessionStorage.setItem("sentry-cid", cid);
}

roomVolume.value = localStorage.getItem("sentry-room") || "80";
voiceVolume.value = localStorage.getItem("sentry-voice") || "100";
soundVolume.value = localStorage.getItem("sentry-sound") || "85";
roomVolume.addEventListener("input", () => {
  localStorage.setItem("sentry-room", roomVolume.value);
  if (listenPlayer) listenPlayer.setGain(roomGain());
});
voiceVolume.addEventListener("input", () => {
  localStorage.setItem("sentry-voice", voiceVolume.value);
  if (talk.ws && talk.ws.readyState === 1) {
    talk.ws.send(JSON.stringify({ t: "gain", gain: voiceGain() }));
  }
});
soundVolume.addEventListener("input", () => localStorage.setItem("sentry-sound", soundVolume.value));

function roomGain() { return Number(roomVolume.value) / 100; }
function voiceGain() { return Number(voiceVolume.value) / 100; }
function soundGain() { return Number(soundVolume.value) / 100; }

function showToast(message) {
  toast.hidden = false;
  toast.textContent = message;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 1800);
}

function showLogin(message) {
  signedIn = false;
  clearInterval(statusTimer);
  releaseTalk();
  stopListen();
  video.removeAttribute("src");
  connecting.textContent = "Choose a camera";
  setupView.hidden = true;
  roomView.hidden = true;
  loginView.hidden = false;
  document.querySelector("#reset-form").hidden = true;
  if (message) {
    loginError.hidden = false;
    loginError.textContent = message;
  }
}

function showSetup() {
  signedIn = false;
  clearInterval(statusTimer);
  releaseTalk();
  stopListen();
  video.removeAttribute("src");
  loginView.hidden = true;
  roomView.hidden = true;
  setupView.hidden = false;
}

function showRoom() {
  signedIn = true;
  loginView.hidden = true;
  setupView.hidden = true;
  roomView.hidden = false;
  connecting.hidden = false;
  connecting.textContent = "Choose a camera";
  video.removeAttribute("src");
  loadSounds();
  refreshStatus();
  clearInterval(statusTimer);
  statusTimer = setInterval(refreshStatus, 2000);
  stayAwake();
}

async function stayAwake() {
  if (!navigator.wakeLock) return;
  try {
    wakeLock = await navigator.wakeLock.request("screen");
  } catch (_err) {
    wakeLock = null;
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && signedIn) stayAwake();
});

function fillBanner() {
  banner.replaceChildren();
  if (!info || window.isSecureContext) {
    banner.hidden = true;
    return;
  }
  const remote = info.links.filter((link) => link.trusted || link.kind === "tailscale");
  const shown = remote.length ? remote : info.links.filter((link) => link.kind !== "local");
  if (!shown.length) {
    banner.hidden = true;
    return;
  }
  const note = document.createElement("p");
  note.textContent = "Open this address on your phone. It is trusted, so the browser does not show a privacy warning.";
  banner.append(note);
  for (const link of shown) {
    const anchor = document.createElement("a");
    anchor.href = link.https;
    anchor.textContent = link.https;
    banner.append(anchor);
  }
  banner.hidden = false;
}

async function boot() {
  try {
    info = await fetch("/api/info").then((res) => res.json());
  } catch (_err) {
    loginHost.textContent = "Cannot reach this PC.";
    return;
  }
  loginHost.textContent = "Camera and speakers on " + info.name + ".";
  machine.textContent = info.name;
  fillBanner();
  if (info.needsSetup) {
    showSetup();
    return;
  }
  const me = await fetch("/api/me");
  if (me.ok) showRoom();
}

document.querySelector("#setup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const setupError = document.querySelector("#setup-error");
  setupError.hidden = true;
  const pin = document.querySelector("#setup-pin").value;
  const confirm = document.querySelector("#setup-confirm").value;
  const res = await fetch("/api/setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin, confirm }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setupError.hidden = false;
    setupError.textContent = data.error || "Could not save the code.";
    return;
  }
  document.querySelector("#setup-pin").value = "";
  document.querySelector("#setup-confirm").value = "";
  info.needsSetup = false;
  showRoom();
});

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  stayAwake();
  loginError.hidden = true;
  const res = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin: pinInput.value }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    loginError.hidden = false;
    loginError.textContent = data.error || "Could not sign in.";
    return;
  }
  pinInput.value = "";
  showRoom();
});

document.querySelector("#reveal").addEventListener("click", () => {
  const showing = pinInput.type === "text";
  pinInput.type = showing ? "password" : "text";
  document.querySelector("#reveal").textContent = showing ? "Show code" : "Hide code";
});

document.querySelector("#signout").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  showLogin();
});

document.querySelector("#reset-open").addEventListener("click", () => {
  document.querySelector("#reset-form").hidden = false;
  document.querySelector("#reset-error").hidden = true;
  document.querySelector("#reset-pin").focus();
});

document.querySelector("#reset-cancel").addEventListener("click", () => {
  document.querySelector("#reset-form").hidden = true;
  document.querySelector("#reset-pin").value = "";
});

document.querySelector("#reset-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const resetError = document.querySelector("#reset-error");
  resetError.hidden = true;
  const res = await fetch("/api/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin: document.querySelector("#reset-pin").value }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    resetError.hidden = false;
    resetError.textContent = data.error || "Could not reset.";
    return;
  }
  document.querySelector("#reset-pin").value = "";
  if (info) info.needsSetup = true;
  showSetup();
});

async function refreshStatus() {
  const res = await fetch("/api/status");
  if (res.status === 401) {
    showLogin("Sign in again.");
    return;
  }
  if (!res.ok) return;
  const data = await res.json();
  if (data.camera && data.camera.on && !String(video.src).includes("/video.mjpg")) {
    connecting.hidden = true;
    video.src = "/video.mjpg?t=" + Date.now();
  }
  if (data.camera && data.camera.ok) {
    cameraNote.hidden = true;
  } else if (data.camera && data.camera.error) {
    cameraNote.hidden = false;
    cameraNote.textContent = data.camera.error;
  }
  applyDevices(data);
  applyWatch(data.watch);
  for (const button of soundsEl.querySelectorAll("button")) {
    button.classList.toggle("on", button.dataset.id === data.looping);
  }
}

async function loadSounds() {
  const res = await fetch("/api/sounds");
  if (!res.ok) return;
  const data = await res.json();
  soundsEl.replaceChildren();
  for (const sound of data.sounds) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.id = sound.id;
    button.textContent = sound.label;
    if (sound.loop) {
      const hint = document.createElement("small");
      hint.textContent = "Loops until stop";
      button.append(hint);
    }
    button.addEventListener("click", () => playSound(sound));
    soundsEl.append(button);
  }
}

async function playSound(sound) {
  const res = await fetch("/api/play", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: sound.id, volume: soundGain(), loop: sound.loop }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(data.error || "Could not play that sound.");
    return;
  }
  showToast(data.looping === sound.id ? sound.label + " is looping" : "Playing " + sound.label);
  refreshStatus();
}

document.querySelector("#stop").addEventListener("click", stopSpeakers);

function fillSelect(select, options, selected, placeholder, blankFirst) {
  select.replaceChildren();
  if (blankFirst || !options.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = placeholder;
    select.append(option);
  }
  if (!options.length) return;
  for (const item of options) {
    const option = document.createElement("option");
    option.value = String(item.key);
    option.textContent = item.label;
    select.append(option);
  }
  const wanted = selected == null ? "" : String(selected);
  if ([...select.options].some((option) => option.value === wanted)) select.value = wanted;
}

function applyDevices(data) {
  const cameras = (data.cameras || []).map((item) => ({ key: String(item.index), label: item.label }));
  const speakers = data.speakers || [];
  const microphones = data.microphones || [];
  const sig = JSON.stringify([cameras, speakers, microphones]);
  const cameraKey = data.camera && data.camera.on && data.camera.index != null ? String(data.camera.index) : "";
  const speakerKey = data.speaker && data.speaker.key ? data.speaker.key : "";
  const micKey = data.microphone && data.microphone.key ? data.microphone.key : "";
  if (sig !== deviceSig) {
    fillingDevices = true;
    deviceSig = sig;
    fillSelect(cameraSelect, cameras, cameraKey, "Choose a camera", true);
    fillSelect(speakerSelect, speakers, speakerKey, "No speakers found");
    fillSelect(micSelect, microphones, micKey, "No microphones found");
    fillingDevices = false;
    return;
  }
  if (document.activeElement === cameraSelect || document.activeElement === speakerSelect || document.activeElement === micSelect) return;
  if (cameraKey) cameraSelect.value = cameraKey;
  else cameraSelect.value = "";
  if (speakerKey && [...speakerSelect.options].some((option) => option.value === speakerKey)) speakerSelect.value = speakerKey;
  if (micKey && [...micSelect.options].some((option) => option.value === micKey)) micSelect.value = micKey;
}

cameraSelect.addEventListener("change", async () => {
  if (fillingDevices || cameraSelect.value === "") return;
  const res = await fetch("/api/camera", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ index: Number(cameraSelect.value) }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(body.error || "Could not switch camera.");
    deviceSig = "";
    refreshStatus();
    return;
  }
  showToast("Switching camera");
  connecting.textContent = "Starting camera…";
  connecting.hidden = false;
  video.src = "/video.mjpg?t=" + Date.now();
});

speakerSelect.addEventListener("change", async () => {
  if (fillingDevices || speakerSelect.value === "") return;
  const res = await fetch("/api/speaker", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: speakerSelect.value }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(body.error || "Could not use that speaker.");
    deviceSig = "";
    refreshStatus();
    return;
  }
  showToast("Speaker: " + (body.name || "updated"));
});

micSelect.addEventListener("change", async () => {
  if (fillingDevices || micSelect.value === "") return;
  const listening = listenBtn.getAttribute("aria-pressed") === "true";
  const res = await fetch("/api/microphone", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: micSelect.value }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    listenError.hidden = false;
    listenError.textContent = body.error || "Could not use that microphone.";
    deviceSig = "";
    refreshStatus();
    return;
  }
  listenError.hidden = true;
  showToast("Room mic: " + (body.name || "updated"));
  if (listening) {
    stopListen();
    startListen();
  }
});

async function stopSpeakers() {
  const res = await fetch("/api/stop", { method: "POST" });
  if (res.ok) showToast("Speakers quiet");
  refreshStatus();
}

document.querySelector("#full").addEventListener("click", async () => {
  const stage = document.querySelector("#stage");
  if (document.fullscreenElement) await document.exitFullscreen();
  else await stage.requestFullscreen();
});

function wsUrl(path) {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return protocol + "//" + location.host + path;
}

function floatTo16(input) {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, input[i]));
    out[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return out;
}

function rms(input) {
  let sum = 0;
  for (let i = 0; i < input.length; i += 8) sum += input[i] * input[i];
  return Math.sqrt(sum / (input.length / 8));
}

function startPostLoop() {
  if (talk.timer) return;
  talk.timer = setInterval(flushTalk, 90);
}

async function flushTalk() {
  if (talk.pending || talk.chunks.length === 0 || talk.mode === "ws") return;
  const parts = talk.chunks;
  talk.chunks = [];
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const merged = new Int16Array(total);
  let offset = 0;
  for (const part of parts) {
    merged.set(part, offset);
    offset += part.length;
  }
  const max = Math.floor(talk.rate * 0.4);
  const send = merged.length > max ? merged.subarray(merged.length - max) : merged;
  const payload = new Int16Array(send);
  talk.pending = true;
  try {
    const res = await fetch("/api/talk", {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Audio-Rate": String(talk.rate),
        "X-Gain": String(voiceGain()),
        "X-Client": cid,
      },
      body: payload,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      talkError.hidden = false;
      talkError.textContent = data.error || "The speakers did not accept the voice.";
    }
  } catch (_err) {
    talkError.hidden = false;
    talkError.textContent = "The voice connection dropped.";
  } finally {
    talk.pending = false;
  }
}

async function beginTalk() {
  const gen = ++generation;
  talkError.hidden = true;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    talkError.hidden = false;
    talkError.textContent = "This browser cannot use the microphone.";
    return;
  }
  if (!window.isSecureContext) {
    talkError.hidden = false;
    talkError.textContent = "Open the trusted phone address above, then hold the button again.";
    return;
  }
  const ctx = new AudioContext();
  talk.ctx = ctx;
  ctx.resume();
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      video: false,
    });
  } catch (_err) {
    if (gen === generation) {
      talkError.hidden = false;
      talkError.textContent = "Allow the microphone for this site, then hold the button again.";
    }
    releaseTalkResources();
    return;
  }
  if (gen !== generation) {
    stream.getTracks().forEach((track) => track.stop());
    releaseTalkResources();
    return;
  }
  talk.stream = stream;
  talk.rate = ctx.sampleRate;
  await ctx.resume();
  if (gen !== generation) {
    releaseTalkResources();
    return;
  }
  const source = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(2048, 1, 1);
  const mute = ctx.createGain();
  mute.gain.value = 0;
  source.connect(proc);
  proc.connect(mute);
  mute.connect(ctx.destination);
  talk.proc = proc;
  talk.chunks = [];
  talk.mode = "ws";
  proc.onaudioprocess = (event) => {
    if (gen !== generation) return;
    const input = event.inputBuffer.getChannelData(0);
    talkBtn.style.setProperty("--level", String(Math.min(1, rms(input) * 9)));
    const pcm = floatTo16(input);
    if (talk.mode === "ws" && talk.ws && talk.ws.readyState === 1) {
      talk.ws.send(pcm);
      return;
    }
    talk.chunks.push(pcm);
  };

  let ws;
  try {
    ws = new WebSocket(wsUrl("/ws/talk?cid=" + encodeURIComponent(cid)));
  } catch (_err) {
    talk.mode = "post";
    startPostLoop();
    return;
  }
  talk.ws = ws;
  let opened = false;
  const failTimer = setTimeout(() => {
    if (opened || gen !== generation) return;
    talk.mode = "post";
    try { ws.close(); } catch (_err) { /* already closing */ }
    startPostLoop();
  }, 2000);
  ws.addEventListener("open", () => {
    if (gen !== generation) {
      ws.close();
      return;
    }
    opened = true;
    clearTimeout(failTimer);
    talk.mode = "ws";
    if (talk.timer) {
      clearInterval(talk.timer);
      talk.timer = null;
    }
    ws.send(JSON.stringify({ t: "hello", rate: talk.rate, gain: voiceGain() }));
  });
  ws.addEventListener("close", () => {
    if (gen === generation && holding && talk.mode === "ws") {
      talk.mode = "post";
      startPostLoop();
    }
  });
}

function releaseTalkResources() {
  if (talk.timer) clearInterval(talk.timer);
  talk.timer = null;
  if (talk.ws) {
    if (talk.ws.readyState === 1) talk.ws.send(JSON.stringify({ t: "bye" }));
    try { talk.ws.close(); } catch (_err) { /* ignore */ }
  }
  talk.ws = null;
  if (talk.proc) talk.proc.onaudioprocess = null;
  talk.proc = null;
  if (talk.ctx) talk.ctx.close().catch(() => {});
  talk.ctx = null;
  if (talk.stream) talk.stream.getTracks().forEach((track) => track.stop());
  talk.stream = null;
  talk.chunks = [];
  talkBtn.style.setProperty("--level", "0");
}

function releaseTalk() {
  generation += 1;
  holding = false;
  talkBtn.classList.remove("holding");
  releaseTalkResources();
}

function pressTalk(event) {
  if (event && event.cancelable) event.preventDefault();
  if (holding) return;
  holding = true;
  talkBtn.classList.add("holding");
  if (event && event.pointerId != null) {
    try { talkBtn.setPointerCapture(event.pointerId); } catch (_err) { /* mouse fallback */ }
  }
  beginTalk();
}

function blockCallout(event) {
  if (event.cancelable) event.preventDefault();
}

talkBtn.addEventListener("touchstart", (event) => {
  blockCallout(event);
  pressTalk(event);
}, { passive: false });
talkBtn.addEventListener("touchmove", blockCallout, { passive: false });
talkBtn.addEventListener("touchend", releaseTalk);
talkBtn.addEventListener("touchcancel", releaseTalk);
talkBtn.addEventListener("gesturestart", blockCallout, { passive: false });
talkBtn.addEventListener("selectstart", blockCallout);
talkBtn.addEventListener("pointerdown", pressTalk, { passive: false });
talkBtn.addEventListener("pointerup", releaseTalk);
talkBtn.addEventListener("pointercancel", releaseTalk);
talkBtn.addEventListener("contextmenu", blockCallout);

window.addEventListener("keydown", (event) => {
  if (event.code === "Escape" && signedIn && !document.fullscreenElement) stopSpeakers();
  if (event.code !== "Space" || event.repeat || !signedIn) return;
  const tag = event.target && event.target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA") return;
  pressTalk(event);
});
window.addEventListener("keyup", (event) => {
  if (event.code === "Space") releaseTalk();
});

class RoomAudio {
  constructor(ctx, rate, gainValue) {
    this.ctx = ctx;
    this.rate = rate;
    this.next = 0;
    this.extra = new Uint8Array(0);
    this.gain = ctx.createGain();
    this.gain.gain.value = gainValue;
    this.gain.connect(ctx.destination);
  }

  setGain(value) { this.gain.gain.value = value; }

  push(bytes) {
    let data = bytes;
    if (this.extra.length) {
      const merged = new Uint8Array(this.extra.length + bytes.length);
      merged.set(this.extra, 0);
      merged.set(bytes, this.extra.length);
      data = merged;
    }
    const even = data.length - (data.length % 2);
    this.extra = data.slice(even);
    if (even < 2) return;
    const view = new DataView(data.buffer, data.byteOffset, even);
    const count = even / 2;
    const buffer = this.ctx.createBuffer(1, count, this.rate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < count; i += 1) channel[i] = view.getInt16(i * 2, true) / 32768;
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.gain);
    const now = this.ctx.currentTime;
    if (this.next < now + 0.02) this.next = now + 0.08;
    if (this.next > now + 0.5) this.next = now + 0.08;
    source.start(this.next);
    this.next += buffer.duration;
  }
}

function stopListen() {
  listenBtn.setAttribute("aria-pressed", "false");
  listenBtn.textContent = "Listen to room";
  if (listenCtrl) listenCtrl.abort();
  listenCtrl = null;
  listenPlayer = null;
}

video.addEventListener("load", () => { connecting.hidden = true; });
video.addEventListener("error", () => {
  if (!signedIn) return;
  connecting.hidden = false;
  setTimeout(() => {
    if (signedIn) video.src = "/video.mjpg?t=" + Date.now();
  }, 800);
});

async function startListen() {
  listenError.hidden = true;
  listenBtn.setAttribute("aria-pressed", "true");
  listenBtn.textContent = "Connecting…";
  const ctx = new AudioContext();
  ctx.resume();
  const ctrl = new AbortController();
  listenCtrl = ctrl;
  let res;
  try {
    res = await fetch("/listen.pcm?cid=" + encodeURIComponent(cid), { signal: ctrl.signal });
  } catch (err) {
    if (err.name !== "AbortError") {
      listenError.hidden = false;
      listenError.textContent = "Could not open the room microphone.";
    }
    stopListen();
    ctx.close().catch(() => {});
    return;
  }
  if (res.status === 401) {
    ctx.close().catch(() => {});
    showLogin("Sign in again.");
    return;
  }
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => ({}));
    listenError.hidden = false;
    listenError.textContent = data.error || "The room microphone is unavailable.";
    stopListen();
    ctx.close().catch(() => {});
    return;
  }
  listenBtn.setAttribute("aria-pressed", "true");
  listenBtn.textContent = "Listening";
  const reader = res.body.getReader();
  let pending = new Uint8Array(0);
  let player = null;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done || ctrl.signal.aborted) break;
      const merged = new Uint8Array(pending.length + value.length);
      merged.set(pending, 0);
      merged.set(value, pending.length);
      if (!player) {
        if (merged.length < 10) {
          pending = merged;
          continue;
        }
        const magic = new TextDecoder().decode(merged.slice(0, 6));
        if (magic !== "SENTRY") throw new Error("unexpected audio stream");
        const rate = new DataView(merged.buffer, merged.byteOffset, merged.byteLength).getUint32(6, true);
        player = new RoomAudio(ctx, rate || 16000, roomGain());
        listenPlayer = player;
        pending = new Uint8Array(0);
        const rest = merged.slice(10);
        if (rest.length) player.push(rest);
        continue;
      }
      pending = new Uint8Array(0);
      player.push(merged);
    }
  } catch (err) {
    if (err.name !== "AbortError") {
      listenError.hidden = false;
      listenError.textContent = "Room audio stopped.";
    }
  } finally {
    if (listenCtrl === ctrl) stopListen();
    ctx.close().catch(() => {});
  }
}

function applyWatch(watch) {
  if (!watch) return;
  const armed = Boolean(watch.armed);
  armBtn.setAttribute("aria-pressed", armed ? "true" : "false");
  armBtn.textContent = armed ? "Armed" : "Arm";
  if (watch.recording) {
    armState.textContent = "Recording a 10 second clip.";
  } else if (armed) {
    armState.textContent = `Armed. ${watch.count} clip${watch.count === 1 ? "" : "s"} saved, newest kept up to ${watch.clipLimit}.`;
  } else {
    armState.textContent = "Disarmed. Nothing is recorded until you arm it.";
  }
  document.querySelector("#motion-bar").style.width = `${watch.motion || 0}%`;
  document.querySelector("#sound-bar").style.width = `${watch.loud || 0}%`;
  if (document.activeElement !== clipLimit) clipLimit.value = watch.clipLimit;
  if (document.activeElement !== motionSensitivity) motionSensitivity.value = watch.motionSensitivity;
  if (document.activeElement !== soundSensitivity) soundSensitivity.value = watch.soundSensitivity;
  if (document.activeElement !== cooldownInput) cooldownInput.value = watch.cooldown;
  const busy = eventsEl.querySelector("img.play") || [...eventsEl.querySelectorAll("audio")].some((node) => !node.paused && !node.ended);
  const sig = (watch.events || []).map((event) => event.id).join(",");
  if (busy || sig === eventSig) return;
  eventSig = sig;
  const rows = watch.events || [];
  if (!rows.length) {
    eventsEl.innerHTML = `<p class="empty">No alerts yet.</p>`;
    return;
  }
  eventsEl.innerHTML = rows.map((event) => {
    const why = event.reason === "motion+sound" ? "Motion and a loud sound" : event.reason === "sound" ? "Loud sound" : "Motion";
    const audio = event.audio ? `<audio controls preload="none" src="/api/clips/${event.id}/audio"></audio>` : "";
    return `<article class="event">
      <img alt="" src="/api/clips/${event.id}/poster">
      <div>
        <strong>${why}</strong>
        <p class="muted">${event.at}</p>
        <button class="ghost play-clip" type="button" data-id="${event.id}">Play 10s</button>
        ${audio}
      </div>
    </article>`;
  }).join("");
}

armBtn.addEventListener("click", async () => {
  const armed = armBtn.getAttribute("aria-pressed") !== "true";
  const res = await fetch("/api/arm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ armed }),
  });
  if (res.status === 401) {
    showLogin("Sign in again.");
    return;
  }
  const data = await res.json().catch(() => ({}));
  if (res.ok) applyWatch(data);
});

async function saveWatchSettings() {
  await fetch("/api/arm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      clipLimit: Number(clipLimit.value),
      motion: Number(motionSensitivity.value),
      sound: Number(soundSensitivity.value),
      cooldown: Number(cooldownInput.value),
    }),
  });
}

clipLimit.addEventListener("change", saveWatchSettings);
motionSensitivity.addEventListener("change", saveWatchSettings);
soundSensitivity.addEventListener("change", saveWatchSettings);
cooldownInput.addEventListener("change", saveWatchSettings);

document.querySelector("#clear-log").addEventListener("click", async () => {
  const res = await fetch("/api/events/clear", { method: "POST" });
  if (res.ok) applyWatch(await res.json());
});

eventsEl.addEventListener("click", (event) => {
  const button = event.target.closest(".play-clip");
  if (!button) return;
  const card = button.closest(".event");
  const image = card.querySelector("img");
  image.classList.add("play");
  image.src = `/api/clips/${button.dataset.id}/play?t=${Date.now()}`;
  image.onload = () => image.classList.remove("play");
});

listenBtn.addEventListener("click", () => {
  if (listenBtn.getAttribute("aria-pressed") === "true") stopListen();
  else startListen();
});

setInterval(() => {
  if (!signedIn) return;
  clock.textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}, 1000);

boot();
