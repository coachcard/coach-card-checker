#!/usr/bin/env python3
"""Parse acrobatic codes and apply no-repeat rules per Appendix 7.

Code formats observed in Appendix 7 examples and the Acro CAEM coach card:

  Group A   <Group>-<Construction>-<Direction>-<P1>[/2<P2>][-<bonus...>]
            e.g. A-Sq-Back-pk/2ln-s1
                 A-Feet-Back-tk/2ln-s1-Feet
                 A-Sq-Back-pk/2ja-s1

  Group B   <Group>-<Construction>-<Connection>-<P1>[/2<P2>][-<bonus...>]
            e.g. B-St-PP-spl
                 B-StH-1F1F-vs
                 B-St-1P1P-bb/2ow

  Group C   <Group>-<Construction>-<Direction>-<P1>[/2<P2>][-<rotation>][-<bonus>]
            e.g. C-Thr^2F-Bln-ow/2tk-Cs1
                 C-Thr>StH-Forw-fl-Jump
                 C-Thr>St-Bln-tk-Cs1

  Group P   <Group>-<Construction>-<Connection>-<P1>[/2<P2>][-<bonus...>]
            e.g. P-Knees-SP+K-bo/2bb-Dive/Porp
                 P-Knees-SP+K-bb/2ow-Pos3
                 P-Knees-3pA-ne-Pos3
                 P-2S-FA+PF-ne/2ey-Trav

The 4th token is a DIRECTION for groups A and C, but a CONNECTION (grip) for
groups B and P. P1 and (optional) P2 follow, with P2 prefixed by the literal
"2" (e.g. "/2ln" means second position is "ln"). Anything trailing those
positions is treated as bonus(es)/rotation, joined with "-".

Pair Acro codes (Appendix 7 Pair) use a totally different shape and the
no-repeat rule there is just: no two identical full codes.
"""
from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any


_RESERVED_CODES = None


def reserved_codes() -> list[str]:
    """Former federation-reserved code strings (2026 Phase 1 / Phase 2), longest
    first. Open to all since January 1 2027 and written WITHOUT the prefix, but
    a card may still carry one: keep masking them so the parser never shreds
    them (check_coach_card reports the prefix as an error).

    These carry their reserving federation(s) as a prefix — 'JPN-ESP-USA-Mov!'
    — so they contain the very '-' this module splits on. Loaded once; [] if the
    reference file is missing."""
    global _RESERVED_CODES
    if _RESERVED_CODES is None:
        p = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "prefixed_codes_2027.json"))
        try:
            with open(p, "r", encoding="utf-8") as f:
                _RESERVED_CODES = sorted(json.load(f).get("codes") or {},
                                         key=len, reverse=True)
        except Exception:
            _RESERVED_CODES = []
    return _RESERVED_CODES


def split_segments(rest: str) -> list[str]:
    """Split an acro code on '-' WITHOUT shredding a federation-reserved code,
    whose federation prefix contains '-' itself.

    'LH-LiH-br/2ow-r1L-Hold/JPN-ESP-USA-Mov!' must give
    [..., 'r1L', 'Hold/JPN-ESP-USA-Mov!'] and not four broken pieces.
    """
    masked, subs = rest, []
    for rc in reserved_codes():
        while rc in masked:
            masked = masked.replace(rc, f"\x00{len(subs)}\x00", 1)
            subs.append(rc)
    parts = masked.split("-")
    if not subs:
        return parts
    out = []
    for p in parts:
        for i, rc in enumerate(subs):
            p = p.replace(f"\x00{i}\x00", rc)
        out.append(p)
    return out


GROUP_DIRECTION_OR_CONNECTION = {
    "A": "direction",
    "C": "direction",
    "B": "connection",
    "P": "connection",
}


@dataclass
class TeamAcroCode:
    raw: str
    group: str | None = None
    construction: str | None = None
    direction: str | None = None
    connection: str | None = None
    p1: str | None = None
    p2: str | None = None
    extras: list[str] = field(default_factory=list)
    parse_error: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "parse_error" or v}


_TEAM_HEAD_RE = re.compile(r"^\s*([ABCP])\s*-\s*(.*)$", re.IGNORECASE)

# European coach cards write decimals with a comma ("r0,5", "2F0,5"); the
# catalogues use a dot. Normalise commas that sit between two digits.
_DECIMAL_COMMA_RE = re.compile(r"(?<=\d),(?=\d)")


