"""Label a batch of frames with CP2K (G51): one folder per frame, then collect.

write: a selected-frames extxyz -> one folder per frame (fNNNN/subsys.inc and
fNNNN/label.inp) plus batch.json, the manifest that rookery's cp2k_pack_jobs
(packed jobs) and collect() below read. Every folder's input is the same text,
written once by the same code as cp2k_make_input(purpose="label"), so all
frames have the same settings by construction; only subsys.inc differs.

collect: after the runs, each frame's label.out -> finished / unconverged /
unfinished / missing / wrong run type / rejected, the `frames` list
cp2k_make_labels takes for the good ones, and the frames to run again.

Text in, dicts out; the tools in hyogo/mcp_tools/cp2k.py do the file I/O.
batch.json is the contract with rookery (hyogo and hpc_codes import nothing
from each other): keep KIND/VERSION and the keys rookery reads stable.
"""
import hashlib
import re

from hyogo.cp2k import input as cp2k_input, labels as cp2k_labels, output as cp2k_output, subsys
from hyogo.mace import dataset

KIND = "cp2k-label-batch"
VERSION = 1
MANIFEST = "batch.json"
INPUT = "label.inp"
OUTPUT = "label.out"
SUBSYS = "subsys.inc"
PROJECT = "label"
GUESS = "guess.wfn"          # guess="previous": the job copies the previous frame's wavefunction here
FRAME = "f{:04d}"
FRAME_RE = re.compile(r"f\d{4,}")
PACK_LOG_RE = re.compile(r"pack-\d+\.log")   # written by rookery's cp2k_pack_jobs scripts
DEFAULT_FRAME_DURATION = "01:00:00"   # CP2K WALLTIME per frame (minus 600 s): a stuck SCF stops there
STATUSES = ("finished", "unconverged", "wrong_run_type", "rejected", "unfinished", "missing")


def sha256(text: str) -> str:
    """A frame's sha256: of its own extxyz lines, as hyogo/mace/dataset.read_frames
    splits them (the same convention as mace_make_finetune's manifest)."""
    return hashlib.sha256(text.encode()).hexdigest()


def _selection(manifest, frames: list[dict]) -> list[dict]:
    """Check a frame-selection manifest against the extxyz: one entry per frame,
    in order, each with the frame's sha256. Returns the entries (small scalars only)."""
    rows = manifest.get("frames") if isinstance(manifest, dict) else manifest
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError('selection is a frame-selection manifest: {"frames": [{"sha256", ...}, ...]} '
                         "(or that list), one entry per frame of the extxyz, in its order")
    if len(rows) != len(frames):
        raise ValueError(f"the selection lists {len(rows)} frames but the extxyz has {len(frames)}: "
                         "pass the manifest written with this extxyz")
    out = []
    for i, (row, f) in enumerate(zip(rows, frames)):
        want = row.get("sha256")
        if not want:
            raise ValueError(f"selection frame {i} has no sha256")
        if want != sha256(f["text"]):
            raise ValueError(f"frame {i} of the extxyz does not match the selection's sha256: "
                             "the file changed after selection, or this is another selection")
        out.append({k: v for k, v in row.items()
                    if k != "sha256" and (v is None or isinstance(v, (str, int, float, bool)))})
    return out


