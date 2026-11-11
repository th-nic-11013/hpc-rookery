import pathlib
import tempfile
from checks._common import ROOT

LABEL = 'hyogo parse_output reads the HOKUSAI runs'


def hyogo_parse_output_reads_the_hokusai_runs():
    """hyogo's cp2k_parse_output gives the values read by hand from four
    HOKUSAI UiO-66 outputs (tests/fixtures/cp2k_outputs/README.md),
    agrees with rookery's parse_bench on energy and forces, flags a cut
    cell_opt as unfinished, and refuses a missing or non-CP2K file."""
    import gzip
    from hyogo import server as hsrv
    from hpc_codes.cp2k import parse_bench
    root = ROOT
    fx = root / "tests" / "fixtures" / "cp2k_outputs"
    # hand-read values, 2026-11-06 (notes kept outside the repo)
    want = {
        "r2scan-sp": dict(run_type="ENERGY_FORCE", energy_ha=-4331.274668682077, gap_ev=3.363827,
                          fmax=8.10922350e-3, trace=-1.21111259818, steps=[55], outer=[2],
                          cp2k_s=279.886, hfx=None),
        "pbe0-sp": dict(run_type="ENERGY_FORCE", energy_ha=-4334.544226923467, gap_ev=4.664273,
                        fmax=1.01534426e-2, trace=-0.955742000377, steps=[6], outer=[1],
                        cp2k_s=581.987, hfx=32019),
        "hse06-sp": dict(run_type="ENERGY_FORCE", energy_ha=-4335.04533898211, gap_ev=3.986995,
                         fmax=None, trace=-0.836146728794, steps=[8], outer=[1],
                         cp2k_s=463.925, hfx=18501),
        "hse06-cellopt": dict(run_type="CELL_OPT", energy_ha=-4335.056972776092, gap_ev=4.034722,
                              fmax=None, trace=None, steps=None, outer=None,
                              cp2k_s=8886.337, hfx=18952),
    }
    close = lambda a, b, tol=1e-9: abs(a - b) <= tol * max(1.0, abs(b))
    with tempfile.TemporaryDirectory() as d:
        for name, w in want.items():
            out = pathlib.Path(d) / f"{name}.out"
            out.write_bytes(gzip.decompress((fx / f"{name}.out.gz").read_bytes()))
            r = hsrv.cp2k_parse_output(str(out))
            assert r["finished"] and r["run_type"] == w["run_type"], (name, r["run_type"])
            assert r["natoms"] == 456 and r["electrons"] == 1936, name
            assert r["started"] == "2026-11-05" and r["threads"] == 7, (name, r["started"])
            assert r["ranks"] == (16 if name == "r2scan-sp" else 32), (name, r["ranks"])
            assert close(r["energy_ha"], w["energy_ha"]) and close(r["gap_ev"], w["gap_ev"]), name
            assert r["scf"]["not_converged"] == 0, name
            if w["steps"] is not None:
                assert r["scf"]["steps"] == w["steps"] and r["scf"]["outer"] == w["outer"], r["scf"]
            if w["fmax"] is not None:
                assert close(r["forces"]["max"], w["fmax"], 1e-6), (name, r["forces"])
            if w["trace"] is not None:
                assert close(r["stress_gpa"]["third_trace"], w["trace"]), name
            assert close(r["cost"]["cp2k_s"], w["cp2k_s"]), name
            assert (r["cost"]["hfx"] or {}).get("eri_ram_mib") == w["hfx"], (name, r["cost"]["hfx"])
            # rookery's benchmark parser reads the same energies and forces
            b = parse_bench.parse_out(out)
            assert b["done"] and close(r["energy_ha"], b["energies"][-1]), name
            if r["forces"]:
                comps = b["forces"]
                fmax = max((comps[i] ** 2 + comps[i + 1] ** 2 + comps[i + 2] ** 2) ** 0.5
                           for i in range(0, len(comps), 3))
                assert close(r["forces"]["max"], fmax), (name, r["forces"]["max"], fmax)
            if name == "hse06-cellopt":
                o, c = r["opt"], r["cell"]
                assert o["converged"] and o["steps"] == 21 and not o["max_iter_hit"], o
                assert close(o["internal_pressure_bar"], 7.3393678606), o
                assert close(o["criteria"]["max_gradient"]["value"], 8.45438e-5), o["criteria"]
                assert o["criteria"]["max_gradient"]["limit"] == 1e-4, o["criteria"]
                assert all(v["converged"] for v in o["criteria"].values()), o["criteria"]
                assert close(c["a"], 20.831819) and c["a"] == c["b"] == c["c"], c
                assert c["alpha"] == c["beta"] == c["gamma"] == 90.0, c
                assert len(r["scf"]["steps"]) == 23, r["scf"]
                assert any("hfx_types.F:762" in x and "14.279 A" in x for x in r["warnings"]), \
                    r["warnings"]
                assert not any("RESTART" in x for x in r["warnings"]), r["warnings"]
                # cut mid-optimisation: no PROGRAM ENDED, not converged
                text = out.read_text().splitlines()
                cut = next(i for i, l in enumerate(text) if "OPTIMIZATION STEP:     10" in l)
                part = pathlib.Path(d) / "cut.out"
                part.write_text("\n".join(text[:cut]) + "\n")
                p = hsrv.cp2k_parse_output(str(part))
                assert not p["finished"] and not p["opt"]["converged"], p["opt"]
                assert p["opt"]["steps"] == 9, p["opt"]
                assert any("no PROGRAM ENDED" in x for x in p["warnings"]), p["warnings"]
                # finished but not converged (as after WALLTIME): the restart switch
                stop = pathlib.Path(d) / "stop.out"
                stop.write_text("\n".join(text[:cut] + [" **** **** ******  **  PROGRAM ENDED AT"]))
                p = hsrv.cp2k_parse_output(str(stop))
                assert any("@SET RESTART 1" in x for x in p["warnings"]), p["warnings"]
        for args, text in (((str(pathlib.Path(d) / "missing.out"),), "fs_download"),
                           ((str(root / "hyogo" / "INVENTORY.md"),), "not a CP2K output")):
            try:
                hsrv.cp2k_parse_output(*args)
            except ValueError as e:
                assert text in str(e), e
            else:
                raise AssertionError(f"cp2k_parse_output accepted {args}")
