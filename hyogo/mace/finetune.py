"""A MACE fine-tuning run, written as files: the data split, the
`mace_run_train` command, the replay-selection command and a manifest.

Text and dicts in, text out: no file I/O, nothing is run. The presets are
the argument sets of the user's finished campaigns (tests/fixtures/
mace_finetune/README.md): multihead replay (02_finetune.slurm, MACE-MH-1,
mace-torch 0.3.16, valid 0.3 meV/atom and 10 meV/A), its restart
(03_restart.slurm), the from-scratch baseline (04_train_from_scratch.slurm)
and LoRA (mh1_omat_pbe_lora.yml). What is not theirs is said in a warning.

Cluster-specific launch lines (nodes, GPUs, srun, modules) are not written
here: the command is a plain `python -m mace.cli.run_train ...` in train.sh,
and the job around it is the facility's (rookery submit_job).
"""
import json
import random
import shlex
from collections import defaultdict

from hyogo.mace import dataset

METHODS = ("multihead", "lora", "naive", "scratch")

#: `mace_run_train --help` of mace-torch 0.3.14 (HOKUSAI ~/software/mlip/2026.10, 2026-11-11).
#: A flag outside this set is refused for that version instead of failing in the job.
RUN_TRAIN_FLAGS = {"0.3.14": frozenset("""amsgrad apply_cutoff atomic_numbers avg_num_neighbors batch_size
beta charges_key checkpoints_dir clip_grad compute_atomic_dipole compute_avg_num_neighbors compute_forces
compute_polarizability compute_stress config config_type_weights correlation default_dtype device dipole_key
dipole_weight disallow_random_padding_pt distance_transform distributed downloads_dir dry_run E0s edge_irreps
elec_temp_key ema ema_decay embedding_specs enable_cueq enable_oeq energy_key energy_weight error_table
eval_interval filter_type_pt force_mh_ft_lr forces_key forces_weight foundation_filter_elements foundation_head
foundation_model foundation_model_elements foundation_model_readout gate head_key heads hidden_irreps
huber_delta interaction interaction_first keep_checkpoints keep_isolated_atoms launcher lbfgs les_arguments
log_dir log_level loss lr lr_factor lr_scheduler_gamma max_ell max_L max_num_epochs mean MLP_irreps model
model_dir multiheads_finetuning multi_processed_test name num_channels num_cutoff_basis num_interactions
num_radial_basis num_samples_pt num_workers only_cueq optimizer pair_repulsion patience pin_memory plot
plot_frequency plot_interaction_e polarizability_key polarizability_weight pseudolabel_replay pt_train_file
pt_valid_file radial_MLP radial_type real_pt_data_ratio_threshold restart_latest results_dir r_max
save_all_checkpoints save_cpu scaling scheduler scheduler_patience seed skip_evaluate_heads stage_two
stage_two_dipole_weight stage_two_energy_weight stage_two_forces_weight stage_two_lr
stage_two_polarizability_weight stage_two_stress_weight stage_two_virials_weight start_stage_two start_swa
statistics_file std stress_key stress_weight subselect_pt swa swa_dipole_weight swa_energy_weight
swa_forces_weight swa_lr swa_polarizability_weight swa_stress_weight swa_virials_weight test_dir test_file
total_charge_key total_spin_key train_file use_agnostic_product use_embedding_readout use_last_readout_only
use_reduced_cg use_so3 valid_batch_size valid_file valid_fraction virials_key virials_weight wandb wandb_dir
wandb_entity wandb_log_hypers wandb_name wandb_project weight_decay weight_pt_head work_dir""".split())}
#: versions a preset has actually trained with (the reference runs)
PROVEN = {"multihead": "0.3.16", "scratch": "0.3.16", "lora": "0.3.16", "naive": None}
#: `mace.cli.fine_tuning_select`: the element-filter flag was renamed between versions
SELECT_ELEMENT_FLAG = {"0.3.14": ("--atomic_numbers", "list"), "0.3.16": ("--filter_atomic_numbers_pt", "csv")}
#: a flag the reference script abbreviates -> its full name (argparse prefix match there)
FULL_NAME = {"weight_pt": "weight_pt_head"}

