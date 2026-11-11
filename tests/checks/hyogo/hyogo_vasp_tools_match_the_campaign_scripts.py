import pathlib
import tempfile
from checks._common import ROOT

LABEL = 'hyogo VASP tools match the campaign scripts'


def hyogo_vasp_tools_match_the_campaign_scripts():
    """hyogo's vasp_make_incar writes the settings the MXene campaign's
    make_magmom_test2.py wrote (tests/fixtures/vasp_inputs/README.md) and
    refuses the mistakes that cost runs (MAGMOM count, ISPIN 1, NBANDS
    vs band groups); vasp_parse_output reads two real VASP 6.4.3 runs
    (tests/fixtures/vasp_outputs/README.md) with the values read by
    hand, flags the NELM one as not converged, and survives NUL holes
    and a cut file."""
    import gzip
    import shutil
    from hyogo import server as hsrv
    from hyogo.vasp.incar import parse as incar_parse
    root = ROOT
    fi = root / "tests" / "fixtures" / "vasp_inputs"
    fo = root / "tests" / "fixtures" / "vasp_outputs"
    settings_of = lambda p: {k: v for k, v in incar_parse(pathlib.Path(p).read_text()).items()
                             if k != "SYSTEM"}
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        shutil.copy(fi / "POSCAR", d / "POSCAR")
        base, poscar = str(fi / "INCAR.label"), str(d / "POSCAR")
        lay = {"ranks": 48, "ncore": 12}
        # --- the three cases of the campaign script, setting for setting
        (d / "rel").mkdir()
        r = hsrv.vasp_make_incar(poscar, base, settings={"NELM": 300, "LORBIT": 10},
                                 magmom={"Cr": 3.0}, parallel=lay, nbands=660,
                                 pre={"nupdown": 64}, out_path=str(d / "rel" / "INCAR"))
        assert settings_of(r["out"]) == settings_of(fi / "expected_rel.INCAR"), r["changed"]
        assert settings_of(r["pre_out"]) == settings_of(fi / "expected_rel.INCAR.pre"), r["pre"]
        assert r["magmom"] == "1*0 2*3 7*0" and r["total_magmom"] == 6 and r["band_groups"] == 4, r
        assert any("ISTART 1" in w for w in r["warnings"]), r["warnings"]
        r = hsrv.vasp_make_incar(poscar, base, settings={"NELM": 300, "LORBIT": 10},
                                 magmom={"Cr": 2.5}, nupdown=60, parallel=lay, nbands=660,
                                 out_path=str(d / "nup.INCAR"))
        assert settings_of(r["out"]) == settings_of(fi / "expected_nup.INCAR"), r["changed"]
        assert any("fixes the total moment" in w for w in r["warnings"]), r["warnings"]
        damped = {"AMIX": 0.2, "BMIX": 1.0, "AMIN": 0.01, "AMIX_MAG": 0.4, "BMIX_MAG": 1.0}
        r = hsrv.vasp_make_incar(poscar, base, settings={"NELM": 300, "LORBIT": 10, **damped},
                                 magmom={"Cr": 1.5}, parallel=lay, nbands=660,
                                 out_path=str(d / "damp.INCAR"))
        assert settings_of(r["out"]) == settings_of(fi / "expected_damp.INCAR"), r["changed"]
        assert not r["warnings"], r["warnings"]
        # a flipped site, a removed key, NBANDS from an OUTCAR, unpinned NBANDS
        r = hsrv.vasp_make_incar(poscar, base, magmom={"Cr": 3}, magmom_sites={"3": -3},
                                 settings={"LORBIT": None}, nbands_from=str(fo / "cr-nup6" / "OUTCAR.gz"),
                                 parallel={"ranks": 48, "ncore": 4}, out_path=str(d / "flip.INCAR"))
        assert r["magmom"] == "1*0 1*3 1*-3 7*0" and r["nbands"] == 24, r
        assert "LORBIT" not in settings_of(r["out"]), r["changed"]
        assert any("LORBIT < 10" in w for w in r["warnings"]), r["warnings"]
        r = hsrv.vasp_make_incar(poscar, base, magmom={"Cr": 3}, parallel=lay,
                                 out_path=str(d / "free.INCAR"))
        assert any("NBANDS is not pinned" in w for w in r["warnings"]), r["warnings"]
        # --- refusals
        for kw, text in (
                ({}, "MAGMOM has 252 values, the POSCAR 10 atoms"),     # the label's own MAGMOM
                ({"magmom": {"Fe": 2}}, "not in the POSCAR species"),
                ({"magmom": {"Cr": 3}, "settings": {"ISPIN": 1}}, "ISPIN 1"),
                ({"magmom": {"Cr": 3}, "parallel": lay, "nbands": 661}, "silently run 664"),
                ({"magmom": {"Cr": 3}, "parallel": {"ranks": 48, "ncore": 5}}, "don't divide"),
                ({"magmom": {"Cr": 3}, "settings": {"NBANDS": 600}}, "own argument"),
                ({"magmom": {"Cr": 3}, "nupdown": 6, "pre": {"nupdown": 6}}, "don't also fix"),
                ({"magmom": {"Cr": 3}, "pre": {"ediff": 1e-4}}, "pre needs nupdown"),
                ({"magmom": {"Cr": 3}, "magmom_sites": {"11": 1}}, "outside 1..10"),
                ({"magmom": {"Cr": 3}, "out_path": str(d / "nup.INCAR")}, "exists")):
            kw = {"out_path": str(d / "x.INCAR"), **kw}
            try:
                hsrv.vasp_make_incar(poscar, base, **kw)
            except ValueError as e:
                assert text in str(e), (text, e)
            else:
                raise AssertionError(f"vasp_make_incar accepted {kw}")
        assert not (d / "x.INCAR").exists()

        # --- parser: values read by hand from the two OUTCARs (2026-11-06)
        want = {"cr-nup6": dict(conv=True, steps=[24], toten=-5.44335354, ef=-41.7336,
                                kb=[-0.04116, -0.0413, -0.04201, -0.0, -0.0, 0.0],
                                f=(-0.000016, -0.000003, -0.000002), el=49.694,
                                mem=419168, nbands=24, mag=6.0, nup=6.0),
                "cr-nup4": dict(conv=False, steps=[300], toten=-4.20528085, ef=-3.6758,
                                kb=[-0.42993, -0.84994, -1.59425, -0.33317, 0.02587, 0.05318],
                                f=(0.000211, -0.000127, -0.000053), el=548.791,
                                mem=479184, nbands=12, mag=4.0, nup=4.0)}
        for name, w in want.items():
            r = hsrv.vasp_parse_output(str(fo / name / "OUTCAR.gz"))
            assert r["oszicar_path"] and r["poscar_path"], r
            assert r["version"] == "6.4.3" and r["flavour"] == "gamma-only", r["version"]
            assert r["finished"] and r["converged"] is w["conv"], (name, r["converged"])
            assert r["natoms"] == 1 and r["species"] == [["Cr", 1]], r["species"]
            assert r["scf"]["steps"] == w["steps"] and r["scf"]["nelm"] == 300, r["scf"]
            assert r["scf"]["nelm_hit"] == (0 if w["conv"] else 1), r["scf"]
            assert r["energy"]["toten"] == w["toten"] and r["e_fermi"] == w["ef"], r["energy"]
            assert r["stress_kb"]["xx_yy_zz_xy_yz_zx"] == w["kb"], r["stress_kb"]
            fmax = sum(x * x for x in w["f"]) ** 0.5
            assert abs(r["forces"]["max"] - fmax) < 1e-12 and r["forces"]["complete"], r["forces"]
            assert r["cost"]["elapsed_s"] == w["el"] and r["cost"]["max_memory_mb"] == w["mem"] / 1024
            assert r["params"]["NBANDS"] == w["nbands"] and r["params"]["NUPDOWN"] == w["nup"], r["params"]
            assert r["layout"] == {"ranks": 48, "nodes": 1, "threads": 1, "kpar": 1, "ncore": 4,
                                   "band_groups": 12}, r["layout"]
            assert r["magnetization"]["total"] == w["mag"] and r["magnetization"]["source"] == "OSZICAR"
            assert r["oszicar"]["F"] == round(w["toten"], 6) or abs(r["oszicar"]["F"] - w["toten"]) < 1e-6
            assert any("LORBIT >= 10" in x for x in r["warnings"]), r["warnings"]
            assert [m["kind"] for m in r["messages"]] == (["advice"] if w["conv"] else ["advice", "warning"])
            nelm_warn = [x for x in r["warnings"] if "NELM" in x]
            assert len(nelm_warn) == (0 if w["conv"] else 1), r["warnings"]
        # damaged copies of the converged run
        text = gzip.decompress((fo / "cr-nup6" / "OUTCAR.gz").read_bytes())
        lines = text.split(b"\n")
        i = next(k for k, l in enumerate(lines) if b"TOTAL-FORCE" in l)
        (d / "cut").mkdir()
        (d / "cut" / "OUTCAR").write_bytes(b"\n".join(lines[:i + 2]) + b"\n")
        p = hsrv.vasp_parse_output(str(d / "cut" / "OUTCAR"))
        assert not p["finished"] and not p["converged"] and p["forces"] is None, p["forces"]
        assert any("General timing" in x for x in p["warnings"]), p["warnings"]
        # a NUL hole over the header (atoms, species), POSCAR beside it
        (d / "nul").mkdir()
        j = next(k for k, l in enumerate(lines) if b"ions per type" in l)
        holed = b"\n".join(lines[:j - 30]) + b"\x00" * 4096 + b"\n".join(lines[j + 5:])
        holed = holed.replace(b"NIONS =", b"\x00" * 7)
        (d / "nul" / "OUTCAR").write_bytes(holed)
        shutil.copy(fo / "cr-nup6" / "POSCAR", d / "nul" / "POSCAR")
        p = hsrv.vasp_parse_output(str(d / "nul" / "OUTCAR"))
        assert p["nul_bytes"] >= 4096 and p["natoms"] == 1 and p["converged"], (p["natoms"], p["warnings"])
        assert any("NUL bytes stripped" in x for x in p["warnings"]), p["warnings"]
        assert any("taken from the POSCAR" in x for x in p["warnings"]), p["warnings"]
        # an OSZICAR from another run
        shutil.copy(fo / "cr-nup4" / "OSZICAR", d / "cut" / "OSZICAR")
        shutil.copy(fo / "cr-nup6" / "OUTCAR.gz", d / "cut" / "OUTCAR.gz")
        p = hsrv.vasp_parse_output(str(d / "cut" / "OUTCAR.gz"))
        assert any("different runs" in x for x in p["warnings"]), p["warnings"]
        for args, text in (((str(d / "missing" / "OUTCAR"),), "fs_download"),
                           ((str(fi / "INCAR.label"),), "not a VASP OUTCAR")):
            try:
                hsrv.vasp_parse_output(*args)
            except ValueError as e:
                assert text in str(e), e
            else:
                raise AssertionError(f"vasp_parse_output accepted {args}")
