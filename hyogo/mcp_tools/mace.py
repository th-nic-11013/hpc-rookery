"""hyogo MCP tools: mace.

hyogo/server.py loads every module in this folder. A new mace tool goes here;
a new group of tools is a new module beside this one.
"""
from pathlib import Path
from hyogo.mace import dataset as mace_dataset, finetune as mace_finetune, log as mace_log
from hyogo.mcp_tools import _read_local, mcp


#: this module's rows of hyogo/INVENTORY.md (scripts/render_inventory.py renders the
#: table; a live row must be a tool served here). Plain literals only: the renderer
#: reads it without importing this module.
TOOL_META = {
    "mlip_check_dataset": {
        "method": "mlip",
        "stage": "check",
        "code": "none",
        "module": "`hyogo/mace/dataset.py`",
        "status": "live",
        "source": "the user's fine-tuning tools: `mcp_tools/data_tools.py` (profile, validate), `combine_relabelled.py` checks; G49",
        "notes": "labelled extxyz → profile (frames, species, keys, config_types) and fitness: label keys in every frame, D3 and D3-free labels not mixed (`dispersion_eV` from `cp2k_make_labels`), stress needs a cell, repeated `source_sha256`. Own small extxyz reader (no ASE); frames stay their own text",
    },
    "mace_make_finetune": {
        "method": "mlip",
        "stage": "input",
        "code": "mace",
        "module": "`hyogo/mace/finetune.py`",
        "status": "live",
        "source": "the user's finished campaigns: `split_train_valid.py`, `select_replay.py`, `02_finetune` / `03_restart` / `04_train_from_scratch` job arguments, the LoRA yml; G49",
        "notes": "a run folder: split (seeded; per config_type; held-out types; byte-identical frames), `train.sh` (`mace_run_train`, presets multihead replay / naive / LoRA / scratch, restart), `select_replay.sh` (`fine_tuning_select`, elements from the data), `e0s.json`, `manifest.json`. E0s must be stated. Arguments checked against mace-torch 0.3.14's `--help` (no LoRA there). Launch lines (GPUs, srun, env) stay the facility's (G38)",
    },
    "mace_parse_log": {
        "method": "mlip",
        "stage": "parse",
        "code": "mace",
        "module": "`hyogo/mace/log.py`",
        "status": "live",
        "source": "the user's `parse_mace.py`; G49",
        "notes": "training log → finished, early stop or max epochs, restarts, per-head validation errors (initial, last, at the saved checkpoint), best epoch, final error table, counted ERROR lines; warns when the initial energy error points at wrong E0s. Mean training loss from `*_train.txt` not carried",
    },
    "mlip_select_frames": {
        "method": "mlip",
        "stage": "select",
        "code": "none",
        "module": "`hyogo/mace/select.py`",
        "status": "live",
        "source": "notes/uio66-labelling-readiness.md §2 (UiO-66 fine-tune sampling plan); G50",
        "notes": "pool extxyz (streamed) → held-out time blocks, skip earlier picks (manifest sha256) or labelled frames (geometry), strata (keys × equal-width volume bins, quota each), FPS on descriptors or volume (or a per-frame score, round 2) with ≥ min_spacing_ps per run, uniform control set; seeded. Without quotas: the strata report. Writes selected.extxyz + manifest.json. Descriptors from the mace pack's `scripts/mace_descriptors.py` (cluster)",
    },
}


def _read_frames(paths: list[str]) -> list[dict]:
    """Every frame of the given local extxyz files, in order."""
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        raise ValueError("no input files: pass the labelled extxyz file(s), e.g. cp2k_make_labels' out")
    frames = []
    for p in paths:
        src, raw = _read_local(p, "labelled extxyz")
        frames += mace_dataset.read_frames(raw.decode(errors="replace"), str(src))
    if not frames:
        raise ValueError(f"no frames in {paths}")
    return frames


