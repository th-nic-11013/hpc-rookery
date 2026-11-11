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
