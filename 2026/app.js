"use strict";

const TYPES = [
  "TRE",
  "Hybrid",
  "Required Hybrid",
  "Pair Acrobatic",
  "Team Acrobatic",
  "Acrobatic",
  "Sustained Surface Connection",
  "Solo Hybrid (DoD)",
  "Duet Hybrid (DoD)",
  "Team Hybrid (DoD)",
  "Team Choreography Hybrid (no DoD)",
];

// Age-category ordering for the routine dropdown.
const AGE_GROUPS = [
  { match: (a) => a === "Senior/Junior", label: "Senior / Junior" },
  { match: (a) => a === "Youth (13-15)", label: "Youth (13–15)" },
  { match: (a) => a === "12U/Youth (13-15)", label: "12U / Youth" },
  { match: (a) => a === "12U", label: "12U" },
  { match: () => true, label: "Other" },
];

const state = {
  routines: {},  // name -> spec
  current: null, // routine name
  file: null,    // File chosen on step 1 (single-card mode)
  files: [],     // all files chosen on step 1 (2+ = batch mode)
  previewUrl: null,
  batch: null,   // { routine, items: [...], current: id } when checking several cards
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// ---------- helpers ----------------------------------------------------
function isHybrid(type) {
  return (type || "").toLowerCase().includes("hybrid");
}
function usesGroup(type) {
  const t = (type || "").toLowerCase();
  return t === "acrobatic" || t === "team acrobatic";
}
function fmtTime(sec) {
  if (sec == null) return "";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
function parseTime(str) {
  const v = (str || "").trim().replace(",", ":").replace(".", ":");
  if (!v) return null;
  if (v.includes(":")) {
    const [m, s] = v.split(":");
    return (parseInt(m, 10) || 0) * 60 + (parseInt(s, 10) || 0);
  }
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : null;
}
const STEP_OF = { "view-setup": 1, "view-entry": 2, "view-report": 3 };
function show(viewId) {
  $$(".view").forEach((v) => v.classList.toggle("hidden", v.id !== viewId));
  const step = STEP_OF[viewId] || 1;
  $$(".stepper li").forEach((li) => {
    const n = Number(li.dataset.step);
    li.classList.toggle("done", n < step);
    li.classList.toggle("active", n === step);
  });
  if (viewId === "view-entry") {
    requestAnimationFrame(() =>
      $$("#rows textarea.codes").forEach((ta) => {
        ta.style.height = "auto";
        ta.style.height = ta.scrollHeight + "px";
      })
    );
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}
function toast(msg) {
  let el = $(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 1800);
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// ---------- step 1 : routine + file --------------------------------
// Says, under the "Enter the card" button, what is still missing.
let routinesLoaded = false;
function updateStartHint() {
  const hint = $("#start-hint");
  if (!hint) return;
  const sel = $("#routine-select");
  const missing = routinesLoaded && !sel.value;
  sel.closest(".field").classList.toggle("needs-value", missing);
  if (!routinesLoaded) {
    hint.textContent = "Loading the routines… (the first visit can take up to 30 seconds)";
  } else if (missing) {
    hint.textContent = "To continue, choose the Routine type above. The coach card file is optional — without one you type the elements in yourself.";
  } else {
    hint.textContent = "";
  }
  hint.classList.toggle("hidden", !hint.textContent);
}

async function loadRoutines() {
  updateStartHint();
  const res = await fetch("/api/routines");
  const data = await res.json();
  if (!data.ok) {
    document.body.insertAdjacentHTML(
      "afterbegin",
      `<div class="err-banner">Failed to load routines: ${data.error || "?"}</div>`
    );
    return;
  }
  const sel = $("#routine-select");
  const buckets = AGE_GROUPS.map(() => []);
  data.routines.forEach((r) => {
    state.routines[r.name] = r;
    const gi = AGE_GROUPS.findIndex((g) => g.match(r.age_group));
    buckets[gi === -1 ? AGE_GROUPS.length - 1 : gi].push(r);
  });
  buckets.forEach((list, i) => {
    if (!list.length) return;
    const og = document.createElement("optgroup");
    og.label = AGE_GROUPS[i].label;
    list.forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r.name;
      opt.textContent = r.name;
      og.appendChild(opt);
    });
    sel.appendChild(og);
  });
  routinesLoaded = true;
  updateStartHint();
}

function renderRoutineInfo(name) {
  const box = $("#routine-info");
  const spec = state.routines[name];
  if (!spec) {
    box.classList.add("hidden");
    return;
  }
  const els = Object.entries(spec.elements || {})
    .map(([k, v]) => `<li>${v} × <b>${k}</b></li>`)
    .join("");
  const rules = (spec.rules || []).map((r) => `<li>${escapeHtml(r)}</li>`).join("");
  box.innerHTML = `
    <div class="kv">
      <span><b>Age group</b> ${spec.age_group}</span>
      <span><b>Total elements</b> ${spec.total_required_elements}</span>
    </div>
    <h3>Required elements</h3>
    <ul>${els || "<li>—</li>"}</ul>
    ${rules ? `<h3>Specific rules</h3><ul>${rules}</ul>` : ""}
  `;
  box.classList.remove("hidden");
}

function addFiles(list) {
  const have = new Set(state.files.map((f) => f.name + "|" + f.size));
  Array.from(list || []).forEach((f) => {
    if (!have.has(f.name + "|" + f.size)) state.files.push(f);
  });
  refreshFiles();
}

function refreshFiles() {
  const n = state.files.length;
  setFile(n === 1 ? state.files[0] : null, true);
  const ul = $("#file-list");
  const txt = $(".dnd-text");
  if (!txt.dataset.orig) txt.dataset.orig = txt.innerHTML;
  if (n > 1) {
    txt.classList.remove("hidden");
    txt.innerHTML = `<strong>${n} cards selected</strong> — drop or click to add more<br><small class="muted">same event, read one after the other</small>`;
    $("#dropzone").classList.add("has-file");
    ul.innerHTML = "";
    state.files.forEach((f, i) => {
      const li = document.createElement("li");
      li.innerHTML = `<span></span><button type="button" title="Remove">×</button>`;
      li.firstChild.textContent = f.name;
      li.lastChild.addEventListener("click", () => { state.files.splice(i, 1); refreshFiles(); });
      ul.appendChild(li);
    });
    ul.classList.remove("hidden");
  } else {
    ul.classList.add("hidden");
    ul.innerHTML = "";
    txt.innerHTML = txt.dataset.orig;
    if (n === 1) txt.classList.add("hidden");
  }
  const btn = $("#btn-start");
  btn.textContent = n > 1 ? `Read the ${n} cards →` : "Enter the card →";
}

function setFile(file, fromList) {
  if (!fromList) state.files = file ? [file] : [];
  if (state.previewUrl) {
    URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = null;
  }
  state.file = file || null;
  const nameEl = $("#dnd-name");
  const fileLine = $(".dnd-file");
  const textLine = $(".dnd-text");
  const dz = $("#dropzone");
  if (file) {
    nameEl.textContent = file.name;
    fileLine.classList.remove("hidden");
    textLine.classList.add("hidden");
    dz.classList.add("has-file");
  } else {
    fileLine.classList.add("hidden");
    textLine.classList.remove("hidden");
    dz.classList.remove("has-file");
    $("#file-input").value = "";
  }
  if (!fromList) refreshFiles();
}

function wireDropzone() {
  const dz = $("#dropzone");
  const input = $("#file-input");
  dz.addEventListener("click", (e) => {
    if (e.target.closest("#dnd-clear")) return;
    input.click();
  });
  dz.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      input.click();
    }
  });
  input.addEventListener("change", () => {
    addFiles(input.files);
    input.value = "";
  });

  // Camera capture — mobile browsers open the rear camera directly for a
  // file input with capture="environment".
  const cam = $("#camera-input");
  const camBtn = $("#btn-camera");
  if (cam && camBtn) {
    camBtn.addEventListener("click", () => cam.click());
    cam.addEventListener("change", () => {
      if (cam.files[0]) {
        // a phone photo is always "image.jpg": give each one its own name
        const f = cam.files[0];
        const named = new File([f], `photo-${state.files.length + 1}-${Date.now() % 100000}.jpg`, { type: f.type });
        addFiles([named]);
      }
      cam.value = "";
    });
  }

  $("#dnd-clear").addEventListener("click", (e) => {
    e.stopPropagation();
    setFile(null);
  });
  ["dragenter", "dragover"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      dz.classList.add("drag");
    })
  );
  ["dragleave", "dragend"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      if (ev === "dragleave" && dz.contains(e.relatedTarget)) return;
      dz.classList.remove("drag");
    })
  );
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("drag");
    if (e.dataTransfer.files && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  });
}