@mcp.tool()
def mlip_check_dataset(paths: list[str], energy_key: str | None = None,
                       forces_key: str | None = None, stress_key: str | None = None,
                       allowed_species: list[str] | None = None) -> dict:
    """Check labelled extended-XYZ files on this machine before training an
    MLIP on them: what is in them and whether they are fit to train on.
    Writes nothing. Use it instead of reading the files by hand.

    Before it: cp2k_make_labels (or any labelled extxyz); fs_download a
    file that is on a cluster.

    Returns {"ok", "problems" (each makes the set unfit: a label key missing
    in some frames, D3 and D3-free labels mixed, stress without a cell,
    elements the foundation model lacks, fewer than 2 frames), "warnings"
    (no stress in every frame, plain `energy`/`forces` key names, the same
    label run twice), "keys": {energy_key, forces_key, stress_key} (what to
    give MACE; stress_key is None unless every frame has it), "d3"
    ("included" | "none" | "mixed"), "profile": {frames, natoms, species,
    atomic_numbers, species_counts, info_keys, arrays, config_types, heads,
    periodic}}. Read problems and warnings to the user.

    Args:
        paths: one or more local extxyz files (.gz ok); ~ is expanded.
        energy_key, forces_key, stress_key: the label names, when they are
            not REF_energy / REF_forces / REF_stress (or another known set).
        allowed_species: the foundation model's elements, to check coverage.
    """
    frames = _read_frames(paths)
    return {"paths": sorted({f["source"] for f in frames}),
            **mace_dataset.check(frames, energy_key, forces_key, stress_key, allowed_species)}


