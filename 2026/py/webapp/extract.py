#!/usr/bin/env python3
"""Best-effort extraction of a coach card into the element list the review
screen expects.

Supported:
  * .xlsx / .xlsm  — deterministic parse of the official World Aquatics coach
    card layout ("ELEMENTS IN ORDER OF PERFORMANCE" table).
  * .pdf           — text-layer parse (digital PDFs only; scans yield nothing).
  * images         — not supported yet (returns an empty list + a note).

Returned dict:
  {
    "ok": bool,
    "source": "xlsx" | "pdf" | "image",
    "extracted": bool,          # did we actually read any elements?
    "elements": [ {type, group, code, slot}, ... ],
    "time_seconds": int | None,
    "detected_routine": str | None,
    "notes": [str, ...],
  }
"""
from __future__ import annotations

import os
import re
import sys

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")

# PART column keyword -> (canonical element type, is_acro, is_hybrid)
_PART_TRANSITION = ("TRA", "TRANS", "TRANSITION")


def _norm(v) -> str:
    if v is None:
        return ""
    return str(v).strip()


_BASE_LABEL_RE = re.compile(
    r"^(ACRO[-_ ]?PAIR|PAIR[-_ ]?ACRO|ACRO[-_ ]?[ABCP]?\d*|BM[-_ ]?HYBRID|"
    r"BM[-_ ]?\w*|TRA|TRE|HYBRID|(SOLO|DUET|TEAM)[-_ ]?HYBRID)$",
    re.I,
)


def _looks_like_code(s: str) -> bool:
    """True if the string looks like a declared-difficulty code / code list
    rather than a column label, a number, or a bonus tag ('4PC', '2.75')."""
    s = (s or "").strip()
    if len(s) < 3 or _BASE_LABEL_RE.match(s):
        return False
    if re.fullmatch(r"[\d.,\s%/+-]+", s):  # pure number / DD / bonus cell
        return False
    if re.fullmatch(r"\d+\s?PC", s, re.I):
        return False
    # a letter next to a digit, or an internal code separator
    return bool(re.search(r"[A-Za-z]\d|\d[A-Za-z]|[A-Za-z].*[-/^*+]", s))


def _time_to_seconds(text: str):
    """Return the largest mm:ss found in the text, in seconds (end of a range)."""
    best = None
    for line in (text or "").splitlines() or [text or ""]:
        if re.search(r"GMT|SIGNATURE|\bDATE\b", line, re.I):
            continue  # skip the signed-at timestamp
        for m in _TIME_RE.finditer(line):
            sec = int(m.group(1)) * 60 + int(m.group(2))
            if sec <= 600 and (best is None or sec > best):  # no routine runs >10 min
                best = sec
    return best


def _acro_type_for_routine(routine: str | None) -> str:
    r = (routine or "").lower()
    if "acrobatic routine" in r or r.endswith("acrobatic") or "open acrobatic" in r:
        return "Acrobatic"
    if "duet" in r:
        return "Pair Acrobatic"
    return "Team Acrobatic"


def _classify(part: str, base_mark: str, routine: str | None):
    """Map a table row to (element_type, group) or (None, None) to skip."""
    p = part.upper().strip()
    bm = base_mark.upper().strip()

    if not p or p in _PART_TRANSITION:
        return None, None

    if p.startswith("ACRO"):
        grp = ""
        m = re.search(r"ACRO[-_ ]?([ABCP])", p)
        if m:
            grp = m.group(1)
        return _acro_type_for_routine(routine), grp

    if "HYBRID" in p or "HYBRID" in bm:
        if "CHOREO" in bm:
            return "Team Choreography Hybrid (no DoD)", ""
        if bm.startswith("SOLO"):
            return "Solo Hybrid (DoD)", ""
        if bm.startswith("DUET"):
            return "Duet Hybrid (DoD)", ""
        if bm.startswith("TEAM") and "DOD" in bm.replace(" ", ""):
            return "Team Hybrid (DoD)", ""
        return "Hybrid", ""

    if p.startswith("TRE"):
        return "TRE", ""
    if "SSC" in p or "SURFACE" in p:
        return "Sustained Surface Connection", ""
    if "PAIR" in p:
        return "Pair Acrobatic", ""
    if "TRE" in bm:
        return "TRE", ""
    return None, None


_ACRO_DD_MAX = 6.5   # no acrobatic is worth more; bigger numbers are hybrid DDs

_DD_RE = re.compile(r"^\s*(\d{1,2}(?:[.,]\d{1,4})?)\s*$")


def _parse_dd(s):
    """A declared-DD cell ('3.25', '4,012') -> float, else None."""
    m = _DD_RE.match(str(s or ""))
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    return v if 0.2 <= v <= 30 else None


def _find_header(rows):
    """Return (row_index, {col_name: col_index}) for the elements table header."""
    want = {
        "time": ("TIME",),
        "part": ("PART",),
        "el": ("EL",),
        "base": ("BASE MARK", "BASEMARK"),
        "decl": ("DECLARED DIFFICULTY", "DECLARED", "DIFFICULTY"),
        "bonus": ("BONUS",),
        "dd": ("DD",),
    }
    for i, row in enumerate(rows):
        cells = [_norm(c).upper() for c in row]
        joined = " ".join(cells)
        if "DECLARED DIFFICULTY" not in joined and not ("PART" in cells and "EL" in cells):
            continue
        colmap = {}
        for idx, c in enumerate(cells):
            for key, needles in want.items():
                if key in colmap:
                    continue
                if any(n == c or (len(n) > 4 and n in c) for n in needles):
                    colmap[key] = idx
        if "part" in colmap and "decl" in colmap:
            return i, colmap
    return None, None


