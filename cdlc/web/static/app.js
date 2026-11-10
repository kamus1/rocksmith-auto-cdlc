"use strict";
// Local web UI for the CDLC generator: no dependencies, talks to cdlc/web/app.py.

const $ = (s) => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null) e.append(k instanceof Node ? k : document.createTextNode(k));
  return e;
};
const ICON = {
  check: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  music: '<svg viewBox="0 0 24 24"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
};

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const body = (r.headers.get("content-type") || "").includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(body?.detail || body || r.statusText);
  return body;
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 4500);
}

// ------------------------------------------------------------------ navigation
document.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", (e) => {
  e.preventDefault(); showView(b.dataset.view);
}));
function showView(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  if (name === "songs") loadSongs();
  if (name === "settings") loadSettings();
  window.scrollTo({ top: 0 });
}

// ------------------------------------------------------------------ environment status
const CHECK_NAMES = { encoder: ".wem encoder", ffmpeg: "ffmpeg", dotnet: ".NET", rsbuild: "RsBuild", dlc: "Game folder", tones: "Tones" };
async function loadStatus() {
  const s = await api("/api/status");
  const missing = s.checks.filter((c) => !c.ok && c.key !== "tones" && c.key !== "rsbuild");
  const pill = $("#status-pill");
  pill.querySelector(".dot").className = "dot " + (missing.length ? "err" : "ok");
  pill.querySelector(".label").textContent = missing.length
    ? `Missing: ${missing.map((c) => CHECK_NAMES[c.key] || c.key).join(", ")}` : "All set";
  return s;
}

// ------------------------------------------------------------------ settings
async function loadSettings() {
  const [s, t, cfg] = await Promise.all([loadStatus(), api("/api/tones"), api("/api/config")]);
  $("#cfg-dlc").value = cfg.dlc_dir;
  $("#cfg-default").dataset.path = cfg.default_dlc_dir;
  $("#checks").replaceChildren(...s.checks.map((c) => el("li", {},
    el("span", { class: "dot " + (c.ok ? "ok" : "err"), style: "margin-top:6px" }),
    el("span", { class: "k" }, CHECK_NAMES[c.key] || c.key),
    el("span", { class: "v" + (c.ok ? "" : " bad") }, c.ok ? String(c.value) : c.help))));
  $("#tone-summary").textContent = t.total
    ? `${t.total} tones extracted from your CDLC. ` + Object.entries(t.categories).filter(([, v]) => v).map(([k, v]) => `${TONE_NAMES[k] || k} ${v}`).join(" · ")
    : "No tone library yet: without it a generic tone is used.";
}

async function saveDlcDir(path) {
  const msg = $("#cfg-msg");
  const fd = new FormData(); fd.append("dlc_dir", path);
  try {
    const cfg = await api("/api/config", { method: "POST", body: fd });
    $("#cfg-dlc").value = cfg.dlc_dir;
    msg.className = "small ok"; msg.textContent = "Saved.";
    loadSettings(); updateDeployHint();
  } catch (e) { msg.className = "small err"; msg.textContent = e.message; }
}
$("#cfg-save").addEventListener("click", () => saveDlcDir($("#cfg-dlc").value));
$("#cfg-default").addEventListener("click", () => saveDlcDir(""));

$("#btn-tones").addEventListener("click", async () => {
  const log = $("#tones-log");
  log.hidden = false; log.textContent = "";
  try {
    const { job_id } = await api("/api/tones/rebuild", { method: "POST" });
    follow(job_id, (ev) => {
      if (ev.type === "log" || ev.type === "step") log.textContent += (ev.line || ev.title) + "\n";
      if (ev.type === "error") log.textContent += `ERROR: ${ev.message}\n${ev.detail || ""}`;
      if (ev.type === "done") { log.textContent += `Done: ${ev.result.total} tones.\n`; loadSettings(); loadTones(); }
    });
  } catch (e) { log.textContent = e.message; }
});

// ------------------------------------------------------------------ new song
const state = { upload: null, info: null, audio: null, running: false };
const GM = { 24: "Nylon guitar", 25: "Acoustic guitar", 26: "Jazz guitar", 27: "Clean guitar",
  28: "Muted guitar", 29: "Overdrive guitar", 30: "Distortion guitar", 31: "Harmonics",
  32: "Acoustic bass", 33: "Bass (finger)", 34: "Bass (pick)", 35: "Fretless bass", 36: "Slap bass", 37: "Slap bass 2",
  38: "Synth bass", 39: "Synth bass 2" };
