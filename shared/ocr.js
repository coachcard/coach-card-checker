/* Coach Card Checker — photo reading in the browser.
 *
 * Same PP-OCRv4 models and same post-processing as RapidOCR (the desktop
 * app's OCR), step for step: DB text detection -> boxes -> perspective crop ->
 * CTC recognition. Libraries: onnxruntime-web (models) + opencv.js (contours,
 * rectangles, warps). A Python transliteration of this file was validated on
 * the 47 example cards before it was written.
 *
 * Public API:  CCOcr.readPhoto(file, onProgress) -> { main, angle, alt?, altAngle? }
 *   main / alt = [{ box: [[x,y]x4], text, conf }]  in the 1150-px, rotated frame
 */
(function () {
  "use strict";

  const BASE = new URL("./", document.currentScript ? document.currentScript.src : location.href).href;
  const ORT_VER = "1.19.2";
  const ORT_URL = `https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VER}/dist/ort.min.js`;
  const CV_URLS = [
    "https://docs.opencv.org/4.10.0/opencv.js",
    "https://cdn.jsdelivr.net/npm/@techstark/opencv-js@4.10.0-release.1/dist/opencv.js",
  ];

  let ready = null;
  let det = null, rec = null, chars = null;

  function loadScript(src) {
    return new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src;
      s.async = true;
      s.onload = () => res();
      s.onerror = () => rej(new Error("could not load " + src));
      document.head.appendChild(s);
    });
  }

  async function loadCv() {
    let lastErr;
    for (const u of CV_URLS) {
      try {
        await loadScript(u);
        let c = window.cv;
        if (c && typeof c.then === "function") c = await c;          // promise-style builds
        if (c && !c.Mat) {
          await new Promise((r) => {
            c.onRuntimeInitialized = r;
            setTimeout(() => c.Mat && r(), 50);
          });
        }
        window.cv = c;
        if (c && c.Mat) return;
      } catch (e) {
        lastErr = e;
      }
    }
    throw lastErr || new Error("opencv.js unavailable");
  }

  async function init(onProgress) {
    if (ready) return ready;
    ready = (async () => {
      onProgress && onProgress(2, "Loading the photo reader (first time only)…");
      await loadScript(ORT_URL);
      ort.env.wasm.wasmPaths = `https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VER}/dist/`;
      ort.env.wasm.numThreads = self.crossOriginIsolated ? Math.min(4, navigator.hardwareConcurrency || 2) : 1;
      onProgress && onProgress(4, "Loading the image toolkit…");
      await loadCv();
      onProgress && onProgress(6, "Loading the reading models…");
      const opts = { executionProviders: ["wasm"], graphOptimizationLevel: "all" };
      [det, rec] = await Promise.all([
        ort.InferenceSession.create(BASE + "models/det.onnx", opts),
        ort.InferenceSession.create(BASE + "models/rec.onnx", opts),
      ]);
      const keys = (await (await fetch(BASE + "models/keys.txt")).text()).split("\n");
      if (keys[keys.length - 1] === "") keys.pop();
      chars = ["blank", ...keys, " "];
    })();
    try {
      await ready;
    } catch (e) {
      ready = null;
      throw e;
    }
    return ready;
  }

  // ---------- small helpers ----------
  const norm = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1]);

  /** RGB Mat -> Float32 CHW tensor data, (x/255 - 0.5)/0.5 */
  function toCHW(mat, outW, outH, padW) {
    const W = padW || outW;
    const data = new Float32Array(3 * outH * W);         // zeros = padding (like RapidOCR)
    const src = mat.data;
    const plane = outH * W;
    for (let y = 0; y < outH; y++) {
      for (let x = 0; x < outW; x++) {
        const i = (y * outW + x) * 3;
        const o = y * W + x;
        data[o] = (src[i] / 255 - 0.5) / 0.5;
        data[plane + o] = (src[i + 1] / 255 - 0.5) / 0.5;
        data[2 * plane + o] = (src[i + 2] / 255 - 0.5) / 0.5;
      }
    }
    return data;
  }

  function resizeMat(src, w, h, interp) {
    const dst = new cv.Mat();
    cv.resize(src, dst, new cv.Size(w, h), 0, 0, interp == null ? cv.INTER_LINEAR : interp);
    return dst;
  }

  /** = DBPostProcess.get_mini_boxes: 4 points ordered tl,tr,br,bl + short side */
  function boxPoints(rect) {          // = OpenCV RotatedRect::points (cv2.boxPoints)
    const t = (rect.angle * Math.PI) / 180, b = Math.cos(t) * 0.5, a = Math.sin(t) * 0.5;
    const cx = rect.center.x, cy = rect.center.y, w = rect.size.width, h = rect.size.height;
    const p0 = [cx - a * h - b * w, cy + b * h - a * w];
    const p1 = [cx + a * h - b * w, cy - b * h - a * w];
    return [p0, p1, [2 * cx - p0[0], 2 * cy - p0[1]], [2 * cx - p1[0], 2 * cy - p1[1]]];
  }

  function miniBox(rect) {
    const pts = boxPoints(rect);
    pts.sort((a, b) => a[0] - b[0]);
    let i1, i2, i3, i4;
    if (pts[1][1] > pts[0][1]) { i1 = 0; i4 = 1; } else { i1 = 1; i4 = 0; }
    if (pts[3][1] > pts[2][1]) { i2 = 2; i3 = 3; } else { i2 = 3; i3 = 2; }
    return [[pts[i1], pts[i2], pts[i3], pts[i4]], Math.min(rect.size.width, rect.size.height)];
  }

  /** = DBPostProcess.box_score_fast */
  function boxScore(predMat, box) {
    const h = predMat.rows, w = predMat.cols;
    const xs = box.map((p) => p[0]), ys = box.map((p) => p[1]);
    const clip = (v, a, b) => Math.min(Math.max(v, a), b);
    const xmin = clip(Math.floor(Math.min(...xs)), 0, w - 1), xmax = clip(Math.ceil(Math.max(...xs)), 0, w - 1);
    const ymin = clip(Math.floor(Math.min(...ys)), 0, h - 1), ymax = clip(Math.ceil(Math.max(...ys)), 0, h - 1);
    const mask = cv.Mat.zeros(ymax - ymin + 1, xmax - xmin + 1, cv.CV_8UC1);
    const poly = cv.matFromArray(4, 1, cv.CV_32SC2, box.flatMap((p) => [Math.trunc(p[0] - xmin), Math.trunc(p[1] - ymin)]));
    const pv = new cv.MatVector();
    pv.push_back(poly);
    cv.fillPoly(mask, pv, new cv.Scalar(1));
    const roi = predMat.roi(new cv.Rect(xmin, ymin, xmax - xmin + 1, ymax - ymin + 1));
    const m = cv.mean(roi, mask)[0];
    roi.delete(); mask.delete(); poly.delete(); pv.delete();
    return m;
  }

  /** = TextDetector.order_points_clockwise (stable sorts, like numpy argsort) */
  function orderPts(pts) {
    const xs = pts.map((p, i) => [p, i]).sort((a, b) => a[0][0] - b[0][0] || a[1] - b[1]).map((t) => t[0]);
    const l = xs.slice(0, 2).map((p, i) => [p, i]).sort((a, b) => a[0][1] - b[0][1] || a[1] - b[1]).map((t) => t[0]);
    const r = xs.slice(2).map((p, i) => [p, i]).sort((a, b) => a[0][1] - b[0][1] || a[1] - b[1]).map((t) => t[0]);
    return [l[0], r[0], r[1], l[1]];
  }

  // ---------- detection (= RapidOCR TextDetector, limit_type "min", 736) ----------
  async function detect(img) {
    const h = img.rows, w = img.cols;
    const ratio = Math.min(h, w) < 736 ? 736 / Math.min(h, w) : 1.0;
    const rh = Math.round(Math.trunc(h * ratio) / 32) * 32;
    const rw = Math.round(Math.trunc(w * ratio) / 32) * 32;
    const small = resizeMat(img, rw, rh);
    const input = new ort.Tensor("float32", toCHW(small, rw, rh), [1, 3, rh, rw]);
    small.delete();
    const out = await det.run({ [det.inputNames[0]]: input });
    const pred = out[det.outputNames[0]];
    const H = pred.dims[2], W = pred.dims[3];
    const predMat = cv.matFromArray(H, W, cv.CV_32FC1, pred.data);
    const bin = new cv.Mat(H, W, cv.CV_8UC1);
    for (let i = 0; i < H * W; i++) bin.data[i] = pred.data[i] > 0.3 ? 255 : 0;
    const kernel = cv.Mat.ones(2, 2, cv.CV_8UC1);
    const dil = new cv.Mat();
    cv.dilate(bin, dil, kernel);
    const contours = new cv.MatVector(), hier = new cv.Mat();
    cv.findContours(dil, contours, hier, cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE);
    const boxes = [];
    const n = Math.min(contours.size(), 1000);
    for (let k = 0; k < n; k++) {
      const c = contours.get(k);
      let [pts, sside] = miniBox(cv.minAreaRect(c));
      c.delete();
      if (sside < 3) continue;
      if (boxScore(predMat, pts) < 0.5) continue;
      const pm = cv.matFromArray(4, 1, cv.CV_32FC2, pts.flat());
      const r = cv.minAreaRect(pm);
      pm.delete();
      const bw = r.size.width, bh = r.size.height;
      const d = (bw * bh * 1.6) / (2 * (bw + bh));            // unclip distance (DB)
      [pts, sside] = miniBox({ center: r.center, size: { width: bw + 2 * d, height: bh + 2 * d }, angle: r.angle });
      if (sside < 5) continue;
      let box = pts.map((p) => [
        Math.trunc(Math.min(Math.max(Math.round((p[0] / W) * w), 0), w)),
        Math.trunc(Math.min(Math.max(Math.round((p[1] / H) * h), 0), h)),
      ]);
      box = orderPts(box).map((p) => [Math.floor(Math.min(Math.max(p[0], 0), w - 1)), Math.floor(Math.min(Math.max(p[1], 0), h - 1))]);
      if (Math.trunc(norm(box[0], box[1])) <= 3 || Math.trunc(norm(box[0], box[3])) <= 3) continue;
      boxes.push(box);
    }
    predMat.delete(); bin.delete(); kernel.delete(); dil.delete(); contours.delete(); hier.delete();
    // = RapidOCR sorted_boxes
    boxes.sort((a, b) => a[0][1] - b[0][1] || a[0][0] - b[0][0]);
    for (let i = 0; i < boxes.length - 1; i++) {
      for (let j = i; j >= 0; j--) {
        if (Math.abs(boxes[j + 1][0][1] - boxes[j][0][1]) < 10 && boxes[j + 1][0][0] < boxes[j][0][0]) {
          const t = boxes[j]; boxes[j] = boxes[j + 1]; boxes[j + 1] = t;
        } else break;
      }
    }
    return boxes;
  }

  // ---------- crop (= get_rotate_crop_image) ----------
  function crop(img, b) {
    const cw = Math.trunc(Math.max(norm(b[0], b[1]), norm(b[2], b[3])));
    const ch = Math.trunc(Math.max(norm(b[0], b[3]), norm(b[1], b[2])));
    const src = cv.matFromArray(4, 1, cv.CV_32FC2, b.flat());
    const dst = cv.matFromArray(4, 1, cv.CV_32FC2, [0, 0, cw, 0, cw, ch, 0, ch]);
    const M = cv.getPerspectiveTransform(src, dst);
    const out = new cv.Mat();
    cv.warpPerspective(img, out, M, new cv.Size(cw, ch), cv.INTER_CUBIC, cv.BORDER_REPLICATE, new cv.Scalar());
    src.delete(); dst.delete(); M.delete();
    if (out.rows / out.cols >= 1.5) {
      const r = new cv.Mat();
      cv.rotate(out, r, cv.ROTATE_90_COUNTERCLOCKWISE);      // = np.rot90
      out.delete();
      return r;
    }
    return out;
  }

  // ---------- recognition (= TextRecognizer, batches of 6, CTC greedy) ----------
  async function recognize(crops) {
    const res = new Array(crops.length).fill(null).map(() => ["", 0]);
    const ratios = crops.map((c) => c.cols / c.rows);
    const order = ratios.map((r, i) => [r, i]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).map((t) => t[1]);
    for (let s = 0; s < crops.length; s += 6) {
      const idx = order.slice(s, s + 6);
      const mwr = Math.max(320 / 48, ...idx.map((i) => ratios[i]));
      const Wp = Math.trunc(48 * mwr);
      const batch = new Float32Array(idx.length * 3 * 48 * Wp);
      idx.forEach((i, k) => {
        const c = crops[i];
        const rw = Math.min(Wp, Math.ceil((48 * c.cols) / c.rows));
        const r = resizeMat(c, rw, 48);
        batch.set(toCHW(r, rw, 48, Wp), k * 3 * 48 * Wp);
        r.delete();
      });
      const out = await rec.run({ [rec.inputNames[0]]: new ort.Tensor("float32", batch, [idx.length, 3, 48, Wp]) });
      const p = out[rec.outputNames[0]];
      const T = p.dims[1], C = p.dims[2];
      idx.forEach((i, k) => {
        let txt = "", sum = 0, cnt = 0, prev = -1;
        for (let t = 0; t < T; t++) {
          const off = (k * T + t) * C;
          let best = 0, bv = -Infinity;
          for (let c = 0; c < C; c++) { const v = p.data[off + c]; if (v > bv) { bv = v; best = c; } }
          if (best !== prev && best !== 0) { txt += chars[best] || ""; sum += bv; cnt++; }
          prev = best;
        }
        res[i] = [txt, cnt ? sum / cnt : 0];
      });
    }
    return res;
  }

  /** = RapidOCR __call__ (use_cls=False). img: RGB cv.Mat. */
  async function ocr(img, useRec) {
    const rawH = img.rows, rawW = img.cols;
    let im = img, rh = 1, rw = 1, own = false;
    const rs = (m, ratio) => {
      const nh = Math.round(Math.trunc(m.rows * ratio) / 32) * 32, nw = Math.round(Math.trunc(m.cols * ratio) / 32) * 32;
      const o = resizeMat(m, nw, nh);
      return [o, m.rows / nh, m.cols / nw];
    };
    if (Math.max(rawH, rawW) > 2000) { [im, rh, rw] = rs(im, 2000 / Math.max(rawH, rawW)); own = true; }
    if (Math.min(im.rows, im.cols) < 30) { const prev = im; [im, rh, rw] = rs(im, 30 / Math.min(im.rows, im.cols)); if (own) prev.delete(); own = true; }
    const boxes = await detect(im);
    const orig = (b) => b.map((p) => [Math.min(Math.max(p[0] * rw, 0), rawW), Math.min(Math.max(p[1] * rh, 0), rawH)]);
    if (!useRec) { if (own) im.delete(); return boxes.map(orig); }
    const crops = boxes.map((b) => crop(im, b));
    const texts = await recognize(crops);
    crops.forEach((c) => c.delete());
    if (own) im.delete();
    const out = [];
    boxes.forEach((b, i) => { if (texts[i][1] >= 0.5) out.push({ box: orig(b), text: texts[i][0], conf: texts[i][1] }); });
    return out;
  }

  function rotated(src, angle) {
    if (!angle) return src.clone();
    const dst = new cv.Mat();
    cv.rotate(src, dst, angle === 90 ? cv.ROTATE_90_CLOCKWISE : angle === 180 ? cv.ROTATE_180 : cv.ROTATE_90_COUNTERCLOCKWISE);
    return dst;
  }

  /** = extract._best_orientation (detection only, on a 560-px thumbnail) */
  async function bestOrientation(img) {
    let thumb = img, own = false;
    const lng = Math.max(img.rows, img.cols);
    if (lng > 560) {
      const s = 560 / lng;
      thumb = resizeMat(img, Math.trunc(img.cols * s), Math.trunc(img.rows * s), cv.INTER_AREA);
      own = true;
    }
    const order = thumb.cols >= thumb.rows ? [0, 180, 90, 270] : [90, 270, 0, 180];
    let bestA = 0, bestS = -1;
    for (const a of order) {
      const r = rotated(thumb, a);
      const boxes = await ocr(r, false);
      r.delete();
      let sc = 0;
      for (const b of boxes) {
        const xs = b.map((p) => p[0]), ys = b.map((p) => p[1]);
        const w = Math.max(...xs) - Math.min(...xs), h = Math.max(...ys) - Math.min(...ys) || 1;
        if (w > h * 1.6) sc += 1 + Math.min(w / h, 12) / 12;
      }
      if (sc > bestS) { bestA = a; bestS = sc; }
    }
    if (own) thumb.delete();
    return bestA;
  }

  /** File -> RGB cv.Mat, EXIF-oriented, longest side <= 1150 (like extract.py) */
  async function loadImage(file) {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    const cvs = document.createElement("canvas");
    cvs.width = bmp.width; cvs.height = bmp.height;
    const ctx = cvs.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(bmp, 0, 0);
    bmp.close && bmp.close();
    const rgba = cv.matFromImageData(ctx.getImageData(0, 0, cvs.width, cvs.height));
    let rgb = new cv.Mat();
    cv.cvtColor(rgba, rgb, cv.COLOR_RGBA2RGB);
    rgba.delete();
    const longest = Math.max(rgb.rows, rgb.cols);
    if (longest > 1150) {
      const s = 1150 / longest;
      const r = resizeMat(rgb, Math.trunc(rgb.cols * s), Math.trunc(rgb.rows * s), cv.INTER_AREA);
      rgb.delete();
      rgb = r;
    }
    return rgb;
  }

  /**
   * Read a photo. countFn(items) -> number of elements the parser finds; used to
   * decide on the 180° retry exactly like the desktop app (< 4 elements).
   */
  async function readPhoto(file, onProgress, countFn) {
    const p = (pct, label) => onProgress && onProgress(pct, label);
    await init(p);
    p(8, "Preparing photo");
    const img = await loadImage(file);
    try {
      p(10, "Checking orientation");
      const angle = await bestOrientation(img);
      p(30, "Reading text");
      let r = rotated(img, angle);
      const main = await ocr(r, true);
      r.delete();
      const result = { main, angle };
      if (countFn && (await countFn(main)) < 4) {
        p(72, "Re-reading");
        const alt = (angle + 180) % 360;
        r = rotated(img, alt);
        result.alt = await ocr(r, true);
        result.altAngle = alt;
        r.delete();
      }
      p(96, "Matching elements");
      return result;
    } finally {
      img.delete();
    }
  }

  window.CCOcr = { init, readPhoto };
})();