// ---------- step 2 : review & edit --------------------------------
function makeRow({ type = "Hybrid", group = "", code = "", codes = "", dd = "", flag = false } = {}) {
  const row = document.createElement("div");
  row.className = "row" + (flag ? " row-auto" : "");

  const num = document.createElement("span");
  num.className = "num";

  const typeSel = document.createElement("select");
  typeSel.className = "type";
  TYPES.forEach((t) => {
    const o = document.createElement("option");
    o.value = t;
    o.textContent = t;
    if (t === type) o.selected = true;
    typeSel.appendChild(o);
  });

  const grpSel = document.createElement("select");
  grpSel.className = "grp";
  ["", "A", "B", "C", "P"].forEach((g) => {
    const o = document.createElement("option");
    o.value = g;
    o.textContent = g || "—";
    if (g === group) o.selected = true;
    grpSel.appendChild(o);
  });

  const codesWrap = document.createElement("div");
  codesWrap.className = "codes-wrap";
  const codesInput = document.createElement("textarea");
  codesInput.className = "codes";
  codesInput.rows = 1;
  codesInput.spellcheck = false;
  codesInput.value = codes || code || "";
  const grow = () => {
    codesInput.style.height = "auto";
    codesInput.style.height = codesInput.scrollHeight + "px";
  };
  codesInput.addEventListener("input", grow);
  const hint = document.createElement("small");
  hint.className = "muted";
  codesWrap.appendChild(codesInput);
  codesWrap.appendChild(hint);

  const ddInput = document.createElement("input");
  ddInput.className = "dd";
  ddInput.type = "text";
  ddInput.inputMode = "decimal";
  ddInput.placeholder = "DD";
  ddInput.title = "Declared DD (from the card) — compared with the catalogue for acrobatics";
  ddInput.value = dd === "" || dd == null ? "" : String(dd);

  const rm = document.createElement("button");
  rm.className = "rm";
  rm.type = "button";
  rm.title = "Remove";
  rm.textContent = "×";
  rm.addEventListener("click", () => row.remove());

  const wrap = (el, text) => {
    const d = document.createElement("div");
    const l = document.createElement("span");
    l.className = "lbl";
    l.textContent = text;
    d.appendChild(l);
    d.appendChild(el);
    return d;
  };

  row.appendChild(num);
  row.appendChild(wrap(typeSel, "Type"));
  row.appendChild(wrap(grpSel, "Group"));
  row.appendChild(codesWrap);
  row.appendChild(wrap(ddInput, "DD"));
  row.appendChild(rm);

  function sync() {
    const t = typeSel.value;
    row.classList.toggle("group-hidden", !usesGroup(t));
    hint.textContent = isHybrid(t)
      ? "codes separated by spaces — e.g. T9b F5a C4+*0.5 A8"
      : usesGroup(t)
        ? "code exactly as on the card (no group letter) — e.g. LH-LiH-ow/2spl-r0.5L-Mov"
        : "one code, exactly as on the card";
  }
  typeSel.addEventListener("change", sync);
  codesInput.addEventListener("input", () => row.classList.remove("row-auto"));
  sync();

  return row;
}