const NOTE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const TONE_NAMES = { clean: "clean", overdrive: "overdrive", crunch: "crunch", distortion: "distortion",
  highgain: "high gain", acoustic: "acoustic", fuzz: "fuzz", auto: "automatic" };
// The track's sounds and where they change ("distortion → clean (b198) → …"), according to the tab
function toneText(t) {
  const segs = t.tone_segments || [];
  if (t.is_percussion || !segs.length) return "—";
  return segs.map((s, i) => (TONE_NAMES[s.category] || s.category) + (i ? ` (b${s.bar})` : "")).join(" → ");
}

function setupDrop(dropId, inputId, onFile) {
  const drop = $(dropId), input = $(inputId);
  input.addEventListener("change", () => input.files[0] && onFile(input.files[0]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault(); drop.classList.remove("over");
    if (e.dataTransfer.files[0]) onFile(e.dataTransfer.files[0]);
  });
}

setupDrop("#drop-tab", "#file-tab", async (file) => {
  const drop = $("#drop-tab");
  $("#chosen-tab").textContent = `${file.name} — reading…`;
  drop.classList.remove("done", "error");
  const fd = new FormData(); fd.append("tab", file);
  try {
    const r = await api("/api/inspect", { method: "POST", body: fd });
    state.upload = r.upload_id; state.info = r.info;
    $("#chosen-tab").textContent = file.name;
    drop.classList.add("done");
    renderSetup(r);
  } catch (e) {
    $("#chosen-tab").textContent = e.message;
    drop.classList.add("error");
    state.upload = null; $("#setup").hidden = true;
  }
  updateBuildButton();
});

setupDrop("#drop-audio", "#file-audio", (file) => {
  state.audio = file;
  $("#chosen-audio").textContent = `${file.name} · ${(file.size / 1048576).toFixed(1)} MB`;
  $("#drop-audio").classList.add("done");
  updateBuildButton();
});

function renderSetup(r) {
  const { info, auto } = r;
  $("#auto-desc").textContent = `Automatic: ${auto.description}`;
  const rows = info.tracks.map((t) => {
    const usable = !t.is_percussion && t.bars_with_notes > 0;
    const tuning = (t.tuning || []).map((m) => NOTE[m % 12]).join(" ");
    return el("tr", { class: usable ? "" : "dim" },
      el("td", {}, t.name || `Track ${t.index + 1}`), el("td", { class: "c" }, t.strings),
      el("td", { class: "mono" }, t.is_percussion ? "—" : tuning),
      el("td", { class: "r" }, t.single_notes), el("td", { class: "r" }, t.chords),
      el("td", { class: "r" }, `${Math.round(100 * t.bars_with_notes / Math.max(1, info.bars))} %`),
      el("td", {}, t.is_percussion ? "Drums" : (GM[t.program] || `MIDI ${t.program}`)),
      el("td", { class: "tones", title: "Sound changes marked in the tab: each one becomes a tone change in the game" }, toneText(t)),
      el("td", { class: "c" }, usable ? el("input", { type: "radio", name: "lead", value: t.index, checked: t.index === auto.lead, "aria-label": `Lead: ${t.name}` }) : ""),
      el("td", { class: "c" }, usable ? el("input", { type: "radio", name: "rhythm", value: t.index, checked: t.index === auto.rhythm, "aria-label": `Rhythm: ${t.name}` }) : ""));
  });
  $("#tracks tbody").replaceChildren(...rows);
  document.querySelector('input[name=rhythm][value=none]').checked = auto.rhythm == null;
  $("#m-artist").value = info.artist || "";
  $("#m-title").value = info.title || "";
  $("#m-album").value = info.album || "";
  $("#m-year").value = "";
  $("#m-variant").value = "";
  $("#setup").hidden = false;
}

async function loadTones() {
  try {
    const t = await api("/api/tones");
    const sel = $("#m-tone");
    sel.replaceChildren(sel.options[0]);
    for (const [k, n] of Object.entries(t.categories)) {
      if (n) sel.append(el("option", { value: k }, `Only ${TONE_NAMES[k] || k} (${n} tones)`));
    }
    $("#tone-banner").hidden = t.total > 0 || bannerDismissed();
  } catch { /* no library: only "automatic" */ }
}