def write(text: str, source: str, kinds: dict, files: dict, functional: str = "r2scan",
          d3: str = "none", cutoff: int = 1000, rel_cutoff: int = 60,
          precision: dict | None = None, scf: str = "ot", frame_duration: str | None = None,
          guess: str = "atomic", config_type: str | None = None, selection=None,
          charge: int = 0, hfx: dict | None = None) -> tuple[dict, dict]:
    """The batch's files ({relative path: text}) and its manifest.

    `text` is the selected-frames extxyz, `kinds`/`files` cp2k_kinds' result
    split as cp2k_make_subsys and cp2k_make_input take them."""
    if guess not in ("atomic", "previous"):
        raise ValueError('guess is "atomic" (every frame from the atomic guess, the default) or '
                         '"previous" (the previous frame\'s wavefunction within a job)')
    frames = dataset.read_frames(text, source)
    if not frames:
        raise ValueError(f"{source}: no frames")
    for f in frames:
        props = f["info"].get("Properties", "species:S:1:pos:R:3").split(":")
        if props[:2] != ["species", "S"] or props[3:5] != ["pos", "R"] or "Lattice" not in f["info"]:
            raise ValueError(f"{source} frame {f['index']}: needs Lattice= and Properties starting "
                             "species:S:1:pos:R:3 (write it with yagura export_frames or "
                             "structure_to_extxyz)")
    sel = _selection(selection, frames) if selection is not None else [{} for _ in frames]
    duration = frame_duration or DEFAULT_FRAME_DURATION
    out, rows, warnings, inp, info0 = {}, [], [], None, None
    elements = None
    for f, s in zip(frames, sel):
        name = FRAME.format(f["index"])
        stext, sinfo = subsys.subsys(f["text"], f"{source} frame {f['index']}", kinds,
                                     "XYZ", charge, None)
        sinfo["out"] = f"{name}/{SUBSYS}"
        kw = dict(purpose="label", d3=d3, cutoff=cutoff, rel_cutoff=rel_cutoff, project=PROJECT,
                  duration=duration, precision=precision, scf=scf, hfx=hfx)
        if guess == "previous":
            kw.update(guess_wfn=GUESS, save_wfn=True)
        itext, iinfo = cp2k_input.make_input("sp", functional, sinfo, files, **kw)
        if inp is None:
            inp, info0, elements = itext, iinfo, sinfo["kinds"]
            warnings += iinfo["warnings"] + [f"{name}: {w}" for w in sinfo["warnings"]]
        elif itext != inp:
            diff = sorted(set(itext.splitlines()) ^ set(inp.splitlines()))[:6]
            raise ValueError(f"{name}'s input differs from {FRAME.format(frames[0]['index'])}'s "
                             f"({diff}): labels need one setting for every frame. For a hybrid, "
                             "fix the HFX radius below every frame's half-width (hfx.radius)")
        elif sinfo["kinds"] != elements:
            raise ValueError(f"{name} has elements {sorted(sinfo['kinds'])}, the first frame "
                             f"{sorted(elements)}")
        else:
            warnings += [f"{name}: {w}" for w in sinfo["warnings"]]
        out[f"{name}/{SUBSYS}"] = stext
        out[f"{name}/{INPUT}"] = itext
        ct = s.get("config_type") or f["info"].get("config_type") or config_type
        rows.append({"name": name, "index": f["index"], "sha256": sha256(f["text"]),
                     "natoms": sinfo["natoms"], "formula": sinfo["formula"],
                     "electrons": sinfo["electrons"], "config_type": ct,
                     "subsys_sha256": sha256(stext), "selection": s})
    settings = {"run_type": "sp", "purpose": "label", "functional": functional,
                "d3": info0["d3"], "scf": info0["scf"], "cutoff": cutoff, "rel_cutoff": rel_cutoff,
                "eps_default": (precision or {}).get("eps_default",
                                                     cp2k_input.PRECISION_DEFAULTS["eps_default"]),
                "frame_duration": duration, "guess": guess, "project": PROJECT,
                "data_files": files, "kinds": elements}
    manifest = {"kind": KIND, "version": VERSION, "source": source,
                "source_sha256": sha256(text), "settings": settings,
                "input": INPUT, "output": OUTPUT, "subsys": SUBSYS,
                "input_sha256": sha256(inp), "guess_file": GUESS if guess == "previous" else None,
                "wfn": f"{PROJECT}-RESTART.wfn", "frames": rows, "warnings": warnings}
    return out, manifest


def check_manifest(m) -> dict:
    if not isinstance(m, dict) or m.get("kind") != KIND:
        raise ValueError(f"not a {KIND} manifest ({MANIFEST} written by cp2k_make_label_batch)")
    if m.get("version") != VERSION:
        raise ValueError(f"{MANIFEST} version {m.get('version')}; this hyogo reads {VERSION}")
    return m