@mcp.tool()
def mace_make_finetune(paths: list[str], out_dir: str, name: str, mace_version: str,
                       e0s: dict | str, method: str = "multihead",
                       foundation: dict | None = None, replay: dict | None = None,
                       valid_fraction: float = 0.1, test_fraction: float = 0.0,
                       holdout: list[str] | None = None, stratify: bool = True,
                       seed: int = 42, distributed: bool = False, restart: bool = False,
                       lr: float | None = None, stress_weight: float | None = None,
                       energy_key: str | None = None, forces_key: str | None = None,
                       stress_key: str | None = None, overrides: dict | None = None,
                       overwrite: bool = False) -> dict:
    """Write a MACE training run as a folder on this machine: the data split
    (data/train.extxyz, valid.extxyz, test.extxyz), train.sh (the
    `python -m mace.cli.run_train` command, relative paths), for multihead
    replay select_replay.sh (`mace.cli.fine_tuning_select`), e0s.json and
    manifest.json (which labelled frame went to which part, with sha256).
    Nothing is run. The argument sets are those of finished campaigns
    (multihead replay, restart, from scratch, LoRA); a value that is not
    from them is named in a warning.

    Before it: mlip_check_dataset. After it: fs_upload the folder, then
    submit_job twice on a GPU partition, from that folder inside the MACE
    environment: `bash select_replay.sh` (multihead only), then
    `bash train.sh`; then fs_download the log and mace_parse_log.
    restart=true writes the same run with --restart_latest: upload only
    train.sh over the old one.

    Returns {"out_dir", "files", "method", "mace_version", "arguments" (as
    written), "keys", "atomic_numbers", "species", "e0s", "d3", "split":
    {train, valid, test (counts), by_config_type}, "replay", "restart",
    "warnings"}. Read the warnings to the user.

    Refuses: a data set mlip_check_dataset calls unfit; no e0s; a method
    without the model or replay data it needs; an argument the given
    mace-torch version does not have (LoRA on 0.3.14); a config_type too
    small to split; an existing out_dir without overwrite.

    Args:
        paths: labelled extxyz file(s) on this machine.
        out_dir: the run folder to write here; ~ is expanded.
        name: names the model, log and checkpoints.
        mace_version: mace-torch on the cluster, e.g. "0.3.14"
            (`python -c "import mace; print(mace.__version__)"`).
        e0s: {atomic number: eV} of isolated atoms at the labels' level of
            theory, or the path of such a json, or "foundation" / "average".
        method: "multihead" (replay, default), "naive", "lora", "scratch".
        foundation: {"model": path on the cluster or model name, "head": the
            head to start from when the model has several, "dtype": the
            model's precision ("float32" | "float64"; the run must use the
            same)}. The facility's model table (the mace pack) has all three.
        replay: {"pool": path on the cluster of the foundation model's
            replay data, "num_samples": 10000} or {"selected": file already
            written by select_replay.sh}.
        valid_fraction, test_fraction, holdout, stratify, seed: the split;
            holdout names config_types kept wholly for test; stratify
            splits inside each config_type.
        distributed: several GPUs through srun (adds --distributed
            --launcher slurm); batch_size stays per process.
        lr, stress_weight, overrides: change the preset ({flag: value};
            False drops a flag).
    """
    frames = _read_frames(paths)
    if isinstance(e0s, str) and e0s not in ("foundation", "average"):
        import json as _json
        src, raw = _read_local(e0s, "E0s json")
        try:
            e0s = _json.loads(raw)
        except ValueError as e:
            raise ValueError(f"{src} is not json ({e}); expected {{atomic number: eV}}") from None
        if e0s and not all(str(k).isdigit() for k in e0s):
            sym = mace_dataset.Z_OF
            try:
                e0s = {sym[k]: v for k, v in e0s.items()}
            except KeyError as e:
                raise ValueError(f"{src}: key {e} is neither an atomic number nor an element symbol") from None
    keys = {k: v for k, v in (("energy_key", energy_key), ("forces_key", forces_key),
                              ("stress_key", stress_key)) if v}
    parts = mace_finetune.split(frames, valid_fraction, test_fraction, seed, stratify, holdout)
    files, facts = mace_finetune.make(frames, name, method, mace_version, e0s, foundation, replay,
                                      keys, parts, seed, distributed, restart, lr, stress_weight,
                                      overrides)
    import hashlib
    import json
    for part in ("train", "valid", "test"):
        if parts[part]:
            files[f"data/{part}.extxyz"] = "".join(frames[i]["text"] for i in parts[part])
    man = mace_finetune.manifest(frames, parts, lambda t: hashlib.sha256(t.encode()).hexdigest())
    files["manifest.json"] = json.dumps({"name": name, "method": method, "seed": seed,
                                         "inputs": sorted({f["source"] for f in frames}),
                                         "arguments": facts["arguments"], "split": man}, indent=1) + "\n"
    out = Path(out_dir).expanduser()
    if out.exists() and any(out.iterdir()) and not overwrite:
        raise ValueError(f"{out} exists and is not empty; pass overwrite=true or another out_dir")
    for rel, text in files.items():
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    by_type = {}
    for part, idx in parts.items():
        for i in idx:
            by_type.setdefault(frames[i]["info"].get("config_type", ""), {"train": 0, "valid": 0, "test": 0})[part] += 1
    return {"out_dir": str(out), "files": sorted(files), **facts,
            "split": {**{k: len(v) for k, v in parts.items()}, "by_config_type": by_type}}


@mcp.tool()
def mace_parse_log(log_path: str, epochs: bool = False) -> dict:
    """Read a MACE training log (`<name>_run-<seed>.log`) on this machine:
    did training finish, how it stopped, the validation errors of every head
    and the checkpoint the final model came from. Writes nothing. Use it
    instead of grepping the log.

    Before it: get_job_status, then fs_download the log (logs/ or the
    run folder).

    Returns {"path", "version", "sessions" (1 + restarts), "heads",
    "foundation_model", "foundation_head", "configurations": {train, valid},
    "finished" (Training complete), "evaluated" (final error table written),
    "stop": {"reason": "early_stop" | "max_epochs", "patience"} or None while
    running or killed, "last_epoch", "best_epoch" (the checkpoint the model
    was saved from), "checkpoint", "model", "epoch_wall_s", "summary": per
    head {"initial" (before training), "last", "at_best_epoch", "min_rmse_f"}
    each {epoch, loss, rmse_e_meV_atom, rmse_f_meV_A, rmse_stress_meV_A3},
    "error_table": {"on", "rows"}, "errors" (MACE ERROR lines, counted),
    "warnings"}, plus "epochs" (every row) with epochs=true. Read the
    warnings to the user: an unfinished run, ERROR lines, an initial energy
    error that points at wrong E0s. With two heads, the new one (Default)
    shows the fit to the labels and pt_head what was kept of the foundation
    model; judging the trade is yours to explain.

    Args:
        log_path: the log on this machine (.gz ok); ~ is expanded.
        epochs: also return every (epoch, head) row.
    """
    src, raw = _read_local(log_path, "MACE log")
    r = mace_log.parse(raw.decode(errors="replace"))
    if not epochs:
        r.pop("epochs")
    return {"path": str(src), **r}