def _rows_to_elements(rows, routine):
    hdr_i, cols = _find_header(rows)
    notes = []
    if hdr_i is None:
        return [], None, ["Could not find the “Elements in order of performance” table."]

    elements = []
    last_time_sec = None
    blanks = 0
    for row in rows[hdr_i + 1:]:
        cells = [_norm(c) for c in row]

        def cell(key):
            idx = cols.get(key)
            return cells[idx] if idx is not None and idx < len(cells) else ""

        joined = " ".join(cells).upper()
        if "DD TOT" in joined or "TOTAL" in joined and "DD" in joined:
            break

        part = cell("part")
        time_txt = cell("time")
        t = _time_to_seconds(time_txt)
        if t is not None:
            last_time_sec = t if last_time_sec is None else max(last_time_sec, t)

        if not any(cells):
            blanks += 1
            if blanks >= 4:
                break
            continue
        blanks = 0

        base = cell("base")
        etype, grp = _classify(part, base, routine)
        if not etype:
            continue

        code = cell("decl")
        # The DECLARED DIFFICULTY column is often merged with BASE MARK by OCR /
        # by a narrow export. If what we grabbed is just the base-mark label
        # (e.g. "ACRO-A", "BM-Hybrid") or a bare number, pick the cell that
        # actually looks like a code instead.
        skip = {cols.get("time"), cols.get("part"), cols.get("el"), cols.get("base")}
        if not _looks_like_code(code):
            cand = [
                (i, c) for i, c in enumerate(cells)
                if i not in skip and _looks_like_code(c)
            ]
            if cand:
                code = max(cand, key=lambda ic: len(ic[1]))[1]

        el = {
            "type": etype,
            "group": grp,
            "code": code,
            "slot": time_txt.replace(" ", "") if time_txt else "",
        }
        dd = _parse_dd(cell("dd"))
        if dd is not None:
            el["dd"] = dd
        elements.append(el)

    if not elements:
        notes.append("Found the table but no element rows were recognised.")
    return elements, last_time_sec, notes


# "FEDERATION / CLUB   CZE - Czechia" near the top of the card. Restricted to
# the header area so a code segment further down can't be mistaken for it.
_FED_RE = re.compile(r"\b([A-Z]{3})\s*[-–]\s*([A-Z][A-Za-z'’ .-]{2,30})")
_FED_STOP_RE = re.compile(r"ELEMENTS\s*IN\s*ORDER|DECLARED\s*DIFFICULT", re.I)


def _detect_federation(text: str):
    """3-letter federation code from the card header, or None.

    Prefers a match on the FEDERATION/CLUB line; otherwise takes the first
    '<ABC> - <Name>' pair above the elements table."""
    if not text:
        return None
    head = _FED_STOP_RE.split(text, 1)[0][:1200]
    for line in head.splitlines():
        if re.search(r"ERATION|CLUB", line, re.I):
            m = _FED_RE.search(line)
            if m:
                return m.group(1)
    m = _FED_RE.search(head)
    return m.group(1) if m else None


def _detect_routine(text: str):
    """Look for the EVENT line, e.g. 'OPEN_TEAM_FREE - Open Team Free'."""
    m = re.search(r"Open (Team|Duet) (Free|Technical)|Open Acrobatic|Solo (Free|Tech)"
                  r"|Women Duet (Free|Technical)|Mixed Duet (Free|Technical)"
                  r"|(Youth|12U)[^\n]{0,40}(Free|Combination)", text or "", re.I)
    return m.group(0).strip() if m else None


_THEME_LABEL_RE = re.compile(r"\bTH[EÈÉ]M[EÉ]\b\s*[:.\-]?\s*", re.I)
# OCR of a photo often clips the left edge of the label column: "HEME", "EME".
_THEME_LABEL_CLIPPED_RE = re.compile(r"^(?:T?H?[EÈÉ]M[EÉ]|THEM)(?:\s{2,}|\s*[:.]\s*|$)", re.I)
# Lines that mark the END of the header block / are never a theme.
_THEME_STOP_RE = re.compile(
    r"COMPETITOR|MONITOR|\bITOR\(|PETITOR|ADJUDICAT|COACH|ELEMENT[S]?\s*IN\s*ORDER"
    r"|REFEREE|JUDGE", re.I)
# Phase / category tokens that sit right before the theme and must be dropped.
_THEME_JUNK_RE = re.compile(
    r"^(QUAL|FNL|PRE|SF|RR|FINAL[S]?|PRELIM\w*|Preliminaries|Finals)\b"
    r"|Junior|Senior|Youth|Female|Male|Mixed|12U|13-15", re.I)


def _looks_like_names(s: str) -> bool:
    """Reserve/competitor lists OCR as a long run of Name+SURNAME with many
    case flips; a theme has at most a couple."""
    if re.search(r"[(（]\s*[RS]\s*[)）]|[a-z]R\)", s):
        return True
    flips = len(re.findall(r"[a-z][A-Z]", s))
    return len(s) > 42 and flips >= 4


def _theme_ok(v: str) -> bool:
    """A plausible theme: some letters, not a name list. All-caps is fine when
    short (ACDC, CANCAN, GHOST); long all-caps runs are surnames."""
    if not v or len(v) < 2 or len(v) > 60 or not re.search(r"[A-Za-z]", v):
        return False
    if _looks_like_names(v):
        return False
    if not re.search(r"[a-z]", v) and len(v) > 25:
        return False
    return True


def _detect_theme(text: str):
    """Acrobatic / Free Combination cards show the routine theme in the header,
    sometimes after a 'THEME' label (photos may clip it to 'HEME' / 'EME'),
    sometimes as a bare line between the phase row and the competitor row.
    Best-effort — the coach can fix it."""
    lines = [ln.strip() for ln in (text or "").splitlines()]

    _phase_only = re.compile(
        r"^(?:QUAL|FNL|PRE|SF|RR)?[-\s]*(?:qualif\w*|prelim\w*|final[s]?|"
        r"round\s*robin|semi[-\s]*final[s]?|preliminaries|finals)$", re.I)

    def _clean(v):
        v = _THEME_LABEL_RE.sub("", v).strip(" :.-\t|")
        v = _THEME_LABEL_CLIPPED_RE.sub("", v).strip(" :.-\t|")
        v = re.sub(r"^[A-Z]{1,4}\s{2,}", "", v)              # clipped label left of the value ("TE  CrazyCats")
        v = re.split(r"\s{2,}|\t", v)[0].strip()           # drop glued next column
        v = re.sub(r"^(?:QUAL|FNL|PRE|SF|RR)-\S*", "", v).strip(" -:|")
        v = _THEME_JUNK_RE.sub("", v).strip(" -:|")
        if _phase_only.match(v):
            return ""
        return v.rstrip(".")

    def _is_label(ln):
        return bool(_THEME_LABEL_RE.search(ln) or _THEME_LABEL_CLIPPED_RE.match(ln))

    for i, ln in enumerate(lines):
        if not _is_label(ln):
            continue
        v = _clean(ln)                                     # value on the label line?
        if _theme_ok(v):
            return v[:80]
        for nxt in lines[i + 1 : i + 3]:                   # else the next usable line
            if not nxt or _THEME_JUNK_RE.match(nxt):
                continue
            if _THEME_STOP_RE.search(nxt) or _looks_like_names(nxt):
                break                                      # reached the competitors: no theme
            v = _clean(nxt)
            if _theme_ok(v):
                return v[:80]
        return None  # label found but no value -> genuinely themeless

    # No THEME label: take the line between the phase row and the competitor row.
    phase = next((i for i, ln in enumerate(lines)
                  if re.search(r"^(QUAL|FNL|PRE|SF)\b|\b(QUAL|FNL|PRE|SF)\s*-|-\s*(Finals|Preliminaries)\b", ln, re.I)), None)
    if phase is not None:
        for nxt in lines[phase + 1 : phase + 4]:
            if not nxt or _THEME_JUNK_RE.match(nxt):
                continue
            if _THEME_STOP_RE.search(nxt) or _looks_like_names(nxt):
                break
            v = _clean(nxt)
            if _theme_ok(v):
                return v[:80]
    return None


