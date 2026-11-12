import pathlib
import tempfile
from checks._common import ROOT

LABEL = 'hyogo scores an MLIP against its labels'

# T1 (HOKUSAI job 9751103, 2026-11-09; ~/agent/work/mlmd-uio66/matpes-test/eval.json): the
# models' dE vs f0 and force RMSE against the D3(BJ) r2SCAN labels of the six fixture frames
T1_DE_DFT_NOD3 = {"f4": 91.33179065687919, "f6": 115.33685354825457, "g1": 74.64463813502925,
                  "g2": 110.31315127094562, "g3": 110.65021178342647}
T1 = {"matpes_r2scan": {
          "dE": {"f4": 89.95939210764323, "f6": 123.18053578594034, "g1": 72.80284282361256,
                 "g2": 113.93131577228974, "g3": 118.85068881693678},
          "frmse": {"f0": 227.13852222413175, "f4": 217.80544053118282, "f6": 188.96283465536519,
                    "g1": 223.06148542586885, "g2": 195.89279038409035, "g3": 195.1205188415119}},
      "mp0_medium": {
          "dE": {"f4": 50.775298839743016, "f6": 67.69252780555279, "g1": 41.73872732487401,
                 "g2": 62.38409231402505, "g3": 64.39211492163035},
          "frmse": {"f0": 135.63566442089987, "f4": 331.00332614488207, "f6": 335.55770236196616,
                    "g1": 312.2938445731348, "g2": 329.0524478127325, "g3": 343.5507201233134}}}


