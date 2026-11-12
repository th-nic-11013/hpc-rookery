import os
import pathlib
import stat
import subprocess
import tarfile
import tempfile
from checks._common import ROOT

LABEL = 'cp2k label batch: inputs, packed jobs, collector (G51)'

FIX = ROOT / "tests" / "fixtures"
KINDS = {"files": {"basis": ["BASIS_MOLOPT_UZH_2026.2"], "potential": "POTENTIAL_UZH_2026.2"},
         "kinds": {e: {"q": q, "basis": f"DZVP-MOLOPT-SCAN-GTH-q{q}", "potential": f"GTH-SCAN-q{q}"}
                   for e, q in {"H": 1, "C": 4, "O": 6, "Zr": 12}.items()}, "errors": {}}
# launch_plan's answer for 456-atom UiO-66 r2SCAN on HOKUSAI (record 2026-11-05, 280 s, 1 node 16x7)
PLAN = {"build": {"build_id": "cp2k-2026.2-psmp-cpu-gcc"},
        "layout": {"nodes": 1, "ranks_per_node": 16, "threads_per_rank": 7},
        "basis": {"match": "exact"},
        "job_spec": {"pre_launch": "source ~/software/cp2k/cp2k_env.sh",
                     "launcher": "srun --mpi=pmix --cpus-per-task=7 --cpu-bind=cores "
                                 "--distribution=block:block",
                     "environment": {"OMP_NUM_THREADS": "7", "OMP_PLACES": "cores",
                                     "OMP_PROC_BIND": "close", "PMIX_MCA_psec": "native"},
                     "resources": {"node_count": 1, "processes_per_node": 16,
                                   "cpu_cores_per_process": 7},
                     "attributes": {"queue_name": "mpc"}},
        "estimate": {"metric": "wall_s", "value": 280, "rough": False}}


def _gz(p):
    import gzip
    return gzip.decompress(p.read_bytes()).decode()


def _frame_xyz(inc: str, config_type: str) -> str:
    from hyogo.cp2k import labels as hl
    s = hl.read_subsys(inc)
    return (f"{len(s['symbols'])}\nLattice=\"" + " ".join(f"{v:.8f}" for r in s["cell"] for v in r) +
            f"\" Properties=species:S:1:pos:R:3 config_type={config_type} pbc=\"T T T\"\n" +
            "\n".join(f"{e} {x:.8f} {y:.8f} {z:.8f}" for e, (x, y, z) in zip(s["symbols"], s["positions"]))
            + "\n")


def _refuses(fn, msg):
    try:
        fn()
    except ValueError as e:
        assert msg in str(e), (msg, e)
    else:
        raise AssertionError(f"accepted: {msg}")