def _from_xlsx(path, routine):
    try:
        from openpyxl import load_workbook
    except Exception:
        return {
            "ok": True, "source": "xlsx", "extracted": False, "elements": [],
            "time_seconds": None, "detected_routine": None,
            "notes": ["Excel support needs the 'openpyxl' package (pip install openpyxl)."],
        }
    wb = load_workbook(path, read_only=True, data_only=True)
    all_notes = []
    best = ([], None, ["empty workbook"])
    joined_text = ""
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        joined_text += "\n".join(" ".join(_norm(c) for c in r) for r in rows) + "\n"
        els, tsec, notes = _rows_to_elements(rows, routine)
        if len(els) > len(best[0]):
            best = (els, tsec, notes)
    wb.close()
    els, tsec, notes = best
    return {
        "ok": True, "source": "xlsx", "extracted": bool(els), "elements": els,
        "time_seconds": tsec, "detected_routine": _detect_routine(joined_text),
        "detected_federation": _detect_federation(joined_text),
        "detected_theme": _detect_theme(joined_text),
        "notes": notes + all_notes,
    }


_OCR_ENGINE = None


# NB: the ~90 MB PP-OCRv4 "server" recognition model was tried here
# (rec_model_path=). On CPU it is ~30-60x slower — >10 min for a single card
# vs ~15-25 s with the bundled mobile model — so it is not usable for the
# interactive webapp without a GPU. Reverted to the bundled model.
def _get_ocr():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        import os as _os

        _os.environ.setdefault("OMP_NUM_THREADS", str(max(1, (_os.cpu_count() or 4))))
        from rapidocr_onnxruntime import RapidOCR

        n = max(2, min(8, _os.cpu_count() or 4))
        try:
            _OCR_ENGINE = RapidOCR(
                det_intra_op_num_threads=n,
                cls_intra_op_num_threads=n,
                rec_intra_op_num_threads=n,
            )
        except Exception:
            _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


_OCR_CACHE = {}


def _ocr_raw(np_img):
    """Run OCR once; cache boxes so re-clustering at different tolerances is free."""
    key = (np_img.shape, int(np_img[::37, ::37].sum()))
    if key in _OCR_CACHE:
        return _OCR_CACHE[key]
    engine = _get_ocr()
    try:
        result, _ = engine(np_img, use_cls=False)  # orientation already handled
    except TypeError:
        result, _ = engine(np_img)
    boxes = []
    for box, text, conf in (result or []):
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        boxes.append(
            {
                "text": str(text).strip(),
                "conf": float(conf) if conf is not None else 0.0,
                "yc": sum(ys) / 4.0,
                "xc": sum(xs) / 4.0,
                "x0": min(xs),
                "h": max(ys) - min(ys),
            }
        )
    if len(_OCR_CACHE) > 24:
        _OCR_CACHE.clear()
    _OCR_CACHE[key] = boxes
    return boxes


def _orientation_score(np_img):
    """Detection-only pass (fast) -> how strongly this looks like upright text:
    number of wide, horizontal text boxes."""
    engine = _get_ocr()
    try:
        result, _ = engine(np_img, use_rec=False, use_cls=False)
    except TypeError:
        result, _ = engine(np_img)
    if not result:
        return 0.0
    score = 0.0
    for item in result:
        # det-only -> item is the 4-point box; full -> item is [box, text, conf]
        box = item[0] if (len(item) == 3 and isinstance(item[1], str)) else item
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        w, h = max(xs) - min(xs), max(ys) - min(ys) or 1
        if w > h * 1.6:          # a horizontal line of text
            score += 1 + min(w / h, 12) / 12
    return score


def _det_boxes(np_img):
    engine = _get_ocr()
    try:
        result, _ = engine(np_img, use_rec=False, use_cls=False)
    except TypeError:
        result, _ = engine(np_img)
    out = []
    for item in result or []:
        box = item[0] if (len(item) == 3 and isinstance(item[1], str)) else item
        out.append(box)
    return out


def _best_orientation(pil_img, np):
    """Pick 0/90/180/270 with the fast detection model on a thumbnail, and return
    (angle, crop_box) — crop_box is the text bounding box in *full-res* pixels of
    the de-rotated image, so the slow OCR pass can skip the blank margins."""
    thumb = pil_img.copy()
    lng = max(thumb.size)
    scale = 1.0
    if lng > 560:
        scale = 560 / lng
        thumb = thumb.resize((int(thumb.width * scale), int(thumb.height * scale)))
    order = (0, 180, 90, 270) if thumb.width >= thumb.height else (90, 270, 0, 180)
    best_a, best_s, best_boxes = 0, -1.0, []
    for a in order:
        boxes = _det_boxes(np.array(thumb.rotate(-a, expand=True)))
        sc = 0.0
        for b in boxes:
            xs = [p[0] for p in b]; ys = [p[1] for p in b]
            w, h = max(xs) - min(xs), (max(ys) - min(ys)) or 1
            if w > h * 1.6:
                sc += 1 + min(w / h, 12) / 12
        if sc > best_s:
            best_a, best_s, best_boxes = a, sc, boxes

    crop = None
    if best_boxes:
        xs = [p[0] for b in best_boxes for p in b]
        ys = [p[1] for b in best_boxes for p in b]
        rot = thumb.rotate(-best_a, expand=True)
        mx = 0.03 * max(rot.size)
        x0, y0 = max(0, min(xs) - mx), max(0, min(ys) - mx)
        x1, y1 = min(rot.width, max(xs) + mx), min(rot.height, max(ys) + mx)
        # only worth it if it trims a real border
        if (x1 - x0) * (y1 - y0) < 0.82 * rot.width * rot.height:
            crop = (x0 / scale, y0 / scale, x1 / scale, y1 / scale)
    return best_a, crop