function templateRows(spec) {
  const rows = [];
  const groupCycle = ["A", "B", "C", "P"];
  let gi = 0;
  Object.entries(spec.elements || {}).forEach(([type, count]) => {
    for (let i = 0; i < count; i++) {
      const group =
        usesGroup(type) && spec.name === "Open Acrobatic" ? groupCycle[gi++ % 4] : "";
      rows.push({ type, group });
    }
  });
  if (!rows.length) rows.push({});
  return rows;
}

function fillRows(rowDefs) {
  const host = $("#rows");
  host.innerHTML = "";
  rowDefs.forEach((d) => host.appendChild(makeRow(d)));
  // size the code textareas to their content once they're in the DOM
  requestAnimationFrame(() => {
    $$("#rows textarea.codes").forEach((ta) => {
      ta.style.height = "auto";
      ta.style.height = ta.scrollHeight + "px";
    });
  });
}

function buildPreview(file) {
  const pane = $("#preview-pane");
  const grid = $("#entry-grid");
  const ext = (file.name.split(".").pop() || "").toLowerCase();
  state.previewUrl = URL.createObjectURL(file);
  let body;
  if (["png", "jpg", "jpeg", "jfif", "webp", "gif", "bmp"].includes(ext)) {
    body = `<img src="${state.previewUrl}" alt="coach card" />`;
  } else if (ext === "pdf") {
    body = `<iframe src="${state.previewUrl}" title="coach card PDF"></iframe>`;
  } else {
    body = `<div class="file-card">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3v5h5M7 3h8l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>
      <span>${escapeHtml(file.name)}</span>
      <small class="muted">No inline preview for this file type</small>
    </div>`;
  }
  const isImg = ["png", "jpg", "jpeg", "jfif", "webp", "gif", "bmp"].includes(ext);
  pane.innerHTML = `
    <div class="preview-head">
      <span>Card${isImg ? " — click to zoom" : ""}</span>
      <button type="button" class="link" id="preview-change">change</button>
    </div>
    <div class="preview-body">${body}</div>`;
  pane.classList.remove("hidden");
  grid.classList.add("has-preview");
  $("#preview-change").addEventListener("click", () => show("view-setup"));
  if (isImg) {
    const im = $(".preview-body img", pane);
    if (im) {
      im.style.cursor = "zoom-in";
      im.addEventListener("click", () => openZoom(state.previewUrl));
    }
  }
}

