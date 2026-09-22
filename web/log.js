const player = document.querySelector("#player");
const empty = document.querySelector("#player-empty");
const title = document.querySelector("#clip-title");
const meta = document.querySelector("#clip-meta");
const items = document.querySelector("#clip-items");
const library = document.querySelector("#library");
const gate = document.querySelector("#gate");
const download = document.querySelector("#download");

let clips = [];
let current = "";
let listSig = "";
let speed = 1;

function reasonLabel(reason) {
  if (reason === "motion+sound") return "Motion and a loud sound";
  if (reason === "sound") return "Loud sound";
  return "Motion";
}

function showClip(id, autoplay) {
  const clip = clips.find((item) => item.id === id) || clips[0];
  if (!clip) {
    current = "";
    player.hidden = true;
    player.removeAttribute("src");
    empty.hidden = false;
    empty.textContent = "No clips yet. Arm the watch, and movement or a loud sound will show up here.";
    title.textContent = "Clip";
    meta.textContent = "";
    download.hidden = true;
    paintList();
    return;
  }
  current = clip.id;
  empty.hidden = true;
  player.hidden = false;
  const nextSrc = `/api/clips/${clip.id}/video`;
  if (!player.src.endsWith(nextSrc)) {
    player.src = nextSrc;
  }
  player.playbackRate = speed;
  title.textContent = reasonLabel(clip.reason);
  meta.textContent = clip.at || "";
  download.hidden = false;
  download.href = nextSrc;
  download.setAttribute("download", `${clip.id}.mp4`);
  paintList();
  if (autoplay) {
    player.play().catch(() => {});
  }
}

function paintList() {
  if (!clips.length) {
    items.innerHTML = `<p class="muted">Nothing saved yet.</p>`;
    return;
  }
  items.innerHTML = clips.map((clip) => `
    <button class="clip-row${clip.id === current ? " current" : ""}" type="button" data-id="${clip.id}">
      <img alt="" src="/api/clips/${clip.id}/poster">
      <span>
        <strong>${reasonLabel(clip.reason)}</strong>
        <span>${clip.at || ""}</span>
      </span>
    </button>
  `).join("");
}

async function refresh() {
  const res = await fetch("/api/events");
  if (res.status === 401) {
    library.hidden = true;
    gate.hidden = false;
    return;
  }
  if (!res.ok) return;
  gate.hidden = true;
  library.hidden = false;
  const data = await res.json();
  const next = data.events || [];
  const sig = next.map((clip) => clip.id).join(",");
  clips = next;
  if (sig !== listSig) {
    listSig = sig;
    if (!current || !clips.some((clip) => clip.id === current)) showClip(clips[0] && clips[0].id, false);
    else paintList();
  }
}

function step(delta) {
  const index = clips.findIndex((clip) => clip.id === current);
  const next = clips[index + delta];
  if (next) showClip(next.id, true);
}

items.addEventListener("click", (event) => {
  const button = event.target.closest(".clip-row");
  if (!button) return;
  showClip(button.dataset.id, true);
});

document.querySelector("#prev").addEventListener("click", () => step(-1));
document.querySelector("#next").addEventListener("click", () => step(1));
document.querySelector("#back").addEventListener("click", () => {
  player.currentTime = Math.max(0, player.currentTime - 5);
});
document.querySelector("#forward").addEventListener("click", () => {
  if (Number.isFinite(player.duration)) player.currentTime = Math.min(player.duration, player.currentTime + 5);
});

document.querySelector(".speed").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  speed = Number(button.dataset.speed) || 1;
  player.playbackRate = speed;
  for (const item of document.querySelectorAll(".speed button")) {
    item.setAttribute("aria-pressed", item === button ? "true" : "false");
  }
});

player.addEventListener("error", () => {
  if (!current) return;
  empty.hidden = false;
  empty.textContent = "This clip could not be prepared. Try it again in a moment.";
});

player.addEventListener("loadeddata", () => {
  empty.hidden = true;
  player.hidden = false;
});

document.querySelector("#clear-log").addEventListener("click", async () => {
  if (!clips.length) return;
  if (!window.confirm("Delete every saved clip?")) return;
  const res = await fetch("/api/events/clear", { method: "POST" });
  if (res.status === 401) {
    location.href = "/";
    return;
  }
  if (!res.ok) return;
  clips = [];
  listSig = "";
  current = "";
  showClip("", false);
});

document.querySelector("#signout").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  location.href = "/";
});

document.addEventListener("keydown", (event) => {
  if (event.target.closest("input, textarea")) return;
  if (event.key === "ArrowLeft") {
    player.currentTime = Math.max(0, player.currentTime - 5);
  } else if (event.key === "ArrowRight" && Number.isFinite(player.duration)) {
    player.currentTime = Math.min(player.duration, player.currentTime + 5);
  } else if (event.key === "n" || event.key === "N") {
    step(1);
  } else if (event.key === "p" || event.key === "P") {
    step(-1);
  }
});

refresh();
setInterval(refresh, 2000);