def _ocr_lines(np_img, row_tol=0.6):
    """Run OCR and rebuild text lines from the detected boxes.

    Returns (rows, score, hint). `rows` is a list of lists of cell strings,
    grouped into visual rows; `row_tol` scales how close boxes must be vertically
    to share a row.
    """
    boxes = _ocr_raw(np_img)
    if not boxes:
        return [], 0.0, 0

    boxes.sort(key=lambda b: b["yc"])
    med_h = sorted(b["h"] for b in boxes)[len(boxes) // 2] or 12.0
    thresh = med_h * row_tol

    rows = []
    cur = [boxes[0]]
    cur_y = boxes[0]["yc"]
    for b in boxes[1:]:
        if abs(b["yc"] - cur_y) <= thresh:
            cur.append(b)
            cur_y = sum(x["yc"] for x in cur) / len(cur)
        else:
            rows.append(cur)
            cur = [b]
            cur_y = b["yc"]
    rows.append(cur)

    text_rows = []
    for r in rows:
        r.sort(key=lambda b: b["x0"])
        text_rows.append([b["text"] for b in r if b["text"]])

    conf_sum = sum(b["conf"] for b in boxes)
    joined = " ".join(t for row in text_rows for t in row).upper()
    hint = sum(kw in joined for kw in ("PERFORMANCE", "DECLARED", "DIFFICULT", "HYBRID", "ACRO", "COACH CARD"))
    return text_rows, conf_sum + hint * 5, hint


def _from_image(path, routine, progress=None):
    def prog(pct, label=""):
        if progress:
            try:
                progress(pct, label)
            except Exception:
                pass

    try:
        import numpy as np
        from PIL import Image
    except Exception:
        return {
            "ok": True, "source": "image", "extracted": False, "elements": [],
            "time_seconds": None, "detected_routine": None,
            "notes": ["Photo reading needs 'rapidocr-onnxruntime' and 'Pillow' "
                      "(pip install rapidocr-onnxruntime)."],
        }
    try:
        _get_ocr()
    except Exception:
        return {
            "ok": True, "source": "image", "extracted": False, "elements": [],
            "time_seconds": None, "detected_routine": None,
            "notes": ["Photo OCR engine not installed — run "
                      "'pip install rapidocr-onnxruntime' to enable it."],
        }

    from PIL import ImageOps

    prog(4, "Preparing photo")
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    # NB: measured dead ends for OCR accuracy on the example cards — all made
    # reads WORSE (l->I, q->g, dropped trailing tokens) or were unusable:
    #   * whole-card upscale to 2200 px
    #   * grayscale + autocontrast + unsharp pre-processing
    #   * det box_thresh / unclip_ratio tuning
    #   * crop to the codes table + upscale (regressed CAN 6/7 -> 4/7)
    #   * PP-OCRv4 *server* rec model — >13 min per card on CPU (hangs)
    # The RapidOCR PP-OCRv4 mobile recogniser is the ceiling; the next real gain
    # needs a GPU for the server model, or a vision-LLM read.
    longest = max(img.size)
    if longest > 1150:
        s = 1150 / longest
        img = img.resize((int(img.width * s), int(img.height * s)))

    def read_at(angle, source):
        arr = np.array(source.rotate(-angle, expand=True))
        boxes = _ocr_raw(arr)
        els = _boxes_to_elements(boxes, routine)          # robust to wrapped rows
        best_rows = _ocr_lines(arr, row_tol=0.6)[0]
        for tol in (0.6, 0.9):
            r = _ocr_lines(arr, row_tol=tol)[0]
            e2 = _loose_rows_to_elements(r, routine)[0]
            if len(e2) > len(els):
                els, best_rows = e2, r
        # The box-based reader pairs each code with the number on its line (the
        # DD) more reliably; give its DD to rows the line-based reader kept.
        box_els = _boxes_to_elements(boxes, routine)
        for el in els:
            if "hybrid" not in el["type"].lower():
                m = next((x for x in box_els if x["code"] == el["code"] and x.get("dd") is not None), None)
                if m:
                    el["dd"] = m["dd"]   # box pairing is the more reliable one
        ts = _time_to_seconds("\n".join("  ".join(x) for x in best_rows))
        return best_rows, els, ts

    # 1) Fast orientation check with the detection model only (~0.5s/rotation).
    prog(10, "Checking orientation")
    best_angle, _crop = _best_orientation(img, np)

    # 2) One full OCR at that orientation.
    prog(30, "Reading text")
    rows, els, tsec = read_at(best_angle, img)

    # 3) Cheap safety net: if it read poorly, try the 180° flip once.
    if len(els) < 4:
        prog(72, "Re-reading")
        alt = (best_angle + 180) % 360
        r2, e2, t2 = read_at(alt, img)
        if len(e2) > len(els):
            rows, els, tsec, best_angle = r2, e2, t2, alt

    prog(96, "Matching elements")

    ocr_text = "\n".join("  ".join(r) for r in rows)
    notes = []

    # Last resort for acro cards: if row parsing barely worked, harvest every
    # token in the OCR text that has the shape of an acrobatic code so the coach
    # at least gets candidates to verify instead of a near-empty table.
    acro_type = _acro_type_for_routine(routine)
    if len(els) < 3 and acro_type in ("Acrobatic", "Team Acrobatic"):
        seen = {e["code"] for e in els}
        flat = ocr_text.replace("\n", " ")
        shape = re.compile(r"[A-Za-z0-9]{2,}(?:[-/][A-Za-z0-9.,>^+*!]+){2,}")
        for tok in shape.findall(flat):
            tok = tok.strip(" -/")
            if len(tok) < 9 or tok in seen or _BASE_LABEL_RE.match(tok):
                continue
            seen.add(tok)
            els.append({"type": acro_type, "group": "", "code": tok, "slot": ""})
        if len(els) > 0:
            notes.append("Some rows were harvested loosely from the text — "
                         "set the group and fix the code on each.")

    base_notes = [
        "Read from a photo by OCR — check EVERY code character by character, "
        "OCR frequently misreads codes (0/O, 1/l, +/*, sub/superscripts).",
    ]
    if best_angle:
        base_notes.append(f"Rotated the photo {best_angle}° to read it.")
    if len(els) < 3:
        base_notes.append(
            "OCR could only make out part of this card — use the raw text below "
            "and the picture to fill the rest in."
        )

    return {
        "ok": True, "source": "image", "extracted": bool(els), "elements": els,
        "time_seconds": tsec, "detected_routine": _detect_routine(ocr_text),
        "detected_federation": _detect_federation(ocr_text),
        "detected_theme": _detect_theme(ocr_text),
        "ocr_text": ocr_text,
        "notes": base_notes + notes,
    }


# Tolerate common OCR slips in "ACRO" (ACR0 / AGRO / ACRO. / ACRO A / ACROA).
_ACRO_LINE_RE = re.compile(r"A[CG]R[O0][\s.\-_]*([ABCP])(?![A-Za-z])", re.I)
_OTHER_KW_RE = re.compile(r"(HYBR[I1l]D|\bHYB\b|\bTRE\b|\bSSC\b)", re.I)


def _clean_hybrid_ocr(s: str) -> str:
    """Fix ways OCR fragments a *single* hybrid token, before re-spacing:
    'F6c* 0, 5' -> 'F6c*0.5', 'F1 0' -> 'F10', '2 R1' -> '2R1'. Deliberately
    narrow so it can't glue two real codes together."""
    s = re.sub(r"[*xX×＊]\s*0?\s*[.,]?\s*([35])(?![0-9]*[.,])", r"*0.\1", s)  # *0,5 / *0,52RB -> *0.5
    s = re.sub(r"(?<=[0-9a-z])([FT])B(?![a-z])", r"\g<1>8", s)          # F8/T8 misread as FB/TB
    s = re.sub(r"^([FT])B(?![a-z])", r"\g<1>8", s)
    s = re.sub(r"\b([TSRFAC])(\d)\s+(\d)(?![0-9])", r"\1\2\3", s)        # F1 0 -> F10
    s = re.sub(r"\b(\d)\s+(R[DCU]\d|2R[0-9B]|RB\d)", r"\1\2", s)         # 2 R1 -> 2R1
    # RC1 / RD1 / RU1 / SC1 / SCD1 / RO1 ... often come out with a stray space
    s = re.sub(r"\b([RS])\s+([CDOU][A-Z]?\d{0,2}|B)\b", r"\1\2", s)
    s = re.sub(r"\b(S?[CR]D?|SC)\s+(\d{1,2}|B)\b", r"\1\2", s)
    return s


def _clean_acro_ocr(code: str) -> str:
    """Fix OCR slips inside team/open acro codes that swap a LETTER for a DIGIT
    ('rO,5'->'r0,5', '20W'->'2ow', 's20'->'s2o').

    Deliberately does NOT touch letter case, and never adds or removes a
    character. Two earlier rules did — lowercasing a capitalised position
    segment ('Ow'->'ow', 'Vs'->'vs', 'oW'->'ow') and turning a bare 'g' into
    'gl' — and both were removed: a wrong case or a missing letter on the card
    is a coach error the checker must REPORT, not silently repair. Case
    differences are now surfaced as a "check the case" note instead (see
    `_snap_elements`)."""
    if not code:
        return code

    # P2 marker '2' + misread 'o' ("2ow" -> "20W"): a '0' wedged between a lone
    # '2' and letters is an 'o'. (The 'W' keeps its case now — reported, not fixed.)
    code = re.sub(r"(?<![0-9])(2)0(?=[A-Za-z])", r"\1o", code)  # 20W -> 2oW
    code = re.sub(r"(?<=[rR])O(?=[.,]?\d)", "0", code)      # rO,5 -> r0,5
    code = re.sub(r"(?<=\d)O(?=[.,]?\d)", "0", code)        # 1O5 -> 105
    code = re.sub(r"O(?=[.,]\d)", "0", code)                # O,5 -> 0,5
    # a trailing '0' straight after a digit is a misread 'o' ("open"): no acro
    # code ends in <digit>0, but s2o / Cs1t2o / Ps1o do.
    code = re.sub(r"(?<=\d)0(?=$|[-/+])", "o", code)        # s20 -> s2o
    return code


def _clean_pair_ocr(code: str) -> str:
    """Pair-acro codes have a fixed casing convention: base jump/lift (J/L/S/W/SL)
    and position markers (B/F) are UPPERCASE, the modifier letters (f/d/p/s/r/t)
    are lowercase. OCR sometimes returns the whole thing in caps ('JFS1B') — put
    the casing back."""
    if not code:
        return code
    m = re.match(r"^([A-Z]{2,3}-)?", code)          # keep a federation prefix
    pre, body = m.group(0), code[m.end():]
    letters = re.sub(r"[^A-Za-z]", "", body)
    if len(letters) >= 2 and letters == letters.upper():
        body = body.lower()
        body = re.sub(r"^(sl|j|l|s|w)", lambda x: x.group(1).upper(), body)
        body = re.sub(r"(?<=\d)([bf])", lambda x: x.group(1).upper(), body)
        body = re.sub(r"([bf])$", lambda x: x.group(1).upper(), body)
    return pre + body


def _respace_hybrid(code: str) -> str:
    """OCR often drops the spaces between hybrid codes ('A1cA8*0.5F5aRD1'). Put a
    break before each token that starts a new hybrid code, then just collapse
    whitespace (never re-join)."""
    code = _clean_hybrid_ocr(re.sub(r"\s+", " ", (code or "").strip()))
    # break before a family code. Lookbehind must be a code-ENDING char only
    # (alnum / + / ]), never '.' '*' ',' which sit inside a *0.5 factor. No
    # leading-digit class here (it would cut 'F10' or '2R1' in half) — the
    # leading-digit codes are listed explicitly in the second pass.
    # leading-digit codes first (2R1, 2RB), so the generic split below can then
    # skip a family letter that sits right after a code-leading digit.
    code = re.sub(
        r"(?<=[0-9A-Za-z)\]+])(?=(?:2RB|2R[0-9]|AB|[RSC]B|[RS][CDOU][B0-9]?|RD\d|RC\d|RU\d))",
        " ", code,
    )
    code = re.sub(r"(?<=[0-9A-Za-z)\]+])(?<!\s\d)(?=[TSRFAC][0-9a-z])", " ", code)
    # the generic split still cuts RC1/RD1/RU1/SC1/SCD1/RO1 and 2R1 in half — stitch back
    code = re.sub(r"\b([RS]C?) (D\d{1,2})\b", r"\1\2", code)         # SC D1 -> SCD1
    code = re.sub(r"\b([RS]) ([CDOU][A-Z]?\d{0,2}|B)\b", r"\1\2", code)
    code = re.sub(r"\b(S?[CR]D?|SC) (\d{1,2}|B)\b", r"\1\2", code)
    code = re.sub(r"\b([12]) (R[0-9B])", r"\1\2", code)
    return re.sub(r"\s{2,}", " ", code).strip()


# The construction (first) token can itself carry '>' or '^' — the Group C
# throws are 'Thr>St', 'Thr>StH', 'Thr>F', 'Thr^2F', 'Thr>StH>1F' ... so the
# first token must accept '>' too, or 'Thr>St-...' gets clipped to 'St-...'.
_ACRO_CODE_RE = re.compile(r"^\d{0,2}[A-Za-z][\w^+*>]*(?:[-/][\w/^+*.,>!]+){2,}$")
# one hybrid family code: opt. 1-2 digits, family letter, a digit, opt. 1-2 letters
_HYB_TOKEN = r"(?:[0-9]{0,2}[TSRFAC][0-9][0-9a-z]{0,2}|AB|SB|2RB|2R[0-9])(?:\*[0-9][.,][0-9])?\+?"
_HYB_RUN_RE = re.compile(r"(?:%s){3,}" % _HYB_TOKEN)
_JUNK = {
    "SIGNATURE", "COACHCARD", "TEAMMANAGER", "DECLAREDDIFFICULTY", "BASEMARK",
    "PERFORMANCE", "ELEMENTSINORDEROFPERFORMANCE", "COMPETITION", "FEDERATION",
    "PRELIMINARIES", "BONUS", "DDTOT",
}


def _hybrid_run(s: str) -> bool:
    """True if the string is a run of 3+ real hybrid family codes."""
    s2 = re.sub(r"\s+", "", s or "")
    if len(s2) < 6 or "-" in s2 or "/" in s2 or s2.upper() in _JUNK:
        return False
    return bool(_HYB_RUN_RE.fullmatch(s2))


_PAIR_LABEL_RE = re.compile(
    r"A[CG]R[O0][\s\-_]*P[A4@]?[Il1]R|P[A4@][Il1]R[\s\-_]*A[CG]R[O0]", re.I
)
_PAIR_CODE_RE = re.compile(r"^[A-Za-z]{1,4}[0-9][A-Za-z]?(?:[.,][0-9])?$")
# a plausible pair-acro code token: starts with a letter, short, only code chars
# (no ':' so a time range can't slip in, no run of label text)
_PAIR_LIKE_RE = re.compile(r"^(?:[A-Z]{2,3}-)?[A-Za-z][A-Za-z0-9!+.,>»/]{1,13}$")
# a token that is clearly a pair-acro code (base jump/lift + modifiers) even with
# no ACRO-PAIR label nearby — used only for Duet routines.
_PAIR_STRONG_RE = re.compile(
    r"^(?:[A-Z]{2,3}-)?(?:SL|J|L|S|W)!?"
    r"(?:2|f|d|p|B|F|s0?[.,]?5|s1|ss1|r0?[.,]?5|r1(?:[.,]5)?|t0?[.,]?5|t1"
    r"|\+(?:pf|f|r0?[.,]?5|r1)|>|»)+$"
)
# acro code shape as a SUBSTRING (box may also hold "TRA ... 3.625")
_ACRO_SUB_RE = re.compile(r"\d{0,2}[A-Za-z][\w^+*>]*(?:[-/][\w^+*.,>!]{1,}){2,}")


def _boxes_to_elements(boxes, routine):
    """Build elements straight from OCR boxes (no row reconstruction). The
    DECLARED DIFFICULTY cell is usually a single box, so this survives photos
    where rows wrap or skew. Group / pair letters come from the nearest label."""
    acro_labels, pair_labels = [], []
    for b in boxes:
        if _PAIR_LABEL_RE.search(b["text"]):
            pair_labels.append((b["yc"], b["xc"]))
            continue
        m = _ACRO_LINE_RE.search(b["text"])
        if m and len(b["text"]) <= 9:
            acro_labels.append((b["yc"], b["xc"], m.group(1).upper()))

    acro_type = _acro_type_for_routine(routine)
    want_pair = "duet" in (routine or "").lower()
    out = []
    nums = [(b["yc"], b["xc"], _parse_dd(b["text"])) for b in boxes if _parse_dd(b["text"]) is not None]
    for b in boxes:
        t = b["text"].strip().strip("|").strip()
        if _BASE_LABEL_RE.match(t) or _PAIR_LABEL_RE.search(t):
            continue
        acro_hit = t if (_ACRO_CODE_RE.match(t) and len(t) >= 9) else None
        if not acro_hit:  # box may be "TRA  Sq-Back-tk-s2o  3.625"
            for sub in _ACRO_SUB_RE.findall(t):
                if len(sub) >= 9 and (sub.count("-") + sub.count("/")) >= 2:
                    acro_hit = sub
                    break
        if acro_hit and (acro_hit.count("-") + acro_hit.count("/")) >= 2:
            grp = ""
            if acro_labels:
                grp = min(acro_labels, key=lambda L: abs(L[0] - b["yc"]) + 0.15 * abs(L[1] - b["xc"]))[2]
            etype = "Pair Acrobatic" if want_pair else acro_type
            code = _clean_pair_ocr(acro_hit) if want_pair else _clean_acro_ocr(acro_hit)
            out.append({"t": etype, "g": "" if want_pair else grp, "c": code, "y": b["yc"], "x": b["xc"]})
        elif want_pair and _PAIR_STRONG_RE.match(t):
            out.append({"t": "Pair Acrobatic", "g": "", "c": _clean_pair_ocr(t), "y": b["yc"], "x": b["xc"]})
        elif _hybrid_run(t):
            out.append({"t": "Hybrid", "g": "", "c": _respace_hybrid(t), "y": b["yc"], "x": b["xc"]})
        elif (
            pair_labels
            and _PAIR_LIKE_RE.match(t)
            and _PAIR_CODE_RE.match(t)
            and re.search(r"\d", t)
            and min(abs(pl[0] - b["yc"]) for pl in pair_labels) < 60
        ):
            out.append({"t": "Pair Acrobatic", "g": "", "c": _clean_pair_ocr(t), "y": b["yc"], "x": b["xc"]})

    out.sort(key=lambda e: (round(e["y"] / 20.0), e["x"]))
    elems, prev = [], None
    ys = sorted(e["y"] for e in out)
    gaps = [b - a for a, b in zip(ys, ys[1:]) if b - a > 4]
    tol = max(6.0, 0.45 * (sorted(gaps)[len(gaps) // 2] if gaps else 30.0))
    for e in out:
        if prev and prev["c"] == e["c"] and prev["t"] == e["t"]:
            continue
        el = {"type": e["t"], "group": e["g"], "code": e["c"], "slot": ""}
        is_acro = "acro" in e["t"].lower()
        same_line = [n for n in nums if abs(n[0] - e["y"]) <= tol and n[1] > e["x"]
                     and (not is_acro or n[2] <= _ACRO_DD_MAX)]
        if same_line:
            # the number whose line is closest (a tilted photo shifts the DD column)
            el["dd"] = min(same_line, key=lambda n: abs(n[0] - e["y"]))[2]
        elems.append(el)
        prev = e
    return elems


def _loose_rows_to_elements(rows, routine):
    """Position-independent parse: for each line that names an element (ACRO-x /
    HYBRID / TRE), take the code-like token(s) on the line as the code. Used for
    OCR output where column alignment is unreliable. ACRO-x wins over the word
    'HYBRID' appearing in a BASE MARK cell like 'BM-Hybrid'."""
    elements = []
    last_time = None
    carry_grp = None  # an "ACRO-x" label seen on a line whose code sat on the next
    hyb_prefix = ""   # hybrid codes from a wrapped line above (no PART keyword)
    for cells in rows:
        line = " ".join(cells).strip()
        t = _time_to_seconds(line)
        if t is not None:
            last_time = t if last_time is None else max(last_time, t)

        want_pair = "duet" in (routine or "").lower()
        strong_pair = next(
            (c for c in cells if want_pair and _PAIR_STRONG_RE.match(c.strip())), None
        )
        if _PAIR_LABEL_RE.search(line) or strong_pair:
            etype, grp = "Pair Acrobatic", ""
        else:
            am = _ACRO_LINE_RE.search(line)
            if am:
                part = "ACRO-" + am.group(1).upper()
            else:
                om = _OTHER_KW_RE.search(line)
                if not om:
                    # no element keyword — but if the line right above named an
                    # ACRO-x and this line carries an acro-shaped code, use it.
                    if carry_grp and _ACRO_SUB_RE.search(line):
                        sub = max(_ACRO_SUB_RE.findall(line), key=len)
                        if len(sub) >= 9 and (sub.count("-") + sub.count("/")) >= 2:
                            elements.append({
                                "type": _acro_type_for_routine(routine),
                                "group": carry_grp,
                                "code": _clean_acro_ocr(sub), "slot": "",
                            })
                            carry_grp = None
                    else:
                        # a wrapped hybrid line: a time range + a run of hybrid
                        # codes but no PART keyword -> stash for the next HYBRID.
                        toks = [c for c in cells
                                if _looks_like_code(c) and not _BASE_LABEL_RE.match(c)]
                        run = _respace_hybrid(" ".join(toks))
                        if (re.search(r"\d{1,2}:\d{2}", line)
                                and re.search(r"[TSRFAC]\d", run)
                                and "-" not in run and "/" not in run):
                            hyb_prefix = (hyb_prefix + " " + run).strip()
                    continue
                part = om.group(1)
            etype, grp = _classify(part, line, routine)
            if not etype:
                continue

        # remember an acro label that had no code on its own line
        if etype in ("Team Acrobatic", "Acrobatic") and not [
            c for c in cells if _ACRO_SUB_RE.search(c) and len(c) >= 9
        ]:
            carry_grp = grp or carry_grp
        else:
            carry_grp = None

        cands = [c for c in cells if _looks_like_code(c) and not _BASE_LABEL_RE.match(c)]
        if etype == "Pair Acrobatic":
            if strong_pair:
                cands = [strong_pair]
            else:
                cands = [c for c in cands if _PAIR_LIKE_RE.match(c)]
        is_hyb = "hybrid" in etype.lower()
        if not cands and not (is_hyb and hyb_prefix):
            continue
        if is_hyb:
            code = _respace_hybrid((hyb_prefix + " " + " ".join(cands)).strip())
            hyb_prefix = ""
        elif etype == "Pair Acrobatic":
            hyb_prefix = ""
            code = _clean_pair_ocr(min(cands, key=len))
        else:
            hyb_prefix = ""
            code = _clean_acro_ocr(max(cands, key=len))
        slot = ""
        for c in cells:
            if re.search(r"\d{1,2}:\d{2}", c):
                slot = c.replace(" ", "")
                break
        el = {"type": etype, "group": grp, "code": code, "slot": slot}
        # only numbers to the RIGHT of the code cell (the EL number sits left)
        # locate the RAW code cell (the cleaned code may differ: 'Cr0,51' -> 'Cr0,5!')
        raw = (min(cands, key=len) if etype == "Pair Acrobatic" else max(cands, key=len)) if cands else ""
        idx = cells.index(raw) if raw in cells else next(
            (i for i, c in enumerate(cells) if code and c and (c in code or code in c)), len(cells) - 1)
        after = cells[idx + 1:]
        dds = [_parse_dd(c) for c in after if _parse_dd(c) is not None
               and (_parse_dd(c) <= _ACRO_DD_MAX)]
        if dds and not is_hyb:
            el["dd"] = dds[-1]
        elements.append(el)
    return elements, last_time, []


def _from_pdf(path, routine):
    try:
        from pypdf import PdfReader
    except Exception:
        return {
            "ok": True, "source": "pdf", "extracted": False, "elements": [],
            "time_seconds": None, "detected_routine": None,
            "notes": ["PDF support needs the 'pypdf' package (pip install pypdf)."],
        }
    reader = PdfReader(path)
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    if not text.strip():
        return {
            "ok": True, "source": "pdf", "extracted": False, "elements": [],
            "time_seconds": None, "detected_routine": None,
            "notes": ["This PDF has no text layer (likely a scan) — transcribe by hand."],
        }
    # Turn each line into a pseudo-row split on 2+ spaces / tabs.
    rows = [re.split(r"\s{2,}|\t", ln.strip()) for ln in text.splitlines() if ln.strip()]
    els, tsec, notes = _rows_to_elements(rows, routine)
    return {
        "ok": True, "source": "pdf", "extracted": bool(els), "elements": els,
        "time_seconds": tsec, "detected_routine": _detect_routine(text),
        "detected_federation": _detect_federation(text),
        "detected_theme": _detect_theme(text),
        "notes": notes + ["PDF table parsing is approximate — check every line."],
        "_raw_text": text,
    }


_SCRIPTS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skill", "scripts")
)
_DOT_DEC_RE = re.compile(r"(?<=\d)\.(?=\d)")


def _snap_elements(elements):
    """Pull OCR'd acro codes onto the official catalogue.

    Applies ONLY unambiguous same-length look-alike character swaps ('Cr0,51'
    vs 'Cr0,5!', '2In' vs '2ln', 'Sg' vs 'Sq'). Never adds, removes or
    re-cases a character — those are coach errors to report, not repair.

    Returns `(fixes, case_checks)`, both lists of display strings.
    """
    try:
        if _SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, _SCRIPTS_DIR)
        from code_validator import snap_team_acro_code  # type: ignore
    except Exception:
        return [], []

    fixes, cases = [], []
    for el in elements:
        code = el.get("code") or ""
        etype = (el.get("type") or "").lower()
        if not code or "pair" in etype or "hybrid" in etype:
            continue
        try:
            new, changes, case_checks = snap_team_acro_code(
                code, el.get("group") or None
            )
        except Exception:
            continue
        # the card writes decimals with a comma; don't "correct" that too.
        # Test for any comma, not just <digit>,<digit> — OCR slips like
        # 's2to,5f' put a letter before it.
        commas = "," in code

        def _disp(s, _c=commas):
            return _DOT_DEC_RE.sub(",", s) if _c else s

        if changes:
            if commas:
                new = _DOT_DEC_RE.sub(",", new)
            if new != code:
                el["code"] = new
                el["snapped"] = True
                for label, a, b in changes:
                    fixes.append(f"{_disp(a)} -> {_disp(b)} ({label})")
        if case_checks:
            el["case_check"] = True
            for label, a, b in case_checks:
                cases.append(
                    f"{label}: read “{_disp(a)}”, catalogue has "
                    f"“{_disp(b)}”"
                )
    return fixes, cases


_EVENTS = None


def _routine_total(routine):
    """Number of declared elements the routine allows (Appendix 3), or None."""
    global _EVENTS
    if not routine:
        return None
    if _EVENTS is None:
        try:
            import json as _json
            p = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "..", "skill", "appendix3_requirements.json"))
            with open(p, "r", encoding="utf-8") as f:
                _EVENTS = _json.load(f).get("events", {})
        except Exception:
            _EVENTS = {}
    ev = _EVENTS.get(routine) or {}
    return ev.get("total_required_elements")