def parse_team_acro_code(code: str, group: str | None = None) -> TeamAcroCode:
    """Parse a team/open acro code.

    On a real coach card the DECLARED DIFFICULTY column has NO group letter — it
    starts straight at the construction, and the group comes from the PART column
    (ACRO-A/B/C/P). So when `group` is given, the whole string is parsed as
    construction-first (a leading 'B-' is the construction, not the group).

    When `group` is None the legacy behaviour applies: a leading '<A-P>-' prefix
    is treated as the group (used by older callers / the module demo).
    """
    out = TeamAcroCode(raw=code)
    if not code:
        out.parse_error = "empty"
        return out
    code = _DECIMAL_COMMA_RE.sub(".", code)

    g = (group or "").strip().upper()
    if g in ("A", "B", "C", "P"):
        out.group = g
        rest = code.strip()
    else:
        m = _TEAM_HEAD_RE.match(code)
        if not m:
            out.parse_error = "no group prefix"
            return out
        out.group = m.group(1).upper()
        rest = m.group(2)
    parts = split_segments(rest) if rest else []
    # Be lenient: parse whatever is available so that min-requirements check
    # can report the specific missing component.
    if len(parts) >= 1 and parts[0]:
        out.construction = parts[0]
    if len(parts) >= 2 and parts[1]:
        if GROUP_DIRECTION_OR_CONNECTION.get(out.group) == "direction":
            out.direction = parts[1]
        else:
            out.connection = parts[1]
    if len(parts) >= 3 and parts[2]:
        pos_block = parts[2]
        if "/2" in pos_block:
            p1, _, p2 = pos_block.partition("/2")
            out.p1 = p1
            out.p2 = p2
        else:
            out.p1 = pos_block
    out.extras = parts[3:] if len(parts) > 3 else []
    return out


# --------------------------------------------------------------------
# No-repeat rule application
# --------------------------------------------------------------------
@dataclass
class NoRepeatViolation:
    group: str
    rule: str
    code_a: str
    code_b: str
    detail: str


def check_team_acro_no_repeat(codes) -> list[NoRepeatViolation]:
    """`codes` is a list of code strings, or of (code, group) pairs. Returns
    violations per Appendix 7 section 29.7.1.2 (o):

      Group A: no shared P1/P2 with another A acro (third-position bonus excepted)
      Group B: no shared construction or connection with another B acro
      Group C: no shared construction with another C acro
      Group P: no shared construction OR connection OR P1/P2 OR bonus with another P
    """
    def _split(item):
        if isinstance(item, (tuple, list)):
            return item[0], (item[1] if len(item) > 1 else None)
        return item, None

    parsed = [
        (c, parse_team_acro_code(c, g))
        for c, g in (_split(x) for x in codes)
        if c
    ]
    violations: list[NoRepeatViolation] = []

    by_group: dict[str, list[tuple[str, TeamAcroCode]]] = {}
    for raw, p in parsed:
        if p.group:
            by_group.setdefault(p.group, []).append((raw, p))

    # Group A — no shared P1 nor P2
    for i, (ra, pa) in enumerate(by_group.get("A", [])):
        for rb, pb in by_group.get("A", [])[i + 1 :]:
            shared = []
            for label, va, vb in (("P1", pa.p1, pb.p1), ("P2", pa.p2, pb.p2)):
                if va and vb and va == vb:
                    shared.append(f"{label}={va}")
            # Cross check P1 against P2 too (positions are the same set)
            cross = []
            if pa.p1 and pb.p2 and pa.p1 == pb.p2:
                cross.append(f"P1({pa.p1})==P2({pb.p2})")
            if pa.p2 and pb.p1 and pa.p2 == pb.p1:
                cross.append(f"P2({pa.p2})==P1({pb.p1})")
            shared.extend(cross)
            if shared:
                violations.append(NoRepeatViolation(
                    group="A",
                    rule="Group A: positions must not repeat (P1 or P2) — third position bonus excepted",
                    code_a=ra, code_b=rb,
                    detail="; ".join(shared),
                ))

    # Group B — no shared construction or connection
    for i, (ra, pa) in enumerate(by_group.get("B", [])):
        for rb, pb in by_group.get("B", [])[i + 1 :]:
            shared = []
            if pa.construction and pa.construction == pb.construction:
                shared.append(f"construction={pa.construction}")
            if pa.connection and pa.connection == pb.connection:
                shared.append(f"connection={pa.connection}")
            if shared:
                violations.append(NoRepeatViolation(
                    group="B",
                    rule="Group B: construction and type of connection (grip) must not repeat",
                    code_a=ra, code_b=rb,
                    detail="; ".join(shared),
                ))

    # Group C — no shared construction
    for i, (ra, pa) in enumerate(by_group.get("C", [])):
        for rb, pb in by_group.get("C", [])[i + 1 :]:
            if pa.construction and pa.construction == pb.construction:
                violations.append(NoRepeatViolation(
                    group="C",
                    rule="Group C: construction must not repeat",
                    code_a=ra, code_b=rb,
                    detail=f"construction={pa.construction}",
                ))

    # Group P — no shared construction OR connection OR P1/P2 (3rd position
    # bonus excepted) OR bonus (new in 2027). Rotations (Pr0.5...) excluded.
    for i, (ra, pa) in enumerate(by_group.get("P", [])):
        for rb, pb in by_group.get("P", [])[i + 1 :]:
            shared = []
            if pa.construction and pa.construction == pb.construction:
                shared.append(f"construction={pa.construction}")
            if pa.connection and pa.connection == pb.connection:
                shared.append(f"connection={pa.connection}")
            for label, va, vb in (("P1", pa.p1, pb.p1), ("P2", pa.p2, pb.p2)):
                if va and vb and va == vb:
                    shared.append(f"{label}={va}")
            # Cross-check P1<->P2
            if pa.p1 and pb.p2 and pa.p1 == pb.p2:
                shared.append(f"P1({pa.p1})==P2")
            if pa.p2 and pb.p1 and pa.p2 == pb.p1:
                shared.append(f"P2({pa.p2})==P1")
            # 2027 (Appendix 3, Sept 27 2026): Group P also cannot repeat the
            # same bonus(es), Pos3 included. Rotations are excluded.
            common = sorted(set(_p_bonuses(pa)) & set(_p_bonuses(pb)))
            for b in common:
                shared.append(f"bonus={b}")
            if shared:
                violations.append(NoRepeatViolation(
                    group="P",
                    rule="Group P: construction, connection, positions (P1/P2) and bonus(es) incl. Pos3 must not repeat",
                    code_a=ra, code_b=rb,
                    detail="; ".join(shared),
                ))

    return violations


