import sys
from checks._common import ROOT

LABEL = 'hyogo MACE fine-tune tools match the campaign (G49)'


def hyogo_mace_finetune_tools_match_the_campaign():
    """G49: mlip_check_dataset, mace_make_finetune and mace_parse_log
    were built from the user's finished fine-tuning campaigns
    (tests/fixtures/mace_finetune/README.md). They must write the same
    mace_run_train arguments as those job scripts, split as their split
    script does, and read the logs to the same numbers as their
    parse_mace.py. Then the UiO-66 label frames go through the whole
    chain. Nothing is trained."""
    import csv
    import gzip
    import hashlib
    import json
    import random
    import re
    import subprocess
    import tempfile
    from pathlib import Path
    from hyogo import server as hsrv
    from hyogo.mace import dataset, finetune, log as mlog
    root = ROOT
    fx = root / "tests" / "fixtures" / "mace_finetune"
    ref = fx / "reference"

    def script_args(name):
        """The flags of the mace_run_train call in a reference job script."""
        text = (ref / name).read_text()
        body = text[text.index("python -m mace.cli.run_train"):]
        body = body[:body.index("\n    '")]
        out = {}
        for m in re.finditer(r"--(\w+)(?:[ \t]+((?:\"[^\n]*?\"|[^\s\\]+)))?", body):
            v = m.group(2)
            out[m.group(1)] = True if v is None else v.strip('"')
        return out

    def synthetic(zs, n=40):
        sym = {v: k for k, v in dataset.Z_OF.items()}
        frames = []
        for i in range(n):
            atoms = "".join(f"{sym[z]} 0.0 0.0 {k}.0 0.0 0.0 0.0\n" for k, z in enumerate(zs))
            frames.append(f"{len(zs)}\nLattice=\"9 0 0 0 9 0 0 0 9\" "
                          f"Properties=species:S:1:pos:R:3:forces:R:3 energy=-{i}.5 "
                          f"config_type={'ab'[i % 2]}\n{atoms}")
        return dataset.read_frames("".join(frames), "syn.extxyz")

    zs = [1, 3, 6, 7, 8, 9, 16]
    e0s = {z: -1.0 for z in zs}
    frames = synthetic(zs)
    parts = finetune.split(frames, 0.1, 0.0, 42)
    common = dict(mace_version="0.3.16", e0s=e0s, split_parts=parts, seed=42, distributed=True)
    found = {"model": "/models/mace-mh-1.model", "head": "omat_pbe"}

    def same_as(script, got, renamed=(), extra=(), paths=("foundation_model", "pt_train_file",
                "train_file", "valid_file", "E0s", "checkpoints_dir", "name")):
        want = script_args(script)
        want = {dict(renamed).get(k, k): v for k, v in want.items()}
        for k in set(want) | set(got):
            if k in extra:
                continue
            assert k in want and k in got, f"{script}: --{k} only in {'the script' if k in want else 'the tool'}"
            if k in paths:
                continue
            w, g = want[k], got[k]
            assert g is True and w is True or str(g) == str(w) or float(g) == float(w), \
                f"{script}: --{k} is {g!r}, the script has {w!r}"

    # multihead replay = 02_finetune.slurm; the one rename is the script's --weight_pt,
    # an argparse abbreviation of --weight_pt_head
    _, mh = finetune.make(frames, "mh", "multihead", foundation=found,
                          replay={"selected": "replay/sel.xyz"}, **common)
    same_as("02_finetune.slurm", mh["arguments"], renamed=[("weight_pt", "weight_pt_head")])
    assert mh["arguments"]["atomic_numbers"] == "[1, 3, 6, 7, 8, 9, 16]"
    # the flag takes its boolean as a word, as the reference script writes it
    mh_files, _ = finetune.make(frames, "mh", "multihead", foundation=found,
                                replay={"selected": "replay/sel.xyz"}, **common)
    assert "--multiheads_finetuning True \\\n" in mh_files["train.sh"], mh_files["train.sh"]
    nv_files, nv = finetune.make(frames, "nv", "naive", foundation=found, **common)
    # without --lr MACE would train a fine-tune at its from-scratch 0.01
    assert nv["arguments"]["lr"] == 1e-4 and "--lr 0.0001" in nv_files["train.sh"]
    assert any("pass foundation.dtype" in w for w in nv["warnings"]), nv["warnings"]
    assert mh["arguments"]["default_dtype"] == "float32" and "loss" not in mh["arguments"]
    assert "--multiheads_finetuning False \\\n" in nv_files["train.sh"], nv_files["train.sh"]
    assert "--ema \\\n" in mh_files["train.sh"] and "--ema True" not in mh_files["train.sh"]
    # a cluster path under the home directory must still expand there
    hm, _ = finetune.make(frames, "hm", "multihead", mace_version="0.3.16", e0s=e0s,
                          foundation={"model": "~/models/my model.model"},
                          replay={"pool": "$HOME/models/replay.xyz"})
    assert "--foundation_model \"$HOME\"/'models/my model.model'" in hm["train.sh"], hm["train.sh"]
    assert "--configs_pt \"$HOME\"/models/replay.xyz" in hm["select_replay.sh"], hm["select_replay.sh"]
    assert "--model \"$HOME\"/'models/my model.model'" in hm["select_replay.sh"]
    # restart = 03_restart.slurm: the same call plus --restart_latest and a checkpoint check
    files, rs = finetune.make(frames, "mh", "multihead", foundation=found,
                              replay={"selected": "replay/sel.xyz"}, restart=True, **common)
    same_as("03_restart.slurm", rs["arguments"], renamed=[("weight_pt", "weight_pt_head")])
    assert "--restart_latest" in files["train.sh"] and "nothing to restart" in files["train.sh"]
    # from scratch = 04_train_from_scratch.slurm (which also carries --restart_latest)
    _, sc = finetune.make(frames, "sc", "scratch", **common)
    same_as("04_train_from_scratch.slurm", sc["arguments"], extra=("restart_latest",))
    # LoRA = the yml of the LoRA run; paths, its label keys, E0s and stress are per-run
    import yaml
    yml = yaml.safe_load((ref / "mh1_omat_pbe_lora.yml").read_text())
    _, lo = finetune.make(frames, "lo", "lora", foundation=found, e0s="average",
                          **{k: v for k, v in common.items() if k != "e0s"} | {"seed": 1234})
    for k, v in yml.items():
        if k in ("train_file", "valid_file", "model_dir", "log_dir", "results_dir", "checkpoints_dir",
                 "foundation_model", "name", "energy_key", "forces_key", "save_cpu"):
            continue
        g = lo["arguments"].get(k, False)
        assert g == v, f"LoRA: {k} is {g!r}, the yml has {v!r}"
    # ...and mace-torch 0.3.14 (HOKUSAI) has no LoRA: refused there, with the way out
    for bad, kw in (("no --lora", dict(method="lora", mace_version="0.3.14", e0s="average", foundation=found)),
                    ("e0s is required", dict(method="multihead", mace_version="0.3.14", e0s=None,
                                             foundation=found, replay={"selected": "x"})),
                    ("needs the replay data", dict(method="multihead", mace_version="0.3.14", e0s=e0s,
                                                   foundation=found)),
                    ("starts from a foundation model", dict(method="naive", mace_version="0.3.14", e0s=e0s)),
                    ("no entry for Z = [16]", dict(method="scratch", mace_version="0.3.14",
                                                   e0s={z: 0 for z in zs[:-1]}))):
        try:
            finetune.make(frames, "x", **kw)
        except ValueError as e:
            assert bad in str(e), f"{bad!r} not in: {e}"
        else:
            raise AssertionError(f"mace_make_finetune did not refuse: {bad}")
    # every flag the multihead preset writes exists in 0.3.14's --help
    _, on14 = finetune.make(frames, "mh", "multihead", mace_version="0.3.14", e0s=e0s, foundation=found,
                            replay={"pool": "/data/replay.xyz"}, distributed=True)
    assert any("0.3.16, not yet with 0.3.14" in w for w in on14["warnings"]), on14["warnings"]
    # replay selection: elements from the data; the filter flag differs by version
    f14, _ = finetune.make(frames, "mh", "multihead", mace_version="0.3.14", e0s=e0s, foundation=found,
                           replay={"pool": "/data/replay.xyz"})
    f16, _ = finetune.make(frames, "mh", "multihead", mace_version="0.3.16", e0s=e0s, foundation=found,
                           replay={"pool": "/data/replay.xyz"})
    assert "--atomic_numbers '[1, 3, 6, 7, 8, 9, 16]'" in f14["select_replay.sh"]
    assert "--filter_atomic_numbers_pt 1,3,6,7,8,9,16" in f16["select_replay.sh"]
    sel = (ref / "select_replay.py").read_text() + (ref / "01_select_replay.slurm").read_text()
    for flag, val in (("num_samples", "10000"), ("subselect", "fps"), ("filtering_type", "combinations"),
                      ("head_pt", "pt_head"), ("head_ft", "default"), ("device", "cuda")):
        assert f"--{flag} {val}" in f16["select_replay.sh"], flag
        assert val in sel

    # the split: stratify=False is split_train_valid.py's algorithm
    src = (ref / "split_train_valid.py").read_text()
    assert "random.Random(args.seed).shuffle(indices)" in src
    assert "n_valid = max(1, round(n_total * args.valid_fraction))" in src
    indices = list(range(len(frames)))
    random.Random(42).shuffle(indices)
    theirs = sorted(indices[:max(1, round(len(frames) * 0.1))])
    assert finetune.split(frames, 0.1, 0.0, 42, stratify=False)["valid"] == theirs
    st = finetune.split(frames, 0.1, 0.1, 42)
    assert not set(st["train"]) & set(st["valid"]) and not set(st["test"]) & (set(st["train"]) | set(st["valid"]))
    for part in ("valid", "test"):
        assert {frames[i]["info"]["config_type"] for i in st[part]} == {"a", "b"}, f"{part} misses a config_type"
    ho = finetune.split(frames, 0.1, 0.0, 42, holdout=["b"])
    assert {frames[i]["info"]["config_type"] for i in ho["test"]} == {"b"} and len(ho["test"]) == 20
    assert all(frames[i]["info"]["config_type"] == "a" for i in ho["train"] + ho["valid"])

    # the log reader gives parse_mace.py's numbers on three real logs
    with tempfile.TemporaryDirectory() as tmp:
        for name, sessions, last, best, stop, heads in (
                ("meoh_v2_multihead", 1, 58, 38, "early_stop", 2),
                ("p3_multihead_restarts", 6, 149, 118, "max_epochs", 2),
                ("p3_lora", 2, 149, 149, "max_epochs", 1)):
            r = hsrv.mace_parse_log(str(fx / f"{name}.log"), epochs=True)
            assert (r["sessions"], r["last_epoch"], r["best_epoch"], r["stop"]["reason"]) == \
                (sessions, last, best, stop), (name, r["sessions"], r["last_epoch"], r["best_epoch"], r["stop"])
            assert r["finished"] and r["evaluated"] and len(r["heads"]) == heads and r["version"] == "0.3.16"
            empty, out = Path(tmp) / "empty.txt", Path(tmp) / f"{name}.csv"
            empty.write_text("")
            subprocess.run([sys.executable, str(ref / "parse_mace.py"), name, str(fx / f"{name}.log"),
                            str(empty), str(out)], check=True, capture_output=True)
            rows = list(csv.DictReader(out.open()))
            mine = {(e["epoch"], e["head"]): e for e in r["epochs"]}
            assert len(rows) == len(mine), f"{name}: {len(mine)} rows, parse_mace.py has {len(rows)}"
            for row in rows:
                e = mine[(int(row["epoch"]), row["head"])]
                assert (e["loss"], e["rmse_e_meV_atom"], e["rmse_f_meV_A"], e["session"]) == \
                    (float(row["valid_loss"]), float(row["valid_rmse_e_mev_atom"]),
                     float(row["valid_rmse_f_mev_A"]), int(row["session"])), (name, row["epoch"], row["head"])
    v2 = hsrv.mace_parse_log(str(fx / "meoh_v2_multihead.log"))
    assert "epochs" not in v2
    tab = {t["set"]: t for t in v2["error_table"]["rows"]}
    assert (tab["valid_Default"]["rmse_e_meV_atom"], tab["valid_Default"]["rmse_f_meV_A"]) == (0.3, 10.0)
    at = v2["summary"]["Default"]["at_best_epoch"]
    assert (at["epoch"], at["rmse_e_meV_atom"], at["rmse_f_meV_A"]) == (38, 0.32, 10.03), at
    assert v2["summary"]["pt_head"]["initial"]["rmse_e_meV_atom"] == 5885.85
    assert v2["checkpoint"].endswith("_epoch-38.pt") and v2["foundation_head"] == "omat_pbe"
    assert any("ERROR x3: Failed to extract energy" in w for w in v2["warnings"]), v2["warnings"]
    assert any("pt_head: initial energy RMSE 5886" in w for w in v2["warnings"])
    # the LoRA run used E0s "average": its 4.6 eV/atom initial error is flagged
    lora = hsrv.mace_parse_log(str(fx / "p3_lora.log"))
    assert any("head Default: initial energy RMSE 4623" in w for w in lora["warnings"]), lora["warnings"]
    # a run this tool wrote, on mace-torch 0.3.14 with stress in the loss (HOKUSAI job 9765431)
    hk = hsrv.mace_parse_log(str(fx / "hokusai_naive_stress_0.3.14.log"))
    assert (hk["version"], hk["finished"], hk["evaluated"], hk["last_epoch"], hk["best_epoch"]) == \
        ("0.3.14", True, True, 1, 1), hk
    assert hk["stop"] == {"reason": "max_epochs"} and hk["configurations"] == {"train": 4, "valid": 2}
    assert hk["summary"]["Default"]["last"]["rmse_stress_meV_A3"] == 3.22
    assert {t["set"]: t["rmse_stress_meV_A3"] for t in hk["error_table"]["rows"]} == \
        {"train_Default": 3.4, "valid_Default": 3.2}
    assert any("initial energy RMSE 5664" in w for w in hk["warnings"]), hk["warnings"]
    cut = (fx / "meoh_v2_multihead.log").read_text().split("Stopping optimization")[0]
    running = mlog.parse(cut)
    assert not running["finished"] and running["stop"] is None and running["last_epoch"] == 58
    assert any("no 'Training complete'" in w for w in running["warnings"])

    # the whole chain on the six UiO-66 label frames (cp2k_make_labels -> check -> run folder)
    lab = root / "tests" / "fixtures" / "cp2k_labels"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        items = []
        for o in sorted(lab.glob("*.out.gz")):
            stem = o.name[:-len(".out.gz")]
            (tmp / f"{stem}.out").write_bytes(gzip.decompress(o.read_bytes()))
            (tmp / f"{stem}.subsys.inc").write_bytes(
                gzip.decompress((lab / f"{stem}.subsys.inc.gz").read_bytes()))
            items.append({"out": str(tmp / f"{stem}.out"), "subsys": str(tmp / f"{stem}.subsys.inc"),
                          "config_type": "o1" if stem.startswith("f") else "o2"})
        hsrv.cp2k_make_labels(items, str(tmp / "labels.extxyz"))
        chk = hsrv.mlip_check_dataset([str(tmp / "labels.extxyz")])
        assert chk["ok"] and chk["d3"] == "included", chk["problems"]
        assert chk["keys"] == {"energy_key": "REF_energy", "forces_key": "REF_forces",
                               "stress_key": "REF_stress"}
        assert chk["profile"]["atomic_numbers"] == [1, 6, 8, 40] and chk["profile"]["frames"] == 6
        assert chk["profile"]["config_types"] == {"o1": 3, "o2": 3}
        assert chk["profile"]["info_keys"]["REF_stress"] == 6 and chk["profile"]["arrays"]["REF_forces"] == 6
        run = hsrv.mace_make_finetune(
            [str(tmp / "labels.extxyz")], str(tmp / "run"), "uio66_r2scan", "0.3.14",
            {1: -13.6, 6: -1029.0, 8: -2041.0, 40: -1270.0},
            foundation={"model": "models/MACE-matpes-r2scan-omat-ft.model", "dtype": "float64"},
            replay={"pool": "models/matpes-r2scan-replay-data.extxyz", "num_samples": 10000},
            valid_fraction=0.34, distributed=True)
        a = run["arguments"]
        assert (a["energy_key"], a["forces_key"], a["stress_key"]) == ("REF_energy", "REF_forces", "REF_stress")
        assert a["atomic_numbers"] == "[1, 6, 8, 40]" and a["stress_weight"] == 1.0
        # stress in the labels: a loss that includes it and an error table that reports it
        # (MACE's default loss is energy+forces only), and the model's own precision
        assert (a["loss"], a["error_table"]) == ("universal", "PerAtomRMSEstressvirials"), a
        assert a["default_dtype"] == "float64", a
        assert "foundation_head" not in a and a["weight_pt_head"] == 1.0 and a["E0s"] == "e0s.json"
        assert run["split"] == {"train": 4, "valid": 2, "test": 0,
                                "by_config_type": {"o1": {"train": 2, "valid": 1, "test": 0},
                                                   "o2": {"train": 2, "valid": 1, "test": 0}}}, run["split"]
        assert any("stress is trained: loss universal, stress_weight 1.0" in w for w in run["warnings"])
        assert any("default_dtype float64, the foundation model's" in w for w in run["warnings"])
        assert set(run["files"]) == {"train.sh", "select_replay.sh", "e0s.json", "manifest.json",
                                     "data/train.extxyz", "data/valid.extxyz"}, run["files"]
        for sh in ("train.sh", "select_replay.sh"):
            assert subprocess.run(["bash", "-n", str(tmp / "run" / sh)]).returncode == 0, f"{sh}: bash -n"
            assert "/Users/" not in (tmp / "run" / sh).read_text(), f"{sh} holds a path of this machine"
        # frames are written byte for byte, and the manifest says where each went
        whole = (tmp / "labels.extxyz").read_text()
        written = (tmp / "run" / "data" / "train.extxyz").read_text() + \
            (tmp / "run" / "data" / "valid.extxyz").read_text()
        src_frames = dataset.read_frames(whole)
        assert sorted(f["text"] for f in dataset.read_frames(written)) == sorted(f["text"] for f in src_frames)
        man = json.loads((tmp / "run" / "manifest.json").read_text())["split"]
        assert sorted(e["index"] for part in man.values() for e in part) == list(range(6))
        for part in man.values():
            for e in part:
                assert e["sha256"] == hashlib.sha256(src_frames[e["index"]]["text"].encode()).hexdigest()
                assert len(e["source_sha256"]) == 64
        assert json.loads((tmp / "run" / "e0s.json").read_text())["40"] == -1270.0
        try:
            hsrv.mace_make_finetune([str(tmp / "labels.extxyz")], str(tmp / "run"), "x", "0.3.14", "average",
                                    method="naive", foundation={"model": "m"}, valid_fraction=0.34)
        except ValueError as e:
            assert "exists and is not empty" in str(e)
        else:
            raise AssertionError("an existing run folder was overwritten")
        # D3 and D3-free labels mixed: unfit, and the writer refuses
        mixed = whole.replace("dispersion_eV=", "was_dispersion_eV=", 1)
        (tmp / "mixed.extxyz").write_text(mixed)
        bad = hsrv.mlip_check_dataset([str(tmp / "mixed.extxyz")])
        assert not bad["ok"] and bad["d3"] == "mixed" and "D3 and D3-free labels are mixed" in bad["problems"][0]
        try:
            hsrv.mace_make_finetune([str(tmp / "mixed.extxyz")], str(tmp / "run2"), "x", "0.3.14", "average",
                                    method="naive", foundation={"model": "m"}, valid_fraction=0.34)
        except ValueError as e:
            assert "not fit to train on" in str(e)
        else:
            raise AssertionError("mixed D3 labels were accepted")