_TYPE_WORD_RE = re.compile(r"HYBRID|ACRO|TRE\b|TECH|PAIR|SuCon|CONNECTION", re.I)


def _multi_card_signs(result, routine):
    """Hints that a photo / PDF shows more than one coach card (or pieces of
    other cards): headers seen twice, element numbers starting again at 1,
    more elements than the routine allows."""
    text = result.get("ocr_text") or result.get("_raw_text") or ""
    signs = []
    if len(re.findall(r"COACH\s*CARD", text, re.I)) >= 2:
        signs.append("the “COACH CARD” title appears twice")
    elif len(re.findall(r"ELEMENTS?\s*IN\s*ORDER", text, re.I)) >= 2:
        signs.append("the “Elements in order of performance” header appears twice")
    elif len(re.findall(r"DD\s*TOT", text, re.I)) >= 2:
        signs.append("two “DD TOT.” lines were found")
    elif len(re.findall(r"EDERATION\s*/\s*CLUB", text, re.I)) >= 2:
        signs.append("the “Federation / Club” line appears twice")

    # Element numbers (EL column) starting again at 1 after 3 or more elements.
    nums = []
    for ln in text.splitlines():
        if not _TYPE_WORD_RE.search(ln):
            continue
        cells = [c.strip() for c in re.split(r"\s{2,}|\t", ln) if c.strip()]
        n = next((int(c) for c in cells if re.fullmatch(r"\d{1,2}", c)), None)
        if n is not None and 1 <= n <= 20:
            nums.append(n)
    restart = any(n == 1 and max(nums[:i] or [0]) >= 3 for i, n in enumerate(nums))
    if restart:
        signs.append("the element numbers start again at 1")

    total = _routine_total(routine)
    els = [e for e in (result.get("elements") or [])
           if "surface connection" not in (e.get("type") or "").lower()]
    if total and len(els) > total:
        signs.append(f"{len(els)} elements were read but this routine has {total}")
    return signs