# what every reference run shares (02/03/04 .slurm)
_COMMON = {"scheduler_patience": 10, "ema": True, "ema_decay": 0.995, "amsgrad": True,
           "clip_grad": 10.0, "patience": 20, "error_table": "PerAtomRMSE",
           "default_dtype": "float32", "device": "cuda"}
PRESETS = {
    "multihead": {**_COMMON, "multiheads_finetuning": True, "filter_type_pt": "combinations",
                  "subselect_pt": "fps", "weight_pt_head": 1.0, "energy_weight": 10.0,
                  "forces_weight": 100.0, "batch_size": 1, "valid_batch_size": 1,
                  "max_num_epochs": 100},
    "scratch": {**_COMMON, "model": "MACE", "hidden_irreps": "128x0e + 128x1o", "r_max": 6.0,
                "num_interactions": 2, "energy_weight": 10.0, "forces_weight": 100.0, "lr": 0.01,
                "lr_factor": 0.5, "batch_size": 4, "valid_batch_size": 4, "max_num_epochs": 100},
    # mh1_omat_pbe_lora.yml
    "lora": {"multiheads_finetuning": False, "energy_weight": 1.0, "forces_weight": 10.0,
             "loss": "universal", "scaling": "rms_forces_scaling", "lora": True, "lora_rank": 4,
             "lora_alpha": 1.0, "lr": 0.0005, "weight_decay": 1e-06, "batch_size": 1,
             "valid_batch_size": 1, "max_num_epochs": 150, "patience": 30, "ema": True,
             "ema_decay": 0.99, "amsgrad": True, "clip_grad": 10.0, "default_dtype": "float32",
             "device": "cuda"},
    # plain fine-tuning of every weight, no replay: the multihead numbers without the replay head
    "naive": {**_COMMON, "multiheads_finetuning": False, "energy_weight": 10.0, "forces_weight": 100.0,
              "batch_size": 1, "valid_batch_size": 1, "max_num_epochs": 100},
}


def split(frames: list[dict], valid_fraction: float = 0.1, test_fraction: float = 0.0,
          seed: int = 42, stratify: bool = True, holdout: list[str] | None = None) -> dict:
    """{"train": [...], "valid": [...], "test": [...]} as frame indices.

    stratify=False, test_fraction=0 is the user's split_train_valid.py
    exactly (one seeded shuffle of all indices, the first round(n x
    fraction) are validation). stratify=True does that inside each
    config_type, so every kind of frame is in every part. holdout: whole
    config_types that go to test and nowhere else.
    """
    if not 0.0 < valid_fraction < 1.0 or not 0.0 <= test_fraction < 1.0 or valid_fraction + test_fraction >= 1.0:
        raise ValueError("valid_fraction must be in (0, 1), test_fraction in [0, 1), and their sum below 1")
    holdout = set(holdout or [])
    types = {f["info"].get("config_type", "") for f in frames}
    if holdout - types:
        raise ValueError(f"holdout config_types not in the data: {sorted(holdout - types)} "
                         f"(present: {sorted(types)})")
    parts = {"train": [], "valid": [], "test": []}
    groups = defaultdict(list)
    for i, f in enumerate(frames):
        ct = f["info"].get("config_type", "")
        if ct in holdout:
            parts["test"].append(i)
        else:
            groups[ct if stratify else ""].append(i)
    for key in sorted(groups):
        idx = list(groups[key])
        order = list(range(len(idx)))
        random.Random(seed).shuffle(order)
        n_valid = max(1, round(len(idx) * valid_fraction))
        n_test = round(len(idx) * test_fraction)
        if test_fraction and not n_test and len(idx) > 2:
            n_test = 1
        if n_valid + n_test >= len(idx):
            raise ValueError(f"config_type {key!r} has {len(idx)} frame(s): too few for a "
                             "train/validation" + ("/test" if n_test else "") + " split; merge it "
                             "with another config_type, pass stratify=false, or hold it out")
        valid, test = set(order[:n_valid]), set(order[n_valid:n_valid + n_test])
        for k, i in enumerate(idx):
            parts["valid" if k in valid else "test" if k in test else "train"].append(i)
    if not parts["train"]:
        raise ValueError("no training frames left after the split")
    return {k: sorted(v) for k, v in parts.items()}


