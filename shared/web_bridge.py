"""Browser bridge for the Coach Card Checker (runs in Pyodide).

Photos are read by ocr.js (same PP-OCRv4 models and the same post-processing
as RapidOCR on the desktop app). This module then runs EXACTLY the steps that
extract._from_image runs after OCR, on the boxes ocr.js returns, so a photo
gives the same elements in the browser as in the desktop app — without numpy
or Pillow.

Excel / PDF go through extract.extract_coach_card unchanged.
"""
import json
import re

import extract as ex


def _to_boxes(raw):
    """ocr.js items {box: [[x,y]x4], text, conf} -> the dicts _ocr_raw returns."""
    out = []
    for it in raw or []:
        box = it["box"]
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        out.append({
            "text": str(it["text"]).strip(),
            "conf": float(it.get("conf") or 0.0),
            "yc": sum(ys) / 4.0,
            "xc": sum(xs) / 4.0,
            "x0": min(xs),
            "h": max(ys) - min(ys),
        })
    return out


def _read(raw, routine):
    """= extract._from_image.read_at(), on pre-computed OCR boxes."""
    boxes = _to_boxes(raw)
    ex._ocr_raw = lambda _arr, _b=boxes: _b      # every caller gets these boxes
    els = ex._boxes_to_elements(boxes, routine)
    best_rows = ex._ocr_lines(None, row_tol=0.6)[0]
    for tol in (0.6, 0.9):
        r = ex._ocr_lines(None, row_tol=tol)[0]
        e2 = ex._loose_rows_to_elements(r, routine)[0]
        if len(e2) > len(els):
            els, best_rows = e2, r
    box_els = ex._boxes_to_elements(boxes, routine)
    for el in els:
        if "hybrid" not in el["type"].lower():
            m = next((x for x in box_els if x["code"] == el["code"] and x.get("dd") is not None), None)
            if m:
                el["dd"] = m["dd"]
    ts = ex._time_to_seconds("\n".join("  ".join(x) for x in best_rows))
    return best_rows, els, ts


def count_elements(raw_json, routine):
    """How many elements the main orientation gives (JS decides on a 180° retry)."""
    try:
        return len(_read(json.loads(raw_json), routine or None)[1])
    except Exception:
        return 0


def from_ocr(raw_json, angle, routine, alt_json=None, alt_angle=None, filename=""):
    """= the end of extract._from_image + extract._post_process."""
    routine = routine or None
    try:
        rows, els, tsec = _read(json.loads(raw_json), routine)
        best_angle = int(angle or 0)
        if alt_json:
            r2, e2, t2 = _read(json.loads(alt_json), routine)
            if len(e2) > len(els):
                rows, els, tsec, best_angle = r2, e2, t2, int(alt_angle or 0)

        ocr_text = "\n".join("  ".join(r) for r in rows)
        notes = []
        acro_type = ex._acro_type_for_routine(routine)
        if len(els) < 3 and acro_type in ("Acrobatic", "Team Acrobatic"):
            seen = {e["code"] for e in els}
            flat = ocr_text.replace("\n", " ")
            shape = re.compile(r"[A-Za-z0-9]{2,}(?:[-/][A-Za-z0-9.,>^+*!]+){2,}")
            for tok in shape.findall(flat):
                tok = tok.strip(" -/")
                if len(tok) < 9 or tok in seen or ex._BASE_LABEL_RE.match(tok):
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
            base_notes.append("OCR could only make out part of this card — use the raw "
                              "text below and the picture to fill the rest in.")
        result = {
            "ok": True, "source": "image", "extracted": bool(els), "elements": els,
            "time_seconds": tsec, "detected_routine": ex._detect_routine(ocr_text),
            "detected_federation": ex._detect_federation(ocr_text),
            "detected_theme": ex._detect_theme(ocr_text),
            "ocr_text": ocr_text, "notes": base_notes + notes,
        }
        result = ex._post_process(result)
    except Exception as exc:  # never let OCR crash the page
        result = {"ok": True, "source": "image", "extracted": False, "elements": [],
                  "time_seconds": None, "detected_routine": None,
                  "notes": [f"Photo reading failed ({exc}). Type the elements by hand "
                            f"using the picture on the left."]}
    result.setdefault("filename", filename)
    return json.dumps(result)


def from_file(path, filename, routine):
    """Excel / PDF: the desktop code, unchanged."""
    try:
        result = ex.extract_coach_card(path, filename, routine or None)
    except Exception as exc:
        result = {"ok": False, "error": "extract_error", "message": str(exc),
                  "notes": [f"Could not read the file ({exc})."]}
    result.setdefault("filename", filename)
    return json.dumps(result)


def run_check(payload_json):
    """= server._api_check."""
    from check_coach_card import check
    try:
        payload = json.loads(payload_json or "{}")
        routine = (payload.get("routine") or "").strip()
        declared = payload.get("declared") or {}
        if not routine:
            return json.dumps({"ok": False, "error": "missing_routine"})
        report = check(routine, declared)
        data = report.to_dict()
        data["ok"] = True
        try:
            data["text"] = report.to_text()
        except Exception:
            data["text"] = ""
        return json.dumps(data)
    except Exception as exc:
        return json.dumps({"ok": False, "error": "engine_error", "message": str(exc)})


def routines():
    """= server._api_routines."""
    from check_coach_card import load_requirements
    reqs = load_requirements()
    out = []
    for name, spec in reqs.get("events", {}).items():
        out.append({
            "name": name,
            "age_group": spec.get("age_group", "?"),
            "time_seconds": spec.get("time_seconds"),
            "total_required_elements": spec.get("total_required_elements"),
            "elements": spec.get("elements", {}),
            "rules": spec.get("rules", []),
        })
    return json.dumps({
        "ok": True, "routines": out,
        "rules_version": reqs.get("_meta", {}).get("rules_version", ""),
        "element_type_aliases": reqs.get("element_type_aliases", {}),
        "family_coverage_rule": reqs.get("family_coverage_rule", {}),
    })