// Banner on "New song" while there is no tone library; the ✕ hides it for good (this browser)
const BANNER_KEY = "cdlc.toneBannerDismissed";
function bannerDismissed() {
  try { return localStorage.getItem(BANNER_KEY) === "1"; } catch { return false; }
}
$("#tone-banner-close").addEventListener("click", () => {
  $("#tone-banner").hidden = true;
  try { localStorage.setItem(BANNER_KEY, "1"); } catch { /* private mode: closed until reload */ }
});
$("#tone-banner-scan").addEventListener("click", async () => {
  const btn = $("#tone-banner-scan"), msg = $("#tone-banner-msg");
  btn.disabled = true; btn.textContent = "Scanning…";
  msg.textContent = "Scanning your CDLC…";
  const fail = (text) => { msg.textContent = text; btn.disabled = false; btn.textContent = "Try again"; };
  try {
    const { job_id } = await api("/api/tones/rebuild", { method: "POST" });
    follow(job_id, (ev) => {
      if (ev.type === "error") fail(`Could not scan: ${ev.message}`);
      if (ev.type === "done") {
        if (!ev.result.total) return fail("No tones found. Check the game folder in Settings.");
        toast(`${ev.result.total} tones found.`);
        loadTones();
      }
    });
  } catch (e) { fail(e.message); }
});

async function updateDeployHint() {
  try {
    const cfg = await api("/api/config");
    $("#deploy-dir").textContent = cfg.dlc_dir_ok ? cfg.dlc_dir : `${cfg.dlc_dir} does not exist: choose it in Settings.`;
  } catch { /* ignore */ }
}

function updateBuildButton() {
  const ready = !!(state.upload && state.audio);
  $("#btn-build").disabled = !ready || state.running;
  $("#build-hint").textContent = state.running ? "Generating…" : ready ? "Ready to generate." :
    !state.upload ? "Tab missing." : "Audio missing.";
}

$("#btn-build").addEventListener("click", async () => {
  const fd = new FormData();
  fd.append("upload_id", state.upload);
  fd.append("audio", state.audio);
  fd.append("lead", document.querySelector("input[name=lead]:checked")?.value ?? "");
  fd.append("rhythm", document.querySelector("input[name=rhythm]:checked")?.value ?? "none");
  for (const k of ["artist", "title", "album", "year", "tone", "variant"]) fd.append(k, $(`#m-${k}`).value);
  fd.append("deploy", $("#m-deploy").checked);
  fd.append("lookup", $("#m-lookup").checked);
  const cover = $("#m-cover").files[0];
  if (cover) fd.append("cover", cover);
  state.running = true; updateBuildButton();
  resetProgress();
  try {
    const { job_id } = await api("/api/build", { method: "POST", body: fd });
    followBuild(job_id);
  } catch (e) {
    showResult(false, el("strong", {}, e.message));
    state.running = false; updateBuildButton();
  }
});

// ------------------------------------------------------------------ progress
const STEP_NAMES = ["Tracks and song data", "Audio → WAV", "Tab timeline",
  "Tab ↔ audio alignment", "XML, tones, audio and packaging", "Copy to the game"];
let elapsedTimer;

function resetProgress() {
  $("#progress").hidden = false;
  $("#result").replaceChildren();
  $("#log").textContent = "";
  $("#log-box").open = false;
  $("#steps").replaceChildren(...STEP_NAMES.map((name, i) => el("li", { class: "pending", "data-n": i },
    el("span", { class: "st" }), el("span", { class: "name" }, name), el("span", { class: "time" }))));
  const t0 = Date.now();
  clearInterval(elapsedTimer);
  elapsedTimer = setInterval(() => ($("#elapsed").textContent = `${((Date.now() - t0) / 1000).toFixed(0)} s`), 500);
  $("#progress").scrollIntoView({ behavior: "smooth", block: "start" });
}

function setStep(li, cls) {
  li.className = cls;
  li.querySelector(".st").innerHTML = cls === "done" ? ICON.check : cls === "error" ? ICON.x : "";
}

function showResult(ok, ...content) {
  $("#result").replaceChildren(el("div", { class: `banner ${ok ? "ok" : "err"}` }, ...content));
}

function warningList(items) {
  return items && items.length ? el("ul", { class: "warnings" }, items.map((w) => el("li", {}, w))) : null;
}

