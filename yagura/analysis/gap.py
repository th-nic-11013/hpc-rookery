"""Judge computed band gaps against reference gaps, knowing what each
functional class is expected to do. Standard library only.

The judgement this replaces was done by hand for UiO-66 (2026-11-07,
notes/uio66-cellopts-results-2026-11-07.md) because lit-search's
`compare` judges a gap only against the same functional (G34, G46):

  - semilocal functionals (LDA, GGA, meta-GGA: PBE, r2SCAN, ...) are
    expected below experiment; that is reported as the expected bias,
    not scored, and a semilocal gap at or above experiment is flagged
  - hybrids (global or screened) are scored against experiment with the
    same tolerance lit-search uses for gaps (agree <= 0.10 eV, marginal
    <= 0.30 eV)
  - against another calculation: the same functional is scored; a
    different one is information, with the expected direction (more
    exact exchange opens the gap) checked when it is determined

The references are passed in, not looked up: take them from lit-search
(or a paper) and give each its kind. yagura reads no library.

Caveats are returned, never hidden: a Kohn-Sham HOMO-LUMO gap is not an
optical gap (exciton binding lowers the optical edge), and a Gamma-only
gap may not be the band gap of the crystal.
"""
import re

# agree / marginal, eV (lit-search's band_gap tolerance)
TOLERANCE = (0.10, 0.30)

# name -> (class, exact-exchange fraction, screened)
FUNCTIONALS = {
    "lda": ("semilocal", 0.0, False), "pbe": ("semilocal", 0.0, False),
    "pbesol": ("semilocal", 0.0, False), "revpbe": ("semilocal", 0.0, False),
    "rpbe": ("semilocal", 0.0, False), "blyp": ("semilocal", 0.0, False),
    "bp86": ("semilocal", 0.0, False), "scan": ("semilocal", 0.0, False),
    "rscan": ("semilocal", 0.0, False), "r2scan": ("semilocal", 0.0, False),
    "tpss": ("semilocal", 0.0, False),
    "pbe0": ("hybrid", 0.25, False), "pbeh": ("hybrid", 0.25, False),
    "b3lyp": ("hybrid", 0.20, False), "scan0": ("hybrid", 0.25, False),
    "r2scan0": ("hybrid", 0.25, False), "r2scanh": ("hybrid", 0.10, False),
    "r2scan50": ("hybrid", 0.50, False), "pbe50": ("hybrid", 0.50, False),
    "hf": ("hybrid", 1.0, False),
    "hse06": ("screened hybrid", 0.25, True), "hse03": ("screened hybrid", 0.25, True),
    "hsesol": ("screened hybrid", 0.25, True),
}
KINDS = ("optical", "fundamental", "calculated")
KPOINTS = ("gamma", "mesh")


def normalise(name: str) -> str:
    """'r²SCAN-D3(BJ)', 'HSE06 + D3' -> 'r2scan', 'hse06': lower case, no
    dispersion suffix, no spaces, dashes or underscores."""
    n = name.lower().replace("²", "2").strip()
    n = re.sub(r"[\s_+-]*(d3\(bj\)|d3bj|d3|d4|d2|vv10|rvv10)\s*$", "", n)
    return re.sub(r"[\s_+-]", "", n)