def cp2k_batch_writes_packs_and_collects_labels():
    """G51: three real r2SCAN frames (tests/fixtures/cp2k_labels: g1, g2, g3)
    -> cp2k_make_label_batch writes one folder per frame with the same
    label input (the E1 r2SCAN settings of the HOKUSAI reference input in
    tests/fixtures/cp2k_label_batch, D3 dropped) and refuses a selection
    whose sha256 doesn't match; cp2k_pack_jobs packs them from
    launch_plan's timing into specs that render for HOKUSAI; the pack
    script, run here with a stub cp2k.psmp, skips finished frames, reruns
    an unfinished one and stops when its time is out; cp2k_collect_labels
    reads the tar the documented fs_compress pattern makes, sorts
    finished / unconverged / wrong run type / unfinished / missing, and
    its `frames` go straight into cp2k_make_labels."""
    import json
    from hyogo import server as hsrv
    from hyogo.cp2k import batch as hb
    from hpc_codes import server as csrv
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        names = ["g1", "g2", "g3"]
        incs = {n: _gz(FIX / "cp2k_labels" / f"{n}.subsys.inc.gz") for n in names}
        outs = {n: _gz(FIX / "cp2k_labels" / f"{n}.out.gz") for n in names}
        xyz = d / "sel.extxyz"
        xyz.write_text("".join(_frame_xyz(incs[n], n) for n in names))
        from hyogo.mace import dataset
        fr = dataset.read_frames(xyz.read_text())
        sel = d / "sel.json"
        sel.write_text(json.dumps({"frames": [{"sha256": hb.sha256(f["text"]), "pool_index": i * 7}
                                              for i, f in enumerate(fr)]}))

        # --- 1. batch input writer
        b = d / "batch"
        r = hsrv.cp2k_make_label_batch(str(xyz), KINDS, str(b), selection_path=str(sel))
        assert r["frames"] == 3 and r["natoms"] == [456] and r["check_input"] == "f0000/label.inp", r
        assert r["config_types"] == names and r["settings"]["d3"] == "none", r
        assert any("no D3" in w for w in r["warnings"]), r["warnings"]
        man = json.loads((b / "batch.json").read_text())
        assert [f["selection"] for f in man["frames"]] == [{"pool_index": 0}, {"pool_index": 7},
                                                           {"pool_index": 14}], man["frames"]
        inps = [(b / f"f{i:04d}" / "label.inp").read_text() for i in range(3)]
        assert inps[0] == inps[1] == inps[2] and hb.sha256(inps[0]) == r["input_sha256"]
        # each subsys.inc is cp2k_make_subsys' text for that frame
        for i, n in enumerate(names):
            one = d / f"{n}.xyz"
            one.write_text(_frame_xyz(incs[n], n))
            s = hsrv.cp2k_make_subsys(str(one), KINDS, str(d / f"{n}.inc"))
            want = pathlib.Path(s["out"]).read_text().splitlines()[2:]   # past the "from <file>" line
            assert (b / f"f{i:04d}" / "subsys.inc").read_text().splitlines()[2:] == want, n
        # the same settings as the E1 HOKUSAI input (sp-g1-r2scan.inp, job 9749330) minus D3
        ref = (FIX / "cp2k_label_batch" / "sp-g1-r2scan.inp").read_text()
        body = lambda t: [l.strip() for l in t.splitlines() if l.strip() and not l.startswith("#")]
        skip = ("@SET PROJECT", "@SET WALLTIME")
        print_only = {"&PRINT", "&END PRINT", "&RESTART ON", "&END RESTART", "&MO_CUBES",
                      "NHOMO 1", "NLUMO 1", "WRITE_CUBE FALSE", "&END MO_CUBES"}
        d3 = {"&VDW_POTENTIAL", "POTENTIAL_TYPE PAIR_POTENTIAL", "&PAIR_POTENTIAL",
              "TYPE                 DFTD3(BJ)", "PARAMETER_FILE_NAME  dftd3.dat",
              "REFERENCE_FUNCTIONAL R2SCAN", "&END PAIR_POTENTIAL", "&END VDW_POTENTIAL"}
        keep = lambda ls: [l for l in ls if not l.startswith(skip) and l not in d3
                           and l not in print_only]
        assert keep(body(ref)) == keep(body(inps[0])), \
            set(keep(body(ref))) ^ set(keep(body(inps[0])))
        for need in ("@SET CUTOFF        1000", "@SET REL_CUTOFF    60", "EPS_DEFAULT 1.0E-12",
                     "BASIS_SET_FILE_NAME BASIS_MOLOPT_UZH_2026.2", "@SET PROJECT       label",
                     "@SET WALLTIME      3000"):
            assert need in inps[0], need
        assert "DFTD3" not in inps[0] and "guess.wfn" not in inps[0]
        # guess="previous": restart from guess.wfn, write the wavefunction
        rg = hsrv.cp2k_make_label_batch(str(xyz), KINDS, str(d / "bg"), guess="previous")
        gi = (d / "bg" / "f0000" / "label.inp").read_text()
        assert "WFN_RESTART_FILE_NAME guess.wfn" in gi and "SCF_GUESS RESTART" in gi, gi
        assert rg["settings"]["guess"] == "previous"
        # refusals
        bad = d / "bad.json"
        bad.write_text(json.dumps({"frames": [{"sha256": "0" * 64}] * 3}))
        _refuses(lambda: hsrv.cp2k_make_label_batch(str(xyz), KINDS, str(d / "x"),
                                                    selection_path=str(bad)), "does not match")
        bad.write_text(json.dumps({"frames": [{"sha256": "0" * 64}] * 2}))
        _refuses(lambda: hsrv.cp2k_make_label_batch(str(xyz), KINDS, str(d / "x"),
                                                    selection_path=str(bad)), "lists 2 frames")
        _refuses(lambda: hsrv.cp2k_make_label_batch(str(xyz), KINDS, str(b)), "already holds")
        _refuses(lambda: hsrv.cp2k_make_label_batch(str(xyz), KINDS, str(d / "x"), guess="last"),
                 "guess is")
        _refuses(lambda: hsrv.cp2k_make_label_batch(str(xyz), KINDS["kinds"], str(d / "x")),
                 "full result")

        # --- 2. packed jobs
        p = csrv.cp2k_pack_jobs("hokusai", str(b), "~/agent/work/cp2k-runs/g51", PLAN)
        assert p["jobs"] == 1 and p["frames_per_job"] == 3 and p["budget_s"] == 420, p
        assert p["duration"] == "00:30:00" and p["check_input"].endswith("f0000/label.inp"), p
        spec = p["packs"][0]["spec"]
        assert spec["executable"] == "bash pack-01.sh" and "launcher" not in spec, spec
        assert spec["directory"] == "~/agent/work/cp2k-runs/g51", spec
        from hpc_agent_core.models import JobSpec
        from facilities.registry import get_backend
        backend = get_backend("hokusai")
        # the account is the user's (config); a placeholder project here, as the facility example
        js = JobSpec(**{**spec, "attributes": {**spec["attributes"], "account": "RB99999"}})
        backend.apply_defaults(js)
        backend.validate_spec(js)
        script = backend.render_script(js)
        assert "bash pack-01.sh" in script and "#SBATCH" in script and "mpc" in script, script
        drv = (b / "pack-01.sh").read_text()
        assert "srun --mpi=pmix --cpus-per-task=7" in drv and "f0000 f0001 f0002" in drv, drv
        # K from the job length; frames_per_job fixes it; a second call numbers after the first
        many = csrv.cp2k_pack_jobs("hokusai", str(b), "~/agent/work/cp2k-runs/g51", PLAN,
                                   frames_per_job=1, frames=["f0001", "f0002"])
        assert [q["script"] for q in many["packs"]] == ["pack-02.sh", "pack-03.sh"], many
        assert len(json.loads((b / "packs.json").read_text())["packs"]) == 3
        _refuses(lambda: csrv.cp2k_pack_jobs("hokusai", str(b), "~/agent/work/x", PLAN,
                                             frames_per_job=300), "at most 204")
        _refuses(lambda: csrv.cp2k_pack_jobs("hokusai", str(b), "~/agent/work/x",
                                             {**PLAN, "estimate": None}), "frame_s")
        _refuses(lambda: csrv.cp2k_pack_jobs("hokusai", str(b), "~/agent/work/x", PLAN,
                                             frames=["f9999"]), "not in this batch")
        w = csrv.cp2k_pack_jobs("hokusai", str(b), "~/runs/x", PLAN, frame_s=2500, overwrite=True)
        assert any("WALLTIME" in x for x in w["warnings"]), w["warnings"]
        assert any("agent/work" in x for x in w["warnings"]), w["warnings"]
        assert sorted(s.name for s in b.glob("pack-*.sh")) == ["pack-01.sh"]
        p = csrv.cp2k_pack_jobs("hokusai", str(b), "~/agent/work/cp2k-runs/g51", PLAN, overwrite=True)

        # --- 3. run the pack here: a stub cp2k.psmp copies the HOKUSAI .out of each frame,
        # f0001 ends unconverged (its outer SCF loop marked failed), f0002 is killed once
        stub_dir = d / "bin"
        stub_dir.mkdir()
        canned = d / "canned"
        canned.mkdir()
        for i, n in enumerate(names):
            t = outs[n].replace("sp-g1-r2scan", "label").replace(f"sp-{n}-r2scan", "label")
            if i == 1:
                t = t.replace("outer SCF loop converged in", "outer SCF loop FAILED to converge after")
                assert "FAILED to converge" in t
            (canned / f"f{i:04d}.out").write_text(t)
        stub = stub_dir / "cp2k.psmp"
        stub.write_text("#!/bin/bash\n# stub: -i label.inp -o label.out\n"
                        "f=$(basename \"$PWD\")\necho run $f >> ../calls\n"
                        "touch label-RESTART.wfn label-RESTART.wfn.bak-1\n"
                        "if [ \"$f\" = f0002 ] && [ ! -e ../killed ]; then touch ../killed; "
                        "head -100 \"$CANNED/$f.out\" > \"$4\"; exit 137; fi\n"
                        "cp \"$CANNED/$f.out\" \"$4\"\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        drv = (b / "pack-01.sh").read_text().replace(
            f"LAUNCH={PLAN['job_spec']['launcher']!r}", "LAUNCH=''")
        assert "LAUNCH=''" in drv, "the launcher line moved"
        (b / "pack-01.sh").write_text(drv)
        env = {**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}", "CANNED": str(canned)}
        run = lambda: subprocess.run(["bash", "pack-01.sh"], cwd=b, env=env, capture_output=True,
                                     text=True, timeout=60)
        (b / "f0000" / "label.out").write_text(outs["g1"].replace("sp-g1-r2scan", "label"))
        assert run().returncode == 0
        calls = (b / "calls").read_text().split()
        assert calls == ["run", "f0001", "run", "f0002"], calls   # f0000 had finished: skipped
        assert not list(b.glob("f*/label-RESTART.wfn*")), "wavefunctions must not pile up"
        log = (b / "pack-01.log").read_text()
        assert "f0000 skip: finished" in log and "f0002 rc=137 unfinished" in log, log
        # --- 4. collect from the documented download: tar of label.out files and the pack log
        arch = d / "outs.tar.gz"
        with tarfile.open(arch, "w:gz") as tar:
            for f in ("f0000/label.out", "f0001/label.out", "f0002/label.out", "pack-01.log"):
                tar.add(b / f, arcname=f"lustre/home/u/agent/work/cp2k-runs/g51/{f}")
            tar.add(b / "f0000" / "label.inp", arcname="evil/../../label.out")
        local = d / "local"
        import shutil
        shutil.copytree(b, local, ignore=shutil.ignore_patterns("label.out", "pack-01.log"))
        c = hsrv.cp2k_collect_labels(str(local), str(arch))
        st = {s["name"]: s["status"] for s in c["states"]}
        assert st == {"f0000": "finished", "f0001": "unconverged", "f0002": "unfinished"}, c["states"]
        assert c["resubmit"] == {"rerun": ["f0002"], "fix": ["f0001"]}, c["resubmit"]
        assert c["unpacked"] == ["f0000", "f0001", "f0002", "pack-01.log"], c["unpacked"]
        assert not (d / "label.out").exists() and (local / "pack-01.log").is_file()
        assert c["frames"] == [{"out": f"{local}/f0000/label.out",
                                "subsys": f"{local}/f0000/subsys.inc", "config_type": "g1"}], c
        assert c["wall_s"]["n"] == 1 and c["wall_s"]["mean"] > 100, c["wall_s"]
        # resubmitting the same pack reruns only the killed frame (the unconverged one ended)
        assert run().returncode == 0
        assert (b / "calls").read_text().split()[-2:] == ["run", "f0002"]
        assert (b / "f0002" / "label.out.prev").is_file()
        for f in ("f0002",):
            shutil.copy(b / f / "label.out", local / f / "label.out")
        c = hsrv.cp2k_collect_labels(str(local))
        assert c["counts"]["finished"] == 2 and c["resubmit"] == {"rerun": [], "fix": ["f0001"]}, c
        lab = hsrv.cp2k_make_labels(c["frames"], str(d / "labels.extxyz"), head="r2scan")
        assert lab["frames"] == 2 and lab["natoms"] == [456, 456] and lab["stress"], lab
        txt = (d / "labels.extxyz").read_text()
        assert [l.split("config_type=")[1].split()[0].strip("\"") for l in txt.splitlines() if "config_type=" in l] == ["g1", "g3"], txt[:300]
        # missing, wrong run type, and a subsys.inc changed after writing
        (local / "f0002" / "label.out").unlink()
        geo = outs["g1"].replace("sp-g1-r2scan", "label").replace(
            "GLOBAL| Run type                                                   ENERGY_FORCE",
            "GLOBAL| Run type                                                        GEO_OPT")
        assert "GEO_OPT" in geo
        (local / "f0000" / "label.out").write_text(geo)
        st = {s["name"]: s["status"] for s in hsrv.cp2k_collect_labels(str(local))["states"]}
        assert st == {"f0000": "wrong_run_type", "f0001": "unconverged", "f0002": "missing"}, st
        with open(local / "f0001" / "subsys.inc", "a") as fh:
            fh.write("# edited\n")
        _refuses(lambda: hsrv.cp2k_collect_labels(str(local)), "changed since")
        _refuses(lambda: hsrv.cp2k_collect_labels(str(d)), "batch.json is missing")
