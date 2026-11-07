"use strict";
// Web local del generador de CDLC: sin dependencias, habla con cdlc/web/app.py.

const $ = (s) => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) e.append(k instanceof Node ? k : document.createTextNode(k ?? ""));
  return e;
};

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const body = (r.headers.get("content-type") || "").includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(body?.detail || body || r.statusText);
  return body;
}

// ------------------------------------------------------------------ pestañas
for (const b of document.querySelectorAll(".tab")) {
  b.addEventListener("click", () => showView(b.dataset.view));
}
function showView(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  if (name === "songs") loadSongs();
  if (name === "env") loadEnv();
}

// ------------------------------------------------------------------ entorno
async function loadStatus() {
  const s = await api("/api/status");
  const warn = $("#warnings");
  warn.replaceChildren(...s.checks.filter((c) => !c.ok && c.key !== "tones" && c.key !== "rsbuild")
    .map((c) => el("div", { class: "warn" }, `Falta: ${c.help}`)));
  return s;
}

async function loadEnv() {
  const [s, t] = await Promise.all([loadStatus(), api("/api/tones")]);
  $("#checks").replaceChildren(...s.checks.map((c) => el("li", {},
    el("span", { class: c.ok ? "ok" : "bad" }, c.ok ? "✓" : "✗"),
    el("span", {}, el("strong", {}, c.key), " — ", c.ok ? String(c.value) : c.help))));
  $("#tone-summary").textContent = t.total
    ? `${t.total} tonos. Por categoría: ` + Object.entries(t.categories).map(([k, v]) => `${k} ${v}`).join(", ")
    : "No hay biblioteca de tonos todavía.";
}

$("#btn-tones").addEventListener("click", async () => {
  const log = $("#tones-log");
  log.hidden = false; log.textContent = "";
  try {
    const { job_id } = await api("/api/tones/rebuild", { method: "POST" });
    follow(job_id, (ev) => {
      if (ev.type === "log" || ev.type === "step") log.textContent += (ev.line || ev.title) + "\n";
      if (ev.type === "error") log.textContent += `ERROR: ${ev.message}\n${ev.detail || ""}`;
      if (ev.type === "done") { log.textContent += `Listo: ${ev.result.total} tonos.\n`; loadEnv(); }
    });
  } catch (e) { log.textContent = e.message; }
});

// ------------------------------------------------------------------ nueva canción
const state = { upload: null, info: null, audio: null };
const GM = { 24: "Guitarra de nailon", 25: "Guitarra acústica", 26: "Guitarra jazz", 27: "Guitarra limpia",
  28: "Guitarra apagada", 29: "Guitarra overdrive", 30: "Guitarra distorsión", 31: "Armónicos",
  32: "Bajo acústico", 33: "Bajo (dedos)", 34: "Bajo (púa)", 35: "Bajo fretless", 36: "Bajo slap", 37: "Bajo slap 2",
  38: "Bajo synth", 39: "Bajo synth 2" };
const NOTE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const TONE_NAMES = { clean: "limpio", overdrive: "overdrive", crunch: "crunch", distortion: "distorsión",
  highgain: "high gain", acoustic: "acústico", fuzz: "fuzz", auto: "automático" };
// Sonidos de la pista y dónde cambia ("distorsión → limpio (c198) → …"), según la tab
function toneText(t) {
  const segs = t.tone_segments || [];
  if (t.is_percussion || !segs.length) return "—";
  return segs.map((s, i) => (TONE_NAMES[s.category] || s.category) + (i ? ` (c${s.bar})` : "")).join(" → ");
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
  $("#chosen-tab").textContent = `${file.name} — leyendo…`;
  $("#drop-tab").classList.remove("done");
  const fd = new FormData(); fd.append("tab", file);
  try {
    const r = await api("/api/inspect", { method: "POST", body: fd });
    state.upload = r.upload_id; state.info = r.info;
    $("#chosen-tab").textContent = file.name;
    $("#drop-tab").classList.add("done");
    renderSetup(r);
  } catch (e) {
    $("#chosen-tab").textContent = e.message;
    state.upload = null; $("#setup").hidden = true;
  }
  updateBuildButton();
});

