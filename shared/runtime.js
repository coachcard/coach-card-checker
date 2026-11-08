/* Coach Card Checker — in-browser runtime (no server).
 *
 * The page (app.js) still calls /api/routines, /api/check and /api/extract as
 * with the desktop server. This file answers those calls inside the browser:
 *   - the rule engine (the same Python files as the desktop app) runs in Pyodide;
 *   - Excel / PDF are read by the same Python code (openpyxl / pypdf);
 *   - photos are read by ocr.js, then parsed by the same Python code.
 * Nothing is uploaded anywhere: the cards never leave the device.
 */
(function () {
  "use strict";

  const me = document.currentScript;
  const YEAR = (me && me.dataset.year) || "2027";
  const PYODIDE_VER = "0.26.4";
  const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VER}/full/`;
  const HERE = new URL("./", location.href).href;           // .../2026/ or .../2027/
  const SHARED = new URL("../shared/", location.href).href;

  // ---------- loading badge ----------
  const badge = document.createElement("div");
  badge.id = "rt-loading";
  badge.setAttribute("role", "status");
  badge.style.cssText =
    "position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:9999;" +
    "background:#1f2937;color:#fff;padding:10px 16px;border-radius:999px;font:14px system-ui,sans-serif;" +
    "box-shadow:0 6px 20px rgba(0,0,0,.25);display:flex;gap:10px;align-items:center;max-width:92vw";
  badge.innerHTML = '<span style="width:14px;height:14px;border:2px solid #fff;border-right-color:transparent;' +
    'border-radius:50%;display:inline-block;animation:rtspin 1s linear infinite"></span><span id="rt-msg">Loading the checker…</span>';
  const st = document.createElement("style");
  st.textContent = "@keyframes rtspin{to{transform:rotate(360deg)}}";
  document.head.appendChild(st);
  const setMsg = (t) => { const m = badge.querySelector("#rt-msg"); if (m) m.textContent = t; };
  const showBadge = (t) => { setMsg(t); if (!badge.isConnected) document.body.appendChild(badge); };
  const hideBadge = () => badge.remove();

  function loadScript(src) {
    return new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src; s.onload = res; s.onerror = () => rej(new Error("could not load " + src));
      document.head.appendChild(s);
    });
  }

  // ---------- Python (Pyodide) ----------
  let pyReady = null, bridge = null, pyodide = null, filePkgs = null;

  function initPython() {
    if (pyReady) return pyReady;
    pyReady = (async () => {
      showBadge("Loading the checker (first visit: about 15–30 s)…");
      await loadScript(PYODIDE_URL + "pyodide.js");
      pyodide = await loadPyodide({ indexURL: PYODIDE_URL });
      setMsg("Loading the " + YEAR + " rules…");
      const manifest = await (await fetch(HERE + "py/manifest.json", { cache: "no-cache" })).json();
      const files = manifest.files.map((p) => ({ src: HERE + "py/" + p, dst: "/app/" + p }));
      files.push({ src: SHARED + "web_bridge.py", dst: "/app/shared/web_bridge.py" });
      await Promise.all(files.map(async (f) => {
        const r = await fetch(f.src, { cache: "no-cache" });
        if (!r.ok) throw new Error("missing " + f.src);
        const data = new Uint8Array(await r.arrayBuffer());
        const dir = f.dst.slice(0, f.dst.lastIndexOf("/"));
        pyodide.FS.mkdirTree(dir);
        pyodide.FS.writeFile(f.dst, data);
      }));
      pyodide.runPython(`
import sys
for p in ("/app/shared", "/app/webapp", "/app/skill/scripts"):
    if p not in sys.path:
        sys.path.insert(0, p)
import web_bridge
`);
      bridge = pyodide.globals.get("web_bridge");
      hideBadge();
    })();
    pyReady.catch((e) => {
      pyReady = null;
      showBadge("Could not load the checker: " + e.message + " — check the internet connection and reload.");
    });
    return pyReady;
  }

  async function initFilePackages() {           // Excel / PDF readers, on first use
    if (filePkgs) return filePkgs;
    filePkgs = (async () => {
      await pyodide.loadPackage("micropip");
      await pyodide.runPythonAsync(`
import micropip
await micropip.install(["openpyxl", "pypdf"])
`);
    })();
    filePkgs.catch(() => { filePkgs = null; });
    return filePkgs;
  }

  // ---------- fake /api/* ----------
  const json = (obj, status = 200) =>
    new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

  function headerOf(init, name) {
    const h = (init && init.headers) || {};
    if (h instanceof Headers) return h.get(name);
    const k = Object.keys(h).find((x) => x.toLowerCase() === name.toLowerCase());
    return k ? h[k] : null;
  }

  const IMG = ["png", "jpg", "jpeg", "jfif", "webp", "gif", "bmp"];

  async function extract(file, filename, routine, emit) {
    const ext = (filename.split(".").pop() || "").toLowerCase();
    await initPython();
    if (IMG.includes(ext)) {
      const r = await window.CCOcr.readPhoto(file, emit, async (items) =>
        bridge.count_elements(JSON.stringify(items), routine || ""));
      return JSON.parse(bridge.from_ocr(
        JSON.stringify(r.main), r.angle, routine || "",
        r.alt ? JSON.stringify(r.alt) : null, r.altAngle == null ? null : r.altAngle, filename));
    }
    if (["xlsx", "xlsm", "pdf"].includes(ext)) {
      emit(10, ext === "pdf" ? "Loading the PDF reader…" : "Loading the Excel reader…");
      await initFilePackages();
      emit(30, ext === "pdf" ? "Reading PDF" : "Reading spreadsheet");
      const path = "/tmp/upload." + ext;
      pyodide.FS.writeFile(path, new Uint8Array(await file.arrayBuffer()));
      const res = JSON.parse(bridge.from_file(path, filename, routine || ""));
      try { pyodide.FS.unlink(path); } catch (e) { /* ignore */ }
      emit(96, "Matching elements");
      return res;
    }
    if (ext === "xls") {
      return { ok: true, source: "xls", extracted: false, elements: [], filename,
               notes: ["Old .xls files can't be read — open the file in Excel and save it as .xlsx, or type the card in."] };
    }
    return { ok: false, error: "unsupported_type", filename, notes: [`Unsupported file type: .${ext || "?"}`] };
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    const path = new URL(url, location.href).pathname;
    const m = path.match(/\/api\/(routines|check|extract)$/);
    if (!m) return realFetch(input, init);
    try {
      if (m[1] === "routines") {
        await initPython();
        return json(JSON.parse(bridge.routines()));
      }
      if (m[1] === "check") {
        await initPython();
        const body = typeof init.body === "string" ? init.body : await new Response(init.body).text();
        return json(JSON.parse(bridge.run_check(body)));
      }
      // extract: stream progress like the desktop server (Server-Sent Events)
      const filename = decodeURIComponent(headerOf(init, "X-Filename") || "upload");
      const routine = decodeURIComponent(headerOf(init, "X-Routine") || "");
      const file = init.body;
      const enc = new TextEncoder();
      const stream = new ReadableStream({
        async start(ctrl) {
          const send = (o) => ctrl.enqueue(enc.encode("data: " + JSON.stringify(o) + "\n\n"));
          let result;
          try {
            result = await extract(file, filename, routine, (pct, label) => send({ pct, label }));
          } catch (e) {
            console.error(e);
            result = { ok: false, error: "extract_error", message: String(e && e.message || e), filename,
                       notes: ["Could not read this file in the browser (" + (e && e.message || e) +
                               "). Type the elements by hand using the picture on the left."] };
          }
          send({ done: true, result });
          ctrl.close();
        },
      });
      return new Response(stream, { headers: { "Content-Type": "text/event-stream; charset=utf-8" } });
    } catch (e) {
      console.error(e);
      return json({ ok: false, error: "engine_error", message: String(e && e.message || e) }, 500);
    }
  };

  // Self-update: GitHub Pages lets browsers keep the page itself for ~10 min,
  // so right after a new version is published a browser can still run the old
  // app.js. Fetch the page fresh; if it points to a newer version, reload once.
  (async () => {
    try {
      const mine = (me && me.src.match(/[?&]v=(\d+)/) || [])[1];
      if (!mine) return;
      const html = await (await fetch(location.href.split("#")[0], { cache: "no-cache" })).text();
      const live = (html.match(/runtime\.js\?v=(\d+)/) || [])[1];
      const key = "cc-reloaded-" + live;
      if (live && live !== mine && !sessionStorage.getItem(key)) {
        sessionStorage.setItem(key, "1");
        location.reload();
      }
    } catch (e) { /* offline or storage blocked: keep the current version */ }
  })();

  // Start loading Python right away (the routine list needs it).
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initPython);
  else initPython();
  window.CCRuntime = { year: YEAR, ready: () => initPython() };
})();