// ---------- card zoom overlay ------------------------------------
function openZoom(url) {
  const ov = $("#card-zoom");
  const img = $("#card-zoom img");
  img.src = url;
  ov.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  const st = { scale: 1, x: 0, y: 0, rot: 0, drag: null };
  const apply = () => {
    // rotate the card, then fit it to the viewport at scale 1
    const q = ((st.rot % 360) + 360) % 360;
    const nw = img.naturalWidth || 1;
    const nh = img.naturalHeight || 1;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const rw = q === 90 || q === 270 ? nh : nw;
    const rh = q === 90 || q === 270 ? nw : nh;
    const fit = Math.min(vw / rw, vh / rh, 1);
    img.style.transform =
      `translate(${st.x}px, ${st.y}px) scale(${st.scale * fit}) rotate(${st.rot}deg)`;
    img.style.cursor = st.scale > 1 ? "grab" : "zoom-in";
  };
  const reset = () => { st.scale = 1; st.x = 0; st.y = 0; apply(); };
  const rotate = (d) => { st.rot += d; st.x = 0; st.y = 0; st.scale = 1; apply(); };
  if (img.complete) reset();
  else img.onload = reset;

  ov._cleanup = () => {
    ov.classList.add("hidden");
    document.body.style.overflow = "";
    img.src = "";
    img.onload = null;
    ov.onwheel = ov.onmousedown = ov.onmousemove = ov.onmouseup = null;
    ov.ontouchstart = ov.ontouchmove = ov.ontouchend = null;
    $("#card-zoom-rotl").onclick = $("#card-zoom-rotr").onclick = null;
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (e) => {
    if (e.key === "Escape") ov._cleanup();
    else if (e.key === "[") rotate(-90);
    else if (e.key === "]") rotate(90);
  };
  document.addEventListener("keydown", onKey);
  $("#card-zoom-rotl").onclick = () => rotate(-90);
  $("#card-zoom-rotr").onclick = () => rotate(90);

  ov.onwheel = (e) => {
    e.preventDefault();
    const prev = st.scale;
    st.scale = Math.min(8, Math.max(1, st.scale * (e.deltaY < 0 ? 1.18 : 1 / 1.18)));
    // zoom toward cursor
    const r = img.getBoundingClientRect();
    const cx = e.clientX - (r.left + r.width / 2);
    const cy = e.clientY - (r.top + r.height / 2);
    const k = st.scale / prev - 1;
    st.x -= cx * k;
    st.y -= cy * k;
    if (st.scale === 1) { st.x = 0; st.y = 0; }
    apply();
  };
  ov.onmousedown = (e) => {
    if (e.target.closest(".card-zoom-tools")) return;
    if (st.scale === 1 && e.target === img) {
      st.scale = 2.5; apply(); return;
    }
    st.drag = { sx: e.clientX - st.x, sy: e.clientY - st.y };
    img.style.cursor = "grabbing";
  };
  ov.onmousemove = (e) => {
    if (!st.drag) return;
    st.x = e.clientX - st.drag.sx;
    st.y = e.clientY - st.drag.sy;
    apply();
  };
  ov.onmouseup = () => { st.drag = null; apply(); };
  img.ondblclick = reset;

  // ---- touch: one finger pans, two fingers pinch-zoom, tap toggles zoom ----
  let pinch = null;
  let tapT = 0;
  const dist = (t) => Math.hypot(
    t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
  ov.ontouchstart = (e) => {
    if (e.target.closest(".card-zoom-tools")) return;
    if (e.touches.length === 2) {
      pinch = { d: dist(e.touches), s: st.scale };
      st.drag = null;
    } else if (e.touches.length === 1) {
      const now = Date.now();
      if (now - tapT < 300) { reset(); tapT = 0; }
      else {
        tapT = now;
        st.drag = { sx: e.touches[0].clientX - st.x, sy: e.touches[0].clientY - st.y };
      }
    }
  };
  ov.ontouchmove = (e) => {
    if (e.target.closest(".card-zoom-tools")) return;
    e.preventDefault();
    if (pinch && e.touches.length === 2) {
      const k = dist(e.touches) / pinch.d;
      st.scale = Math.min(8, Math.max(1, pinch.s * k));
      if (st.scale === 1) { st.x = 0; st.y = 0; }
      apply();
    } else if (st.drag && e.touches.length === 1) {
      st.x = e.touches[0].clientX - st.drag.sx;
      st.y = e.touches[0].clientY - st.drag.sy;
      apply();
    }
  };
  ov.ontouchend = (e) => {
    if (e.touches.length === 0) { pinch = null; st.drag = null; apply(); }
  };
}

function clearPreview() {
  $("#preview-pane").classList.add("hidden");
  $("#preview-pane").innerHTML = "";
  $("#entry-grid").classList.remove("has-preview");
}

function showOcrText(text, openIt) {
  const pane = $("#preview-pane");
  if (!text || pane.classList.contains("hidden")) return;
  const d = document.createElement("details");
  d.className = "ocr-text";
  if (openIt) d.open = true;
  d.innerHTML = "<summary>Text read from the photo</summary><pre></pre>";
  d.querySelector("pre").textContent = text;
  pane.appendChild(d);
}

let _progTimer = null;
let _progStart = 0;
function showProgress() {
  const el = $("#extract-progress");
  el.classList.remove("hidden");
  $(".progress-fill", el).style.width = "0%";
  $(".progress-label", el).textContent = "Reading the card…";
  _progStart = Date.now();
  clearInterval(_progTimer);
  _progTimer = setInterval(() => {
    const s = Math.floor((Date.now() - _progStart) / 1000);
    $(".progress-time", el).textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }, 1000);
}
function setProgress(pct, label) {
  const el = $("#extract-progress");
  if (typeof pct === "number") $(".progress-fill", el).style.width = Math.max(0, Math.min(100, pct)) + "%";
  if (label) $(".progress-label", el).textContent = label + "…";
}
function hideProgress() {
  clearInterval(_progTimer);
  _progTimer = null;
  $("#extract-progress").classList.add("hidden");
}

function setBanner(html, kind) {
  const b = $("#extract-banner");
  if (!html) {
    b.classList.add("hidden");
    b.innerHTML = "";
    return;
  }
  b.className = "banner " + (kind || "");
  b.innerHTML = html;
  b.classList.remove("hidden");
}

async function startEntry() {
  const name = $("#routine-select").value;
  if (!name) return;
  state.current = name;
  const spec = state.routines[name];
  spec.name = name;
  $("#entry-routine").textContent = `${name} · ${spec.age_group}`;

  setBanner("");
  clearPreview();

  const themeRow = $("#theme-row");
  if (themeRow) themeRow.hidden = !themeRequired(name);

  if (state.files.length > 1) return startBatch(name, spec);
  state.batch = null;
  setBatchNav(null);

  if (!state.file) {
    fillRows(templateRows(spec));
    show("view-entry");
    return;
  }

  // Stay on step 1 behind the progress overlay until extraction finishes.
  setBanner("");
  showProgress();
  const result = await extractFile(state.file, name, setProgress);
  hideProgress();
  const renamed = renamedFromCard(state.file, result);
  if (renamed !== state.file) {
    state.file = renamed;
    state.files = [renamed];
    if (result) result.filename = renamed.name;
    const nameEl = $("#dnd-name");
    if (nameEl) nameEl.textContent = renamed.name;
  }
  loadEntry(result, state.file, spec, name);
}

// Photos taken with the phone camera arrive as "image.jpg" / "IMG_1234.jpg".
// Give them a readable name from what was read on the card (federation / club,
// start order, theme), so they can be told apart in the list and the reports.
const GENERIC_NAME_RE =
  /^(image|img|photo|pxl|dsc|dscn|dcim|capture|camera|cam|screenshot|scan|picture|pic|whatsapp image)?[\s_\-\d().:]*(\.\w+)?$/i;

function renamedFromCard(file, result, taken) {
  if (!file || !result || !result.suggested_name || !GENERIC_NAME_RE.test(file.name)) return file;
  const ext = (file.name.match(/\.\w+$/) || [".jpg"])[0].toLowerCase();
  let base = result.suggested_name, n = 2, name = base + ext;
  while (taken && taken.has(name.toLowerCase())) name = `${base} (${n++})${ext}`;
  try {
    return new File([file], name, { type: file.type, lastModified: file.lastModified });
  } catch (e) {
    return file;                                   // very old browsers: keep the original
  }
}

// Read one card on the server (SSE progress). Never throws.
async function extractFile(file, name, onProgress) {
  let result = null;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 240000);
  try {
    const res = await fetch("/api/extract", {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        Accept: "text/event-stream",
        "X-Filename": encodeURIComponent(file.name),
        "X-Routine": encodeURIComponent(name),
      },
      body: file,
      signal: ctrl.signal,
    });
    if (res.body && (res.headers.get("Content-Type") || "").includes("event-stream")) {
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let j;
        while ((j = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, j).trim();
          buf = buf.slice(j + 2);
          if (!chunk.startsWith("data:")) continue;
          let msg;
          try {
            msg = JSON.parse(chunk.slice(5).trim());
          } catch {
            continue;
          }
          if (msg.done) result = msg.result;
          else if (onProgress) onProgress(msg.pct, msg.label);
        }
      }
    } else {
      result = await res.json();
    }
  } catch (e) {
    result =
      e.name === "AbortError"
        ? { ok: false, notes: ["Reading the file took too long and was stopped. Fill the table in by hand."] }
        : { ok: false, notes: ["Could not reach the server: " + e.message] };
  } finally {
    clearTimeout(timer);
  }
  if (!result) result = { ok: false, notes: ["No response from the reader. Fill the table in by hand."] };
  return result;
}

