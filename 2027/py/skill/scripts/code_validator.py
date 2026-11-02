#!/usr/bin/env python3
"""Validate coach-card codes against official Excel catalogues + compatibility grids."""
from __future__ import annotations
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.normpath(os.path.join(HERE, ".."))


def _load(name):
    with open(os.path.join(SKILL_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)


_HYBRID = None
_PAIR = None
_TEAM = None
_COMPAT = None


def _ensure_loaded():
    global _HYBRID, _PAIR, _TEAM, _COMPAT
    if _HYBRID is None:
        _HYBRID = _load("catalogue_hybrid.json")
    if _PAIR is None:
        _PAIR = _load("catalogue_pair_acro.json")
    if _TEAM is None:
        _TEAM = _load("catalogue_team_acro.json")
    if _COMPAT is None:
        _COMPAT = _load("compatibility_grids.json")


_FACTOR_RE = re.compile(r"[\*xX]\s*0?[\.,][35]\b|\(\s*0?[\.,][35]\s*\)")

# European coach cards write decimals with a comma ("r0,5", "2F0,5", "Css1t2,5").
# The catalogues (built from the Excel files) use a dot. Normalise any comma that
# sits between two digits before doing catalogue look-ups.
_DECIMAL_COMMA_RE = re.compile(r"(?<=\d),(?=\d)")


_PREFIXED = None


def prefixed_codes():
    """{prefixed 2026 code: base code}, longest first. Since January 1 2027 the
    former federation-reserved acrobatics are open to all and are written
    without the prefix; check_coach_card flags a prefix as an error, and here
    the prefix is dropped so the rest of the code is validated normally."""
    global _PREFIXED
    if _PREFIXED is None:
        try:
            data = _load("prefixed_codes_2027.json").get("codes", {})
        except Exception:
            data = {}
        _PREFIXED = dict(sorted(((k, v["base"]) for k, v in data.items()),
                                key=lambda kv: len(kv[0]), reverse=True))
    return _PREFIXED


def strip_federation_prefixes(code):
    for pref, base in prefixed_codes().items():
        if pref in code:
            code = code.replace(pref, base)
    return code


def _normalize_code(code):
    if not code:
        return code
    return strip_federation_prefixes(_DECIMAL_COMMA_RE.sub(".", code))


# OCR routinely confuses the letter O with the digit 0 and flips the case of
# isolated letters. Rather than hard-fail a code that only differs from the
# catalogue by case or O/0, we match it "loosely" and report the difference as
# a single note at the end of the report. `_loose_key` collapses both.
_LOOSE_OO = str.maketrans("Oo", "00")


def _loose_key(s):
    return (s or "").translate(_LOOSE_OO).lower()


def _strip_factor(code):
    return _FACTOR_RE.sub("", code).strip()


_SEP_RE = re.compile(r"[>^+\-/]")


def _suggest(code, valid):
    candidates = set()
    for v in valid:
        if v.startswith(code):
            candidates.add(v)
    if not candidates and code and code[-1].isalpha():
        stem = code[:-1]
        for v in valid:
            if v.startswith(stem):
                candidates.add(v)
    # Separator-insensitive match: a card may drop the ">"/"^"/"+" that the
    # catalogue keeps (e.g. "ThrSt" -> "Thr>St", "LspotL" -> "L+spot").
    if not candidates and code:
        bare = _SEP_RE.sub("", code).lower()
        if bare:
            for v in valid:
                if _SEP_RE.sub("", v).lower().startswith(bare):
                    candidates.add(v)
    return sorted(candidates)[:5]


MIN_REQUIREMENTS = {
    "A": ["Construction", "Direction", "Position1"],
    "B": ["Construction", "Connection", "Position1"],
    "C": ["Construction", "Direction", "Position1"],
    "P": ["Construction", "Connection", "Position1"],
}


# --------------------------------------------------------------------
# OCR-aware catalogue snapping
# --------------------------------------------------------------------
# Characters the recogniser cannot tell apart on a photo of small print.
#
# STRICT RULES — these encode what is an OCR fault vs. a coach's mistake:
#   * NO letter is ever added or removed. A missing or extra letter is exactly
#     the kind of error the checker exists to REPORT. Hence same-length only.
#   * NO case is ever changed. 'Bln' vs 'bln' is a coach mistake to flag, not
#     an OCR artefact to paper over. So a class never holds the same letter in
#     both cases — 'o'/'O' are kept apart, each only confusable with '0'.
#   * Only genuinely different characters that LOOK alike are swapped:
#     lowercase-L / capital-i / one / bang, letter-o / digit-zero, g / q ...
_OCR_CLASSES = (
    "lI1!|",        # thin verticals — by far the biggest source of error here
    "o0", "O0",     # letter-o vs digit-zero, per case; never o <-> O
    "gq9",
    "s5", "S5",
    "z2", "Z2",
    "b6", "B8",
    "uv", "UV",
    "nh",
    ".,",           # decimal separator: the card writes ',', catalogues '.'
)

_OCR_NEAR: dict[str, set] = {}
for _cls in _OCR_CLASSES:
    for _ch in _cls:
        _OCR_NEAR.setdefault(_ch, set()).update(set(_cls) - {_ch})

_SUB_NEAR = 0.4     # one OCR look-alike swap
_SUB_FAR = 1.0      # a genuinely different character -> disqualifies
_SNAP_MAX = 0.85    # so: at most two look-alike swaps, never a real difference
_SNAP_MARGIN = 0.3  # ...and only if the runner-up is clearly worse


def _ocr_dist(a, b):
    """Weighted Hamming distance over OCR look-alikes, or None if `b` is not a
    candidate at all.

    Deliberately NOT Levenshtein: differing lengths return None, so a segment is
    never repaired by inventing or dropping a character. Case changes cost full
    price (see `_OCR_CLASSES`), which puts them out of range too.
    """
    if len(a) != len(b):
        return None
    d = 0.0
    for ca, cb in zip(a, b):
        if ca == cb:
            continue
        d += _SUB_NEAR if cb in _OCR_NEAR.get(ca, ()) else _SUB_FAR
        if d > _SNAP_MAX:
            return None
    return d


def _snap_ex(value, vocab):
    """Return `(replacement, case_variant)` for one OCR'd segment.

    `replacement` is a catalogue entry to substitute, or None to leave the
    segment exactly as read. `case_variant` is set when the segment matches a
    real code except for letter case — that is never auto-corrected, but the
    coach is told to check it (an image misread, or a genuine slip on the card).
    """
    if not value or not vocab or value in vocab:
        return None, None

    # Guard 1 — a case-only variant of this segment IS a real code, so the case
    # is what differs ('r0.5l' vs 'r0.5L'). Never rewrite it into some third
    # code ('r0.5!'), and never quietly fix the case either: report it.
    low = value.lower()
    for v in vocab:
        if v.lower() == low:
            return None, v

    # Guard 2 — this segment is a real code with one character removed ('r!'
    # from 'r1!'). We cannot tell "OCR swapped a character" from "a character
    # went missing", and a missing character is a coach error to report, not
    # repair — so refuse rather than snap it onto a shorter code ('r1').
    n = len(value)
    for v in vocab:
        if len(v) == n + 1 and any(
            v[:i] + v[i + 1:] == value for i in range(len(v))
        ):
            return None, None

    scored = []
    for v in vocab:
        d = _ocr_dist(value, v)
        if d is not None and d <= _SNAP_MAX:
            scored.append((d, v))
    if not scored:
        return None, None
    scored.sort()
    if len(scored) > 1 and scored[1][0] - scored[0][0] < _SNAP_MARGIN:
        return None, None
    return scored[0][1], None


def _snap(value, vocab):
    """Nearest catalogue entry to an OCR'd segment, or None to leave it alone.

    Returns a correction ONLY when it is unambiguous — a single clear winner
    within `_SNAP_MAX` that beats the runner-up by `_SNAP_MARGIN`. Anything
    doubtful is left as read so the coach sees it flagged rather than silently
    rewritten into a different element.
    """
    return _snap_ex(value, vocab)[0]


def snap_team_acro_code(code, group=None):
    """Correct an OCR'd team/open acro code against the official catalogue.

    Returns `(corrected_code, changes, case_checks)`:
      * `changes`     — `(component, before, after)` actually applied; only
                        unambiguous look-alike character swaps, same length.
      * `case_checks` — `(component, as_read, catalogue_form)` where the segment
                        matches a real code apart from letter case. NOT applied;
                        the coach is asked to check whether it is an image
                        misread or a genuine mistake on the card.
    Segments already in the catalogue, and segments with no unambiguous
    neighbour, are returned untouched.
    """
    from acro_parser import parse_team_acro_code, split_segments
    _ensure_loaded()

    code = _normalize_code(code or "")
    if not code:
        return code, [], []
    p = parse_team_acro_code(code, group)
    if p.parse_error or not p.group:
        return code, [], []
    valid = _TEAM["valid_codes"].get(p.group, {})
    if not valid:
        return code, [], []

    parts = split_segments(code)
    changes = []
    case_checks = []

    def _vocab(*keys):
        return sorted({c for k in keys for c in valid.get(k, [])})

    def _fix(value, label, *keys):
        got, case_v = _snap_ex(value, _vocab(*keys))
        if case_v:
            case_checks.append((label, value, case_v))
        if got and got != value:
            changes.append((label, value, got))
            return got
        return value

    if len(parts) >= 1 and parts[0]:
        parts[0] = _fix(parts[0], "Construction", "Construction")
    if len(parts) >= 2 and parts[1]:
        if p.group in ("A", "C"):
            parts[1] = _fix(parts[1], "Direction", "Direction")
        else:
            parts[1] = _fix(parts[1], "Connection", "Connection")
    if len(parts) >= 3 and parts[2]:
        blk = parts[2]
        if "/2" in blk:
            a, _, b = blk.partition("/2")
            # P2 is stored without its leading '2' -> snap against Position1
            parts[2] = (_fix(a, "Position1", "Position1", "Position2") + "/2"
                        + _fix(b, "Position2", "Position1"))
        else:
            parts[2] = _fix(blk, "Position1", "Position1", "Position2")

    extras = _vocab("Rotation Base", "Rotation Plane", "Bonus")
    for i in range(3, len(parts)):
        ex = parts[i]
        if not ex or ex in extras:
            continue
        got, case_v = _snap_ex(ex, extras)
        if case_v:
            case_checks.append(("Bonus/Rotation", ex, case_v))
        if got:
            changes.append(("Bonus/Rotation", ex, got))
            parts[i] = got
            continue
        # compound cell like 'Hold/SdUp' or 'Mov1+t/Pos3' — snap each piece.
        # ('r/L', 'r0.5/', 'PH/' are whole catalogue entries and exited above.)
        if "/" in ex:
            out, hit = [], False
            for pc in ex.split("/"):
                g2, cv2 = (_snap_ex(pc, extras) if pc and pc not in extras
                           else (None, None))
                if cv2:
                    case_checks.append(("Bonus/Rotation", pc, cv2))
                if g2:
                    changes.append(("Bonus/Rotation", pc, g2))
                    hit = True
                out.append(g2 or pc)
            if hit:
                parts[i] = "/".join(out)

    return "-".join(parts), changes, case_checks


def validate_hybrid_code(code):
    _ensure_loaded()
    code = _normalize_code(code)
    bare = _strip_factor(code)
    valid = _HYBRID["all_codes"]
    if bare in valid:
        return {"code": code, "ok": True}
    lk = _loose_key(bare)
    loose = [v for v in valid if _loose_key(v) == lk]
    if loose:
        return {"code": code, "ok": True, "canonical": loose[0],
                "casing_note": f"{bare} -> {loose[0]}"}
    return {
        "code": code, "ok": False,
        "reason": f"'{bare}' not found in Hybrid catalogue (Appendix 6).",
        "suggestions": _suggest(bare, valid),
    }


# Pair-acro codes are compositional (Appendix 7 Pair): a base jump/lift + a run
# of modifier tokens. The Excel catalogue only lists ~56 example codes, so also
# accept any code that parses cleanly into base + known modifiers.
_PAIR_BASE_RE = re.compile(r"^(?:[A-Z]{2,3}-)?(?:SL|J|L|S|W)!?", re.I)
_PAIR_MOD_RE = re.compile(
    r"(?:2|ss?1(?:\.5)?|s0\.5|r0\.5|r1(?:\.5)?|t0\.5|t1|pf|pd|[fdpbf]"
    r"|\+(?:pf|f|r0\.5|r1(?:\.5)?)|>|»)",
    re.I,
)


def _pair_shape_ok(code: str) -> bool:
    for cand in (code, _pair_recase(code)):
        m = _PAIR_BASE_RE.match(cand)
        if not m:
            continue
        rest = cand[m.end():]
        while rest:
            mm = _PAIR_MOD_RE.match(rest)
            if not mm:
                break
            rest = rest[mm.end():]
        if not rest:
            return True
    return False


def _pair_recase(code):
    """Undo an all-caps OCR of a pair code: 'JFS1B' -> 'Jfs1B'."""
    m = re.match(r"^([A-Z]{2,3}-)?", code)
    pre, body = m.group(0), code[m.end():]
    letters = re.sub(r"[^A-Za-z]", "", body)
    if len(letters) >= 2 and letters == letters.upper():
        body = body.lower()
        body = re.sub(r"^(sl|j|l|s|w)", lambda x: x.group(1).upper(), body)
        body = re.sub(r"(?<=\d)([bf])", lambda x: x.group(1).upper(), body)
        body = re.sub(r"([bf])$", lambda x: x.group(1).upper(), body)
    return pre + body


def validate_pair_acro_code(code):
    _ensure_loaded()
    code = _normalize_code(code)
    valid = _PAIR["all_codes"]
    if code in valid or _pair_shape_ok(code):
        rec = _pair_recase(code)
        if code not in valid and rec != code and (rec in valid or _pair_shape_ok(rec)):
            return {"code": code, "ok": True, "canonical": rec,
                    "casing_note": f"{code} -> {rec}"}
        return {"code": code, "ok": True}
    rec = _pair_recase(code)
    if rec != code and (rec in valid or _pair_shape_ok(rec)):
        return {"code": code, "ok": True, "canonical": rec,
                "casing_note": f"{code} -> {rec}"}
    lk = _loose_key(code)
    loose = [v for v in valid if _loose_key(v) == lk]
    if loose:
        return {"code": code, "ok": True, "canonical": loose[0],
                "casing_note": f"{code} -> {loose[0]}"}
    return {
        "code": code, "ok": False,
        "reason": f"'{code}' is not a recognised Pair Acro code (Appendix 7).",
        "suggestions": _suggest(code, valid),
    }


def _tokenize_extras(extras_list, known_codes):
    """Tokenize extras by greedy-matching against the longest-known code,
    then splitting the rest on / and +."""
    tokens = []
    sorted_codes = sorted([c for c in known_codes if not c.startswith("_")], key=len, reverse=True)
    for ex in extras_list:
        remaining = ex
        while remaining:
            matched = False
            for code in sorted_codes:
                if remaining.startswith(code):
                    tokens.append(code)
                    remaining = remaining[len(code):].lstrip("/+ ")
                    matched = True
                    break
            if not matched:
                m = re.match(r"^([^/+]+)[/+]?", remaining)
                if m:
                    tokens.append(m.group(1))
                    remaining = remaining[len(m.group(0)):]
                else:
                    break
    return tokens


def validate_team_acro_code(code, group=None):
    """Parse team acro and verify components + compatibility.

    `group` (A/B/C/P) comes from the coach card's PART column; when given, the
    code is parsed construction-first (no group-letter prefix expected).
    """
    from acro_parser import parse_team_acro_code
    _ensure_loaded()

    code = _normalize_code(code)
    p = parse_team_acro_code(code, group)
    if p.parse_error:
        return {"code": code, "ok": False, "reason": f"parse error: {p.parse_error}"}

    grp = p.group
    valid = _TEAM["valid_codes"].get(grp, {})
    issues = []

    # Min requirements
    required = MIN_REQUIREMENTS.get(grp, [])
    parsed_components = {
        "Construction": p.construction, "Direction": p.direction,
        "Connection": p.connection, "Position1": p.p1,
    }
    for req in required:
        if not parsed_components.get(req):
            issues.append({
                "component": f"MIN_REQUIRED: {req}", "value": "(missing)",
                "valid_examples": valid.get(req, [])[:5],
            })

    def _check(label, value, bucket_keys, severity="fail"):
        if value is None:
            return
        all_valid = sorted({c for k in bucket_keys for c in valid.get(k, [])})
        if value in all_valid:
            return
        lk = _loose_key(value)
        loose = [c for c in all_valid if _loose_key(c) == lk]
        if loose:
            issues.append({
                "component": f"CASING: {label}", "value": value,
                "valid_examples": [loose[0]], "severity": "info",
            })
            return
        issues.append({
            "component": label, "value": value,
            "valid_examples": _suggest(value, all_valid) or all_valid[:5],
            "severity": severity,
        })

    _check("Construction", p.construction, ["Construction"])
    if grp in ("A", "C"):
        _check("Direction", p.direction, ["Direction"])
    else:
        _check("Connection", p.connection, ["Connection"])

    # Construction x Connection compat (B, P)
    if grp in ("B", "P") and p.construction and p.connection:
        compat = _COMPAT.get(f"Group {grp}", {}).get("construction_to_connections", {})
        valid_for_construction = compat.get(p.construction)
        if valid_for_construction is not None and p.connection not in valid_for_construction:
            issues.append({
                "component": f"COMPAT: Construction '{p.construction}' x Connection '{p.connection}' invalid",
                "value": "connection not allowed for this construction",
                "valid_examples": valid_for_construction[:8],
            })

    rotation_constraints = _COMPAT.get(f"Group {grp}", {}).get("rotation_constraints", {})

    # Group B detailed rotation
    if grp == "B":
        no_rot_conns = rotation_constraints.get("connections_no_rotation", [])
        rot_detailed = _COMPAT.get("Group B", {}).get("rotation_codes_detailed", {})
        positions_135_180 = _COMPAT.get("Group B", {}).get("positions_135_180", [])

        tokens = _tokenize_extras(p.extras, list(rot_detailed.keys()))
        for tok in tokens:
            if not tok:
                continue
            if p.connection in no_rot_conns and tok.lower().startswith("r") and any(ch.isdigit() for ch in tok):
                issues.append({
                    "component": f"COMPAT: Group B connection '{p.connection}' forbids construction rotation",
                    "value": f"declared rotation '{tok}'",
                    "valid_examples": ["(use twirl bonus instead)"],
                })
            if tok in rot_detailed:
                spec = rot_detailed[tok]
                if "connections_handstand" in spec:
                    valid_conns = set(spec["connections_handstand"]) | set(spec.get("connections_head_down", []))
                    if p.connection not in valid_conns:
                        issues.append({
                            "component": f"COMPAT: Group B rotation '{tok}' requires Handstand connection or head-down support",
                            "value": f"declared with connection '{p.connection}'",
                            "valid_examples": sorted(valid_conns)[:8],
                            "severity": "warn",
                        })
                elif "connections" in spec:
                    if p.connection not in spec["connections"]:
                        issues.append({
                            "component": f"COMPAT: Group B rotation '{tok}' requires specific connection",
                            "value": f"declared with connection '{p.connection}'",
                            "valid_examples": spec["connections"],
                            "severity": "warn",
                        })
                if "position_constraint" in spec and tok.endswith("+"):
                    all_positions = []
                    if p.p1: all_positions.append(p.p1)
                    if p.p2: all_positions.append(p.p2)
                    if not any(pos in positions_135_180 for pos in all_positions):
                        issues.append({
                            "component": f"COMPAT: Group B rotation '{tok}' requires position with leg at 135-180 deg",
                            "value": f"declared positions: P1={p.p1} P2={p.p2}",
                            "valid_examples": positions_135_180,
                            "severity": "warn",
                        })

    # Group C
    if grp == "C":
        no_rot = rotation_constraints.get("constructions_no_rotation", [])
        twirl_only = rotation_constraints.get("constructions_twirl_only", [])
        rot_to_construct_c = _COMPAT.get("Group C", {}).get("rotation_to_constructions", {})
        if p.construction in no_rot or p.construction in twirl_only:
            for ex in p.extras:
                for tok in re.split(r"[/+]", ex):
                    tok = tok.strip()
                    if not tok:
                        continue
                    if re.match(r"^C[srdRP][0-9]|^[0-9]F[0-9]", tok):
                        if p.construction in no_rot:
                            issues.append({
                                "component": f"COMPAT: Group C construction '{p.construction}' has no rotation",
                                "value": f"declared rotation '{tok}'",
                                "valid_examples": ["(remove rotation)"],
                            })
                        else:
                            issues.append({
                                "component": f"COMPAT: Group C construction '{p.construction}' twirl-only",
                                "value": f"declared rotation '{tok}'",
                                "valid_examples": ["(use twirl bonus instead)"],
                            })
        tokens = _tokenize_extras(p.extras, list(rot_to_construct_c.keys()))
        for tok in tokens:
            if tok in rot_to_construct_c:
                allowed = rot_to_construct_c[tok]
                if p.construction not in allowed:
                    issues.append({
                        "component": f"COMPAT: Group C rotation '{tok}' must use construction in {allowed}",
                        "value": f"declared with '{p.construction}'",
                        "valid_examples": allowed,
                    })

    # Group P rotation
    if grp == "P":
        rot_to_construct = _COMPAT.get("Group P", {}).get("rotation_to_constructions", {})
        for ex in p.extras:
            for tok in re.split(r"[/+]", ex):
                tok = tok.strip()
                if not tok or tok.startswith("_"):
                    continue
                if tok in rot_to_construct:
                    allowed = rot_to_construct[tok]
                    if p.construction not in allowed:
                        issues.append({
                            "component": f"COMPAT: Group P rotation '{tok}' must use construction in {allowed}",
                            "value": f"declared with '{p.construction}'",
                            "valid_examples": allowed,
                        })

    # Bonus constraints (all groups) — WARN
    if grp in ("A", "B", "C", "P"):
        bonus_constraints = _COMPAT.get(f"Group {grp}", {}).get("bonus_constraints", {})
        declared_bonuses = []
        for ex in p.extras:
            for sub in re.split(r"[/+]", ex):
                sub = sub.strip()
                if sub:
                    declared_bonuses.append(sub)

        # Mutually exclusive bonus groups
        for grp_def in bonus_constraints.get("_mutual_exclusive_groups", []):
            bonuses_in_group = grp_def.get("bonuses", [])
            present = [b for b in declared_bonuses if b in bonuses_in_group]
            if len(present) > 1:
                issues.append({
                    "component": f"COMPAT: Group {grp} mutually exclusive bonuses ({grp_def.get('name','')})",
                    "value": f"declared together: {present}",
                    "valid_examples": [f"(only one of {bonuses_in_group} per acro)"],
                    "severity": "warn",
                })

        # Per-bonus constraints
        for bonus_name, spec in bonus_constraints.items():
            if bonus_name.startswith("_"):
                continue
            if bonus_name not in declared_bonuses:
                continue
            if "construction_must_be_in" in spec and p.construction not in spec["construction_must_be_in"]:
                issues.append({
                    "component": f"COMPAT: Bonus '{bonus_name}' requires construction in {spec['construction_must_be_in']}",
                    "value": f"declared with construction '{p.construction}'",
                    "valid_examples": spec["construction_must_be_in"],
                    "severity": "warn",
                })
            if "p1_must_be" in spec and p.p1 != spec["p1_must_be"]:
                label = spec.get("p1_must_be_label", spec["p1_must_be"])
                issues.append({
                    "component": f"COMPAT: Bonus '{bonus_name}' requires P1='{spec['p1_must_be']}' ({label})",
                    "value": f"declared P1='{p.p1}'",
                    "valid_examples": [spec["p1_must_be"]],
                    "severity": "warn",
                })
            if "p1_must_be_in" in spec and p.p1 not in spec["p1_must_be_in"]:
                issues.append({
                    "component": f"COMPAT: Bonus '{bonus_name}' requires P1 in {spec['p1_must_be_in']}",
                    "value": f"declared P1='{p.p1}'",
                    "valid_examples": spec["p1_must_be_in"],
                    "severity": "warn",
                })
            if "requires_bonus" in spec and spec["requires_bonus"] not in declared_bonuses:
                issues.append({
                    "component": f"COMPAT: Bonus '{bonus_name}' requires bonus '{spec['requires_bonus']}' also declared",
                    "value": f"missing required bonus '{spec['requires_bonus']}'",
                    "valid_examples": [spec["requires_bonus"]],
                    "severity": "warn",
                })
            for incomp in spec.get("incompatible_with_bonuses", []):
                if incomp in declared_bonuses:
                    issues.append({
                        "component": f"COMPAT: Bonus '{bonus_name}' incompatible with bonus '{incomp}'",
                        "value": "both declared in same acro",
                        "valid_examples": [f"(remove '{incomp}')"],
                        "severity": "warn",
                    })
            if spec.get("cannot_with_rotation"):
                # Detect any rotation-like extra
                for ex in p.extras:
                    if any(re.match(r"^r[0-9]", t.strip()) for t in re.split(r"[/+]", ex)):
                        issues.append({
                            "component": f"COMPAT: Bonus '{bonus_name}' with construction rotation",
                            "value": "both in the same acro",
                            "valid_examples": [
                                f"(the {bonus_name} and the rotation must be performed one "
                                "after the other, not simultaneously)"
                            ],
                            "severity": "warn",
                        })
                        break

    # Group C: position source per construction (per-slot — P1 and P2 may differ)
    # Per §29.7.1.6.d: e.g. Thr^2F has P1=B (lifted) and P2=A (flying).
    if grp == "C" and p.construction:
        pos_src = _COMPAT.get("Group C", {}).get("position_source_by_construction", {})
        spec = pos_src.get(p.construction)
        if isinstance(spec, dict):
            a_pos = set(_TEAM["valid_codes"]["A"].get("Position1", []))
            b_pos = set(_TEAM["valid_codes"]["B"].get("Position1", []))
            for slot_label, slot_key, pos in [("Position1", "p1", p.p1), ("Position2", "p2", p.p2)]:
                if not pos:
                    continue
                required_src = spec.get(slot_key)
                if required_src == "B" and pos in a_pos and pos not in b_pos:
                    issues.append({
                        "component": f"COMPAT: Group C construction '{p.construction}' requires Group B for {slot_label}",
                        "value": f"{slot_label}='{pos}' is a Group A position",
                        "valid_examples": sorted(b_pos)[:8],
                        "severity": "warn",
                    })
                elif required_src == "A" and pos in b_pos and pos not in a_pos:
                    issues.append({
                        "component": f"COMPAT: Group C construction '{p.construction}' requires Group A for {slot_label}",
                        "value": f"{slot_label}='{pos}' is a Group B position",
                        "valid_examples": sorted(a_pos)[:8],
                        "severity": "warn",
                    })

    # Plane T × Direction compat (Group A and C) — WARN
    if grp in ("A", "C") and p.direction:
        plane_constraint = _COMPAT.get(f"Group {grp}", {}).get("plane_t_direction_constraint", {})
        if plane_constraint:
            cw_codes = set(plane_constraint.get("cartwheel_codes", []))
            cw_dirs = plane_constraint.get("cartwheel_required_direction", [])
            hs_codes = set(plane_constraint.get("handspring_codes", []))
            hs_dirs = plane_constraint.get("handspring_required_direction", [])
            for ex in p.extras:
                for tok in re.split(r"[/+]", ex):
                    tok = tok.strip()
                    if tok in cw_codes and p.direction not in cw_dirs:
                        issues.append({
                            "component": f"COMPAT: Group {grp} Cartwheel '{tok}' requires direction Side",
                            "value": f"declared direction '{p.direction}'",
                            "valid_examples": cw_dirs,
                            "severity": "warn",
                        })
                    if tok in hs_codes and p.direction not in hs_dirs:
                        issues.append({
                            "component": f"COMPAT: Group {grp} Handspring '{tok}' requires direction Forw or Back",
                            "value": f"declared direction '{p.direction}'",
                            "valid_examples": hs_dirs,
                            "severity": "warn",
                        })

    # 2027 clarification (Appendix 7, 29.7.1.2 g viii): the 3rd position bonus
    # requires both Position 1 and Position 2 to be declared (else Base Mark).
    _bon = [t.strip() for ex in p.extras for t in re.split(r"[/+]", ex)]
    if "Pos3" in _bon and not (p.p1 and p.p2):
        issues.append({
            "component": "RULE: Pos3 bonus requires Position 1 and Position 2 declared",
            "value": f"P1={p.p1 or '-'} P2={p.p2 or '-'}",
            "valid_examples": ["(declare Position 2, e.g. '<P1>/2<P2>', or remove Pos3)"],
            "severity": "fail",
        })

    _check("Position1", p.p1, ["Position1", "Position2"])
    if p.p2:
        _check("Position2", p.p2, ["Position1", "Position2"])

    all_extra = sorted({c for k in ("Rotation Base", "Rotation Plane", "Bonus") for c in valid.get(k, [])})
    # Use greedy tokenisation against the catalogue so that valid codes that
    # contain '/' or '+' (e.g. 'Mov1+t', 'Ps1t0.5o') are not chopped apart by
    # a naive split on those separators.
    sorted_extras = sorted(all_extra, key=len, reverse=True)
    for ex in p.extras:
        remaining = ex.strip()
        while remaining:
            remaining = remaining.lstrip("/+ ")
            if not remaining:
                break
            matched = None
            for cand in sorted_extras:
                if remaining == cand:
                    matched = cand
                    break
                tail = remaining[len(cand):len(cand)+1]
                if remaining.startswith(cand) and tail in ("/", "+", " ", ""):
                    matched = cand
                    break
            if matched:
                remaining = remaining[len(matched):]
                continue
            m = re.match(r"^([^/+]+)", remaining)
            if not m:
                break
            sub = m.group(1).strip()
            remaining = remaining[len(m.group(1)):]
            if sub:
                lk = _loose_key(sub)
                loose = [x for x in all_extra if _loose_key(x) == lk]
                if loose:
                    issues.append({
                        "component": "CASING: Bonus/Rotation",
                        "value": sub,
                        "valid_examples": [loose[0]], "severity": "info",
                    })
                else:
                    issues.append({
                        "component": "Bonus/Rotation",
                        "value": sub,
                        "valid_examples": _suggest(sub, all_extra) or all_extra[:5],
                    })

    if not issues:
        return {"code": code, "ok": True, "parsed": p.to_dict()}
    return {
        "code": code, "ok": False,
        "reason": "components missing or compatibility violated",
        "issues": issues,
        "parsed": p.to_dict(),
    }


def validate_open_acro_code(code, group=None):
    return validate_team_acro_code(code, group)