function followBuild(jobId) {
  let current = null, since = 0;
  const finishCurrent = (t, cls = "done") => {
    if (!current) return;
    setStep(current, cls);
    current.querySelector(".time").textContent = `${(t - since).toFixed(1)} s`;
  };
  const end = () => { clearInterval(elapsedTimer); state.running = false; updateBuildButton(); };
  follow(jobId, (ev) => {
    if (ev.type === "step") {
      finishCurrent(ev.t);
      current = document.querySelector(`#steps li[data-n="${ev.n}"]`);
      since = ev.t;
      if (current) { setStep(current, "running"); current.querySelector(".name").textContent = ev.title; }
      $("#log").textContent += `[${ev.n}/${ev.of}] ${ev.title}\n`;
    } else if (ev.type === "log") {
      $("#log").textContent += `      ${ev.line}\n`;
    } else if (ev.type === "done") {
      finishCurrent(ev.t);
      const r = ev.result;
      showResult(true,
        el("strong", {}, `${r.meta.artist} — ${r.meta.title}${r.version ? ` (v${r.version})` : ""} ready`),
        el("span", {}, r.deployed ? "Copied to the game. Go back to the Rocksmith title screen to load it."
          : `Not copied to the game: ${r.psarc}`),
        el("span", { class: "muted small" }, `${r.tracks} · Tone: ${r.tone}`),
        warningList(r.warnings));
      end();
    } else if (ev.type === "error") {
      finishCurrent(ev.t, "error");
      showResult(false, el("strong", {}, ev.message), el("span", { class: "muted small" }, "Details are in the log."));
      if (ev.detail) $("#log").textContent += `\n${ev.detail}`;
      $("#log-box").open = true;
      end();
    }
    const log = $("#log"); log.scrollTop = log.scrollHeight;
  });
}

function follow(jobId, onEvent) {
  const es = new EventSource(`/api/jobs/${jobId}/events`);
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    onEvent(ev);
    if (ev.type === "done" || ev.type === "error") es.close();
  };
  return es;
}

// ------------------------------------------------------------------ my songs
async function loadSongs() {
  const list = $("#song-list");
  const songs = await api("/api/songs");
  if (!songs.length) {
    list.replaceChildren(el("div", { class: "empty", style: "grid-column: 1 / -1" },
      el("p", {}, "No songs generated yet."),
      el("button", { class: "btn", onclick: () => showView("new") }, "Create the first one")));
    return;
  }
  list.replaceChildren(...songs.map((s) => {
    const deployed = el("span", { class: "badge" }, el("span", { class: "dot " + (s.deployed ? "ok" : "") }),
      s.deployed ? "In the game" : "Not in the game");
    const badges = [
      s.version ? el("span", { class: "badge mono" }, `v${s.version}`) : null,
      deployed,
      s.warnings.length ? el("span", { class: "badge warn", title: s.warnings.join("\n") },
        `${s.warnings.length} warning${s.warnings.length > 1 ? "s" : ""}`) : null,
    ];
    return el("article", { class: "song" },
      el("div", { class: "art", html: s.cover ? "" : ICON.music },
        s.cover ? el("img", { src: `/api/songs/${s.name}/cover`, alt: "", loading: "lazy" }) : null),
      el("div", { class: "song-body" },
        el("span", { class: "song-title" }, s.title), el("span", { class: "song-artist" }, s.artist),
        el("div", { class: "badges" }, badges),
        el("span", { class: "faint small" }, `Generated ${new Date(s.built * 1000).toLocaleString("en-US")}`),
        el("div", { class: "song-actions" },
          el("button", { class: "btn sm", disabled: !s.report, onclick: () => showReport(s) }, "Report"),
          el("button", { class: "btn sm", onclick: async (e) => {
            e.target.disabled = true;
            try {
              const r = await api(`/api/songs/${s.name}/deploy`, { method: "POST" });
              toast(`Copied as ${r.deployed}. Go back to the game's title screen.`);
              loadSongs();
            } catch (err) { toast(err.message); e.target.disabled = false; }
          } }, s.deployed ? "Copy again" : "Copy to the game"))));
  }));
}

async function showReport(s) {
  $("#report-title").textContent = `${s.artist} — ${s.title}`;
  $("#report-text").textContent = await api(`/api/songs/${s.name}/report`);
  $("#report").showModal();
}

// ------------------------------------------------------------------ startup
loadStatus();
loadTones();
updateDeployHint();
api("/api/jobs/current").then((j) => {
  if (j.id && j.kind === "build") { state.running = true; updateBuildButton(); resetProgress(); followBuild(j.id); }
});