_P_BONUS_VOCAB = None


def _p_bonuses(p: "TeamAcroCode") -> list[str]:
    """Group P bonus codes declared in an acro (catalogue 'Bonus' list, longest
    match first). Rotations are left out. The Pos3 bonus IS a bonus: it cannot
    be declared on two Group P acros (confirmed by J. Lamontagne, Oct 2026)."""
    global _P_BONUS_VOCAB
    if _P_BONUS_VOCAB is None:
        try:
            path = os.path.normpath(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "catalogue_team_acro.json"))
            with open(path, "r", encoding="utf-8") as f:
                vb = json.load(f)["valid_codes"]["P"].get("Bonus", [])
        except Exception:
            vb = []
        _P_BONUS_VOCAB = sorted(vb, key=len, reverse=True)
    out = []
    for ex in p.extras:
        rest = ex.strip()
        while rest:
            rest = rest.lstrip("/+ ")
            hit = next((b for b in _P_BONUS_VOCAB if rest.startswith(b)
                        and rest[len(b):len(b) + 1] in ("", "/", "+", " ")), None)
            if hit:
                out.append(hit)
                rest = rest[len(hit):]
            else:
                m = re.match(r"^[^/+]+", rest)
                if not m:
                    break
                rest = rest[m.end():]
    return out


def check_pair_acro_no_repeat(codes: list[str]) -> list[NoRepeatViolation]:
    """Pair acro rule (Appendix 7 §29.7.2.1.xiii):
       In duets/mixed duets with 2+ pair acrobatics, a pair acrobatic code
       must not be repeated (exact match).
    """
    seen: dict[str, int] = {}
    violations: list[NoRepeatViolation] = []
    for c in codes:
        if not c:
            continue
        if c in seen:
            violations.append(NoRepeatViolation(
                group="Pair",
                rule="Pair Acro: a pair acrobatic code must not be repeated",
                code_a=c, code_b=c,
                detail=f"'{c}' appears more than once",
            ))
        seen[c] = seen.get(c, 0) + 1
    return violations


if __name__ == "__main__":
    # Quick demo
    samples = [
        "A-Sq-Back-pk/2ln-s1",
        "A-Sq-Back-pk/2ja-s1",       # same P1 'pk' as previous => violation
        "A-Sq-Back-tk/2spl-s1",      # OK
        "B-St-1P1P-bb/2ow",
        "B-St-PP-bb/2ow",            # same construction 'St' & same P1 'bb'/P2 'ow' => violation
        "B-StH-FF-sd",               # OK
        "C-Thr>St-Bln-tk-Cs1",
        "C-Thr>St-Forw-sd/2tk-Cd-Jump",  # same construction 'Thr>St' => violation
        "C-Thr>F-Forw-sd/2tk-Cd-Jump",   # OK
        "P-Knees-SP+K-bb/2ow-Pos3",
        "P-Knees-3pA-ne-Pos3",       # same construction 'Knees' & same bonus 'Pos3' => violation
        "P-2S-FA+PF-ne/2ey-Trav",    # OK
    ]
    for v in check_team_acro_no_repeat(samples):
        print(f"FAIL [{v.group}] {v.code_a} <-> {v.code_b} ({v.detail})")

    pair_samples = ["Jfs1B", "Js1B+f", "Jfs1B"]
    for v in check_pair_acro_no_repeat(pair_samples):
        print(f"FAIL [{v.group}] {v.detail}")
