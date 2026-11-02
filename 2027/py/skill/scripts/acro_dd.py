"""Team / Open acrobatic Degree of Difficulty (Appendix 7, Team Acrobatics
Catalogue): DD = BM (0.50) + C + S + D + P + R + T + B, each component value
read from catalogue_team_acro.json.

compute_team_acro_dd(code, group) -> {"ok", "dd", "parts", "unknown"}
`ok` is False when a component could not be priced (unknown token): the DD is
then not reliable and callers should not judge a limit on it.
"""
from __future__ import annotations
import json, os, re

BASE_MARK = 0.5
_HERE = os.path.dirname(os.path.abspath(__file__))
_CAT = None


def _cat():
    global _CAT
    if _CAT is None:
        with open(os.path.join(_HERE, "..", "catalogue_team_acro.json"), encoding="utf-8") as f:
            data = json.load(f)["components_by_group"]
        _CAT = {g: {comp: {e["code"]: float(e["DD"] or 0) for e in lst} for comp, lst in comps.items()}
                for g, comps in data.items()}
    return _CAT


def _loose(s):
    """Same tolerance as the code check: letter case and O/0 ignored."""
    return (s or "").translate(str.maketrans("Oo", "00")).lower()


def _lookup(group, comps, code):
    """Exact match first; otherwise a case / O-0 insensitive match, used only
    when it points to ONE catalogue code (the case itself is reported by the
    code check as a CHECK, it does not block the DD)."""
    cat = _cat()
    for comp in comps:
        v = cat.get(group, {}).get(comp, {}).get(code)
        if v is not None:
            return v
    if code:
        hits = {c: v for comp in comps for c, v in cat.get(group, {}).get(comp, {}).items()
                if _loose(c) == _loose(code)}
        if len(set(hits.values())) == 1 and len(hits) >= 1:
            return next(iter(hits.values()))
    return None


def _pos_value(group, pos, second):
    """Position value. Position 2 codes are catalogued with a '2' prefix.
    Group C borrows positions from Group A or B."""
    comp = "Position2" if second else "Position1"
    key = ("2" + pos) if second else pos
    for g in [group] + ([x for x in "AB" if x != group] if group == "C" else []):
        v = _lookup(g, [comp], key)
        if v is not None:
            return v
    return None


def compute_team_acro_dd(code: str, group: str | None = None) -> dict:
    from acro_parser import parse_team_acro_code
    try:
        from code_validator import _normalize_code
        code = _normalize_code(code)
    except Exception:
        pass
    p = parse_team_acro_code(code, group)
    g = p.group
    parts, unknown = [("BM", BASE_MARK)], []
    if not g:
        return {"ok": False, "dd": None, "parts": parts, "unknown": ["group"]}

    def add(label, value, token):
        if token is None:
            return
        if value is None:
            unknown.append(f"{label} '{token}'")
        else:
            parts.append((f"{label} {token}", value))

    add("Construction", _lookup(g, ["Construction"], p.construction), p.construction)
    if g in ("A", "C"):
        add("Direction", _lookup(g, ["Direction"], p.direction), p.direction)
    else:
        add("Connection", _lookup(g, ["Connection"], p.connection), p.connection)
    if p.p1:
        add("Position1", _pos_value(g, p.p1, False), p.p1)
    if p.p2:
        add("Position2", _pos_value(g, p.p2, True), p.p2)

    vocab = {}
    for comp in ("Rotation Base", "Rotation Plane", "Bonus"):
        for c, v in _cat().get(g, {}).get(comp, {}).items():
            vocab.setdefault(c, v)
    keys = sorted(vocab, key=len, reverse=True)
    for ex in p.extras:
        rest = ex.strip()
        while rest:
            rest = rest.lstrip("/+ ")
            if not rest:
                break
            hit = next((k for k in keys if rest.startswith(k)
                        and rest[len(k):len(k) + 1] in ("", "/", "+", " ")), None)
            if not hit:
                m0 = re.match(r"^[^/+]+", rest)
                tok = m0.group(0) if m0 else rest
                cand = [k for k in keys if _loose(k) == _loose(tok)]
                if len({vocab[k] for k in cand}) == 1:
                    parts.append((cand[0], vocab[cand[0]]))
                    rest = rest[len(tok):]
                    continue
            if hit:
                parts.append((hit, vocab[hit]))
                rest = rest[len(hit):]
            else:
                m = re.match(r"^[^/+]+", rest)
                unknown.append(f"'{m.group(0) if m else rest}'")
                rest = rest[m.end():] if m else ""
    dd = round(sum(v for _, v in parts), 4)
    return {"ok": not unknown, "dd": dd, "parts": parts, "unknown": unknown, "group": g}