def _flag_lines(args: dict) -> list[str]:
    out = []
    for k, v in args.items():
        if v is True:
            out.append(f"--{k}")
        elif v is False or v is None:
            continue
        else:
            out.append(f"--{k} {shlex.quote(str(v))}")
    return out


def _script(title: str, module: str, args: dict, pre: list[str] = ()) -> str:
    lines = _flag_lines(args)
    body = " \\\n".join([f"python -m {module}"] + [f"    {l}" for l in lines])
    return ("#!/bin/bash\n"
            f"# {title}\n"
            "# Written by hyogo mace_make_finetune. Run from this folder, inside the MACE\n"
            "# environment; the job around it (GPUs, srun, modules) is the facility's.\n"
            "set -euo pipefail\n" + "".join(l + "\n" for l in pre) + body + "\n")


def make(frames: list[dict], name: str, method: str, mace_version: str, e0s,
         foundation: dict | None = None, replay: dict | None = None,
         keys: dict | None = None, split_parts: dict | None = None, seed: int = 42,
         distributed: bool = False, restart: bool = False, lr: float | None = None,
         stress_weight: float | None = None, overrides: dict | None = None) -> tuple[dict, dict]:
    """The run's files as {relative name: text} and its facts.

    frames are dataset.read_frames() output, split_parts split()'s. e0s is
    "foundation", "average" or {Z: eV}. foundation = {"model": path on the
    cluster or a MACE model name, "head": its head to start from (multi-head
    models)}. replay = {"pool": path on the cluster, "num_samples": N} or
    {"selected": path of an already selected file}.
    """
    if method not in METHODS:
        raise ValueError(f"method {method!r} is not one of {', '.join(METHODS)}")
    if not name or not all(c.isalnum() or c in "-_." for c in name):
        raise ValueError(f"name {name!r}: letters, digits, '-', '_' and '.' only (it names the "
                         "model and checkpoint files)")
    w = []
    chk = dataset.check(frames, **(keys or {}))
    if not chk["ok"]:
        raise ValueError("the data set is not fit to train on: " + "; ".join(chk["problems"])
                         + " (mlip_check_dataset shows the whole report)")
    w += chk["warnings"]
    keys, prof = chk["keys"], chk["profile"]
    zs = prof["atomic_numbers"]

    if method != "scratch" and not (foundation or {}).get("model"):
        raise ValueError(f"method {method!r} starts from a foundation model: pass "
                         'foundation={"model": <path on the cluster>, "head": <its head, if it has several>}')
    if method == "multihead" and not (replay and (replay.get("pool") or replay.get("selected"))):
        raise ValueError('multihead replay needs the replay data: replay={"pool": <path on the '
                         'cluster>, "num_samples": N} or {"selected": <already selected file>}')
    if method != "multihead" and replay:
        raise ValueError(f"replay data is only used by method \"multihead\", not {method!r}")
    if restart and method == "lora":
        w.append("restarting a LoRA run: the rank must be the one the checkpoint was made with")

    # E0s: explicit, as in the reference campaign (dft_e0/: computed with the labelling code)
    if e0s is None:
        raise ValueError('e0s is required: {Z: energy in eV} computed for isolated atoms at the '
                         'labels\' level of theory (same code, functional, basis/cutoff), or '
                         '"foundation" / "average" as a stated choice')
    if isinstance(e0s, dict):
        try:
            table = {int(k): float(v) for k, v in e0s.items()}
        except (TypeError, ValueError):
            raise ValueError("e0s must map atomic numbers to energies in eV, e.g. {1: -13.6, 8: -432.1}") from None
        lack = [z for z in zs if z not in table]
        if lack:
            raise ValueError(f"e0s has no entry for Z = {lack} (the data has Z = {zs})")
        if set(table) - set(zs):
            w.append(f"e0s has elements the data does not: Z = {sorted(set(table) - set(zs))}")
        e0s_arg, e0s_kind = "e0s.json", "given"
    elif e0s in ("foundation", "average"):
        e0s_arg, e0s_kind, table = e0s, e0s, None
        if e0s == "foundation" and method == "scratch":
            raise ValueError('e0s="foundation" needs a foundation model; method is "scratch"')
        w.append('E0s "foundation": the model\'s own atom energies are on its training code\'s energy '
                 "zero; labels from another code or pseudopotential family (CP2K/GTH vs VASP/PAW) "
                 "are on a different one. Check the Initial RMSE_E in the log: hundreds of meV/atom "
                 "means the E0s do not fit" if e0s == "foundation" else
                 'E0s "average": fitted from the training set, not isolated-atom energies; the '
                 "reference campaign computed them with the labelling code instead")
    else:
        raise ValueError('e0s must be {Z: eV}, "foundation" or "average"')

    args = {"name": name}
    if method != "scratch":
        if foundation.get("head"):
            args["foundation_head"] = foundation["head"]
        args["foundation_model"] = foundation["model"]
    if method == "multihead":
        args["pt_train_file"] = replay.get("selected") or "replay/selected_replay.xyz"
    args["train_file"] = "data/train.extxyz"
    args["valid_file"] = "data/valid.extxyz"
    if split_parts and split_parts.get("test"):
        args["test_file"] = "data/test.extxyz"
    args["energy_key"], args["forces_key"] = keys["energy_key"], keys["forces_key"]
    if keys["stress_key"]:
        args["stress_key"] = keys["stress_key"]
    if method in ("multihead", "scratch"):
        args["atomic_numbers"] = "[" + ", ".join(str(z) for z in zs) + "]"
    preset = dict(PRESETS[method])
    args["E0s"] = e0s_arg
    args.update(preset)
    if keys["stress_key"]:
        if stress_weight is None:
            stress_weight = 1.0
            w.append("stress_weight 1.0 (MACE's default): the reference runs trained without stress "
                     "(weight 0), so this value is untested; watch RMSE_stress")
        args["stress_weight"] = stress_weight
    else:
        args["stress_weight"] = 0.0
    if lr is not None:
        if method == "multihead":
            args["force_mh_ft_lr"] = True   # else MACE sets 1e-4 itself and ignores --lr
        args["lr"] = lr
    elif method == "multihead":
        w.append("multihead: MACE sets the learning rate to 1e-4 itself (pass lr to change it)")
    args["seed"] = seed
    if distributed:
        args["distributed"] = True
        args["launcher"] = "slurm"
    args["checkpoints_dir"] = "checkpoints"
    if restart:
        args["restart_latest"] = True
    args["keep_checkpoints"] = True
    for k, v in (overrides or {}).items():
        args[FULL_NAME.get(k, k)] = v

    known = RUN_TRAIN_FLAGS.get(mace_version)
    if known is not None:
        bad = sorted(k for k in args if k not in known)
        if bad:
            hint = (" (LoRA arrived after this version: use method \"multihead\" or \"naive\", or a "
                    "newer mace-torch)" if any(k.startswith("lora") for k in bad) else "")
            raise ValueError(f"mace-torch {mace_version} has no --{', --'.join(bad)}{hint}")
    else:
        w.append(f"mace-torch {mace_version}: arguments not checked against its --help (checked: "
                 f"{', '.join(sorted(RUN_TRAIN_FLAGS))}); run `python -m mace.cli.run_train --help` there")
    if PROVEN[method] and PROVEN[method] != mace_version:
        w.append(f"this {method} argument set has trained with mace-torch {PROVEN[method]}, not yet with "
                 f"{mace_version}")
    if PROVEN[method] is None:
        w.append(f"method {method!r} has no reference run: its numbers are the multihead ones without replay")

    files = {}
    pre = []
    if restart:
        pre = ['ls checkpoints/*.pt >/dev/null 2>&1 || { echo "no checkpoint (.pt) in checkpoints/: '
               'nothing to restart" >&2; exit 1; }']
    else:
        pre = ["mkdir -p checkpoints"]
    title = {"multihead": "MACE multihead replay fine-tuning", "lora": "MACE LoRA fine-tuning",
             "naive": "MACE fine-tuning (all weights, no replay)", "scratch": "MACE from scratch"}[method]
    files["train.sh"] = _script(title + (" (restart)" if restart else "") + f": {name}",
                                "mace.cli.run_train", args, pre)
    if table is not None:
        files["e0s.json"] = json.dumps({str(z): table[z] for z in sorted(table)}, indent=2) + "\n"

    select = None
    if method == "multihead" and not replay.get("selected"):
        flag, form = SELECT_ELEMENT_FLAG.get(mace_version, SELECT_ELEMENT_FLAG["0.3.16"])
        if mace_version not in SELECT_ELEMENT_FLAG:
            w.append(f"replay selection: element-filter flag for mace-torch {mace_version} unknown; "
                     f"wrote {flag} (0.3.16). Check `python -m mace.cli.fine_tuning_select --help`")
        elif mace_version == "0.3.14":
            w.append("replay selection on mace-torch 0.3.14 uses --atomic_numbers (0.3.16: "
                     "--filter_atomic_numbers_pt); the flag is in its --help but no selection has "
                     "run with it yet")
        n = int(replay.get("num_samples", 10000))
        if n <= 0:
            raise ValueError("replay num_samples must be positive")
        sel = {"configs_pt": replay["pool"], "configs_ft": "data/train.extxyz", "num_samples": n,
               "subselect": "fps", "model": foundation["model"], "output": "replay/selected_replay.xyz",
               "filtering_type": "combinations",
               flag[2:]: ("[" + ", ".join(map(str, zs)) + "]") if form == "list" else ",".join(map(str, zs)),
               "head_pt": "pt_head", "head_ft": "default", "weight_pt": 1.0, "device": "cuda"}
        files["select_replay.sh"] = _script(
            f"Replay selection for {name}: {n} frames by farthest-point sampling, elements Z = {zs}",
            "mace.cli.fine_tuning_select", sel,
            ["mkdir -p replay"]) + 'test -s replay/selected_replay.xyz || { echo "no replay file written" >&2; exit 1; }\n'
        select = {"num_samples": n, "pool": replay["pool"], "elements": zs}
        if n < len(frames):
            w.append(f"replay has {n} frames for {len(frames)} target frames: the reference used 10,000")

    facts = {"method": method, "mace_version": mace_version, "arguments": args, "keys": keys,
             "atomic_numbers": zs, "species": prof["species"], "e0s": e0s_kind, "d3": chk["d3"],
             "replay": select, "restart": restart, "warnings": w}
    return files, facts


def manifest(frames: list[dict], parts: dict, sha256_of) -> dict:
    """Which labelled frame went where: per part, each frame's index in the
    input, its file, config_type and the label run's source_sha256."""
    out = {}
    for part, idx in parts.items():
        out[part] = [{"index": i, "from": frames[i]["source"], "frame": frames[i]["index"],
                      "config_type": frames[i]["info"].get("config_type", ""),
                      "source_sha256": frames[i]["info"].get("source_sha256"),
                      "sha256": sha256_of(frames[i]["text"])} for i in idx]
    return out
