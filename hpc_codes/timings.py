"""Summarise a table of production timings into `benchmarks` records (G36).

A production campaign (e.g. labelling a training set with VASP single
points) times every frame. The records keep one summary per layout group,
never one record per frame: `samples` holds the statistics of seconds per
SCF step (each run's SCF loop time / its SCF steps) and of the steps and
times per run, and `metric` stays what readers of one run expect (schema:
benchmarks.samples). Local only: reads a table on this computer.
"""
import csv
import datetime
import re
import statistics
from pathlib import Path

from hpc_codes import records

#: columns every row needs: total ranks, nodes, SCF steps, SCF loop seconds
REQUIRED = ("ranks", "nodes", "nscf", "loop")
#: besides the layout, these split groups: another band or k-point count is
#: another problem, even at the same layout
PROBLEM = {"nbands": "nb", "nkpts": "nk"}   # column: its id part when needed
#: a layout key's id part: VASP ids end -k<KPAR>c<NCORE> (records skill)
LAYOUT_ID = {"KPAR": "k", "NCORE": "c", "NPAR": "p"}
#: what a summary takes from `like`; everything else is the table's
FROM_LIKE = ("facility", "partition", "node_type", "code", "version", "build_id",
             "build_external", "build_desc", "system", "method", "run_type")


def _id_part(text) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def _layout_keys(code: str) -> list[str]:
    """The code's `layout` keys from the schema (VASP: KPAR, NCORE, NPAR)."""
    f = records.types()["benchmarks"]["fields"]["layout"]
    pc = f.get("per_code") or {}
    return list((pc.get(code, pc.get("*", {})).get("fields") or f.get("fields") or {}))


def _read(path: Path) -> list[dict]:
    lines = path.read_text().splitlines()
    if not lines:
        raise ValueError(f"{path} is empty")
    return list(csv.DictReader(lines, delimiter="\t" if "\t" in lines[0] else ","))


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return int(x) if x.is_integer() else x


def _stats(xs: list[float]) -> dict:
    return {"mean": round(statistics.fmean(xs), 3),
            "std": round(statistics.stdev(xs), 3) if len(xs) > 1 else None,
            "min": round(min(xs), 3), "median": round(statistics.median(xs), 3),
            "max": round(max(xs), 3)}


