import pathlib
import tempfile
from checks._common import ROOT

LABEL = 'hyogo writes MLIP labels from the .out'


def hyogo_writes_mlip_labels_from_the_out():
    """G43/G48/G39: cp2k_make_labels turns six real r2SCAN single points
    (tests/fixtures/cp2k_labels/README.md) into a MACE extxyz whose
    energies, forces and stress equal the values read from the .out by
    the T1 scratch script; ASE reads it back; each refusal fires.
    cp2k_parse_output returns the D3 energy and, on request, the force
    vectors (agreeing with rookery's parse_bench). cp2k_make_input writes
    D3-free label inputs, refuses a label run without an explicit d3."""
    import gzip
    import hashlib
    import numpy as np
    from hyogo import server as hsrv
    from hpc_codes.cp2k import parse_bench
    root = ROOT
    fx = root / "tests" / "fixtures" / "cp2k_labels"
    names = ["f0", "f4", "f6", "g1", "g2", "g3"]
    HA, BOHR = 27.211386245988, 0.529177210903
    # read from the .out by hand / by prep_frames.py (T1, 2026-11-10)
    e_ha = {"f0": -4331.290510609114790, "f4": -4329.822648671946808, "f6": -4329.492885091510288,
            "g1": -4330.083526623568105, "g2": -4329.558230738028215, "g3": -4329.754518004212514}
    disp = {"f0": -0.49296898210552, "f4": -0.55561696321359, "f6": -0.62812280703689,
            "g1": -0.53685677067104, "g2": -0.60928280355682, "g3": -0.81121842562775}
    # D3-free dE vs f0 in meV/atom (eval.json, T1)
    de_nod3 = {"f4": 91.33179065687919, "f6": 115.33685354825457, "g1": 74.64463813502925,
               "g2": 110.31315127094562, "g3": 110.65021178342647}
    f0_atom1 = [3.04250713e-03, 3.06068636e-03, -5.27325955e-05]          # Ha/bohr
    g3_stress = [[-0.786827370621, 0.608844789297, -0.0496170247816],      # GPa, CP2K sign
                 [0.608844789297, -1.77506239311, -0.216342540857],
                 [-0.0496170247816, -0.216342540857, -1.88814737907]]
    close = lambda a, b, tol=1e-9: abs(a - b) <= tol * max(1.0, abs(b))
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        for n in names:
            (d / f"{n}.out").write_bytes(gzip.decompress((fx / f"{n}.out.gz").read_bytes()))
            (d / f"{n}.inc").write_bytes(gzip.decompress((fx / f"{n}.subsys.inc.gz").read_bytes()))
        # cp2k_parse_output: D3 energy always, vectors on request (G39, G43)
        for n in names:
            r = hsrv.cp2k_parse_output(str(d / f"{n}.out"), vectors=True)
            assert close(r["dispersion_ha"], disp[n]), (n, r["dispersion_ha"])
            v = r["forces"]["vectors"]
            assert len(v) == 456 == r["forces"]["natoms"], n
            comps = parse_bench.parse_out(d / f"{n}.out")["forces"]
            assert np.allclose(np.ravel(v), comps, rtol=0, atol=1e-14), n
            if n == "f0":
                assert v[0] == f0_atom1, v[0]
            assert "vectors" not in hsrv.cp2k_parse_output(str(d / f"{n}.out"))["forces"], n
        # the existing fixtures have D3: the parser reads it there too
        plain = d / "plain.out"
        plain.write_bytes(gzip.decompress((root / "tests" / "fixtures" / "cp2k_outputs" /
                                           "r2scan-sp.out.gz").read_bytes()))
        assert hsrv.cp2k_parse_output(str(plain))["dispersion_ha"] is not None

        frames = [{"out": str(d / f"{n}.out"), "subsys": str(d / f"{n}.inc"), "config_type": n}
                  for n in names]
        r = hsrv.cp2k_make_labels(frames, str(d / "labels.extxyz"), head="r2scan")
        assert r["frames"] == 6 and r["natoms"] == [456] * 6 and r["stress"], r
        assert r["d3"] == "included" and any("include D3" in w for w in r["warnings"]), r
        assert r["versions"] == ["CP2K version 2026.2"], r
        try:
            from ase.io import read as ase_read
        except ImportError:
            ase_read = None
        if ase_read is not None:      # ASE is the yagura extra; hyogo itself doesn't need it
            atoms = ase_read(str(d / "labels.extxyz"), ":")
            assert len(atoms) == 6
            for n, a in zip(names, atoms):
                assert a.info["config_type"] == n and a.info["head"] == "r2scan", a.info
                assert close(a.info["REF_energy"], e_ha[n] * HA), n
                assert close(a.info["dispersion_eV"], disp[n] * HA), n
                assert a.info["source_sha256"] == hashlib.sha256(
                    (d / f"{n}.out").read_bytes()).hexdigest(), n
                assert len(a) == 456 and list(a.pbc) == [True] * 3, n
                assert a.info["REF_stress"].shape == (6,), a.info["REF_stress"]
                if n == "f0":
                    assert np.allclose(a.arrays["REF_forces"][0],
                                       np.array(f0_atom1) * HA / BOHR, rtol=1e-11), n
                if n == "g3":   # ASE sign = -CP2K's pressure-like tensor; Voigt xx yy zz yz xz xy
                    s = -np.array(g3_stress) / 160.21766208
                    want = [s[0, 0], s[1, 1], s[2, 2], s[1, 2], s[0, 2], s[0, 1]]
                    assert np.allclose(a.info["REF_stress"], want, rtol=1e-11, atol=0), n
            e0 = (atoms[0].info["REF_energy"] - atoms[0].info["dispersion_eV"])
            for n, a in zip(names[1:], atoms[1:]):
                got = (a.info["REF_energy"] - a.info["dispersion_eV"] - e0) / 456 * 1e3
                assert abs(got - de_nod3[n]) < 1e-6, (n, got)
        # refusals
        text = (d / "f0.out").read_text().splitlines()
        cut = d / "cut.out"
        cut.write_text("\n".join(l for l in text if "PROGRAM ENDED" not in l) + "\n")
        old = d / "old.out"
        old.write_text("\n".join(l.replace("2026.2", "2024.1") for l in text) + "\n")
        nostress = d / "nostress.out"
        nostress.write_text("\n".join(l for l in text if not l.startswith(" STRESS|")) + "\n")
        nod3 = d / "nod3.out"
        nod3.write_text("\n".join(l for l in text if "Dispersion energy:" not in l) + "\n")
        short = d / "short.inc"
        inc = (d / "f0.inc").read_text().splitlines()
        drop = next(i for i, l in enumerate(inc) if l.startswith("&COORD")) + 1
        short.write_text("\n".join(inc[:drop] + inc[drop + 1:]) + "\n")
        f0 = {"out": str(d / "f0.out"), "subsys": str(d / "f0.inc")}
        for fr, kw, msg in (([{**f0, "out": str(cut)}], {}, "PROGRAM ENDED"),
                            ([{**f0, "out": str(old)}], {}, "allow_untested_version"),
                            ([{**f0, "out": str(nostress)}], {}, "require_stress"),
                            ([f0, {**f0, "out": str(nod3)}], {}, "mix runs with and without D3"),
                            ([{**f0, "subsys": str(short)}], {}, "455 atoms in its subsys.inc"),
                            ([{**f0, "subsys": str(d / "missing.inc")}], {}, "fs_download"),
                            ([{"out": str(d / "f0.out")}], {}, "each frame is"),
                            ([f0], {"overwrite": False}, "exists")):
            dst = d / ("labels.extxyz" if msg == "exists" else "bad.extxyz")
            try:
                hsrv.cp2k_make_labels(fr, str(dst), **kw)
            except ValueError as e:
                assert msg in str(e), (msg, e)
            else:
                raise AssertionError(f"cp2k_make_labels accepted {msg}")
            assert msg == "exists" or not dst.exists(), msg
        # an untested version passes when asked, with a warning; no stress when not required
        r = hsrv.cp2k_make_labels([{**f0, "out": str(old)}], str(d / "old.extxyz"),
                                  allow_untested_version=True)
        assert any("2024.1 is not a tested layout" in w for w in r["warnings"]), r
        r = hsrv.cp2k_make_labels([{**f0, "out": str(nostress)}], str(d / "ns.extxyz"),
                                  require_stress=False)
        assert not r["stress"] and "REF_stress" not in (d / "ns.extxyz").read_text(), r

        # G48: D3-free label inputs, everything printed in the .out
        k = {"files": {"basis": ["BASIS_MOLOPT_UZH_2026.2"], "potential": "POTENTIAL_UZH_2026.2"},
             "kinds": {e: {"q": q, "basis": f"DZVP-MOLOPT-SCAN-GTH-q{q}", "potential": f"GTH-SCAN-q{q}"}
                       for e, q in {"H": 1, "C": 4, "O": 6, "Zr": 12}.items()}, "errors": {}}
        xyz = d / "f0.xyz"
        from hyogo.cp2k import labels as hl
        s0 = hl.read_subsys((d / "f0.inc").read_text())
        xyz.write_text(f"{len(s0['symbols'])}\nLattice=\"" +
                       " ".join(f"{v:.8f}" for row in s0["cell"] for v in row) +
                       "\" Properties=species:S:1:pos:R:3 pbc=\"T T T\"\n" +
                       "\n".join(f"{el} {x:.8f} {y:.8f} {z:.8f}"
                                 for el, (x, y, z) in zip(s0["symbols"], s0["positions"])) + "\n")
        sub = hsrv.cp2k_make_subsys(str(xyz), k, str(d / "lab.inc"))
        r = hsrv.cp2k_make_input("sp", "r2scan", sub, k, project="lab", purpose="label", d3="none",
                                 out_path=str(d / "lab.inp"))
        inp = (d / "lab.inp").read_text()
        assert r["d3"] == "none" and r["purpose"] == "label" and r["label"]["prints"], r
        assert "VDW_POTENTIAL" not in inp and "DFTD3" not in inp, inp
        for need in ("RUN_TYPE    ENERGY_FORCE", "PRINT_LEVEL LOW", "STRESS_TENSOR ANALYTICAL",
                     "&FORCES ON", "&STRESS_TENSOR ON", "STRESS_UNIT GPa"):
            assert need in inp, need
        assert "FILENAME" not in inp, "label output must stay in the .out"
        assert any("no D3" in w for w in r["warnings"]), r["warnings"]
        r = hsrv.cp2k_make_input("sp", "r2scan", sub, k, project="labd3", purpose="label", d3="bj",
                                 print_level="MEDIUM", out_path=str(d / "labd3.inp"))
        inp = (d / "labd3.inp").read_text()
        assert "DFTD3(BJ)" in inp and "PRINT_LEVEL MEDIUM" in inp and "&STRESS_TENSOR ON" in inp
        # the default input is unchanged: D3 on, LOW
        r = hsrv.cp2k_make_input("sp", "r2scan", sub, k, project="plain", out_path=str(d / "p.inp"))
        inp = (d / "p.inp").read_text()
        assert "DFTD3(BJ)" in inp and "PRINT_LEVEL LOW" in inp and r["label"] is None
        for kw, msg in ((dict(purpose="label"), "needs d3 stated"),
                        (dict(purpose="label", d3="none", run_type="geo_opt"), "single point"),
                        (dict(print_level="HIGH"), "print_level"),
                        (dict(d3="off"), '"none"')):
            rt = kw.pop("run_type", "sp")
            try:
                hsrv.cp2k_make_input(rt, "r2scan", sub, k, out_path=str(d / "bad.inp"),
                                     overwrite=True, **kw)
            except ValueError as e:
                assert msg in str(e), (msg, e)
            else:
                raise AssertionError(f"cp2k_make_input accepted {kw}")
