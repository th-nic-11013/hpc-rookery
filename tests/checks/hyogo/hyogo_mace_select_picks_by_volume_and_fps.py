from checks._common import ROOT

LABEL = 'hyogo mlip_select_frames picks by volume spread and farthest point (G50)'


def hyogo_mace_select_picks_by_volume_and_fps():
    """G50: mlip_select_frames turns a pool of MD frames into the frames to
    label (notes/uio66-labelling-readiness.md §2): held-out time blocks,
    strata x volume bins with quotas, FPS on descriptors (or volume, or a
    round-2 score) with >= 1 ps between picks of one run, a control set,
    round-2 exclusion by sha256 or by geometry, seeded. Synthetic pools,
    then the six real UiO-66 frames of tests/fixtures/cp2k_labels (their
    labelled copies must be found in the pool by geometry). The cluster
    descriptor script runs here with --dry-run only (no mace-torch)."""
    import gzip
    import hashlib
    import json
    import subprocess
    import sys
    import tempfile
    from pathlib import Path
    import numpy as np
    from hyogo import server as hsrv
    from hyogo.cp2k import labels as clab
    from hyogo.mace import dataset

    def raises(fn, *words):
        try:
            fn()
        except ValueError as e:
            for w in words:
                assert w in str(e), f"{w!r} not in: {e}"
            return str(e)
        raise AssertionError(f"no refusal ({words})")

    rng = np.random.default_rng(7)

    def run(path, src, ct, n, v0, dv, dt=0.025, extra=""):
        out = []
        for k in range(n):
            a = (v0 + dv * np.sin(k / 37) + rng.normal(0, dv / 5)) ** (1 / 3)
            pos = rng.random((5, 3)) * a
            atoms = "".join(f"{el} {p[0]:.6f} {p[1]:.6f} {p[2]:.6f}\n" for el, p in zip("Zr O O C H".split(), pos))
            out.append(f"5\nLattice=\"{a:.6f} 0 0 0 {a:.6f} 0 0 0 {a:.6f}\" Properties=species:S:1:pos:R:3 "
                       f"source={src} frame={k} timestep={k * 25} time_ps={k * dt:.3f} config_type={ct}{extra} "
                       f"pbc=\"T T T\"\n{atoms}")
        Path(path).write_text("".join(out))

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        run(d / "p300.extxyz", "A3-300K", "P1-300K", 800, 9000, 60)
        run(d / "p600.extxyz", "A3-600K", "P1-600K", 800, 9050, 120)
        run(d / "p1g.extxyz", "P2-1GPa", "P2-1GPa", 400, 7500, 300)
        P = [str(d / f) for f in ("p300.extxyz", "p600.extxyz", "p1g.extxyz")]
        H = [{"source": "A3-*", "last_ps": 5}]
        pool_frames = []
        for p in P:
            pool_frames += dataset.read_frames(Path(p).read_text(), Path(p).name)
        by_sha = {hashlib.sha256(f["text"].encode()).hexdigest(): f for f in pool_frames}

        # report only: nothing written, capacities at the 1 ps spacing
        rep = hsrv.mlip_select_frames(P, holdout=H)
        assert "out_dir" not in rep and rep["feature"] == "volume" and rep["frames"] == 2000
        s = rep["strata"]
        assert set(s) == {"P1-300K", "P1-600K", "P2-1GPa"}
        assert s["P1-300K"]["frames"] == 800 and s["P1-300K"]["eligible"] == 599     # last 5 ps = 201 frames
        assert s["P1-300K"]["spaced_capacity"] == 15 and s["P2-1GPa"]["spaced_capacity"] == 10
        assert len(s["P2-1GPa"]["bins"]) == 5 and sum(b["eligible"] for b in s["P2-1GPa"]["bins"]) == 400
        assert rep["holdout"] == [{"rule": H[0], "frames": 402, "new": 402}]

        # round 1, volume only
        Q = {"P1-300K": 6, "P1-600K": 6, "P2-1GPa": 5}
        r1 = hsrv.mlip_select_frames(P, quotas=Q, out_dir=str(d / "r1"), holdout=H)
        man = json.loads((d / "r1" / "manifest.json").read_text())
        fr = man["frames"]
        ctl = [f for f in fr if f["why"]["by"] == "control"]
        assert r1["picked"] == len(fr) == 18 and len(ctl) == 1          # 5 % of 17, rounded
        for g, q in Q.items():
            got = [f for f in fr if f["stratum"] == g and f["why"]["by"] != "control"]
            assert len(got) == q, (g, len(got))
            assert r1["by_stratum"][g]["by_volume_bin"].count(0) == 0, r1["by_stratum"][g]   # every bin used
        for src in {f["source"] for f in fr}:
            t = sorted(f["time_ps"] for f in fr if f["source"] == src)
            assert all(b - a >= 1 - 1e-9 for a, b in zip(t, t[1:])), (src, t)
            if src.startswith("A3"):
                assert max(t) < 20 - 5 - 1e-9, (src, max(t))              # held-out last 5 ps
        sel = dataset.read_frames((d / "r1" / "selected.extxyz").read_text(), "sel")
        assert len(sel) == 18
        for f, m in zip(sel, fr):
            src = by_sha[m["sha256"]]                                     # sha256 = the pool frame's own text
            assert src["source"] == m["pool_file"] and src["index"] == m["pool_index"] == m["frame"]
            assert f["text"].splitlines()[2:] == src["text"].splitlines()[2:]   # atoms byte for byte
            assert f["info"]["pool_sha256"] == m["sha256"] and f["info"]["stratum"] == m["stratum"]
            assert f["info"]["time_ps"] == src["info"]["time_ps"]

        # seeded: the same call gives the same bytes
        hsrv.mlip_select_frames(P, quotas=Q, out_dir=str(d / "r1b"), holdout=H)
        for n in ("selected.extxyz", "manifest.json"):
            assert (d / "r1" / n).read_bytes() == (d / "r1b" / n).read_bytes(), n

        # round 2: earlier picks excluded by manifest or by selected extxyz, spacing kept from them
        for ex in ([str(d / "r1" / "manifest.json")], [str(d / "r1" / "selected.extxyz")]):
            r2 = hsrv.mlip_select_frames(P, quotas={"P1-300K": 3, "P1-600K": 3, "P2-1GPa": 2},
                                         out_dir=str(d / "r2"), holdout=H, exclude=ex, overwrite=True,
                                         min_spacing_ps=0.5)
            assert r2["excluded"]["by_sha256"] == 18, r2["excluded"]
            fr2 = json.loads((d / "r2" / "manifest.json").read_text())["frames"]
            assert not {f["sha256"] for f in fr2} & {f["sha256"] for f in fr}
            for src in {f["source"] for f in fr2}:
                t = sorted(f["time_ps"] for f in fr + fr2 if f["source"] == src)
                assert all(b - a >= 0.5 - 1e-9 for a, b in zip(t, t[1:])), (src, t)

        # a test set from the held-out block only
        te = hsrv.mlip_select_frames(P, quotas={"P1-300K": 3, "P1-600K": 3}, out_dir=str(d / "test"),
                                     holdout=H, from_holdout=True, control_fraction=0)
        assert te["picked"] == 6 and te["warnings"] == [], te["warnings"]
        assert all(f["time_ps"] >= 14.975 - 1e-9 for f in json.loads((d / "test" / "manifest.json").read_text())["frames"])

        # descriptors: three far-off frames in one stratum are found by FPS, not by volume
        n = 2000
        desc = rng.normal(0, 0.05, (n, 4))
        far = {900: [5, 0, 0, 0], 960: [0, 5, 0, 0], 1100: [0, 0, 5, 5]}        # P1-600K, times 2.5/4/7.5 ps
        for i, v in far.items():
            desc[i] = v
        np.save(d / "desc.npy", desc)
        r = hsrv.mlip_select_frames(P, quotas={"P1-600K": 4}, out_dir=str(d / "fps"), holdout=H,
                                    descriptors=str(d / "desc.npy"), volume_bins=1, control_fraction=0)
        assert r["feature"] == "descriptors" and any("by count only" in w for w in r["warnings"])
        got = {800 + f["pool_index"] for f in json.loads((d / "fps" / "manifest.json").read_text())["frames"]}
        assert set(far) <= got, got
        # round-2 hook: a score column replaces FPS (highest first, spacing kept)
        sc = np.full(n, np.nan)
        sc[800:1400] = np.arange(600)                      # P1-600K: later frames score higher
        np.save(d / "score.npy", sc)
        r = hsrv.mlip_select_frames(P, quotas={"P1-600K": 3}, out_dir=str(d / "sc"), holdout=H,
                                    scores=str(d / "score.npy"), volume_bins=1, control_fraction=0)
        t = [f["time_ps"] for f in json.loads((d / "sc" / "manifest.json").read_text())["frames"]]
        assert r["feature"] == "score" and t == [14.95, 13.95, 12.95], t   # 14.975 ps is held out

        # the cluster script's --dry-run json is the contract the tool checks
        script = ROOT / "codes" / "mace" / "finetune" / "scripts" / "mace_descriptors.py"
        dry = subprocess.run([sys.executable, str(script), "--dry-run", "--out", str(d / "x.npy"), *P],
                             capture_output=True, text=True, check=True)
        meta = json.loads(dry.stdout)
        assert [f["frames"] for f in meta["pool"]] == [800, 800, 400]
        assert [f["sha256"] for f in meta["pool"]] == [f["sha256"] for f in rep["pool"]]
        meta["blocks"] = {"Zr": [0, 2], "O": [2, 4]}
        np.save(d / "x.npy", desc)
        (d / "x.json").write_text(json.dumps(meta))
        r = hsrv.mlip_select_frames(P, descriptors=str(d / "x.npy"))
        assert r["feature"] == "descriptors" and not r["warnings"]
        raises(lambda: hsrv.mlip_select_frames(P[::-1], descriptors=str(d / "x.npy")), "computed on")

        # refusals
        np.save(d / "short.npy", desc[:-1])
        raises(lambda: hsrv.mlip_select_frames(P, descriptors=str(d / "short.npy")), "1999", "2000 frames")
        raises(lambda: hsrv.mlip_select_frames(P, quotas={"P1-300K": 16}, out_dir=str(d / "big"), holdout=H),
               "quotas larger", "'P1-300K': quota 16, but only 15")
        assert not (d / "big").exists()
        raises(lambda: hsrv.mlip_select_frames(P, quotas={"P9": 1}, out_dir=str(d / "x")), "not in the pool")
        raises(lambda: hsrv.mlip_select_frames(P, holdout=[{"source": "B*", "last_ps": 5}]), "matches no frame")
        raises(lambda: hsrv.mlip_select_frames(P, quotas=Q, out_dir=str(d / "r1"), holdout=H), "not empty")
        raises(lambda: hsrv.mlip_select_frames(P, strata=["T_K"]), "'T_K'", "missing")
        (d / "notime.extxyz").write_text(Path(P[2]).read_text().replace(" time_ps=", " t_ps="))
        raises(lambda: hsrv.mlip_select_frames([str(d / "notime.extxyz")]), "'time_ps' missing in 400")
        assert hsrv.mlip_select_frames([str(d / "notime.extxyz")], min_spacing_ps=0)["frames"] == 400

    # the six real UiO-66 frames: a pool from their subsys.inc, the labels found again by geometry
    lab = ROOT / "tests" / "fixtures" / "cp2k_labels"
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        items, text = [], []
        for k, o in enumerate(sorted(lab.glob("*.out.gz"))):
            stem = o.name[:-len(".out.gz")]
            (d / f"{stem}.out").write_bytes(gzip.decompress(o.read_bytes()))
            sub = gzip.decompress((lab / f"{stem}.subsys.inc.gz").read_bytes()).decode()
            (d / f"{stem}.subsys.inc").write_text(sub)
            items.append({"out": str(d / f"{stem}.out"), "subsys": str(d / f"{stem}.subsys.inc")})
            s = clab.read_subsys(sub)
            pos = np.array(s["positions"])
            if k == 0:
                pos[0] += s["cell"][0]                         # wrapped differently in the pool
            text.append(f"{len(pos)}\nLattice=\"{' '.join(f'{v:.6f}' for r in s['cell'] for v in r)}\" "
                        f"Properties=species:S:1:pos:R:3 source=O-{stem} frame={k} time_ps={k * 2.0} "
                        f"config_type=uio66 pbc=\"T T T\"\n"
                        + "".join(f"{e} {x:.6f} {y:.6f} {z:.6f}\n" for e, (x, y, z) in zip(s["symbols"], pos)))
        (d / "pool.extxyz.gz").write_bytes(gzip.compress("".join(text).encode()))
        hsrv.cp2k_make_labels(items, str(d / "labels.extxyz"))
        pool = [str(d / "pool.extxyz.gz")]
        rep = hsrv.mlip_select_frames(pool, volume_bins=3, strata=[])
        assert rep["strata"]["all"]["volume_A3"][0] < 4810 and rep["strata"]["all"]["volume_A3"][1] > 9035
        assert [b["eligible"] for b in rep["strata"]["all"]["bins"]] == [1, 2, 3]    # 4805 | 6503 6839 | 7794 8118 9040
        r = hsrv.mlip_select_frames(pool, quotas={"all": 3}, out_dir=str(d / "sel"), volume_bins=3, strata=[],
                                    control_fraction=0)
        assert r["by_stratum"]["all"]["by_volume_bin"] == [1, 1, 1], r["by_stratum"]
        ex = hsrv.mlip_select_frames(pool, exclude=[str(d / "labels.extxyz")], volume_bins=3, strata=[])
        assert ex["excluded"]["by_geometry"] == 6 and ex["excluded"]["not_in_pool"] == 0, ex["excluded"]
        assert ex["strata"]["all"]["eligible"] == 0