def classify(functional: str, exact_exchange: float | None = None,
             screened: bool | None = None) -> dict:
    """The class of a functional. An unlisted one needs exact_exchange
    (and screened, for a range-separated hybrid); a listed one may not
    contradict the table."""
    key = normalise(functional)
    if key in FUNCTIONALS:
        cls, x, s = FUNCTIONALS[key]
        if exact_exchange is not None and abs(exact_exchange - x) > 1e-9:
            raise ValueError(f"{functional} has exact_exchange {x}, not {exact_exchange}; "
                             "leave it out for a listed functional")
        if screened is not None and screened != s:
            raise ValueError(f"{functional} is {'' if s else 'not '}screened; "
                             "leave screened out for a listed functional")
        return {"functional": functional, "key": key, "class": cls,
                "exact_exchange": x, "screened": s}
    if exact_exchange is None:
        raise ValueError(f"unknown functional {functional!r}: pass exact_exchange (0 for "
                         "a semilocal one) and screened, or use one of "
                         + ", ".join(sorted(FUNCTIONALS)))
    if not 0.0 <= exact_exchange <= 1.0:
        raise ValueError("exact_exchange is a fraction between 0 and 1")
    s = bool(screened)
    cls = "semilocal" if exact_exchange == 0 else "screened hybrid" if s else "hybrid"
    return {"functional": functional, "key": key, "class": cls,
            "exact_exchange": exact_exchange, "screened": s}


def _score(delta: float) -> str:
    x = abs(delta)
    return "agree" if x <= TOLERANCE[0] else "marginal" if x <= TOLERANCE[1] else "disagree"


def _direction(mine: dict, theirs: dict) -> int | None:
    """+1 if mine is expected to give the larger gap, -1 smaller, 0 the
    same, None when it isn't determined (screened vs global with a
    different fraction)."""
    if mine["screened"] == theirs["screened"]:
        d = mine["exact_exchange"] - theirs["exact_exchange"]
        return 0 if abs(d) < 1e-9 else (1 if d > 0 else -1)
    # same fraction: screening closes the gap; a screened hybrid against a
    # semilocal one still opens it
    if mine["exact_exchange"] == theirs["exact_exchange"]:
        return -1 if mine["screened"] else 1
    if theirs["exact_exchange"] == 0:
        return 1
    if mine["exact_exchange"] == 0:
        return -1
    return None


def _judge(gap: dict, cls: dict, ref: dict, rcls: dict | None) -> dict:
    delta = gap["gap_ev"] - ref["value"]
    row = {"reference": ref.get("source") or ref["kind"], "kind": ref["kind"],
           "value": ref["value"], "delta": round(delta, 3),
           "delta_pct": round(100 * delta / ref["value"], 1)}
    if ref["kind"] != "calculated":
        if cls["class"] == "semilocal":
            if delta < 0:
                row["verdict"] = "expected bias"
                row["reason"] = "semilocal gaps are expected below experiment; not scored"
            else:
                row["verdict"] = "unexpected"
                row["reason"] = ("a semilocal gap at or above experiment is unusual: check the "
                                 "reference kind, the structure and the SCF")
        else:
            row["verdict"] = _score(delta)
            row["reason"] = (f"{cls['class']} vs {ref['kind']} gap, tolerance "
                             f"{TOLERANCE[0]}/{TOLERANCE[1]} eV")
        return row
    if cls["key"] == rcls["key"]:
        row["verdict"] = _score(delta)
        row["reason"] = ("same functional: a difference is basis, geometry, k-points or "
                         "code, not physics")
        return row
    d = _direction(cls, rcls)
    sign = 0 if abs(delta) <= TOLERANCE[0] else (1 if delta > 0 else -1)
    if d is None or d == 0:
        row["verdict"] = "info"
        row["reason"] = (f"different functional ({ref['functional']}); " +
                         ("same exact-exchange fraction, so no expected direction" if d == 0 else
                          "direction not determined between a screened and a global hybrid "
                          "with different fractions"))
    elif d == sign:
        row["verdict"] = "info: consistent"
        row["reason"] = (f"different functional ({ref['functional']}); the gap moves the way "
                         "the exact-exchange difference predicts")
    else:
        row["verdict"] = "info: unexpected direction"
        want = {1: "larger", -1: "smaller", 0: "about the same"}[d]
        row["reason"] = (f"different functional ({ref['functional']}); expected a {want} "
                         "gap than the reference: check the two calculations")
    return row