@mcp.tool()
def mlip_select_frames(pool: list[str], quotas: dict | None = None, out_dir: str | None = None,
                       strata: list[str] | None = None, volume_bins: int = 5,
                       descriptors: str | None = None, scores: str | None = None,
                       holdout: list[dict] | None = None, from_holdout: bool = False,
                       exclude: list[str] | None = None, min_spacing_ps: float = 1.0,
                       control_fraction: float = 0.05, seed: int = 0,
                       keys: dict | None = None, overwrite: bool = False) -> dict:
    """Pick the frames to label from a pool of MD frames (extxyz on this
    machine): hold out whole time blocks, skip frames already picked or
    labelled, stratify by stratum keys x volume bins with a quota per
    stratum, pick inside each volume bin by farthest-point sampling (on
    descriptors, else on the volume) never closer than min_spacing_ps to
    another pick from the same run, and add a uniformly spaced control set.
    Seeded: the same inputs give the same picks.

    Call it first without quotas: it writes nothing and returns the strata
    report (frames, eligible, volume range, per-bin counts and how many
    frames fit at the spacing). Then call with quotas and out_dir.

    Before it: structure_to_extxyz on the LAMMPS dumps (yagura) and a
    structure check; for descriptors, the mace pack's
    scripts/mace_descriptors.py on the cluster, then fs_download its .npy
    and .json. After it: label the selected.extxyz frames (cp2k_make_subsys
    + cp2k_make_input purpose="label" per frame); for round 2, pass this
    manifest.json and the labelled extxyz in exclude.

    Writes (with quotas) out_dir/selected.extxyz (the pool frames byte for
    byte, plus pool_sha256, pool_file, pool_index, stratum, picked_by on
    the comment line) and out_dir/manifest.json (inputs with sha256,
    arguments, per frame: pool file, index, source, time_ps, volume,
    stratum, sha256, why it was picked).

    Returns {"pool": files, "frames", "feature" ("descriptors" | "volume" |
    "score"), "strata" (the report), "holdout", "excluded", and with quotas
    "out_dir", "files", "picked", "by_stratum", "warnings"}. Read the
    warnings and the strata report to the user.

    Refuses: descriptor or score rows that do not match the pool (count, or
    the descriptor json naming other pool files); a stratum key, the run key
    or (with spacing or time windows) the time key missing in some frame;
    quotas larger than what a stratum holds at the spacing (with the report
    in the message); quotas naming strata the pool lacks; a holdout rule that
    matches nothing; an existing out_dir without overwrite.

    Args:
        pool: extxyz files of pool frames (.gz ok); ~ is expanded.
        quotas: {stratum: frames}; a stratum is the strata keys' values
            joined by "|" (e.g. "P1-300K"); omit for the report only.
        out_dir: where to write selected.extxyz and manifest.json.
        strata: info keys to stratify by (default ["config_type"]; "source"
            is the run); [] for one stratum.
        volume_bins: equal-width volume bins per stratum (the quota is
            spread evenly over them, so the tails get frames).
        descriptors: .npy, one row per pool frame in pool order (its .json
            beside it is checked when present).
        scores: .npy, one number per pool frame; when given, the highest
            scores are picked instead of FPS (round 2: committee
            disagreement). NaN frames are never picked.
        holdout: rules, each {"source": glob, "match": {key: value},
            "last_ps" | "first_ps" | "from_ps"/"to_ps": window per run};
            no window holds out every frame matched.
        from_holdout: pick from the held-out frames instead (a test set).
        exclude: earlier manifest.json files, selected extxyz, or labelled
            extxyz (matched by geometry: atom order must be the pool's).
        min_spacing_ps: least time between two picks from one run (also
            from excluded frames found in the pool); 0 turns it off.
        control_fraction: control frames, as a fraction of the quota sum,
            added on top.
        seed: the random choices (first FPS pick, control offset).
        keys: info key names when not the defaults {"source": "source",
            "frame": "frame", "time": "time_ps", "volume": "volume"};
            {"source": None} names each run after its file.
    """
    import hashlib
    import json
    import numpy as np
    from hyogo.mace import select as sel

    if isinstance(pool, str):
        pool = [pool]
    if not pool:
        raise ValueError("no pool files: pass the extxyz of the pool frames")
    paths = []
    for p in pool:
        src = Path(p).expanduser()
        if not src.is_file():
            raise ValueError(f"{src} (pool extxyz) is not a file on this machine; fs_download it first")
        paths.append(src)
    if volume_bins < 1 or min_spacing_ps < 0 or not 0 <= control_fraction < 1:
        raise ValueError("volume_bins >= 1, min_spacing_ps >= 0 and 0 <= control_fraction < 1")
    k = sel._keys(keys)
    strata = ["config_type"] if strata is None else list(strata)
    data = sel.read_pool(paths)
    n = len(data["frames"])
    fx = sel.facts(data, k, need_time=min_spacing_ps > 0)
    labels = sel.strata_labels(data, fx, strata, k)
    held, held_rep = sel.holdout_mask(data, fx, holdout)
    shas, geoms = sel.read_exclude([Path(p).expanduser() for p in exclude or []])
    excl, excl_rep = sel.excluded_mask(data, fx, shas, geoms)
    eligible = (held if from_holdout else ~held) & ~excl
    warnings = []
    if excl_rep["not_in_pool"]:
        warnings.append(f"{excl_rep['not_in_pool']} excluded frame(s) matched no pool frame (first: "
                        f"{excl_rep['not_in_pool_first']}): from another pool, or atom order differs")

    desc, blocks, desc_rec = None, None, None
    if descriptors:
        dp = Path(descriptors).expanduser()
        if not dp.is_file():
            raise ValueError(f"{dp} (descriptors .npy) is not a file on this machine")
        desc = np.load(dp, mmap_mode="r")
        if desc.ndim != 2 or desc.shape[0] != n:
            raise ValueError(f"descriptors {dp.name} have shape {desc.shape}, the pool has {n} frames: "
                             "one row per pool frame, in pool order")
        meta_p = dp.with_suffix(".json")
        if meta_p.is_file():
            meta = json.loads(meta_p.read_text())
            want = [(f["sha256"], f["frames"]) for f in meta.get("pool", [])]
            have = [(f["sha256"], f["frames"]) for f in data["files"]]
            if want != have:
                raise ValueError(f"{meta_p.name} says the descriptors were computed on "
                                 f"{[f.get('name') for f in meta.get('pool', [])]}, not on these pool files "
                                 "(sha256 or frame counts differ); pass the same files in the same order")
            blocks = [tuple(v) for v in (meta.get("blocks") or {}).values()] or None
        else:
            warnings.append(f"no {meta_p.name} beside the descriptors: rows matched to the pool by count only")
        desc = np.asarray(desc, dtype=float)
        if not np.isfinite(desc).all():
            raise ValueError(f"descriptors {dp.name} contain NaN or inf")
        desc_rec = {"path": str(dp), "sha256": sel.file_sha256(dp), "shape": list(desc.shape),
                    "blocks": blocks}
    sc, sc_rec = None, None
    if scores:
        sp_ = Path(scores).expanduser()
        if not sp_.is_file():
            raise ValueError(f"{sp_} (scores .npy) is not a file on this machine")
        sc = np.asarray(np.load(sp_), dtype=float)
        if sc.shape != (n,):
            raise ValueError(f"scores {sp_.name} have shape {sc.shape}, the pool has {n} frames")
        sc_rec = {"path": str(sp_), "sha256": sel.file_sha256(sp_)}
        if np.isnan(sc[eligible]).any():
            warnings.append(f"{int(np.isnan(sc[eligible]).sum())} eligible frames have no score (NaN): never picked")
    feature = "score" if sc is not None else "descriptors" if desc is not None else "volume"

    report = sel.plan(fx, labels, eligible, volume_bins, min_spacing_ps)
    head = {"pool": [{kk: f[kk] for kk in ("path", "sha256", "frames")} for f in data["files"]],
            "frames": n, "feature": feature, "strata": report, "holdout": held_rep,
            "excluded": excl_rep}
    if quotas is None:
        return {**head, "warnings": warnings,
                "next": "choose quotas per stratum from this report, then call again with quotas and out_dir"}
    if not out_dir:
        raise ValueError("out_dir is needed with quotas")
    try:
        quotas = {str(g): int(v) for g, v in quotas.items()}
    except (TypeError, ValueError):
        raise ValueError("quotas must be {stratum: number of frames}") from None
    out = Path(out_dir).expanduser()
    if out.exists() and any(out.iterdir()) and not overwrite:
        raise ValueError(f"{out} exists and is not empty; pass overwrite=true or another out_dir")

    feats = sel.features(desc, fx["volume"], blocks)
    picks, w2, counts = sel.select(fx, labels, eligible, excl, quotas, volume_bins, min_spacing_ps,
                                   control_fraction, seed, feats, sc)
    warnings += w2
    out.mkdir(parents=True, exist_ok=True)
    rows, wmap = [], {}
    for order, (i, g, why) in enumerate(picks):
        f = data["frames"][i]
        name = data["files"][f["file"]]["name"]
        extra = (f"pool_sha256={f['sha256']} pool_file={json.dumps(name)} pool_index={f['index']} "
                 f"stratum={json.dumps(g)} picked_by={why['by']}")
        wmap[(f["file"], f["index"])] = (order, extra)
        t = fx["time"][i]
        fr = f["info"].get(k["frame"]) if k["frame"] else None
        rows.append({"pool_file": name, "pool_index": f["index"],
                     "frame": int(fr) if fr is not None and fr.lstrip("-").isdigit() else fr,
                     "source": str(fx["source"][i]), "time_ps": None if np.isnan(t) else float(t),
                     "volume_A3": round(float(fx["volume"][i]), 4), "stratum": g,
                     "sha256": f["sha256"], "why": why})
    sel.write_frames(paths, wmap, out / "selected.extxyz")
    # no timestamp: the same inputs give a byte-identical manifest
    man = {"tool": "mlip_select_frames", "pool": head["pool"], "pool_frames": n, "feature": feature,
           "descriptors": desc_rec, "scores": sc_rec,
           "arguments": {"quotas": quotas, "strata": strata, "volume_bins": volume_bins,
                         "holdout": holdout, "from_holdout": from_holdout, "exclude": exclude,
                         "min_spacing_ps": min_spacing_ps, "control_fraction": control_fraction,
                         "seed": seed, "keys": k},
           "holdout": held_rep, "excluded": excl_rep, "by_stratum": counts, "warnings": warnings,
           "frames": rows}
    (out / "manifest.json").write_text(json.dumps(man, indent=1) + "\n")
    sha = hashlib.sha256((out / "selected.extxyz").read_bytes()).hexdigest()
    return {**head, "out_dir": str(out), "files": ["manifest.json", "selected.extxyz"],
            "selected_sha256": sha, "picked": len(rows), "by_stratum": counts, "warnings": warnings}
