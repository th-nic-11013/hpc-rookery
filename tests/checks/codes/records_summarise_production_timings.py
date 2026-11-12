import os
import tempfile
from pathlib import Path
from unittest.mock import patch

LABEL = 'production timings go in as one summary record per layout (G36)'


def records_summarise_production_timings():
    """G36 follow-up: a per-frame table of production timings becomes one
    `benchmarks` record per layout group, holding the statistics in
    `samples`, not one record per frame. A synthetic VASP table with two
    layouts (NCORE 4 x 12 frames, NCORE 12 x 3) and frames of a second
    system gives two summaries: the statistics of s per SCF step match
    numpy, metric.value / extra.scf_steps is the mean, the 3-frame group is
    warned about, the other system's frames and a row without SCF steps
    are left out. Both validate and round-trip through records_append /
    records_query in a temporary store. A CP2K record without `samples`
    still validates; a summary whose metric does not follow from its
    statistics, or whose statistics are out of order, is refused; a table
    of several systems without frame_prefix is refused."""
    import numpy as np
    from hpc_codes import records, server
    from hpc_codes.records import RecordsError

    rng = np.random.default_rng(36)
    rows, truth = [], {}
    for ncore, n, rate in ((4, 12, 70.0), (12, 3, 55.0)):
        nscf = rng.integers(40, 90, n)
        loop = nscf * rate * rng.uniform(0.9, 1.1, n)
        truth[ncore] = (loop / nscf, nscf)
        rows += [f"Si64/f{ncore:02d}{i:03d}\t48\t1\t1\t1\t{ncore}\t5\t612\t{s}\t{t!r}"
                 for i, (s, t) in enumerate(zip(nscf.tolist(), loop.tolist()))]
    rows += ["Si64/bad\t48\t1\t1\t1\t4\t5\t612\t0\t",            # no SCF: left out
             "Ge64/f001\t48\t1\t1\t1\t4\t5\t612\t50\t9999.0"]       # another system
    header = "frame\tranks\tthreads\tnodes\tkpar\tncore\tnkpts\tnbands\tnscf\tloop"

    build = {"build_id": "vasp-6.4.3-std-cpu-intel", "facility": "hokusai", "code": "vasp",
             "version": "6.4.3", "variant": {"exe": "std", "target": "cpu"},
             "toolchain": "Intel oneAPI 2024, Intel MPI, MKL", "install_path": "~/vasp/6.4.3",
             "env_script": "~/vasp/6.4.3/env.sh",
             "test_status": {"level": "smoke", "result": "pass"}, "default": True,
             "date": "2026-11-12"}
    base = {"partition": "mpc", "node_type": "MPC test", "code": "vasp", "version": "6.4.3",
            "build_id": build["build_id"],
            "system": {"label": "Si-64", "atoms": 64, "elements": {"Si": 64},
                       "periodic": "xyz", "kpoints": "3x3x1"},
            "method": {"functional": "PBE", "basis": "PAW_PBE 54", "cutoff_ry": 38.2,
                       "hybrid": False, "scf": "Normal, EDIFF 1e-6"}}
    cp2k = {"id": "hokusai-cp2k-2026-11-12-si64-aimdstep-1n-16x7", "facility": "hokusai",
            "partition": "mpc", "node_type": "MPC test", "code": "cp2k", "version": "2026.2",
            "build_id": "cp2k-ext", "build_external": True, "build_desc": "GCC, MKL",
            "system": base["system"], "method": {**base["method"], "basis": "DZVP"},
            "run_type": "aimd_step", "nodes": 1, "ranks_per_node": 16, "threads_per_rank": 7,
            "metric": {"name": "s_per_step", "value": 12.5}, "date": "2026-11-12", "valid": True}

    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as store, \
            patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": store}):
        table = Path(tmp) / "perf.tsv"
        table.write_text("\n".join([header, *rows]) + "\n")
        try:
            server.records_summarise_timings("hokusai", str(table), base=base)
        except ValueError as e:
            assert "frame_prefix" in str(e) and "Ge64" in str(e), e
        else:
            raise AssertionError("pooled the frames of two systems")

        server.records_append("hokusai", "software_registry", build)
        args = dict(facility="hokusai", table_path=str(table), base=base,
                    frame_prefix="Si64/", date="2026-11-12")
        dry = server.records_summarise_timings(**args, dry_run=True)
        assert dry["appended"] is False and len(dry["records"]) == 2, dry
        assert sorted(os.listdir(store)) == ["software_registry.yaml"], "a dry run wrote records"
        assert any("1 row(s) without SCF" in w for w in dry["warnings"]), dry["warnings"]
        tiny = [w for w in dry["warnings"] if "only 3 run(s)" in w]
        assert len(tiny) == 1 and "c12" in tiny[0], dry["warnings"]

        out = server.records_summarise_timings(**args)
        assert out["appended"] and not out["check_errors"], out["check_errors"]
        for ncore, (per, nscf) in truth.items():
            hits = server.records_query("hokusai", "benchmarks",
                                        {"samples.quantity": "s_per_scf_step", "layout.NCORE": ncore})
            assert len(hits) == 1 and hits[0]["_scope"] == "user", hits
            r = hits[0]
            s = r["samples"]
            assert r["id"] == f"hokusai-vasp-2026-11-12-si64-singlepoint-summary-1n-48x1-k1c{ncore}", r["id"]
            assert r["layout"] == {"KPAR": 1, "NCORE": ncore} and r["ranks_per_node"] == 48, r
            assert s["n"] == len(per) and s["source"] == "perf.tsv", s
            want = {"mean": per.mean(), "std": per.std(ddof=1), "min": per.min(),
                    "median": np.median(per), "max": per.max()}
            for k, v in want.items():
                assert abs(s[k] - v) < 5e-4, (ncore, k, s[k], v)
            assert s["scf_steps"] == {"min": int(nscf.min()), "median": float(np.median(nscf)),
                                      "max": int(nscf.max())}, s["scf_steps"]
            assert r["extra"]["scf_steps"] == round(float(np.median(nscf))), r["extra"]
            assert abs(r["metric"]["value"] / r["extra"]["scf_steps"] - per.mean()) < 1e-3, r["metric"]
            assert r["metric"]["name"] == "wall_s" and r["run_type"] == "single_point", r

        assert not records.errors("hokusai", "benchmarks", cp2k), records.errors("hokusai", "benchmarks", cp2k)
        good = {k: v for k, v in out["records"][0].items()}
        refused = [
            ({**good, "id": "x1", "metric": {"name": "wall_s", "value": good["metric"]["value"] * 1.1}},
             "is not samples.mean x extra.scf_steps"),
            ({**good, "id": "x2", "samples": {**good["samples"], "min": good["samples"]["max"] + 1}},
             "min <= median, mean <= max"),
            ({**good, "id": "x3", "extra": {}}, "needs extra.scf_steps"),
            ({**good, "id": "x4", "samples": {**good["samples"], "n": "many"}}, "'many' is not int"),
        ]
        for rec, why in refused:
            try:
                server.records_append("hokusai", "benchmarks", rec)
            except RecordsError as e:
                assert why in str(e), (why, str(e))
            else:
                raise AssertionError(f"appended a bad summary: {why}")
        try:
            server.records_summarise_timings(**args)
        except RecordsError as e:
            assert "already exists" in str(e), e
        else:
            raise AssertionError("summarised the same table twice under the same ids")
        assert len(server.records_query("hokusai", "benchmarks", {"code": "vasp"})) == 2
        assert not server.records_check("hokusai")["errors"]