def compare(gaps: list[dict], references: list[dict], kpoints: str = "gamma",
            computed_gap: str = "ks") -> dict:
    """Judge each computed gap against each reference. See the server
    tool gap_compare for the argument forms."""
    if not gaps:
        raise ValueError("gaps is empty: pass at least one {functional, gap_ev}")
    if not references:
        raise ValueError("references is empty: pass at least one {value, kind}")
    if kpoints not in KPOINTS:
        raise ValueError(f"kpoints is one of {KPOINTS}")
    if computed_gap not in ("ks", "quasiparticle"):
        raise ValueError('computed_gap is "ks" (Kohn-Sham HOMO-LUMO) or "quasiparticle"')

    refs = []
    for r in references:
        bad = set(r) - {"value", "kind", "functional", "source", "exact_exchange", "screened"}
        if bad:
            raise ValueError(f"unknown reference field(s) {sorted(bad)}")
        if r.get("kind") not in KINDS:
            raise ValueError(f"reference kind is one of {KINDS}, got {r.get('kind')!r}")
        if not isinstance(r.get("value"), (int, float)) or r["value"] <= 0:
            raise ValueError("reference value is a positive gap in eV")
        rcls = None
        if r["kind"] == "calculated":
            if not r.get("functional"):
                raise ValueError("a calculated reference needs its functional")
            rcls = classify(r["functional"], r.get("exact_exchange"), r.get("screened"))
        refs.append((r, rcls))

    out, seen = [], set()
    for g in gaps:
        bad = set(g) - {"functional", "gap_ev", "label", "exact_exchange", "screened"}
        if bad:
            raise ValueError(f"unknown gap field(s) {sorted(bad)}")
        if not g.get("functional"):
            raise ValueError("each gap needs its functional")
        if not isinstance(g.get("gap_ev"), (int, float)) or g["gap_ev"] <= 0:
            raise ValueError(f"{g['functional']}: gap_ev is a positive gap in eV "
                             "(cp2k_parse_output's gap_ev)")
        label = g.get("label") or g["functional"]
        if label in seen:
            raise ValueError(f"two gaps are labelled {label!r}: give each a label")
        seen.add(label)
        cls = classify(g["functional"], g.get("exact_exchange"), g.get("screened"))
        out.append({"label": label, "functional": g["functional"], "class": cls["class"],
                    "exact_exchange": cls["exact_exchange"], "screened": cls["screened"],
                    "gap_ev": g["gap_ev"],
                    "rows": [_judge(g, cls, r, rc) for r, rc in refs]})

    closest = {}
    for k, (r, _) in enumerate(refs):
        if r["kind"] == "calculated":
            continue
        scored = [(abs(e["rows"][k]["delta"]), e["label"]) for e in out
                  if e["class"] != "semilocal"]
        closest[r.get("source") or f"{r['kind']} {r['value']}"] = \
            [lab for _, lab in sorted(scored)]

    caveats = []
    kinds = {r["kind"] for r, _ in refs}
    if computed_gap == "ks" and "optical" in kinds:
        caveats.append("Kohn-Sham HOMO-LUMO gaps are compared with an optical (absorption-edge) "
                       "gap: exciton binding lowers the optical gap below the fundamental one, so "
                       "a hybrid that matches it may underestimate the fundamental gap")
    if computed_gap == "ks" and "fundamental" in kinds:
        caveats.append("a Kohn-Sham gap is not a quasiparticle gap; hybrids only approximate it")
    if kpoints == "gamma":
        caveats.append("Gamma-only gaps: the band edges may lie elsewhere in the Brillouin "
                       "zone. Fine for a large cell with flat bands; check with a k-mesh or a "
                       "supercell otherwise")
    if any(e["class"] == "semilocal" for e in out) and kinds - {"calculated"}:
        caveats.append("semilocal gaps are reported, not scored, against experiment")
    return {"gaps": out, "closest": closest, "caveats": caveats,
            "tolerance_ev": {"agree": TOLERANCE[0], "marginal": TOLERANCE[1]}}