def hyogo_scores_an_mlip_against_its_labels():
    """G47: mlip_compare scores predictions (the mace pack's
    eval/scripts/predict.py) against cp2k_make_labels' extxyz of the six
    r2SCAN fixture frames: exact arithmetic on made-up predictions
    (relative and absolute energies, force RMSE and cosine, stress, worst
    frames, per config_type); every refusal (other frames, atom order, D3
    pairings, mixed labels); predict.py hashes the atoms as hyogo does and
    parses its arguments without torch; and, from the predictions of the
    real HOKUSAI run in tests/fixtures/mlip_predictions/, T1's numbers."""
    import copy
    import gzip
    import importlib.util
    import json
    import subprocess
    import sys
    import numpy as np
    from hyogo import server as hsrv
    from hyogo.mace import dataset, eval as mev
    fx = ROOT / "tests" / "fixtures" / "cp2k_labels"
    fxp = ROOT / "tests" / "fixtures" / "mlip_predictions"
    script = ROOT / "codes" / "mace" / "eval" / "scripts" / "predict.py"
    names = ["f0", "f4", "f6", "g1", "g2", "g3"]
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        for n in names:
            (d / f"{n}.out").write_bytes(gzip.decompress((fx / f"{n}.out.gz").read_bytes()))
            (d / f"{n}.inc").write_bytes(gzip.decompress((fx / f"{n}.subsys.inc.gz").read_bytes()))
        labels = d / "labels.extxyz"
        hsrv.cp2k_make_labels([{"out": str(d / f"{n}.out"), "subsys": str(d / f"{n}.inc"),
                                "config_type": n} for n in names], str(labels))
        frames = dataset.read_frames(labels.read_text(), str(labels))
        lab = mev.read_labels(frames)
        assert lab["d3"] == "included" and [f["config_type"] for f in lab["frames"]] == names
        lf = lab["frames"]

        # predict.py: --help without torch or mace; its atoms hash is hyogo's
        h = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True)
        assert h.returncode == 0 and "--dispersion" in h.stdout, h.stderr[-500:]
        spec = importlib.util.spec_from_file_location("g47_predict", script)
        pr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pr)
        assert pr.FORMAT == mev.FORMAT
        try:
            from ase.io import read as ase_read
        except ImportError:
            ase_read = None
        if ase_read is not None:   # ASE is the yagura extra; hyogo itself doesn't need it
            for a, f in zip(ase_read(str(labels), ":"), lf):
                assert pr.atoms_sha256(a.get_chemical_symbols(), a.get_positions()) == f["atoms_sha256"]

        # made-up predictions: label forces + a known offset, energies with a known slope
        def fake(dispersion="none", de=0.0, dforce=0.0, dstress=0.0, e0s=None):
            out = []
            for i, f in enumerate(lf):
                e = f["energy"] - (f["dispersion"] if dispersion == "none" else 0.0) + de * i
                out.append({"index": i, "config_type": f["config_type"], "source_sha256": f["source_sha256"],
                            "natoms": f["natoms"], "atoms_sha256": f["atoms_sha256"], "energy": e,
                            "forces": (f["forces"] + dforce).tolist(),
                            "stress": (f["stress"] + dstress).tolist()})
            return {"format": mev.FORMAT, "model": {"name": "fake", "e0s": e0s}, "dispersion": dispersion,
                    "labels": {"sha256": "x"}, "frames": out}

        def write(p, name):
            path = d / f"{name}.predictions.json"
            path.write_text(json.dumps(p))
            return str(path)

        close = lambda a, b, tol=1e-9: abs(a - b) <= tol * max(1.0, abs(b))
        # D3 labels against a D3-included model: exact, forces 0, cosine 1
        r = hsrv.mlip_compare([str(labels)], [write(fake("included"), "same")], per_frame=True)
        m = r["models"]["fake"]
        o = m["overall"]
        assert o["forces"]["rmse_meV_A"] == 0 and close(o["forces"]["cosine_mean"], 1.0), o
        assert o["energy_rel_meV_atom"]["max_abs"] < 1e-9 and o["stress"]["rmse_GPa"] == 0, o
        assert set(m["by_config_type"]) == set(names) and m["by_config_type"]["f0"]["energy_rel_meV_atom"] is None
        assert r["reference_frame"] == {"index": 0, "config_type": "f0"} and len(m["frames"]) == 6
        assert any("other bytes" in w for w in r["warnings"]), r["warnings"]
        assert m["frames"][3]["dE_label_meV_atom"] - (
            (lf[3]["energy"] / 456 - lf[0]["energy"] / 456) * 1e3) < 1e-9
        # offsets: 10 meV per frame, 0.1 eV/A on every force component, 1 meV/A^3 stress
        p = fake("included", de=0.010, dforce=0.1, dstress=0.001)
        r = hsrv.mlip_compare(str(labels), write(p, "off"), reference_frame="f4", worst=2, per_frame=True)
        m = r["models"]["fake"]
        for row in m["frames"]:
            assert close(row["dE_error_meV_atom"], (row["index"] - 1) * 10 / 456, 1e-7), row
            assert close(row["force_rmse_meV_A"], 100.0, 1e-9) and close(row["stress_rmse_meV_A3"], 1.0, 1e-7), row
        assert m["overall"]["forces"]["cosine_mean"] < 1
        assert [w["config_type"] for w in m["worst"]["energy_rel"]] == ["g3", "g2"], m["worst"]
        assert len(m["worst"]["force_rmse"]) == 2 and "frames" not in hsrv.mlip_compare(
            str(labels), write(p, "off"))["models"]["fake"]
        # absolute energy after E0s: model E0s 1 eV/atom above the labels' -> -1000 meV/atom
        zs = sorted({str(dataset.Z_OF[s]) for s in lf[0]["symbols"]}, key=int)
        e0l = {z: -10.0 * int(z) for z in zs}
        e0m = {z: v + 1.0 for z, v in e0l.items()}
        (d / "e0s.json").write_text(json.dumps(e0l))
        r = hsrv.mlip_compare(str(labels), write(fake("included", e0s=e0m), "e0"), e0s=str(d / "e0s.json"),
                              out_path=str(d / "report.json"))
        ea = r["models"]["fake"]["overall"]["energy_abs_meV_atom"]
        assert close(ea["mean"], -1000.0, 1e-9) and ea["std"] < 1e-6, ea
        assert "frames" in json.loads((d / "report.json").read_text())["models"]["fake"]
        r = hsrv.mlip_compare(str(labels), write(fake("included"), "noe0"), e0s=e0l)
        assert r["models"]["fake"]["overall"]["energy_abs_meV_atom"] is None
        assert any("no model E0s" in w for w in r["models"]["fake"]["warnings"])
        # T1's way: D3 labels, D3-free model -> energies without dispersion_eV, a warning
        r = hsrv.mlip_compare(str(labels), write(fake("none"), "nod3"), d3_mismatch="energy_only")
        m = r["models"]["fake"]
        assert m["overall"]["energy_rel_meV_atom"]["max_abs"] < 1e-9, m["overall"]
        assert m["label_energy"] == "REF_energy - dispersion_eV" and any("upper bounds" in w for w in m["warnings"])

        # refusals
        def refused(msg, preds, lab=str(labels), **kw):
            try:
                hsrv.mlip_compare(lab, [write(x, f"bad{i}") for i, x in enumerate(preds)], **kw)
            except ValueError as e:
                assert msg in str(e), (msg, str(e))
            else:
                raise AssertionError(f"mlip_compare accepted: {msg}")

        refused("would mix in D3", [fake("none")])
        refused("d3_mismatch must be", [fake("none")], d3_mismatch="nope")
        short = fake("included")
        short["frames"] = short["frames"][:5]
        refused("5 predicted frames for 6", [short])
        swap = fake("included")
        swap["frames"][2]["source_sha256"] = "0" * 64
        refused("source_sha256 differs", [swap])
        order = fake("included")
        f = copy.deepcopy(frames[1])
        rows = f["text"].splitlines()
        rows[2], rows[3] = rows[3], rows[2]
        order["frames"][1]["atoms_sha256"] = mev.read_labels(
            [{**f, "text": "\n".join(rows) + "\n"}])["frames"][0]["atoms_sha256"]
        refused("atom order", [order])
        old = fake("included")
        old["format"] = "eval_models.py"
        refused("not a predictions file", [old])
        undecided = fake("included")
        undecided["dispersion"] = None
        refused("not stated", [undecided])
        refused('names 0 frames', [fake("included")], reference_frame="x9")
        refused("not a frame index", [fake("included")], reference_frame=6)
        refused("lack atomic numbers", [fake("included", e0s=e0m)], e0s={"1": 0.0})
        # D3-free labels never take a D3 model, energy_only or not
        nod3 = d / "nod3.extxyz"
        nod3.write_text("".join(f["text"] for f in frames).replace(" dispersion_eV=", " was_dispersion_eV="))
        refused("would mix in D3", [fake("included")], lab=str(nod3), d3_mismatch="energy_only")
        mixed = d / "mixed.extxyz"
        mixed.write_text(frames[0]["text"].replace(" dispersion_eV=", " was_dispersion_eV=")
                         + "".join(f["text"] for f in frames[1:]))
        refused("mixed", [fake("included")], lab=str(mixed))

        # T1: the real predictions of MATPES-r2SCAN-0 and MP-0 (HOKUSAI) on these frames
        for model, want in T1.items():
            path = fxp / f"{model}.predictions.json.gz"
            assert path.exists(), f"missing {path} (the G47 eval job's output)"
            r = hsrv.mlip_compare(str(labels), str(path), d3_mismatch="energy_only", per_frame=True)
            m = r["models"][model]
            assert m["dispersion"] == "none" and m["model"]["sha256"], m["model"]
            for row in m["frames"]:
                ct = row["config_type"]
                assert abs(row["force_rmse_meV_A"] - want["frmse"][ct]) < 1e-3, (model, ct, row)
                if ct != "f0":
                    assert abs(row["dE_label_meV_atom"] - T1_DE_DFT_NOD3[ct]) < 1e-6, (ct, row)
                    assert abs(row["dE_model_meV_atom"] - want["dE"][ct]) < 1e-3, (model, ct, row)
