#!/usr/bin/env python3
"""Verify an Artistic Swimming coach card against Appendix 3 of the WA CR rules.

The script consumes:
  - the routine name (must match a key in appendix3_requirements.json), and
  - a list of declared elements with their type, time slot, and (when applicable)
    acrobatic group letter.

It produces a structured report (PASS / WARN / FAIL per check) that the calling
agent can render to the user.

CLI usage:
  python3 check_coach_card.py \
      --routine "Open Acrobatic" \
      --declared-json '{"time_seconds": 182, "elements": [...]}'

Programmatic usage:
  from check_coach_card import check, load_requirements
  result = check("Open Acrobatic", declared)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


# Regex that catches the most common ways a "factor" is written next to a hybrid
# code on a coach card: *0.5, *0,5 (French), x0.5, X.5, *.3, ( 0.5 ), etc.
# The factor is used as a WEIGHT (0.5 or 0.3) in the per-hybrid family/technique
# sums (Appendix 6 sec. 29.6.1.8 c): family sum <= 5.0, technique sum <= 3.0
# (2.0 for Connections in Duet/Mixed Duet).
FACTOR_RE = re.compile(r"[\*xX]\s*0?[\.,][35]\b|\(\s*0?[\.,][35]\s*\)|\s0?[\.,][35]\b")

# Regex that extracts the leading family letter from a hybrid code such as
# "T3", "T8b", "RU5", "ROB/RO1", "SB", "F1a", "A3b", "C3". The first letter
# of the (uppercased) code is the family letter.
CODE_FAMILY_RE = re.compile(r"^\s*\d*([TSRFAC])", re.IGNORECASE)


HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REQS_PATH = os.path.normpath(os.path.join(HERE, "..", "appendix3_requirements.json"))


@dataclass
class CheckResult:
    label: str
    status: str  # "pass" | "info" | "warn" | "fail"
    detail: str = ""


@dataclass
class Report:
    routine: str
    age_group: str
    summary: str = "PASS"
    checks: list[CheckResult] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def add(self, check: CheckResult) -> None:
        self.checks.append(check)
        if check.status == "fail":
            self.summary = "FAIL"
        elif check.status == "warn" and self.summary != "FAIL":
            self.summary = "WARN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "routine": self.routine,
            "age_group": self.age_group,
            "summary": self.summary,
            "checks": [c.__dict__ for c in self.checks],
            "recommendations": self.recommendations,
        }

    def to_text(self) -> str:
        sym = {"pass": "OK  ", "info": "CHECK", "warn": "WARN", "fail": "FAIL"}
        lines = [
            f"Coach Card Check — {self.routine} ({self.age_group})",
            "",
            f"Summary: {self.summary}",
            "",
        ]
        for c in self.checks:
            badge = sym.get(c.status, "?")
            line = f"  [{badge}] {c.label}"
            if c.detail:
                line += f" — {c.detail}"
            lines.append(line)
        if self.recommendations:
            lines.append("")
            lines.append("Recommendations:")
            for r in self.recommendations:
                lines.append(f"  - {r}")
        return "\n".join(lines)


def load_requirements(path: str = DEFAULT_REQS_PATH) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def normalise_type(label: str, aliases: dict[str, list[str]]) -> str | None:
    """Map a declared element-type string to a canonical key."""
    if not label:
        return None
    s = label.strip().lower()
    for canonical, alts in aliases.items():
        if canonical.lower() == s:
            return canonical
        for a in alts:
            if a.lower() == s:
                return canonical
    # tolerant containment match (e.g. "Hybrid #2" -> Hybrid)
    for canonical, alts in aliases.items():
        all_terms = [canonical.lower()] + [a.lower() for a in alts]
        for term in all_terms:
            if term in s:
                return canonical
    return None


def check(
    routine: str,
    declared: dict[str, Any],
    requirements_path: str = DEFAULT_REQS_PATH,
) -> Report:
    """Run all available checks for the given routine + declared elements.

    `declared` shape:
        {
          "time_seconds": int,           # routine length, optional
          "elements": [                  # one entry per declared element
            {
              "type": "Acrobatic",      # canonical or alias
              "group": "P",             # letter for acrobatics in Open Acro
              "code": "P-Knees-...",
              "time_slot": "0:03-0:20"
            },
            ...
          ]
        }
    """
    reqs = load_requirements(requirements_path)
    aliases = reqs.get("element_type_aliases", {})

    if routine not in reqs["events"]:
        report = Report(routine=routine, age_group="?", summary="FAIL")
        report.add(CheckResult(
            label="Routine recognized",
            status="fail",
            detail=f"'{routine}' is not in Appendix 3. Known routines: {', '.join(reqs['events'].keys())}",
        ))
        return report

    spec = reqs["events"][routine]
    report = Report(routine=routine, age_group=spec["age_group"])

    # ---- 1. Total count -----------------------------------------------------
    declared_elements = declared.get("elements", [])
    declared_total = len(declared_elements)
    required_total = spec["total_required_elements"]
    if declared_total == required_total:
        report.add(CheckResult(
            label=f"Total declared elements",
            status="pass",
            detail=f"required {required_total}, declared {declared_total}",
        ))
    else:
        report.add(CheckResult(
            label=f"Total declared elements",
            status="fail",
            detail=f"required {required_total}, declared {declared_total}",
        ))
        report.recommendations.append(
            f"Adjust the number of declared elements to {required_total}."
        )

    # ---- 2. Per-type counts -------------------------------------------------
    type_counter: Counter[str] = Counter()
    unknown_types: list[str] = []
    for el in declared_elements:
        canonical = normalise_type(el.get("type", ""), aliases)
        if canonical:
            type_counter[canonical] += 1
        else:
            unknown_types.append(el.get("type", "<missing>"))

    if unknown_types:
        report.add(CheckResult(
            label="Element types recognized",
            status="warn",
            detail=f"Could not normalise: {unknown_types}",
        ))
        report.recommendations.append(
            "Re-check element type spelling against the canonical list in appendix3_requirements.json."
        )

    for required_type, required_count in spec["elements"].items():
        actual = type_counter.get(required_type, 0)
        if actual == required_count:
            report.add(CheckResult(
                label=f"{required_type}",
                status="pass",
                detail=f"required {required_count}, declared {actual}",
            ))
        elif actual == 0 and required_count > 0:
            report.add(CheckResult(
                label=f"{required_type}",
                status="fail",
                detail=f"required {required_count}, none declared",
            ))
            report.recommendations.append(
                f"Add {required_count} {required_type} element(s) to the routine."
            )
        else:
            report.add(CheckResult(
                label=f"{required_type}",
                status="fail",
                detail=f"required {required_count}, declared {actual}",
            ))
            delta = required_count - actual
            verb = "Add" if delta > 0 else "Remove"
            report.recommendations.append(
                f"{verb} {abs(delta)} {required_type} element(s)."
            )

    # surplus types not part of the spec
    extras = {k: v for k, v in type_counter.items() if k not in spec["elements"]}
    for extra_type, extra_count in extras.items():
        report.add(CheckResult(
            label=f"Unexpected element type: {extra_type}",
            status="warn",
            detail=f"declared {extra_count}; not part of {routine}'s spec",
        ))

    # ---- 3. Routine length: intentionally NOT checked ----------------------
    # (the coach controls duration on deck; the card check focuses on elements)

    # ---- 4. Acrobatic group distribution rules (Open Acrobatic) -------------
    if routine == "Open Acrobatic":
        groups = Counter()
        for el in declared_elements:
            canonical = normalise_type(el.get("type", ""), aliases)
            if canonical == "Acrobatic":
                grp = (el.get("group") or "").upper().strip()
                if grp:
                    groups[grp] += 1

        for required_grp in ("A", "B", "C", "P"):
            cnt = groups.get(required_grp, 0)
            if cnt >= 1:
                report.add(CheckResult(
                    label=f"At least one acrobatic from group {required_grp}",
                    status="pass",
                    detail=f"declared {cnt}",
                ))
            else:
                report.add(CheckResult(
                    label=f"At least one acrobatic from group {required_grp}",
                    status="fail",
                    detail=f"declared {cnt}",
                ))
                report.recommendations.append(
                    f"Add at least one acrobatic from group {required_grp}."
                )

        for grp, cnt in groups.items():
            if cnt > 2:
                report.add(CheckResult(
                    label=f"Max 2 acrobatics from group {grp}",
                    status="fail",
                    detail=f"declared {cnt}",
                ))
                report.recommendations.append(
                    f"Reduce group {grp} acrobatics to a maximum of 2."
                )
            else:
                report.add(CheckResult(
                    label=f"Max 2 acrobatics from group {grp}",
                    status="pass",
                    detail=f"declared {cnt}",
                ))

        # ---- 5. No-repeat acrobatic warning (visual flag) -------------------
        codes_by_group: dict[str, list[str]] = {}
        for el in declared_elements:
            canonical = normalise_type(el.get("type", ""), aliases)
            if canonical == "Acrobatic":
                grp = (el.get("group") or "").upper().strip()
                code = (el.get("code") or "").strip()
                codes_by_group.setdefault(grp, []).append(code)
        flagged = []
        for grp, codes in codes_by_group.items():
            seen: dict[str, int] = {}
            for c in codes:
                if not c:
                    continue
                seen[c] = seen.get(c, 0) + 1
            for c, n in seen.items():
                if n > 1:
                    flagged.append(f"{grp}: '{c}' appears {n}x")
        if flagged:
            report.add(CheckResult(
                label="Acrobatic codes appear unique (basic check)",
                status="warn",
                detail="; ".join(flagged) + " — verify against catalogue rules (construction/grip/positions)",
            ))
            report.recommendations.append(
                "Compare flagged acrobatics against the no-repeat definitions for their group "
                "(see appendix3_requirements.json -> no_repeat_acrobatic_definition)."
            )
        else:
            report.add(CheckResult(
                label="Acrobatic codes appear unique (basic check)",
                status="pass",
                detail="No identical acrobatic codes detected within the same group",
            ))

    # ---- 6. Family coverage (Free routines, Appendix 6 §29.6.1.8.b) --------
    fc_rule = reqs.get("family_coverage_rule")
    if fc_rule:
        for c in check_family_coverage(routine, declared, spec, fc_rule):
            report.add(c)

    # ---- 8. Acro no-repeat (Appendix 7 §29.7.1.2 o, §29.7.2.1 xiii) -------
    for c in check_acro_no_repeat(routine, declared):
        report.add(c)

    # ---- 9. Code existence (Appendix 6 + 7 catalogues) --------------------
    for c in check_code_existence(routine, declared):
        report.add(c)

    # ---- 7. Per-hybrid family/technique limits (Appendix 6 §29.6.1.8.c/e) --
    for c in check_per_hybrid_family_limits(routine, declared):
        report.add(c)

    # ---- 10. Federation-reserved codes (Phase 1 / Phase 2 approvals) -------
    for c in check_reserved_codes(declared):
        report.add(c)

    # ---- 12. Codes not permitted in 12U / Youth ----------------------------
    for c in check_age_restricted_codes(routine, declared, spec, reqs):
        report.add(c)

    # ---- 13. Acrobatic DD limits (12U/Youth safety; tech team max 3.0) -----
    for c in check_acro_dd_limits(routine, declared, spec, reqs, aliases):
        report.add(c)

    # ---- Declared acrobatic DD vs DD computed from the catalogues ----------
    for c in check_declared_acro_dd(declared, aliases):
        report.add(c)

    # ---- 11. Theme declared (Acrobatic Routine + Free Combination) ---------
    for c in check_theme_declared(routine, declared, reqs):
        report.add(c)
        if c.status == "fail":
            report.recommendations.append(
                "Write the routine theme on the coach card (required for Acrobatic "
                "Routine and Free Combination)."
            )

    # Precautionary CHECK items (compatibility notes, case / 0-O differences) are
    # comments, not rule breaks — keep them together at the bottom of the report.
    _info = [c for c in report.checks if c.status == "info"]
    if _info:
        report.checks = [c for c in report.checks if c.status != "info"] + _info

    return report


_RESERVED = None


def load_reserved_codes(path: str | None = None) -> dict:
    """Phase 1 / Phase 2 acrobatics: codes carrying the reserving federation(s)
    as a prefix. Cached after the first read; {} if the file is absent."""
    global _RESERVED
    if _RESERVED is None:
        p = path or os.path.normpath(
            os.path.join(HERE, "..", "reserved_codes.json"))
        try:
            with open(p, "r", encoding="utf-8") as f:
                _RESERVED = json.load(f)
        except Exception:
            _RESERVED = {}
    return _RESERVED


def check_reserved_codes(declared) -> list[CheckResult]:
    """A federation-reserved code may only be declared by the federation(s) that
    reserved it, until the unprefixed code opens to everyone (see
    `open_to_all_from` in reserved_codes.json).

    The card's federation comes from `declared["federation"]` (read off the
    coach card, or set by the user). Without it we cannot judge, so we report
    the reserved codes found and ask for the federation instead of guessing.
    """
    data = load_reserved_codes()
    codes = (data or {}).get("codes") or {}
    if not codes:
        return []

    elements = declared.get("elements", []) or []
    fed = (declared.get("federation") or "").strip().upper()

    # Longest first, so 'ESP-GRE-RUS-SL!2+r2' beats a shorter prefix and
    # 'CHI-Js1Ro' beats 'CHI-Js1R'. Each match is blanked out before looking
    # again, so one element declaring two reserved codes reports both.
    known = sorted(codes, key=len, reverse=True)
    found: list[tuple[str, str]] = []          # (declared code, reserved code)
    for el in elements:
        code = (el.get("code") or "")
        if not code:
            continue
        rest = code
        for rc in known:
            while rc in rest:
                found.append((code, rc))
                rest = rest.replace(rc, "\x00" * len(rc), 1)

    if not found:
        return []

    if not fed:
        listing = "; ".join(f"{rc}" for _, rc in found)
        return [CheckResult(
            label="Federation-reserved codes declared",
            status="info",
            detail=(f"{listing} — reserved to specific federations. Set the "
                    f"card's federation so this can be verified."),
        )]

    out: list[CheckResult] = []
    for code, rc in found:
        allowed = codes[rc].get("allowed") or codes[rc].get("federations") or []
        nice = "/".join(codes[rc].get("federations") or allowed)
        if fed in {a.upper() for a in allowed}:
            out.append(CheckResult(
                label=f"Reserved code '{rc}'",
                status="pass",
                detail=f"reserved to {nice}; this card is {fed} — allowed.",
            ))
        else:
            opens = (data.get("open_to_all_from") or "").strip()
            when = f" It opens to every federation on {opens}" if opens else ""
            base = codes[rc].get("base")
            out.append(CheckResult(
                label=f"Reserved code '{rc}' not available to {fed}",
                status="fail",
                detail=(f"'{rc}' is reserved to {nice}, but this card is {fed}."
                        f"{when}"
                        + (f", as '{base}'." if base and opens else ".")
                        + f" Declared in: {code}"),
            ))
    return out


def _strip_reserved_prefixes(code: str) -> str:
    """'NAB-1F>1P' -> '1F>1P': the safety restriction applies to the element,
    whatever federation reserved it."""
    codes = (load_reserved_codes() or {}).get("codes") or {}
    for rc in sorted(codes, key=len, reverse=True):
        if rc in code:
            code = code.replace(rc, codes[rc].get("base") or rc)
    return code


def _team_acro_group(el):
    grp = (el.get("group") or "").strip().upper()
    if grp:
        return grp
    try:
        from acro_parser import parse_team_acro_code
        return parse_team_acro_code(el.get("code") or "").group
    except Exception:
        return None


def _acro_tokens(code: str):
    """Components of an acro code (split on - / + while keeping '1PH/ + H1P/'-
    style connections recognisable via the raw string)."""
    return [t.strip() for t in re.split(r"[-/+]", code or "") if t.strip()]


def check_age_restricted_codes(routine, declared, spec, reqs):
    """Acrobatic components not permitted in 12U / Youth (2026 approvals:
    1FH/, 1F>1P; Team Acro Catalogue: 1P>H, H>1P; Senior/Junior only: Ret1Pa,
    RetF, RetSq, RetPa). Declared in a 12U/Youth routine -> FAIL."""
    rule = reqs.get("age_restricted_codes") or {}
    if spec.get("age_group") not in rule.get("age_groups", []):
        return []
    try:
        from acro_parser import parse_team_acro_code
    except Exception:
        return []
    conns = rule.get("connections", {})
    bonuses = rule.get("bonuses", {})
    out = []
    for idx, el in enumerate(declared.get("elements", []), start=1):
        t = (el.get("type") or "").lower()
        if "acro" not in t or "pair" in t:
            continue
        code = _strip_reserved_prefixes(el.get("code") or "")
        if not code:
            continue
        p = parse_team_acro_code(code, el.get("group"))
        hits = []
        if p.connection and p.connection in conns:
            hits.append((p.connection, conns[p.connection]))
        extras = [x.strip() for ex in p.extras for x in re.split(r"[/+]", ex) if x.strip()]
        # bonus codes such as '1F>1P' contain no separator, so a plain token
        # comparison is exact
        for b, why in bonuses.items():
            if b in extras:
                hits.append((b, why))
        for comp, why in hits:
            out.append(CheckResult(
                label=f"{el.get('type')} #{idx}: '{comp}' not permitted in {spec['age_group']}",
                status="fail",
                detail=f"{why} Declared: {el.get('code')}",
            ))
    if not out:
        out.append(CheckResult(
            label=f"No acrobatic components restricted for {spec['age_group']}",
            status="pass", detail="none of " + ", ".join(list(conns) + list(bonuses)) + " declared"))
    return out



_PAIR_DD = None


def _pair_total_dd(code):
    """Total DD (incl. BM) of a pair acro listed in the pair catalogue, else None."""
    global _PAIR_DD
    if _PAIR_DD is None:
        try:
            with open(os.path.join(HERE, "..", "catalogue_pair_acro.json"), encoding="utf-8") as f:
                _PAIR_DD = {i["code"]: i.get("total_DD") for i in json.load(f)["items"]}
        except Exception:
            _PAIR_DD = {}
    try:
        from code_validator import _normalize_code
        code = _normalize_code(code)
    except Exception:
        pass
    v = _PAIR_DD.get(code)
    return float(v) if isinstance(v, (int, float)) else None


def _dd_matches(declared_dd, computed):
    """Cards print 3 decimals: 3.5625 shows as 3.562 or 3.563."""
    return abs(declared_dd - computed) < 0.0011


def check_declared_acro_dd(declared, aliases):
    """Compare the DD written on the card for each acrobatic with the DD
    computed from the catalogues (team/open: BM + components, acro_dd.py;
    pair: catalogue total DD). A difference -> WARN with the breakdown."""
    try:
        from acro_dd import compute_team_acro_dd
    except Exception:
        return []
    out, ok, unknown = [], [], []
    for idx, el in enumerate(declared.get("elements", []), start=1):
        if el.get("dd") in (None, ""):
            continue
        t = normalise_type(el.get("type", ""), aliases)
        code = (el.get("code") or "").strip()
        if t not in ("Team Acrobatic", "Acrobatic", "Pair Acrobatic") or not code:
            continue
        try:
            dd_card = float(str(el["dd"]).replace(",", "."))
        except ValueError:
            continue
        if t == "Pair Acrobatic":
            calc, parts = _pair_total_dd(code), None
            if calc is None:
                continue   # pair acros have no DD limit: no note when not priced
        else:
            r = compute_team_acro_dd(code, el.get("group"))
            if not r["ok"]:
                unknown.append(f"#{idx} {code} ({', '.join(r['unknown'])})")
                continue
            calc, parts = r["dd"], r["parts"]
        if _dd_matches(dd_card, calc):
            ok.append(f"#{idx} {dd_card:g}")
        else:
            detail = f"card {dd_card:g} vs catalogue {calc:g}"
            if parts:
                detail += " = " + " + ".join(f"{n} {v:g}" for n, v in parts)
            out.append(CheckResult(
                label=f"{el.get('type')} #{idx}: declared DD {dd_card:g} differs from catalogue {calc:g}",
                status="warn",
                detail=detail + f" | code: {code}"))
    if ok and not out:
        out.append(CheckResult(label="Declared acrobatic DD matches the catalogue", status="pass",
                               detail="; ".join(ok)))
    if unknown:
        out.append(CheckResult(label="Declared acrobatic DD not verified", status="info",
                               detail="could not compute the DD of: " + "; ".join(unknown)))
    return out


_AGE_KEY = {"12U": "12U", "Youth (13-15)": "Youth"}   # "12U/Youth" (Mixed Duet) has pair acros only


def check_acro_dd_limits(routine, declared, spec, reqs, aliases):
    """Acrobatic DD ceilings, DD computed from the Team Acrobatic Catalogue
    (BM 0.50 + components, see acro_dd.py):
      * 12U / Youth team acrobatics: Total DD (MAX) per group (Appendix 3);
      * an event-level ceiling `team_acro_max_dd` when the routine has one
        (2026 Open Team Technical: 3.0 incl. Base Mark).
    Over the limit -> FAIL. A DD that cannot be computed -> CHECK."""
    limits = (reqs.get("youth_12u_acrobatic_safety_limits") or {}).get(_AGE_KEY.get(spec.get("age_group"), ""), {})
    event_max = spec.get("team_acro_max_dd")
    if not limits and not event_max:
        return []
    try:
        from acro_dd import compute_team_acro_dd
    except Exception:
        return []
    out, ok_lines = [], []
    for idx, el in enumerate(declared.get("elements", []), start=1):
        if normalise_type(el.get("type", ""), aliases) not in ("Team Acrobatic", "Acrobatic"):
            continue
        code = (el.get("code") or "").strip()
        if not code:
            continue
        r = compute_team_acro_dd(code, el.get("group"))
        grp = r.get("group") or _team_acro_group(el)
        caps = []
        if limits and grp and f"Group {grp}" in limits:
            caps.append((limits[f"Group {grp}"]["total_max"], f"{_AGE_KEY[spec['age_group']]} safety limit, Group {grp}"))
        if event_max:
            caps.append((event_max, f"{routine} maximum (incl. Base Mark)"))
        if not caps:
            continue
        if not r["ok"]:
            out.append(CheckResult(
                label=f"{el.get('type')} #{idx}: DD limit not verified",
                status="info",
                detail=f"could not price {', '.join(r['unknown'])} - check the DD by hand. Code: {code}"))
            continue
        for cap, why in caps:
            if r["dd"] > cap + 1e-9:
                out.append(CheckResult(
                    label=f"{el.get('type')} #{idx}: DD {r['dd']:g} exceeds {cap:g} ({why})",
                    status="fail",
                    detail="computed: " + " + ".join(f"{n} {v:g}" for n, v in r["parts"]) + f" | code: {code}"))
            else:
                ok_lines.append(f"#{idx} {r['dd']:g} <= {cap:g}")
    if ok_lines and not any(c.status == "fail" for c in out):
        out.append(CheckResult(label="Acrobatic DD within limits", status="pass", detail="; ".join(ok_lines)))
    return out


def _extract_family(code: str) -> str | None:
    """Return the canonical family letter (T/S/R/F/A/C) from a hybrid code."""
    if not code:
        return None
    m = CODE_FAMILY_RE.match(code)
    return m.group(1).upper() if m else None


def _has_factor(code: str) -> bool:
    """True if a coach-card code shows a factor (*0.5, x0.3, etc.)."""
    if not code:
        return False
    return bool(FACTOR_RE.search(code))


def _factor_value(code: str) -> float:
    if not code:
        return 0.0
    m = FACTOR_RE.search(code)
    if not m:
        return 1.0
    blob = m.group(0)
    if "5" in blob:
        return 0.5
    if "3" in blob:
        return 0.3
    return 1.0


TECHNIQUE_RE = re.compile(r"^\s*(\d*[A-Z]+\d*[A-Za-z]*\+?)")


def _technique_id(code: str):
    if not code:
        return None
    cleaned = FACTOR_RE.sub("", code).strip().upper()
    m = TECHNIQUE_RE.match(cleaned)
    return m.group(1) if m else None


def check_family_coverage(routine, declared, spec, rule):
    results = []
    if routine not in rule.get("applies_to_routines", []):
        return results

    families_required = ["T", "S", "R", "F", "A", "C"]
    if routine in rule.get("solo_routines_no_C_family", []):
        families_required = ["T", "S", "R", "F", "A"]

    is_combo = routine in rule.get("free_combination_cross_hybrid_routines", [])

    agg = {f: [] for f in families_required}
    for el in declared.get("elements", []):
        codes_field = el.get("codes")
        if codes_field is None and el.get("code"):
            codes_field = [{"code": el["code"]}]
        codes_field = codes_field or []
        el_type = (el.get("type") or "").lower()

        # Family coverage only counts hybrid rows. Acros/TREs/SSCs never count.
        # In Free Combination, only the DD hybrids (Solo/Duet/Team DoD) count
        # — the Team Choreography Hybrid (no DoD, factor 1.0) is excluded under
        # the strict reading of Appendix 6 §29.6.1.8.b ("…across solo, duet
        # and team DD hybrids").
        if "hybrid" not in el_type:
            continue
        if is_combo and "choreography" in el_type:
            continue
        if is_combo and "no dod" in el_type:
            continue

        for c in codes_field:
            code_str = c.get("code", "") if isinstance(c, dict) else str(c)
            factored = (
                c.get("factored")
                if isinstance(c, dict) and "factored" in c
                else _has_factor(code_str)
            )
            fam = _extract_family(code_str)
            if fam in agg:
                agg[fam].append({"code": code_str, "factored": bool(factored)})

    label_suffix = " (counted across solo/duet/team DD hybrids)" if is_combo else ""

    for fam in families_required:
        items = agg[fam]
        if not items:
            results.append(CheckResult(
                label=f"Family {fam} declared{label_suffix}",
                status="fail",
                detail="No code from this family is declared in the routine.",
            ))
        elif not any(not it["factored"] for it in items):
            results.append(CheckResult(
                label=f"Family {fam} non-factored declaration present{label_suffix}",
                status="fail",
                detail=f"All {len(items)} declaration(s) for family {fam} are factored.",
            ))
        else:
            non_fact = [it["code"] for it in items if not it["factored"]]
            results.append(CheckResult(
                label=f"Family {fam} non-factored declaration present{label_suffix}",
                status="pass",
                detail=f"non-factored example(s): {', '.join(non_fact[:3])}",
            ))
    return results


def check_theme_declared(routine, declared, reqs):
    """Acrobatic Routine and Free Combination coach cards must state the routine
    theme. Missing theme -> FAIL. Other routines: not checked."""
    results = []
    rule = reqs.get("theme_required_routines") or {}
    if routine not in rule.get("routines", []):
        return results

    theme = (declared.get("theme") or "").strip()
    if theme:
        results.append(CheckResult(
            label="Theme declared",
            status="pass",
            detail=f"\"{theme}\"",
        ))
    else:
        results.append(CheckResult(
            label="Theme declared",
            status="fail",
            detail=f"{routine} coach cards must state the routine theme - none found on the card.",
        ))
    return results


def check_per_hybrid_family_limits(routine, declared):
    results = []
    is_duet = ("duet" in routine.lower())

    for idx, el in enumerate(declared.get("elements", []), start=1):
        codes_field = el.get("codes")
        if codes_field is None and el.get("code"):
            codes_field = [{"code": el["code"]}]
        if not codes_field:
            continue
        el_type = (el.get("type") or "").lower()
        # Skip rows that are not hybrids (Acrobatics, TREs, SSCs)
        if any(k in el_type for k in ("acro", "tre", "surface", "connection")):
            continue

        per_family_raw = {}
        per_family_weight = {}
        per_family_codes = {}
        per_technique_weight = {}
        per_technique_family = {}
        per_technique_codes = {}

        # Build the full declaration string (all codes in the hybrid, in order)
        all_codes_in_hyb = []
        for c in codes_field:
            code_str = c.get("code", "") if isinstance(c, dict) else str(c)
            if code_str:
                all_codes_in_hyb.append(code_str)
        full_hybrid = " ".join(all_codes_in_hyb)

        for c in codes_field:
            code_str = c.get("code", "") if isinstance(c, dict) else str(c)
            fam = _extract_family(code_str)
            if not fam:
                continue
            # weight = 1.0 unfactored, 0.5 for *0.5, 0.3 for *0.3
            w = _factor_value(code_str) if _has_factor(code_str) else 1.0
            per_family_raw[fam] = per_family_raw.get(fam, 0) + 1
            per_family_weight[fam] = per_family_weight.get(fam, 0.0) + w
            per_family_codes.setdefault(fam, []).append(code_str)
            tech = _technique_id(code_str)
            if tech:
                per_technique_weight[tech] = per_technique_weight.get(tech, 0.0) + w
                per_technique_family[tech] = fam
                per_technique_codes.setdefault(tech, []).append(code_str)

        hyb_label = el.get("type") or f"Hybrid #{idx}"
        if hyb_label == "Hybrid":
            hyb_label = f"Hybrid #{idx}"

        # Per-family: sum of factor weights must not exceed 5.0 (Appendix 6
        # sec. 29.6.1.8 c). As long as the weighted sum is <= 5.0 the family is
        # fine — a raw count above 5 with legitimate factoring is NOT flagged.
        for fam, wsum in per_family_weight.items():
            codes_str = ", ".join(per_family_codes[fam])
            raw = per_family_raw[fam]
            if round(wsum, 3) > 5.0:
                results.append(CheckResult(
                    label=f"{hyb_label}: family {fam} weighted sum",
                    status="fail",
                    detail=f"weighted {wsum:g} (raw {raw}) exceeds max 5.0 per family - "
                           f"codes: [{codes_str}] | full hybrid: \"{full_hybrid}\"",
                ))

        # Per-technique: sum of factor weights must not exceed 3.0
        # (2.0 for Connections in Duet / Mixed Duet).
        for tech, wsum in per_technique_weight.items():
            fam = per_technique_family[tech]
            tech_max = 2.0 if (fam == "C" and is_duet) else 3.0
            tech_codes_str = ", ".join(per_technique_codes.get(tech, []))
            if round(wsum, 3) > tech_max:
                extra = " (Connections in Duet/Mixed Duet)" if tech_max == 2.0 else ""
                results.append(CheckResult(
                    label=f"{hyb_label}: technique {tech} weighted sum",
                    status="fail",
                    detail=f"weighted {wsum:g} exceeds max {tech_max:g}{extra} - "
                           f"codes: [{tech_codes_str}] | full hybrid: \"{full_hybrid}\"",
                ))

    if not results:
        results.append(CheckResult(
            label="Per-hybrid family/technique limits (Appendix 6 sec. 29.6.1.8 c)",
            status="pass",
            detail="No family > 5 weighted, no technique > 3 (or 2 for C in duet).",
        ))
    return results


def fmt_time(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


def parse_time_slot_to_seconds(slot: str):
    if not slot or "-" not in slot:
        return None
    end = slot.split("-")[-1].strip()
    if ":" not in end:
        return None
    try:
        m, s = end.split(":")
        return int(m) * 60 + int(s)
    except ValueError:
        return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--routine", required=True)
    p.add_argument("--declared-json", required=True)
    p.add_argument("--requirements-path", default=DEFAULT_REQS_PATH)
    p.add_argument("--format", choices=("text", "json"), default="text")
    args = p.parse_args(argv)

    declared = json.loads(args.declared_json)
    report = check(args.routine, declared, args.requirements_path)

    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.to_text())

    return 0 if report.summary != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())


# --- Integration of Appendix 7 no-repeat rules ---------------------------
try:
    from acro_parser import (
        check_team_acro_no_repeat,
        check_pair_acro_no_repeat,
    )
except ImportError:
    check_team_acro_no_repeat = None
    check_pair_acro_no_repeat = None


def check_acro_no_repeat(routine: str, declared) -> list[CheckResult]:
    results = []
    if check_team_acro_no_repeat is None:
        return results
    pair_codes = []
    team_codes = []
    open_acro_codes = []
    aliases = {
        "Pair Acrobatic": ("Pair Acrobatic", "Pair Acro", "Pair Acrobatics", "ACRO-PAIR"),
        "Team Acrobatic": ("Team Acrobatic", "Team Acro", "Team Acrobatics", "ACRO-TEAM"),
        "Acrobatic":      ("Acrobatic", "Acro", "Acrobatics"),
    }
    for el in declared.get("elements", []):
        t = (el.get("type") or "").strip()
        code = (el.get("code") or "").strip()
        grp = (el.get("group") or "").strip().upper() or None
        if not code:
            continue
        if any(t.lower() == a.lower() for a in aliases["Pair Acrobatic"]):
            pair_codes.append(code)
        elif any(t.lower() == a.lower() for a in aliases["Team Acrobatic"]):
            team_codes.append((code, grp))
        elif any(t.lower() == a.lower() for a in aliases["Acrobatic"]):
            open_acro_codes.append((code, grp))

    # Pair Acros
    if pair_codes:
        viols = check_pair_acro_no_repeat(pair_codes)
        if viols:
            for v in viols:
                results.append(CheckResult(
                    label=f"Pair Acro no-repeat: {v.code_a}",
                    status="fail",
                    detail=v.detail,
                ))
        else:
            results.append(CheckResult(
                label="Pair Acro no-repeat",
                status="pass",
                detail=f"{len(pair_codes)} code(s), all distinct",
            ))

    # Team Acros (and Open Acrobatic) — both follow the per-group rules
    all_team_like = team_codes + open_acro_codes
    if all_team_like:
        viols = check_team_acro_no_repeat(all_team_like)
        if viols:
            for v in viols:
                results.append(CheckResult(
                    label=f"Acro no-repeat (Group {v.group})",
                    status="fail",
                    detail=f"{v.code_a}  vs  {v.code_b}  ({v.detail})",
                ))
        else:
            results.append(CheckResult(
                label="Team/Open Acro no-repeat (per-group rules)",
                status="pass",
                detail=f"{len(all_team_like)} acro(s), no per-group repetition detected",
            ))

    return results


# --- Integration of code-existence validation (Excel catalogues) ---------
try:
    from code_validator import (
        validate_hybrid_code,
        validate_pair_acro_code,
        validate_team_acro_code,
        validate_open_acro_code,
    )
except ImportError:
    validate_hybrid_code = None
    validate_pair_acro_code = None
    validate_team_acro_code = None
    validate_open_acro_code = None


def check_code_existence(routine: str, declared) -> list[CheckResult]:
    """For every declared code, look it up in the official catalogue.
    Codes the catalogue does not contain become FAIL with a suggestion."""
    results = []
    if validate_hybrid_code is None:
        return results

    # Codes accepted only after ignoring letter-case or O/0 confusion. Collected
    # here and surfaced as a single CHECK line at the end instead of failing.
    casing_notes: list[str] = []

    for idx, el in enumerate(declared.get("elements", []), start=1):
        t = (el.get("type") or "").strip()
        tlow = t.lower()

        # Hybrid rows: each item in `codes` is an individual hybrid code
        if "hybrid" in tlow:
            codes_field = el.get("codes") or ([{"code": el.get("code")}] if el.get("code") else [])
            full_hybrid = " ".join(
                (c.get("code", "") if isinstance(c, dict) else str(c))
                for c in codes_field
                if (c.get("code", "") if isinstance(c, dict) else str(c))
            )
            for c in codes_field:
                code_str = c.get("code", "") if isinstance(c, dict) else str(c)
                if not code_str:
                    continue
                r = validate_hybrid_code(code_str)
                if r.get("casing_note"):
                    casing_notes.append(f"{t} #{idx}: {r['casing_note']}")
                if not r["ok"]:
                    sugg = r.get("suggestions") or []
                    detail = r["reason"]
                    if sugg:
                        detail += f" Try: {', '.join(sugg)}"
                    detail += f" | full hybrid: \"{full_hybrid}\""
                    results.append(CheckResult(
                        label=f"{t} #{idx}: hybrid code '{code_str}'",
                        status="fail",
                        detail=detail,
                    ))

        # Pair acro rows
        elif tlow in ("pair acrobatic", "pair acro", "pair acrobatics", "acro-pair"):
            code_str = (el.get("code") or "").strip()
            if not code_str:
                continue
            r = validate_pair_acro_code(code_str)
            if r.get("casing_note"):
                casing_notes.append(f"Pair Acro #{idx}: {r['casing_note']}")
            if not r["ok"]:
                sugg = r.get("suggestions") or []
                detail = r["reason"]
                if sugg:
                    detail += f" Try: {', '.join(sugg)}"
                results.append(CheckResult(
                    label=f"Pair Acro #{idx}: code '{code_str}'",
                    status="fail",
                    detail=detail,
                ))

        # Team / Open acro rows (same per-group format)
        elif tlow in ("team acrobatic", "team acro", "team acrobatics",
                      "acrobatic", "acro", "acrobatics", "acro-team"):
            code_str = (el.get("code") or "").strip()
            if not code_str:
                continue
            r = validate_team_acro_code(code_str, el.get("group"))
            if not r["ok"]:
                if "issues" in r:
                    for i in r["issues"]:
                        component = i.get("component", "")
                        # Case / O-0 differences: accept the code, collect the
                        # difference for the single CHECK line at the end.
                        if component.startswith("CASING:"):
                            ex = i.get("valid_examples") or []
                            canon = ex[0] if ex else "?"
                            casing_notes.append(
                                f"{t} #{idx}: {i['value']} -> {canon}"
                            )
                            continue
                        sev = i.get("severity", "fail")
                        examples = i.get("valid_examples")
                        # Compatibility warnings are precautionary ("verify against
                        # the CR / the card"), not confirmed rule breaks — surface
                        # them in the softer CHECK tier, not WARN.
                        if sev == "warn" and component.startswith("COMPAT:"):
                            sev = "info"
                        if isinstance(examples, list) and examples:
                            # COMPAT / MIN_REQUIRED issues are rule violations, not
                            # failed catalogue look-ups — don't mislabel them.
                            if component.startswith("COMPAT:") or component.startswith("MIN_REQUIRED"):
                                joined = ", ".join(examples)
                                detail = joined if joined.startswith("(") else "expected: " + joined
                            else:
                                detail = "not in catalogue. Try: " + ", ".join(examples)
                        else:
                            detail = ""
                        results.append(CheckResult(
                            label=f"{t} #{idx}: '{code_str}' — {component} '{i['value']}'",
                            status=sev,
                            detail=detail,
                        ))
                else:
                    results.append(CheckResult(
                        label=f"{t} #{idx}: code '{code_str}'",
                        status="fail",
                        detail=r.get("reason", ""),
                    ))

    if not results:
        results.append(CheckResult(
            label="All codes exist in official catalogues (Appendix 6 + 7)",
            status="pass",
            detail="every declared code is recognised",
        ))

    if casing_notes:
        results.append(CheckResult(
            label="Case / 0-O differences vs catalogue",
            status="info",
            detail="; ".join(casing_notes)
                   + "  - accepted as matches; fix on the card to match the "
                     "catalogue spelling exactly.",
        ))
    return results
