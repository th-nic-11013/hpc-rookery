import pathlib

LABEL = 'hyogo writes E0s from isolated atoms (G52)'


def hyogo_writes_e0s_from_isolated_atoms():
    """G52: cp2k_make_input(purpose="atom") writes the label settings
    for one atom in a non-periodic box (no stress, no D3, multiplicity
    stated), and cp2k_make_e0s turns the runs into {Z: eV} that
    mace_make_finetune reads, refusing runs that don't match each
    other or the labels. The outputs are synthetic (the parser's lines
    only); the inputs are the tool's own, whose cp2k --check passed for
    H, C, O, Zr at 15 and 20 A (2026-11-11)."""
    import json as _json
    import tempfile
    from hyogo import server as hsrv
    from hyogo.cp2k.labels import HA_EV
    k = {"files": {"basis": ["BASIS_MOLOPT_UZH_2026.2"], "potential": "POTENTIAL_UZH_2026.2"},
         "kinds": {e: {"q": q, "basis": f"DZVP-MOLOPT-SCAN-GTH-q{q}", "potential": f"GTH-SCAN-q{q}"}
                   for e, q in {"H": 1, "O": 6, "Zr": 12}.items()}, "errors": {}}

    def out(e_ha, version="2026.2", natoms=1, converged=True, ended=True):
        return "\n".join([
            f" CP2K| version string:                                       CP2K version {version}",
            " GLOBAL| Run type                                                   ENERGY_FORCE",
            f"                             - Atoms:                                          {natoms}",
            "  *** SCF run converged in    12 steps ***" if converged else
            "  *** SCF run NOT converged ***",
            f" ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]          {e_ha:.12f}",
            "  **** **** ******  **  PROGRAM ENDED AT               2026-11-11 10:00:00.000"
            if ended else "", ""])

    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp)

        def atom(el, m, box=15, e_ha=-1.0, tag="", **kw):
            f = d / f"{el}-{box}"
            f.mkdir(exist_ok=True)
            xyz = f / "atom.xyz"
            xyz.write_text(f"1\nLattice=\"{box} 0 0 0 {box} 0 0 0 {box}\" "
                           f"Properties=species:S:1:pos:R:3 pbc=\"F F F\"\n{el} {box/2} {box/2} {box/2}\n")
            kk = {**k, "kinds": {el: k["kinds"][el]}}
            sub = hsrv.cp2k_make_subsys(str(xyz), kk, str(f / "subsys.inc"), periodic="NONE",
                                        overwrite=True)
            r = hsrv.cp2k_make_input("sp", "r2scan", sub, kk, purpose="atom", multiplicity=m,
                                     overwrite=True, **{"cutoff": 1000, **kw})
            o = f / f"e0-{el}-m{m}{tag}.out"
            o.write_text(out(e_ha))
            return {"out": str(o), "inp": r["out"], "subsys": str(f / "subsys.inc")}, r, sub

        # the atom input: label settings, one atom, no stress, no D3, MT Poisson
        h, r, hsub = atom("H", 2, e_ha=-0.5)
        inp = pathlib.Path(r["out"]).read_text()
        assert pathlib.Path(r["out"]).name == "e0-H-m2.inp" and r["d3"] == "none", r
        assert r["atom"]["element"] == "H" and r["atom"]["box_A"] == 15.0, r["atom"]
        for need in ("(isolated atom, E0 reference)", "@SET MULTIPLICITY  2", "@SET UKS           .TRUE.",
                     "@SET CUTOFF        1000", "PERIODIC NONE", "PSOLVER  MT", "&OT ON"):
            assert need in inp, need
        for never in ("STRESS_TENSOR", "VDW_POTENTIAL", "&SMEAR"):
            assert never not in inp, never
        assert not r["warnings"], r["warnings"]
        # refusals of the atom mode
        for kw, msg in ((dict(multiplicity=None), "needs multiplicity stated"),
                        (dict(stress=True), "no stress"),
                        (dict(d3="bj"), "without D3"),
                        (dict(run_type="geo_opt"), "single point")):
            rt = kw.pop("run_type", "sp")
            try:
                hsrv.cp2k_make_input(rt, "r2scan", hsub, {**k, "kinds": {"H": k["kinds"]["H"]}},
                                     purpose="atom", **{"multiplicity": 2, **kw},
                                     out_path=str(d / "bad.inp"), overwrite=True)
            except ValueError as e:
                assert msg in str(e), (msg, e)
            else:
                raise AssertionError(f"purpose=\"atom\" accepted {kw}")
        small = d / "small.xyz"
        small.write_text("1\nLattice=\"10 0 0 0 10 0 0 0 10\" Properties=species:S:1:pos:R:3 "
                         "pbc=\"F F F\"\nO 5 5 5\n")
        ok = {**k, "kinds": {"O": k["kinds"]["O"]}}
        ssub = hsrv.cp2k_make_subsys(str(small), ok, str(d / "small.inc"), periodic="NONE")
        try:
            hsrv.cp2k_make_input("sp", "r2scan", ssub, ok, purpose="atom", multiplicity=3,
                                 out_path=str(d / "bad.inp"), overwrite=True)
        except ValueError as e:
            assert "at least 12" in str(e), e
        else:
            raise AssertionError("purpose=\"atom\" accepted a 10 A box")

        # the label run whose settings the atoms must share
        lab = d / "lab"
        lab.mkdir()
        (lab / "w.xyz").write_text("3\nLattice=\"12 0 0 0 12 0 0 0 12\" Properties=species:S:1:pos:R:3 "
                                   "pbc=\"T T T\"\nO 6 6 6\nH 6.96 6 6\nH 5.76 6.93 6\n")
        lk = {**k, "kinds": {e: k["kinds"][e] for e in ("H", "O")}}
        lsub = hsrv.cp2k_make_subsys(str(lab / "w.xyz"), lk, str(lab / "subsys.inc"))
        lr = hsrv.cp2k_make_input("sp", "r2scan", lsub, lk, purpose="label", d3="none",
                                  cutoff=1000, out_path=str(lab / "lab.inp"))
        label = {"inp": lr["out"], "subsys": str(lab / "subsys.inc")}
        # D3 in the labels is no mismatch: an atom has no dispersion energy
        lr3 = hsrv.cp2k_make_input("sp", "r2scan", lsub, lk, purpose="label", d3="bj",
                                   cutoff=1000, out_path=str(lab / "lab3.inp"))

        o3, *_ = atom("O", 3, e_ha=-15.80)
        o1, *_ = atom("O", 1, e_ha=-15.70)
        o3b, *_ = atom("O", 3, box=20, e_ha=-15.80002)
        r = hsrv.cp2k_make_e0s([h, o3, o1, o3b], str(d / "e0s.json"), label=label)
        e0 = _json.loads((d / "e0s.json").read_text())
        assert set(e0) == {"1", "8"}, e0
        assert abs(e0["1"] - (-0.5 * HA_EV)) < 1e-9, e0
        assert abs(e0["8"] - (-15.80002 * HA_EV)) < 1e-9, e0   # the larger box
        pe = r["per_element"]["O"]
        assert pe["multiplicity"] == 3 and pe["box_A"] == 20.0, pe
        assert abs(pe["others"][0]["delta_ev"] - 0.10002 * HA_EV) < 1e-6, pe["others"]
        assert abs(pe["box_check_ev"] - (-0.00002 * HA_EV)) < 1e-6, pe
        assert r["label_checked"] and r["settings"]["cutoff"] == 1000.0, r["settings"]
        assert not r["warnings"], r["warnings"]       # 0.5 meV box difference is within 1 meV
        prov = _json.loads(pathlib.Path(r["provenance"]).read_text())
        assert prov["per_element"]["H"]["sha256"] and prov["per_element"]["O"]["source"] == o3b["out"]
        r = hsrv.cp2k_make_e0s([h, o3, o1], str(d / "e0s3.json"),
                               label={"inp": lr3["out"], "subsys": label["subsys"]})
        assert r["label_checked"], r

        # warnings: ground state not lowest, box difference, missing label element
        o1low, *_ = atom("O", 1, e_ha=-15.90, tag="low")
        r = hsrv.cp2k_make_e0s([h, o3, o1low], str(d / "w1.json"))
        assert any("ground state is 3" in w for w in r["warnings"]), r["warnings"]
        assert r["per_element"]["O"]["multiplicity"] == 1, r
        o3far, *_ = atom("O", 3, box=20, e_ha=-15.81, tag="far")
        r = hsrv.cp2k_make_e0s([o3, o1, o3far], str(d / "w2.json"))
        assert any("boxes differ by" in w for w in r["warnings"]), r["warnings"]
        r = hsrv.cp2k_make_e0s([o3, o1], str(d / "w3.json"), label=label)
        assert any("no E0 run for ['H']" in w for w in r["warnings"]), r["warnings"]
        r = hsrv.cp2k_make_e0s([h, o3], str(d / "w4.json"))
        assert any("only multiplicity 3 was run" in w for w in r["warnings"]), r["warnings"]

        # refusals
        def variant(run, text, name):
            p = pathlib.Path(run["out"]).with_name(name)
            p.write_text(text)
            return {**run, "out": str(p)}
        oz, *_ = atom("O", 3, tag="cut", cutoff=800,
                      out_path=str(d / "O-15" / "cut.inp"))   # 800 Ry, its own files
        pinp = pathlib.Path(o3["inp"]).with_name("plain.inp")
        pinp.write_text(pathlib.Path(o3["inp"]).read_text().replace(
            "(isolated atom, E0 reference)", ""))
        sinp = pathlib.Path(o3["inp"]).with_name("smear.inp")
        sinp.write_text(pathlib.Path(o3["inp"]).read_text().replace("&OT ON", "&SMEAR ON"))
        per = d / "per.inc"
        per.write_text(pathlib.Path(o3["subsys"]).read_text().replace("PERIODIC NONE", "PERIODIC XYZ"))
        lab800 = hsrv.cp2k_make_input("sp", "r2scan", lsub, lk, purpose="label", d3="none",
                                      cutoff=800, out_path=str(lab / "lab800.inp"))
        cases = (
            ([h, variant(o3, out(-15.8, ended=False), "a.out")], {}, "no PROGRAM ENDED"),
            ([h, variant(o3, out(-15.8, converged=False), "b.out")], {}, "did not converge"),
            ([h, variant(o3, out(-15.8, natoms=3), "c.out")], {}, "3 atoms"),
            ([h, variant(o3, out(-15.8, version="2024.1"), "d.out")], {}, "not a version the tests"),
            ([h, oz], {}, "differ in ['cutoff']"),
            ([h, {**o3, "inp": str(pinp)}], {}, "purpose=\"atom\""),
            ([h, {**o3, "inp": str(sinp)}], {}, "smeared"),
            ([h, {**o3, "subsys": str(per)}], {}, "periodic"),
            ([o3, variant(o3, out(-15.8), "dup.out")], {}, "both O multiplicity 3"),
            ([h, o3], {"label": {"inp": lab800["out"], "subsys": label["subsys"]}},
             "the atoms and the labels differ in ['cutoff']"),
            ([h, o3], {"label": {"inp": o3["inp"], "subsys": o3["subsys"]}}, "isolated-atom input"),
            ([{"out": h["out"]}], {}, "each run is"),
            ([{**h, "out": str(d / "missing.out")}], {}, "fs_download"),
            ([h], {"out_path": str(d / "e0s.txt")}, "ends in .json"),
            ([h], {"out_path": str(d / "e0s.json")}, "exists"))
        for runs, kw, msg in cases:
            dst = kw.pop("out_path", str(d / "bad.json"))
            try:
                hsrv.cp2k_make_e0s(runs, dst, **kw)
            except ValueError as e:
                assert msg in str(e), (msg, e)
            else:
                raise AssertionError(f"cp2k_make_e0s accepted {msg}")
            assert msg == "exists" or not pathlib.Path(dst).exists(), msg
        r = hsrv.cp2k_make_e0s([variant(o3, out(-15.8, version="2024.1"), "d.out")],
                               str(d / "old.json"), allow_untested_version=True)
        assert any("2024.1 is untested" in w for w in r["warnings"]), r["warnings"]