setupDrop("#drop-audio", "#file-audio", (file) => {
  state.audio = file;
  $("#chosen-audio").textContent = `${file.name} (${(file.size / 1048576).toFixed(1)} MB)`;
  $("#drop-audio").classList.add("done");
  updateBuildButton();
});

function renderSetup(r) {
  const { info, auto } = r;
  $("#auto-desc").textContent = `Elección automática: ${auto.description}`;
  const rows = info.tracks.map((t) => {
    const usable = !t.is_percussion && t.bars_with_notes > 0;
    const tuning = (t.tuning || []).map((m) => NOTE[m % 12]).join(" ");
    return el("tr", { class: usable ? "" : "dim" },
      el("td", {}, t.name), el("td", { class: "c" }, t.strings), el("td", {}, t.is_percussion ? "—" : tuning),
      el("td", { class: "c" }, t.single_notes), el("td", { class: "c" }, t.chords),
      el("td", { class: "c" }, `${Math.round(100 * t.bars_with_notes / Math.max(1, info.bars))} %`),
      el("td", {}, t.is_percussion ? "Batería" : (GM[t.program] || `MIDI ${t.program}`)),
      el("td", { class: "tones", title: "Cambios de sonido que marca la tab: cada uno será un cambio de tono en el juego" }, toneText(t)),
      el("td", { class: "c" }, usable ? el("input", { type: "radio", name: "lead", value: t.index, checked: t.index === auto.lead }) : ""),
      el("td", { class: "c" }, usable ? el("input", { type: "radio", name: "rhythm", value: t.index, checked: t.index === auto.rhythm }) : ""));
  });
  $("#tracks tbody").replaceChildren(...rows);
  document.querySelector('input[name=rhythm][value=none]').checked = auto.rhythm == null;
  $("#m-artist").value = info.artist || "";
  $("#m-title").value = info.title || "";
  $("#m-album").value = info.album || "";
  $("#m-year").value = "";
  $("#setup").hidden = false;
}

async function loadTones() {
  try {
    const t = await api("/api/tones");
    const sel = $("#m-tone");
    for (const [k, n] of Object.entries(t.categories)) {
      if (n) sel.append(el("option", { value: k }, `${k} (${n} tonos)`));
    }
  } catch { /* sin biblioteca: solo "automático" */ }
}

function updateBuildButton() {
  $("#btn-build").disabled = !(state.upload && state.audio) || state.running;
}

$("#btn-build").addEventListener("click", async () => {
  const fd = new FormData();
  fd.append("upload_id", state.upload);
  fd.append("audio", state.audio);
  fd.append("lead", document.querySelector("input[name=lead]:checked")?.value ?? "");
  fd.append("rhythm", document.querySelector("input[name=rhythm]:checked")?.value ?? "none");
  for (const k of ["artist", "title", "album", "year", "tone"]) fd.append(k, $(`#m-${k}`).value);
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
    showResult(false, e.message);
    state.running = false; updateBuildButton();
  }
});

// ------------------------------------------------------------------ progreso
const STEP_NAMES = ["Pistas y datos de la canción", "Audio → WAV", "Línea de tiempo de la tab",
  "Alineación tab ↔ audio", "XML, audio Wwise y empaquetado", "Copia a dlc/"];

function resetProgress() {
  $("#progress").hidden = false;
  $("#result").replaceChildren();
  $("#log").textContent = "";
  $("#steps").replaceChildren(...STEP_NAMES.map((name, i) => el("li", { class: "pending", "data-n": i },
    el("span", { class: "dot" }, "○"), el("span", { class: "name" }, name), el("span", { class: "time" }))));
  $("#progress").scrollIntoView({ behavior: "smooth", block: "start" });
}

function setStep(li, cls, symbol) {
  li.className = cls; li.querySelector(".dot").textContent = symbol;
}