// Fill step 2 from a reader result and show it.
function loadEntry(result, file, spec, name) {
  const fedInput0 = $("#federation");
  if (fedInput0) fedInput0.value = "";
  const tInput0 = $("#theme");
  if (tInput0) tInput0.value = "";
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  buildPreview(file);
  show("view-entry");

  const notes = (result.notes || []).map((n) => `<li>${escapeHtml(n)}</li>`).join("");
  const got = (result.ok && result.elements) || [];
  const need = spec.total_required_elements || 0;

  // Federation read off the card — pre-filled, always editable. It gates the
  // federation-reserved code check (Phase 1 / Phase 2 acrobatics).
  const fedInput = $("#federation");
  if (fedInput && result.detected_federation) {
    fedInput.value = result.detected_federation;
  }

  // Theme — pre-fill from the card reader when the routine needs one (the
  // field's visibility was already set from the routine name above).
  const tInput = $("#theme");
  if (tInput && themeRequired(state.current) && result.detected_theme && !tInput.value) {
    tInput.value = result.detected_theme;
  }

  if (got.length) {
    const rows = got.map((el) => ({ ...el, flag: true }));
    while (need && rows.length < need) rows.push({}); // blanks to fill in
    fillRows(rows);
    const partial = need && got.length < need;
    setBanner(
      `Read <strong>${got.length}</strong>${need ? " of " + need : ""} element(s) from ` +
        `<strong>${escapeHtml(result.filename || file.name)}</strong>. ` +
        (partial
          ? `The rest is blank — add the missing lines from the card. `
          : "") +
        `Highlighted rows came from the file — check every one against the card.` +
        (notes ? `<ul>${notes}</ul>` : ""),
      partial || result.multi_card ? "warn" : "ok"
    );
  } else {
    fillRows(templateRows(spec));
    setBanner(
      `Couldn't auto-read <strong>${escapeHtml(file.name)}</strong>. ` +
        `Below is the blank <strong>${escapeHtml(name)}</strong> template — ` +
        `fill it in from the card shown alongside.` +
        (notes ? `<ul>${notes}</ul>` : ""),
      "warn"
    );
  }
  showOcrText(result.ocr_text, got.length < need);
}

function buildDeclared() {
  const elements = $$("#rows .row").map((row) => {
    const type = $(".type", row).value;
    const group = $(".grp", row).value.trim();
    const raw = $(".codes", row).value.trim();
    const base = { type };
    const ddv = parseFloat(($(".dd", row)?.value || "").replace(",", "."));
    if (!isNaN(ddv)) base.dd = ddv;
    if (isHybrid(type)) {
      base.codes = raw.split(/\s+/).filter(Boolean).map((c) => ({ code: c }));
    } else if (usesGroup(type)) {
      // Coach card codes have NO group-letter prefix — they start at the
      // construction. The group comes from the ACRO-x column (the dropdown).
      base.code = raw;
      if (group) base.group = group;
    } else {
      base.code = raw;
    }
    return base;
  });
  const fed = ($("#federation")?.value || "").trim().toUpperCase();
  const out = { elements };
  if (fed) out.federation = fed;
  const theme = ($("#theme")?.value || "").trim();
  if (theme) out.theme = theme;
  return out;
}

// Acrobatic Routine + Free Combination must declare a theme on the card.
function themeRequired(name) {
  return /acrobatic|combination|combo/i.test(name || "");
}