def summarise(facility: str, table_path: str, like: str | None = None, base: dict | None = None,
              frame_prefix: str | None = None, date: str | None = None, tag: str | None = None,
              min_samples: int = 10, notes: str | None = None, dry_run: bool = False,
              nelm: int | None = None) -> dict:
    """Build one summary `benchmarks` record per layout group of a per-run
    timing table, validate them all, and append them unless dry_run.
    Unconverged runs are left out: nscf >= nelm (the SCF step limit), or a
    false `converged` column."""
    if nelm is not None and nelm < 1:
        raise ValueError(f"nelm is the SCF step limit (VASP NELM), a positive integer: {nelm}")
    start = {}
    if like:
        found = records.query(facility, "benchmarks", {"id": like})
        if not found:
            raise ValueError(f"no benchmarks record {like!r} on {facility} (records_query to list them)")
        start = {k: found[0][k] for k in FROM_LIKE if k in found[0]}
    base = base or {}
    rec0 = {**start, **base, "facility": facility,
            "system": {**(start.get("system") or {}), **(base.get("system") or {})},
            "method": {**(start.get("method") or {}), **(base.get("method") or {})}}
    rec0.setdefault("run_type", "single_point")
    if rec0["run_type"] != "single_point":
        raise ValueError("a timings table summarises single points (SCF loop time / SCF steps); "
                         f"run_type is {rec0['run_type']!r}")
    code = rec0.get("code")
    if not code or not rec0["system"].get("label"):
        raise ValueError("no code or system to record: pass like=<a benchmarks id> of the same system "
                         "and method, or base={code, version, build_id, partition, node_type, "
                         "system, method}")
    if tag is not None and not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", tag):
        raise ValueError(f"tag is a short lower-case id part, e.g. prod or pbe-u: {tag!r}")

    path = Path(table_path).expanduser()
    rows = _read(path)
    cols = set(rows[0]) if rows else set()
    missing = [c for c in REQUIRED if c not in cols]
    if missing:
        raise ValueError(f"{path.name} has no column(s) {missing}; it needs {list(REQUIRED)}, "
                         "one row per run (optional: frame, threads, nbands, nkpts and the "
                         f"layout keys {[k.lower() for k in _layout_keys(code)]})")
    if frame_prefix is not None:
        if "frame" not in cols:
            raise ValueError("frame_prefix needs a frame column")
        rows = [r for r in rows if str(r["frame"]).startswith(frame_prefix)]
        if not rows:
            raise ValueError(f"no row's frame starts with {frame_prefix!r}")
    elif "frame" in cols:
        tops = sorted({r["frame"].split("/")[0] for r in rows if "/" in (r["frame"] or "")})
        if len(tops) > 1:
            raise ValueError(f"the frames come from {len(tops)} folders ({', '.join(tops[:8])}"
                             f"{', ...' if len(tops) > 8 else ''}): a record has one system, so "
                             "pass frame_prefix (e.g. 'X/') and base.system for each")

    warnings, skipped, unconverged = [], 0, 0
    has_conv = "converged" in cols
    lay = [k for k in _layout_keys(code) if k.lower() in cols]
    prob = [c for c in PROBLEM if c in cols]
    keys = ["nodes", "ranks", "threads", *[k.lower() for k in lay], *prob]
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        v = {c: _num(r.get(c)) for c in (*REQUIRED, *keys)}
        v["threads"] = v["threads"] or 1
        if not v["nscf"] or v["nscf"] <= 0 or not v["loop"] or v["loop"] <= 0:
            skipped += 1
            continue
        if (nelm is not None and v["nscf"] >= nelm) or \
                (has_conv and str(r.get("converged", "")).strip().lower() in ("f", "false", "0", "no", "n")):
            unconverged += 1
            continue
        groups.setdefault(tuple(v[k] for k in keys), []).append(v)
    if skipped:
        warnings.append(f"{skipped} row(s) without SCF steps or loop time were left out")
    if unconverged:
        why = " or ".join(w for w, on in ((f"nscf >= nelm {nelm}", nelm is not None),
                                          ("converged false", has_conv)) if on)
        warnings.append(f"{unconverged} unconverged run(s) ({why}) were left out: a run stopped at "
                        "the SCF step limit is not a production timing")
    elif nelm is None and not has_conv:
        warnings.append("no nelm and no converged column: runs that stopped at the SCF step limit "
                        "can't be told apart and are included; pass nelm (VASP NELM)")
    if not groups:
        raise ValueError(f"no usable rows in {path.name}")

    date = date or datetime.date.today().isoformat()
    label = _id_part(rec0["system"]["label"])
    out = []
    for key, g in groups.items():
        k = dict(zip(keys, key))
        N, R, T = k["nodes"], k["ranks"], k["threads"]
        if not N or not R or R % N:
            raise ValueError(f"{R} ranks do not divide over {N} node(s) in group {k}")
        per = [x["loop"] / x["nscf"] for x in g]
        steps = [x["nscf"] for x in g]
        s = _stats(per)
        median_steps = max(1, round(statistics.median(steps)))
        layout = {K: k[K.lower()] for K in lay if k[K.lower()] is not None}
        lay_id = ("-" + "".join(f"{LAYOUT_ID.get(K, K.lower())}{v}" for K, v in layout.items())
                  if layout else "")
        rid = (f"{facility}-{_id_part(code)}-{date}-{label}-singlepoint" + (f"-{tag}" if tag else "")
               + f"-summary-{N}n-{R // N}x{T}{lay_id}")
        wall = _stats([x["loop"] for x in g])
        text = (f"summary of {len(g)} production runs from {path.name}: s per SCF step = each run's "
                f"SCF loop time / its SCF steps"
                + "".join(f"; {c} {k[c]}" for c in prob if k[c] is not None)
                + (f"; runs with nscf >= NELM {nelm} left out" if nelm is not None else "")
                + (f". {notes}" if notes else "") + " (records_summarise_timings)")
        rec = {"id": rid, **{f: rec0[f] for f in FROM_LIKE if f in rec0},
               "nodes": N, "ranks_per_node": R // N, "threads_per_rank": T,
               **({"layout": layout} if layout else {}),
               "metric": {"name": "wall_s", "value": round(s["mean"] * median_steps, 3)},
               "extra": {"scf_steps": median_steps},
               "samples": {"n": len(g), "quantity": "s_per_scf_step", **s,
                           "scf_steps": {"min": min(steps), "median": float(statistics.median(steps)),
                                         "max": max(steps)},
                           "wall_s": {f: wall[f] for f in ("mean", "min", "median", "max")},
                           "source": path.name},
               "job_id": None, "date": date, "valid": True, "notes": text}
        if len(g) < min_samples:
            warnings.append(f"{rid}: only {len(g)} run(s) in this layout group (fewer than "
                            f"{min_samples}): its statistics are rough")
        out.append((k, rec))

    ids = [rec["id"] for _, rec in out]
    for k, rec in out:   # same layout, another problem size: name it
        if ids.count(rec["id"]) > 1:
            rec["id"] += "".join(f"-{PROBLEM[c]}{k[c]}" for c in prob)
    recs = [rec for _, rec in out]
    errs = [e for rec in recs for e in records.errors(facility, "benchmarks", rec)]
    if errs:
        raise records.RecordsError("nothing appended:\n  " + "\n  ".join(errs)
                                   + ("\n(an id that exists: pass a tag, or supersede the old record)"
                                      if any("already exists" in e for e in errs) else ""))
    result = {"appended": False, "warnings": warnings, "records": recs,
              "groups": [{"id": r["id"], "n": r["samples"]["n"], "layout": r.get("layout"),
                          "ranks_per_node": r["ranks_per_node"],
                          "s_per_scf_step": r["samples"]["mean"], "std": r["samples"]["std"],
                          "scf_steps": [r["samples"]["scf_steps"][f] for f in ("min", "median", "max")]}
                         for r in recs]}
    if dry_run:
        return result
    for rec in recs:
        records.append(facility, "benchmarks", rec)
    result["appended"] = True
    result["check_errors"] = records.check(facility)["errors"]
    return result