function showResult(ok, ...content) {
  $("#result").replaceChildren(el("div", { class: `msg ${ok ? "ok" : "bad"}` }, ...content));
}

function followBuild(jobId) {
  let current = null, since = 0;
  const finishCurrent = (t, cls = "done", sym = "✓") => {
    if (!current) return;
    setStep(current, cls, sym);
    current.querySelector(".time").textContent = `${(t - since).toFixed(1)} s`;
  };
  follow(jobId, (ev) => {
    if (ev.type === "step") {
      finishCurrent(ev.t);
      current = document.querySelector(`#steps li[data-n="${ev.n}"]`);
      since = ev.t;
      if (current) {
        setStep(current, "running", "●");
        current.querySelector(".name").textContent = ev.title;
      }
      $("#log").textContent += `[${ev.n}/${ev.of}] ${ev.title}\n`;
    } else if (ev.type === "log") {
      $("#log").textContent += `      ${ev.line}\n`;
    } else if (ev.type === "done") {
      finishCurrent(ev.t);
      const r = ev.result;
      const extra = [];
      if (r.gaps.length) extra.push(el("p", {}, `Audio que no está en la tab: ${r.gaps.map(([b, s]) => `${s} s antes del compás ${b}`).join(", ")}.`));
      if (r.suspicious_bars) extra.push(el("p", {}, `${r.suspicious_bars} compases con un tempo raro: revisa el informe.`));
      showResult(true,
        el("strong", {}, `${r.meta.artist} - ${r.meta.title} listo.`),
        el("p", {}, r.deployed ? "Copiado a dlc/. Vuelve a la pantalla de título del juego para que lo cargue." : `Sin copiar: ${r.psarc}`),
        el("p", { class: "hint" }, `${r.tracks} · tono: ${r.tone}`), ...extra);
      state.running = false; updateBuildButton();
    } else if (ev.type === "error") {
      finishCurrent(ev.t, "error", "✗");
      showResult(false, el("strong", {}, ev.message));
      if (ev.detail) $("#log").textContent += `\n${ev.detail}`;
      $("#log-box").open = true;
      state.running = false; updateBuildButton();
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

// ------------------------------------------------------------------ mis canciones
async function loadSongs() {
  const list = $("#song-list");
  const songs = await api("/api/songs");
  if (!songs.length) { list.replaceChildren(el("p", { class: "empty" }, "Todavía no hay canciones generadas.")); return; }
  list.replaceChildren(...songs.map((s) => {
    const status = el("span", { class: "hint" }, s.deployed ? `En el juego: ${s.deployed}` : "No está en dlc/");
    return el("div", { class: "card" },
      el("div", { class: "art" }, s.cover ? el("img", { src: `/api/songs/${s.name}/cover`, alt: "" }) : "♪"),
      el("div", { class: "body" },
        el("span", { class: "title" }, s.title), el("span", {}, s.artist),
        el("span", { class: "hint" }, `Generado ${new Date(s.built * 1000).toLocaleString()}`), status,
        el("div", { class: "row" },
          el("button", { disabled: !s.report, onclick: () => showReport(s.name) }, "Informe"),
          el("button", { onclick: async (e) => {
            e.target.disabled = true;
            try { const r = await api(`/api/songs/${s.name}/deploy`, { method: "POST" });
              status.textContent = `En el juego: ${r.deployed} — vuelve a la pantalla de título`; }
            catch (err) { status.textContent = err.message; }
            e.target.disabled = false;
          } }, s.deployed ? "Volver a desplegar" : "Desplegar"))));
  }));
}

async function showReport(name) {
  $("#report-text").textContent = await api(`/api/songs/${name}/report`);
  $("#report").showModal();
}

// ------------------------------------------------------------------ inicio
loadStatus();
loadTones();
api("/api/jobs/current").then((j) => {
  if (j.id && j.kind === "build") { state.running = true; updateBuildButton(); resetProgress(); followBuild(j.id); }
});