def _post_process(result, routine=None):
    """Catalogue snapping + the notes that explain it, on every source."""
    try:
        els = result.get("elements") or []
        fixes, cases = _snap_elements(els)
        notes = result.setdefault("notes", [])
        if fixes:
            notes.append(
                "Snapped to the catalogue (OCR look-alikes): " + "; ".join(fixes)
                + " — confirm each against the card."
            )
        if cases:
            notes.append(
                "CHECK THE CASE — " + "; ".join(cases) + ". Left exactly as "
                "read, on purpose: this is either an OCR misread of the photo, "
                "or a real mistake on the coach card. Compare with the picture — "
                "if the card really shows the catalogue spelling, correct it "
                "here; if the card itself is wrong, leave it so the report "
                "flags it."
            )
    except Exception:
        pass
    try:
        if result.get("source") in ("image", "pdf"):
            signs = _multi_card_signs(result, routine)
            if signs:
                result["multi_card"] = True
                result.setdefault("notes", []).insert(0,
                    "WARNING — this photo may show more than one coach card ("
                    + "; ".join(signs) + "). Lines from another card may have been "
                    "added: compare every row with the card on the left and delete "
                    "the extra ones — or retake the photo with only this card in the frame.")
    except Exception:
        pass
    result.pop("_raw_text", None)
    return result


def extract_coach_card(path, filename, routine=None, progress=None):
    def prog(pct, label=""):
        if progress:
            try:
                progress(pct, label)
            except Exception:
                pass

    ext = os.path.splitext(filename or path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        prog(30, "Reading spreadsheet")
        r = _from_xlsx(path, routine)
        prog(96, "Matching elements")
        return _post_process(r, routine)
    if ext == ".pdf":
        prog(30, "Reading PDF")
        r = _from_pdf(path, routine)
        prog(96, "Matching elements")
        return _post_process(r, routine)
    if ext in (".png", ".jpg", ".jpeg", ".jfif", ".webp", ".gif", ".bmp"):
        try:
            return _post_process(_from_image(path, routine, progress=progress), routine)
        except Exception as exc:  # never let OCR crash the request
            return {
                "ok": True, "source": "image", "extracted": False, "elements": [],
                "time_seconds": None, "detected_routine": None,
                "notes": [f"Photo OCR failed ({exc}). Type the elements by hand "
                          f"using the picture on the left."],
            }
    return {"ok": False, "error": "unsupported_type",
            "notes": [f"Unsupported file type: {ext or '?'}"]}