async function runCheck() {
  const payload = { routine: state.current, declared: buildDeclared() };
  const btn = $("#btn-check");
  btn.disabled = true;
  btn.textContent = "Analyzing…";
  try {
    const res = await fetch("/api/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.ok) {
      alert("Engine error: " + (data.message || data.error || "unknown"));
      return;
    }
    const it = batchItem();
    if (it) {
      it.declared = payload.declared;
      it.report = data;
      it.reviewed = true;
      it.federation = payload.declared.federation || it.federation;
      renderBatch();
    }
    renderReport(data);
    show("view-report");
    updateReportNav();
  } catch (e) {
    alert("Could not reach the server: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Run analysis";
  }
}

// ---------- step 3 : report --------------------------------------
let lastReportText = "";

// Name of the coach card the report is about (file name), shown on the report.
function reportCardName() {
  const b = state.batch;
  if (b && b.current != null) {
    const it = batchItem(b.current);
    if (it && it.file) return it.file.name;
  }
  return state.file ? state.file.name : "";
}

function renderReport(data) {
  const cardName = reportCardName();
  lastReportText = (cardName ? "Coach card: " + cardName + "\n" : "") + (data.text || "");
  const s = (data.summary || "").toLowerCase();
  const sum = $("#report-summary");
  sum.className = "s-" + s;
  const counts = { pass: 0, info: 0, warn: 0, fail: 0 };
  (data.checks || []).forEach((c) => (counts[c.status] = (counts[c.status] || 0) + 1));
  const careful =
    data.summary === "PASS" && counts.info
      ? ` <span class="careful">— ${counts.info} point${counts.info > 1 ? "s" : ""} to double-check</span>`
      : "";
  sum.innerHTML = `
    ${cardName ? `<span class="card-name">${escapeHtml(cardName)}</span>` : ""}
    ${data.summary}${careful}
    <span class="meta">${data.routine} · ${data.age_group} —
      ${counts.fail} FAIL · ${counts.warn} WARN · ${counts.info} CHECK · ${counts.pass} OK</span>
  `;

  const host = $("#report-checks");
  host.innerHTML = "";
  const badge = { pass: "OK", info: "CHECK", warn: "WARN", fail: "FAIL" };
  const rank = { fail: 0, warn: 1, info: 2, pass: 3 };
  const ordered = (data.checks || [])
    .map((c, idx) => [c, idx])
    .sort((a, b) => (rank[a[0].status] ?? 9) - (rank[b[0].status] ?? 9) || a[1] - b[1])
    .map((pair) => pair[0]);
  ordered.forEach((c) => {
    const div = document.createElement("div");
    div.className = "check c-" + c.status;
    div.dataset.status = c.status;
    div.innerHTML = `
      <div class="badge">${badge[c.status] || "?"}</div>
      <div>
        <div class="label"></div>
        ${c.detail ? '<div class="detail"></div>' : ""}
      </div>`;
    $(".label", div).textContent = c.label;
    if (c.detail) $(".detail", div).textContent = c.detail;
    host.appendChild(div);
  });
  applyFilters();

  const reco = $("#report-reco");
  if ((data.recommendations || []).length) {
    reco.innerHTML =
      "<h3>Recommendations</h3><ul>" +
      data.recommendations.map((r) => `<li>${escapeHtml(r)}</li>`).join("") +
      "</ul>";
  } else {
    reco.innerHTML = "";
  }
}

function applyFilters() {
  const on = new Set($$(".f-status:checked").map((c) => c.value));
  $$("#report-checks .check").forEach((el) => {
    el.style.display = on.has(el.dataset.status) ? "" : "none";
  });
}

// ---------- wiring -----------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  loadRoutines();
  wireDropzone();
  show("view-setup");

  $("#routine-select").addEventListener("change", (e) => {
    const name = e.target.value;
    renderRoutineInfo(name);
    $("#btn-start").disabled = !name;
    updateStartHint();
  });

  $("#btn-start").addEventListener("click", startEntry);
  $("#btn-add-row").addEventListener("click", () => $("#rows").appendChild(makeRow()));
  $("#btn-back").addEventListener("click", () => (state.batch ? showBatch() : show("view-setup")));
  $("#btn-check").addEventListener("click", runCheck);
  $("#btn-edit").addEventListener("click", () => show("view-entry"));
  const restart = () => {
    state.batch = null;
    setBatchNav(null);
    setFile(null);
    show("view-setup");
  };
  $("#btn-restart").addEventListener("click", restart);
  $("#btn-batch-new").addEventListener("click", () => {
    if (!state.batch || confirmLeaveBatch()) restart();
  });
  $("#btn-next-card").addEventListener("click", () => {
    const nxt = nextToReview();
    if (nxt) openBatchItem(nxt.id); else showBatch();
  });
  $("#btn-batch-add").addEventListener("click", () => $("#batch-add-input").click());
  $("#batch-add-input").addEventListener("change", (e) => {
    batchAddFiles(e.target.files);
    e.target.value = "";
  });
  $("#btn-batch-auto").addEventListener("click", batchAutoCheck);
  $("#btn-batch-copy").addEventListener("click", () => copyText(batchSummaryText(), "Summary copied"));
  $("#btn-batch-copy-all").addEventListener("click", () => copyText(batchAllReportsText(), "All reports copied"));
  $("#btn-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(lastReportText);
      toast("Report copied");
    } catch {
      toast("Copy failed");
    }
  });
  $$(".f-status").forEach((c) => c.addEventListener("change", applyFilters));

  const zoom = $("#card-zoom");
  const closeZoom = () => zoom._cleanup && zoom._cleanup();
  $("#card-zoom-close").addEventListener("click", closeZoom);
  zoom.addEventListener("click", (e) => {
    if (e.target === zoom) closeZoom();
  });
});


// =================================================================
// SEVERAL CARDS OF ONE EVENT ("batch")
// Cards are read one at a time in the background (OCR is CPU-bound);
// each one is reviewed and analysed with the normal step 2 / step 3.
// =================================================================
let _batchSeq = 0;