def classify(out_text: str | None, subsys_text: str, row: dict, source: str) -> dict:
    """One frame's state from its label.out text (None when it has none)."""
    st = {"name": row["name"], "status": None, "why": None, "wall_s": None, "scf_steps": None}
    if out_text is None:
        return {**st, "status": "missing", "why": f"no {OUTPUT} (not run yet, or not downloaded)"}
    try:
        r = cp2k_output.parse(out_text)
    except ValueError as e:
        return {**st, "status": "unfinished", "why": f"{OUTPUT} is not a CP2K output yet: {e}"}
    st["wall_s"] = r["cost"]["cp2k_s"]
    st["scf_steps"] = sum(r["scf"]["steps"]) or None
    if r["run_type"] is not None and r["run_type"] != "ENERGY_FORCE":
        return {**st, "status": "wrong_run_type",
                "why": f"run type {r['run_type']}, not ENERGY_FORCE: not this batch's {INPUT}"}
    if r["project"] not in (None, PROJECT):
        return {**st, "status": "rejected",
                "why": f"project {r['project']!r}, not {PROJECT!r}: not this batch's {INPUT}"}
    if not r["finished"]:
        return {**st, "status": "unfinished",
                "why": "no PROGRAM ENDED: killed (job walltime or node failure) or still running"}
    if r["scf"]["evaluations"] < 1 or r["scf"]["not_converged"]:
        return {**st, "status": "unconverged",
                "why": "SCF not converged (or stopped by the per-frame WALLTIME)"}
    try:
        cp2k_labels.frame(out_text, subsys_text, source, "", True, False)
    except ValueError as e:
        return {**st, "status": "rejected", "why": str(e)}
    return {**st, "status": "finished"}


def collect(manifest: dict, outs: dict, subsys_texts: dict, folder: str) -> dict:
    """Per-frame states, cp2k_make_labels' `frames`, and what to run again.

    outs: {frame name: label.out text or None}; subsys_texts: {name: text}."""
    m = check_manifest(manifest)
    states, good, warnings = [], [], []
    for row in m["frames"]:
        n = row["name"]
        stext = subsys_texts.get(n)
        if stext is None:
            raise ValueError(f"{folder}/{n}/{SUBSYS} is missing: the batch folder is incomplete")
        if sha256(stext) != row["subsys_sha256"]:
            raise ValueError(f"{folder}/{n}/{SUBSYS} changed since cp2k_make_label_batch wrote it: "
                             "its labels would not be the frame that was selected")
        st = classify(outs.get(n), stext, row, f"{folder}/{n}/{OUTPUT}")
        states.append(st)
        if st["status"] == "finished":
            f = {"out": f"{folder}/{n}/{OUTPUT}", "subsys": f"{folder}/{n}/{SUBSYS}"}
            if row.get("config_type"):
                f["config_type"] = row["config_type"]
            good.append(f)
    counts = {s: sum(st["status"] == s for st in states) for s in STATUSES}
    rerun = [st["name"] for st in states if st["status"] in ("unfinished", "missing")]
    fix = [st["name"] for st in states if st["status"] in ("unconverged", "wrong_run_type", "rejected")]
    walls = [st["wall_s"] for st in states if st["status"] == "finished" and st["wall_s"]]
    if fix:
        warnings.append(f"{len(fix)} frame(s) ran but gave no label ({', '.join(fix[:8])}"
                        f"{' ...' if len(fix) > 8 else ''}): rerunning the same input won't help; "
                        "drop them, or write new inputs for them (other SCF settings)")
    if rerun:
        warnings.append(f"{len(rerun)} frame(s) unfinished or missing: download first if they ran "
                        "(see `download`), else resubmit their packs (finished frames are skipped) or "
                        "cp2k_pack_jobs(frames=resubmit.rerun)")
    return {"batch": folder, "counts": counts, "frames": good, "states": states,
            "resubmit": {"rerun": rerun, "fix": fix},
            "wall_s": ({"n": len(walls), "mean": sum(walls) / len(walls), "max": max(walls)}
                       if walls else None),
            "warnings": warnings}
