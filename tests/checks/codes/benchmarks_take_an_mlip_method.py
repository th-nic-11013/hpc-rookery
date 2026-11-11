import os
import tempfile
from unittest.mock import patch

LABEL = 'benchmarks take an MLIP method form (G37)'


def benchmarks_take_an_mlip_method():
    """G37: a benchmark of an ML potential records method.model (and
    precision, interface, device) instead of the DFT fields, with
    run_type md_step and kpoints 'none'; a DFT record still needs its
    fields; a record mixing both forms is refused; launch_plan's grouping
    key reads either form."""
    from hpc_codes import launch, records
    mlip = {"id": "hokusai-lammps-2026-10-20-uio66conv-mdstep-macemp0m-f32-1n-1x56",
            "facility": "hokusai", "partition": "mpc", "node_type": "MPC test",
            "code": "lammps", "version": "4 Jul 2026", "build_id": "lammps-x",
            "build_external": True, "build_desc": "test",
            "system": {"label": "UiO-66-conv", "atoms": 456,
                       "elements": {"Zr": 24, "O": 128, "C": 192, "H": 112},
                       "periodic": "xyz", "kpoints": "none"},
            "method": {"model": "MACE-MP-0 medium", "precision": "f32",
                       "interface": "LAMMPS pair_style metatomic", "device": "cpu"},
            "run_type": "md_step", "nodes": 1, "ranks_per_node": 1, "threads_per_rank": 56,
            "metric": {"name": "s_per_step", "value": 0.2723},
            "extra": {"ns_per_day": 0.1587, "nve_drift_meV_atom_ps": 0.017, "concurrent_runs": 2},
            "date": "2026-10-20", "valid": True}
    with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}):
        assert not records.errors("hokusai", "benchmarks", mlip), \
            records.errors("hokusai", "benchmarks", mlip)
        dft_only = {**mlip, "method": {"dispersion": "D3"}}
        errs = records.errors("hokusai", "benchmarks", dft_only)
        for name in ("functional", "basis", "cutoff_ry", "hybrid", "scf"):
            assert any(f"missing required {name} (or set model)" in e for e in errs), (name, errs)
        mixed = {**mlip, "method": {**mlip["method"], "functional": "PBE"}}
        errs = records.errors("hokusai", "benchmarks", mixed)
        assert any("a model and DFT fields ['functional']" in e for e in errs), errs
        bad = {**mlip, "method": {**mlip["method"], "precision": "f16"}}
        assert any("not in enum[f32|f64]" in e for e in records.errors("hokusai", "benchmarks", bad))
    assert launch._method_key(mlip) == ("UiO-66-conv", "MACE-MP-0 medium", "f32",
                                        "LAMMPS pair_style metatomic", "cpu", "4 Jul 2026")
    dft = {**mlip, "method": {"functional": "PBE", "basis": "DZVP", "cutoff_ry": 400,
                              "hybrid": False, "scf": "OT"}}
    assert launch._method_key(dft)[1:3] == ("PBE", "DZVP")