function batchItem(id) {
  const b = state.batch;
  if (!b) return null;
  const want = id == null ? b.current : id;
  return b.items.find((x) => x.id === want) || null;
}

function startBatch(name, spec) {
  state.batch = { routine: name, spec, items: [], current: null, running: false };
  batchAddFiles(state.files);
  showBatch();
}

function batchAddFiles(list) {
  const b = state.batch;
  Array.from(list || []).forEach((file) => {
    b.items.push({ id: ++_batchSeq, file, status: "wait", pct: 0, label: "",
                   result: null, declared: null, report: null, reviewed: false,
                   federation: "", thumb: /^image\//.test(file.type) ? URL.createObjectURL(file) : null });
  });
  renderBatch();
  runBatchQueue();
}

async function runBatchQueue() {
  const b = state.batch;
  if (!b || b.running) return;
  b.running = true;
  try {
    for (;;) {
      if (state.batch !== b) return;            // user started over
      const it = b.items.find((x) => x.status === "wait");
      if (!it) break;
      it.status = "reading";
      renderBatch();
      const res = await extractFile(it.file, b.routine, (pct, label) => {
        it.pct = pct || 0;
        it.label = label || "";
        renderBatchRow(it);
      });
      const taken = new Set(b.items.filter((x) => x !== it).map((x) => x.file.name.toLowerCase()));
      it.file = renamedFromCard(it.file, res, taken);
      if (res) res.filename = it.file.name;
      it.result = res;
      it.federation = (res && res.detected_federation) || "";
      it.status = res && res.ok && (res.elements || []).length ? "read" : "error";
      renderBatch();
    }
  } finally {
    b.running = false;
  }
}

function showBatch() {
  const b = state.batch;
  $("#batch-routine").textContent = `${b.routine} · ${b.spec.age_group}`;
  setBatchNav(null);
  renderBatch();
  show("view-batch");
}

function statusCell(it) {
  if (it.status === "wait") return `<span class="bstat wait">waiting</span>`;
  if (it.status === "reading") return `<span class="bstat wait">reading… ${Math.round(it.pct || 0)}%</span>`;
  const n = ((it.result && it.result.elements) || []).length;
  const need = state.batch.spec.total_required_elements || 0;
  if (it.status === "error") return `<span class="bstat err">not read — fill in by hand</span>`;
  const multi = it.result && it.result.multi_card
    ? ` <span class="bstat err" title="This photo may show more than one coach card — check the rows when you review it">more than one card?</span>`
    : "";
  return `<span class="bstat read">${n}${need ? "/" + need : ""} elements</span>${multi}`;
}

function resultCell(it) {
  if (!it.report) return it.status === "read" || it.status === "error" ? `<span class="muted">to review</span>` : "";
  const s = it.report.summary || "?";
  const c = { pass: 0, warn: 0, fail: 0 };
  (it.report.checks || []).forEach((x) => (c[x.status] = (c[x.status] || 0) + 1));
  const sub = s === "PASS" ? "" : `<span class="sub">${c.fail} F · ${c.warn} W</span>`;
  const tag = it.reviewed ? "" : ` <span class="muted" title="Analysed exactly as read, not reviewed">(not reviewed)</span>`;
  return `<span class="bstat link ${s.toLowerCase()}" data-report="${it.id}">${s}${sub}</span>${tag}`;
}

function renderBatchRow(it) {
  const tr = document.querySelector(`#batch-rows tr[data-id="${it.id}"]`);
  if (!tr) return renderBatch();
  tr.children[3].innerHTML = statusCell(it);
}

function renderBatch() {
  const b = state.batch;
  if (!b) return;
  const tb = $("#batch-rows");
  tb.innerHTML = "";
  b.items.forEach((it, i) => {
    const tr = document.createElement("tr");
    tr.dataset.id = it.id;
    if (b.current === it.id) tr.classList.add("current");
    const canOpen = it.status === "read" || it.status === "error";
    tr.innerHTML = `
      <td>${i + 1}</td>
      <td class="file">${it.thumb ? `<img class="thumb" src="${it.thumb}" alt="">` : ""}<span></span></td>
      <td>${escapeHtml(it.federation || "—")}</td>
      <td>${statusCell(it)}</td>
      <td>${resultCell(it)}</td>
      <td><button class="${it.reviewed ? "ghost" : "primary"}" ${canOpen ? "" : "disabled"}>${it.reviewed ? "Edit" : "Review"}</button></td>`;
    tr.querySelector("td.file span").textContent = it.file.name;
    tr.querySelector("button").addEventListener("click", () => openBatchItem(it.id));
    const rep = tr.querySelector("[data-report]");
    if (rep) rep.addEventListener("click", () => openBatchReport(it.id));
    tb.appendChild(tr);
  });
  const done = b.items.filter((x) => x.status === "read" || x.status === "error").length;
  const rev = b.items.filter((x) => x.reviewed).length;
  const res = b.items.filter((x) => x.report);
  const cnt = (s) => res.filter((x) => x.report.summary === s).length;
  $("#batch-progress").innerHTML =
    `<b>${done}/${b.items.length}</b> read · <b>${rev}</b> reviewed` +
    (res.length ? ` · ${cnt("PASS")} PASS · ${cnt("WARN")} WARN · ${cnt("FAIL")} FAIL` : "") +
    (b.items.some((x) => x.status === "wait" || x.status === "reading")
      ? " · <i>reading in the background (about 30 s per photo)…</i>" : "");
}

function setBatchNav(it) {
  $$(".batch-nav").forEach((nav) => {
    if (!it) { nav.classList.add("hidden"); nav.innerHTML = ""; return; }
    const b = state.batch;
    const pos = b.items.indexOf(it) + 1;
    nav.innerHTML = `<button type="button" class="ghost">← All cards</button>
      <span>Card ${pos} of ${b.items.length} —</span> <b></b>`;
    nav.querySelector("b").textContent = it.file.name;
    nav.querySelector("button").addEventListener("click", showBatch);
    nav.classList.remove("hidden");
  });
}

function declaredToRows(declared) {
  return (declared.elements || []).map((el) => ({
    type: el.type,
    group: el.group || "",
    code: el.code || "",
    codes: el.codes ? el.codes.map((c) => c.code).join(" ") : "",
    dd: el.dd == null ? "" : el.dd,
  }));
}

function openBatchItem(id) {
  const b = state.batch;
  const it = batchItem(id);
  if (!it) return;
  b.current = id;
  state.current = b.routine;
  setBanner("");
  clearPreview();
  const themeRow = $("#theme-row");
  if (themeRow) themeRow.hidden = !themeRequired(b.routine);
  $("#entry-routine").textContent = `${b.routine} · ${b.spec.age_group}`;
  if (it.declared) {
    // already reviewed: restore what the user typed
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    buildPreview(it.file);
    show("view-entry");
    fillRows(declaredToRows(it.declared));
    $("#federation").value = it.declared.federation || "";
    if ($("#theme")) $("#theme").value = it.declared.theme || "";
    setBanner("Already analysed — your corrections are kept. Change anything and run the analysis again.", "info");
    showOcrText(it.result && it.result.ocr_text, false);
  } else {
    loadEntry(it.result || { ok: false, notes: [] }, it.file, b.spec, b.routine);
  }
  setBatchNav(it);
}

function openBatchReport(id) {
  const it = batchItem(id);
  if (!it || !it.report) return;
  state.batch.current = id;
  renderReport(it.report);
  show("view-report");
  setBatchNav(it);
  updateReportNav();
}

function nextToReview() {
  const b = state.batch;
  if (!b) return null;
  const i = b.items.findIndex((x) => x.id === b.current);
  const order = b.items.slice(i + 1).concat(b.items.slice(0, i + 1));
  return order.find((x) => !x.reviewed && (x.status === "read" || x.status === "error")) || null;
}

function updateReportNav() {
  const btn = $("#btn-next-card");
  const restart = $("#btn-restart");
  if (!state.batch) {
    btn.classList.add("hidden");
    restart.classList.remove("hidden");
    return;
  }
  setBatchNav(batchItem());
  restart.classList.add("hidden");
  btn.classList.remove("hidden");
  const nxt = nextToReview();
  const waiting = state.batch.items.some((x) => x.status === "wait" || x.status === "reading");
  btn.textContent = nxt ? "Next card to review →" : waiting ? "All cards (others still reading) →" : "All cards →";
}

async function batchAutoCheck() {
  const b = state.batch;
  const todo = b.items.filter((x) => x.status === "read" && !x.reviewed);
  if (!todo.length) return toast("Nothing to check — review the cards or wait for the reading to finish");
  const btn = $("#btn-batch-auto");
  btn.disabled = true;
  for (const it of todo) {
    const r = it.result || {};
    const declared = {
      elements: (r.elements || []).map((el) => {
        const o = { type: el.type };
        if (el.group) o.group = el.group;
        if (isHybrid(el.type)) o.codes = String(el.code || el.codes || "").split(/\s+/).filter(Boolean).map((c) => ({ code: c }));
        else o.code = el.code || "";
        if (el.dd != null) o.dd = el.dd;
        return o;
      }),
    };
    if (r.detected_federation) declared.federation = r.detected_federation;
    if (r.detected_theme && themeRequired(b.routine)) declared.theme = r.detected_theme;
    try {
      const res = await fetch("/api/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ routine: b.routine, declared }),
      });
      const data = await res.json();
      if (data.ok) { it.report = data; it.declared = declared; it.reviewed = false; }
    } catch (e) { /* leave it to review */ }
    renderBatch();
  }
  btn.disabled = false;
}

function batchSummaryText() {
  const b = state.batch;
  const lines = [`${b.routine} — ${b.items.length} coach card(s)`, ""];
  b.items.forEach((it, i) => {
    const r = it.report;
    let s = r ? r.summary : it.status === "wait" || it.status === "reading" ? "not read yet" : "not analysed";
    if (r) {
      const c = { fail: 0, warn: 0 };
      (r.checks || []).forEach((x) => (c[x.status] = (c[x.status] || 0) + 1));
      s += ` (${c.fail} FAIL, ${c.warn} WARN)` + (it.reviewed ? "" : " — not reviewed");
    }
    lines.push(`${i + 1}. ${it.file.name}${it.federation ? " [" + it.federation + "]" : ""}: ${s}`);
  });
  return lines.join("\n");
}

function batchAllReportsText() {
  const parts = [batchSummaryText()];
  state.batch.items.forEach((it, i) => {
    if (it.report && it.report.text)
      parts.push(`\n${"=".repeat(60)}\n${i + 1}. ${it.file.name}${it.reviewed ? "" : " (not reviewed)"}\n${it.report.text}`);
  });
  return parts.join("\n");
}

async function copyText(txt, ok) {
  try { await navigator.clipboard.writeText(txt); toast(ok); } catch { toast("Copy failed"); }
}

function confirmLeaveBatch() {
  const busy = state.batch.items.some((x) => x.status === "wait" || x.status === "reading");
  const unrev = state.batch.items.some((x) => !x.report);
  return !(busy || unrev) || confirm("Leave these cards? Results not copied will be lost.");
}
