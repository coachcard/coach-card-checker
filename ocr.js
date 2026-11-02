/* Coach Card Checker — photo reading in the browser (page side).
 *
 * The heavy work (PP-OCRv4 models, OpenCV) runs in ocr-worker.js, a Web Worker,
 * so the page stays responsive while a photo is read — also in batch mode,
 * where you can review one card while the others are still being read.
 * Photos are read one at a time, in order.
 *
 * Public API:  CCOcr.readPhoto(file, onProgress, countFn) -> { main, angle, alt?, altAngle? }
 *   main / alt = [{ box: [[x,y]x4], text, conf }]  in the 1150-px, rotated frame
 */
(function () {
  "use strict";

  const WORKER_URL = new URL("ocr-worker.js", document.currentScript ? document.currentScript.src : location.href).href;
  const ORT_FALLBACK = "1.17.3";       // for phones/browsers where 1.19 has no usable backend
  // iPhone / iPad Safari: onnxruntime 1.19 reports "no available backend"; 1.17 works.
  const IOS = /iP(hone|ad|od)/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  let worker = null, nextId = 1, ortVer = IOS ? ORT_FALLBACK : null;
  const pending = new Map();          // id -> { resolve, reject, onProgress }
  let queue = Promise.resolve();      // one photo at a time

  function getWorker() {
    if (worker) return worker;
    worker = new Worker(WORKER_URL + (ortVer ? "?ort=" + ortVer : ""));
    worker.onmessage = (ev) => {
      const m = ev.data || {};
      const p = pending.get(m.id);
      if (!p) return;
      if (m.progress) { p.onProgress && p.onProgress(m.progress[0], m.progress[1]); return; }
      pending.delete(m.id);
      if (m.error) p.reject(new Error(m.error));
      else p.resolve(m.result);
    };
    worker.onerror = (e) => {
      const err = new Error((e && e.message) || "photo reader crashed");
      for (const p of pending.values()) p.reject(err);
      pending.clear();
      worker = null;                  // a fresh worker next time
    };
    return worker;
  }

  function call(msg, onProgress, transfer) {
    return new Promise((resolve, reject) => {
      const id = nextId++;
      pending.set(id, { resolve, reject, onProgress });
      getWorker().postMessage(Object.assign({ id }, msg), transfer || []);
    });
  }

  /** File -> RGBA pixels, EXIF-oriented (decoding is quick; the rest is in the worker) */
  async function pixelsOf(file) {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    // Phones take 12–48 MP photos; iPhones can't draw a canvas above ~16 MP and the
    // reader only uses 1150 px anyway. Cap at 2300 px (the worker then reduces to 1150).
    const k = Math.min(1, 2300 / Math.max(bmp.width, bmp.height));
    const cvs = document.createElement("canvas");
    cvs.width = Math.max(1, Math.round(bmp.width * k)); cvs.height = Math.max(1, Math.round(bmp.height * k));
    const ctx = cvs.getContext("2d", { willReadFrequently: true });
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(bmp, 0, 0, cvs.width, cvs.height);
    bmp.close && bmp.close();
    const img = ctx.getImageData(0, 0, cvs.width, cvs.height);
    cvs.width = cvs.height = 0;
    return img;
  }

  function isBackendError(e) {
    return /backend|webassembly|wasm|simd/i.test(String((e && e.message) || e));
  }

  async function readOne(file, onProgress, countFn) {
    try {
      return await readOnce(file, onProgress, countFn);
    } catch (e) {
      if (ortVer || !isBackendError(e)) throw e;
      console.warn("photo reader: retrying with onnxruntime-web " + ORT_FALLBACK, e);
      if (worker) { worker.terminate(); worker = null; }
      pending.clear();
      ortVer = ORT_FALLBACK;
      return await readOnce(file, onProgress, countFn);
    }
  }

  async function readOnce(file, onProgress, countFn) {
    const p = (pct, label) => onProgress && onProgress(pct, label);
    p(1, "Preparing photo");
    const img = await pixelsOf(file);
    const buf = img.data.buffer;
    try {
      const r = await call({ cmd: "read", pixels: buf, width: img.width, height: img.height }, p, [buf]);
      const result = { main: r.main, angle: r.angle };
      if (countFn && (await countFn(r.main)) < 4) {          // same 180° retry rule as the desktop app
        const a = await call({ cmd: "alt" }, p);
        result.alt = a.alt;
        result.altAngle = a.altAngle;
      }
      p(96, "Matching elements");
      return result;
    } finally {
      call({ cmd: "done" }).catch(() => {});
    }
  }

  function readPhoto(file, onProgress, countFn) {
    const job = queue.then(() => readOne(file, onProgress, countFn));
    queue = job.catch(() => {});
    if (onProgress) onProgress(1, "Waiting for the previous photo…");
    return job;
  }

  window.CCOcr = { readPhoto };
})();
