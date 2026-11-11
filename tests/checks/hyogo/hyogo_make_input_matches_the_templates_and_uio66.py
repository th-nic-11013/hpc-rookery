import pathlib
import tempfile
from checks._common import ROOT

LABEL = 'hyogo make_input matches the templates and UiO-66'


def hyogo_make_input_matches_the_templates_and_uio66():
    """cp2k_make_input writes, after dropping comments and expanding @SET,
    the four revPBE templates and the five hand-written UiO-66 functional
    inputs (whose cp2k --check passed); cp2k_make_subsys writes their
    AUX_FIT lines; each refusal fires before anything is written."""
    import re
    from hyogo import server as hsrv
    root = ROOT
    fx = root / "tests" / "fixtures" / "cp2k_inputs" / "uio66"

    def norm(text, fixed=None):
        """Drop comments and blanks, run @SET/@IF/@ENDIF as CP2K's preprocessor
        does (`fixed` pins variables, e.g. {"RESTART": "1"}), expand ${...}."""
        sets, out, live = dict(fixed or {}), [], []
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith(("#", "!")):
                continue
            s = re.sub(r"\$\{(\w+)\}", lambda m: sets[m.group(1)], s)
            if s.upper() == "@ENDIF":
                live.pop()
                continue
            m = re.match(r"@IF\s+(\S+)\s*(==|/=)\s*(\S+)$", s)
            if m:
                live.append((m.group(1) == m.group(3)) == (m.group(2) == "=="))
                continue
            if not all(live):
                continue
            m = re.match(r"@SET\s+(\S+)\s+(.*)", s)
            if m:
                if m.group(1) not in (fixed or {}):
                    sets[m.group(1)] = m.group(2).strip()
                continue
            out.append(" ".join(s.split()).upper())
        return out

    q = {"H": 1, "C": 4, "O": 6, "Zr": 12}

    def uzh(fam, stem, aux):
        k = {e: {"q": n, "basis": f"DZVP-MOLOPT-{fam}-GTH-q{n}", "potential": f"{stem}-q{n}",
                 **({"aux_basis": f"admm-dzp-q{n}"} if aux else {})} for e, n in q.items()}
        return {"files": {"basis": ["BASIS_MOLOPT_UZH_2026.2"] + (["BASIS_ADMM_UZH"] if aux else []),
                          "potential": "POTENTIAL_UZH_2026.2"}, "kinds": k, "errors": {}}

    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        xyz = d / "uio66.xyz"
        xyz.write_text((fx / "uio66_prim_114_ch108.xyz").read_text())
        for f, fam, stem, aux in (("pbe", "PBE", "GTH-PBE", False), ("r2scan", "SCAN", "GTH-SCAN", False),
                                  ("pbe0", "PBE0", "GTH-PBE0", True), ("hse06", "PBE0", "GTH-PBE0", True),
                                  ("r2scan0", "SCAN", "GTH-SCAN", True)):
            k = uzh(fam, stem, aux)
            sub = hsrv.cp2k_make_subsys(str(xyz), k, str(d / f"{f}.inc"))
            if aux:
                a = (d / f"{f}.inc").read_text().splitlines()
                b = (fx / f"subsys_{'pbe0' if fam == 'PBE0' else 'scan'}_admm.inc").read_text().splitlines()
                assert a[:1] + a[2:] == b[:1] + b[2:], f
            r = hsrv.cp2k_make_input("sp", f, sub, k, project=f"sp-{f}", cutoff=600,
                                     print_gap=True, save_wfn=True,
                                     hfx={"memory_mb": 2000} if aux else None)
            assert r["out"] == str(d / f"sp-{f}.inp"), r
            # the hand-written inputs leave CHARGE/MULTIPLICITY/UKS at CP2K's
            # defaults, and the GGA ones list BASIS_ADMM_UZH though unused
            got = [l for l in norm(pathlib.Path(r["out"]).read_text())
                   if not l.startswith(("CHARGE ", "MULTIPLICITY ", "UKS "))]
            want = [l for l in norm((fx / f"sp_{f}.inp").read_text())
                    if aux or l != "BASIS_SET_FILE_NAME BASIS_ADMM_UZH"]
            assert got == want, (f, [l for l in got if l not in want], [l for l in want if l not in got])
            assert r["settings"].get("HFX_RADIUS") == (5.9 if aux else None), r
            if f == "hse06":  # erfc(0.11 r)/r = 1e-6 at 14.3 A, capped below half of 11.97 A
                assert any("reaches 14.3 A" in w for w in r["warnings"]), r
        # r2SCANh: the r2SCAN0 input with 10 % exact exchange and its own D3(BJ) key
        k = uzh("SCAN", "GTH-SCAN", True)
        sub = hsrv.cp2k_make_subsys(str(xyz), k, str(d / "r2scanh.inc"))
        r = hsrv.cp2k_make_input("sp", "r2scanh", sub, k, project="sp-r2scanh", cutoff=600,
                                 print_gap=True, save_wfn=True, hfx={"memory_mb": 2000})
        got = [l for l in norm(pathlib.Path(r["out"]).read_text())
               if not l.startswith(("CHARGE ", "MULTIPLICITY ", "UKS "))]
        want = [{"SCALE 0.75": "SCALE 0.9", "FRACTION 0.25": "FRACTION 0.10",
                 "REFERENCE_FUNCTIONAL R2SCAN0": "REFERENCE_FUNCTIONAL R2SCANH",
                 "PROJECT SP-R2SCAN0": "PROJECT SP-R2SCANH"}.get(l, l)
                for l in norm((fx / "sp_r2scan0.inp").read_text())]
        assert got == want, ([l for l in got if l not in want], [l for l in want if l not in got])
        assert any("10% exact exchange" in w for w in r["warnings"]), r

        legacy = {e: {"q": n, "basis": "DZVP-MOLOPT-SR-GTH", "potential": f"GTH-PBE-q{n}"}
                  for e, n in q.items()}
        sub = hsrv.cp2k_make_subsys(str(xyz), legacy, str(d / "rev.inc"))
        for rt, tpl in (("sp", "single-point/templates/single_point.inp"),
                        ("geo_opt", "geo-opt/templates/geo_opt.inp"),
                        ("cell_opt", "geo-opt/templates/cell_opt.inp"),
                        ("md_nvt", "aimd/templates/aimd_nvt.inp")):
            r = hsrv.cp2k_make_input(rt, "revpbe", sub, out_path=str(d / f"{rt}.inp"))
            got = norm(pathlib.Path(r["out"]).read_text())
            want = norm((root / "codes" / "cp2k" / tpl).read_text())
            assert got == want, (rt, [l for l in got if l not in want], [l for l in want if l not in got])

        # odd electrons give UKS; PERIODIC NONE gives &POISSON; duration sets WALLTIME
        water = root / "tests" / "fixtures" / "structures" / "clean.xyz"
        sub = hsrv.cp2k_make_subsys(str(water), {e: legacy[e] for e in ("O", "H")},
                                    str(d / "w.inc"), periodic="NONE", charge=1)
        r = hsrv.cp2k_make_input("md_nvt", "revpbe", sub, out_path=str(d / "w.inp"),
                                 duration="02:00:00", md={"timestep": 1.0})
        text = pathlib.Path(r["out"]).read_text()
        assert r["settings"]["UKS"] == ".TRUE." and r["settings"]["MULTIPLICITY"] == 2, r
        assert r["settings"]["CHARGE"] == 1 and r["settings"]["WALLTIME"] == 7200 - 900, r
        assert "PERIODIC NONE" in text and "&POISSON" in text, text
        assert any("hydrogen" in w for w in r["warnings"]), r

        pbe0 = uzh("PBE0", "GTH-PBE0", True)
        sub0 = hsrv.cp2k_make_subsys(str(xyz), pbe0, str(d / "p0.inc"))
        # a cell wider than twice the erfc range keeps CP2K's own range, uncapped
        wide = {**sub0, "widths": [40.0, 40.0, 40.0]}
        r = hsrv.cp2k_make_input("sp", "hse06", wide, pbe0, out_path=str(d / "wide.inp"),
                                 stress=True, guess_wfn="../pbe/pbe-RESTART.wfn",
                                 precision={"eps_pgf_orb": 1e-20}, hfx={"eps_schwarz": 1e-7})
        text = pathlib.Path(r["out"]).read_text()
        assert r["settings"]["HFX_RADIUS"] == 15.9 and not any("capped" in w for w in r["warnings"]), r
        for want in ("STRESS_TENSOR ANALYTICAL", "STRESS_UNIT GPa", "SCF_GUESS RESTART",
                     "WFN_RESTART_FILE_NAME ../pbe/pbe-RESTART.wfn", "SCREEN_ON_INITIAL_P  TRUE",
                     "EPS_PGF_ORB 1.0E-20", "EXCH_CORRECTION_FUNC     DEFAULT",
                     "MIN_PAIR_LIST_RADIUS -1", "EPS_SCHWARZ_FORCES   ${HFX_EPS_SCHWARZ}",
                     "@SET HFX_EPS_SCHWARZ  1.0E-7"):
            assert want in text, (want, text)
        assert not any("atomic guess" in w for w in r["warnings"]), r
        # purpose="guess": PBE in the hybrid's basis and potentials, wavefunction kept
        g = hsrv.cp2k_make_input("sp", "pbe", sub0, pbe0, purpose="guess")
        text = pathlib.Path(g["out"]).read_text()
        assert g["out"] == str(d / "guess.inp") and g["wfn"] == "guess-RESTART.wfn", g
        for want in ("RUN_TYPE    ENERGY", "# GUESS ONLY", "&RESTART ON", "SCF_GUESS ATOMIC",
                     "MAX_SCF   50", "POTENTIAL_FILE_NAME POTENTIAL_UZH_2026.2"):
            assert want in text, (want, text)
        assert "&HF" not in text and "AUXILIARY_DENSITY" not in text, text
        assert any("NOT a result" in w for w in g["warnings"]), g
        h = hsrv.cp2k_make_input("sp", "hse06", sub0, pbe0, out_path=str(d / "h.inp"),
                                 guess_wfn=g["wfn"])
        assert "MAX_SCF   30" in pathlib.Path(h["out"]).read_text(), h
        plain0 = uzh("PBE0", "GTH-PBE0", False)
        subp = hsrv.cp2k_make_subsys(str(xyz), plain0, str(d / "p.inc"))
        scan = uzh("SCAN", "GTH-SCAN", False)
        subs = hsrv.cp2k_make_subsys(str(xyz), scan, str(d / "s.inc"))
        for args, kw, text in (
                (("sp", "r2scan", sub, {"files": {"basis": ["BASIS_MOLOPT"],
                                                  "potential": "GTH_POTENTIALS"}}), {},
                 "needs GTH-SCAN potentials"),
                (("sp", "pbe0", subp, plain0), {}, "uses ADMM"),
                (("sp", "pbe0", sub0, pbe0), {"hfx": {"radius": 6.0}}, "below half"),
                (("sp", "hse06", sub0, pbe0), {"hfx": {"radius": 6.0}}, "below half"),
                (("sp", "hse06", sub0, pbe0), {"hfx": {"eps_schwarz": 0.1}}, "small threshold"),
                (("geo_opt", "pbe0", sub0, pbe0), {"stress": True}, "stress is for sp"),
                (("sp", "pbe0", sub0, pbe0), {"guess_wfn": "a b.wfn"}, "without spaces"),
                (("sp", "pbe", sub0, pbe0), {}, "needs GTH-PBE potentials"),
                (("sp", "hse06", sub0, pbe0), {"purpose": "guess"}, "the hybrid itself"),
                (("geo_opt", "pbe", sub0, pbe0), {"purpose": "guess"}, "single point"),
                (("sp", "pbe", sub0), {"purpose": "guess"}, "hybrid's data files"),
                (("sp", "pbe", sub0, pbe0), {"purpose": "seed"}, "purpose is"),
                (("sp", "revpbe", sub), {"precision": {"eps": 1}}, "precision takes"),
                (("sp", "r2scan", subs, scan), {"d3": "zero"}, "only with BJ"),
                (("sp", "r2scan", subs), {}, "pass cp2k_kinds' result"),
                (("neb", "revpbe", sub), {}, "run_type is one of"),
                (("sp", "b3lyp", sub), {}, "functional is one of"),
                (("sp", "revpbe", sub), {"multiplicity": 1}, "impossible"),
                (("geo_opt", "revpbe", sub), {"duration": "12h"}, "HH:MM:SS"),
                (("sp", "revpbe", sub), {"md": {"steps": 5}}, "md is for md_nvt"),
                (("sp", "revpbe", sub), {"out_path": str(d / "w.inp")}, "exists")):
            try:
                hsrv.cp2k_make_input(*args, **kw)
            except ValueError as e:
                assert text in str(e), (text, e)
            else:
                raise AssertionError(f"cp2k_make_input accepted {args[:2]} {kw}")

        # G18: the user's PBE-D3(BJ) cell_opt (cubic kept cubic, tight forces,
        # restart after a walltime stop). Compared: &CELL_OPT, the restart block
        # and SYMMETRY in &CELL; the DFT block and includes are that run's own.
        src = root / "tests" / "fixtures" / "cp2k_inputs" / "uio66_pbe_cellopt"
        conv = d / "conv.xyz"
        cell_inc = (src / "cell.inc").read_text()
        a = float(re.search(r"^\s*A\s+(\S+)", cell_inc, re.M).group(1))
        xyz_lines = (fx / "uio66_prim_114_ch108.xyz").read_text().splitlines()
        conv.write_text("\n".join([xyz_lines[0], f'Lattice="{a} 0 0 0 {a} 0 0 0 {a}" Properties=species:S:1:pos:R:3']
                                  + xyz_lines[2:]) + "\n")
        pbe = uzh("PBE", "GTH-PBE", False)
        subc = hsrv.cp2k_make_subsys(str(conv), pbe, str(d / "c.inc"), symmetry="CUBIC")
        assert subc["symmetry"] == "CUBIC" and "  SYMMETRY CUBIC" in (d / "c.inc").read_text(), subc
        assert norm(cell_inc)[-2:] == ["SYMMETRY CUBIC", "&END CELL"], norm(cell_inc)
        opt = {"max_iter": 300, "max_force": 1e-4, "rms_force": 5e-5,
               "keep_angles": True, "keep_symmetry": True}
        r = hsrv.cp2k_make_input("cell_opt", "pbe", subc, pbe, out_path=str(d / "co.inp"),
                                 project="pbe", cutoff=1000, opt=opt)
        assert r["restart"] == {"set": "RESTART", "file": "pbe-1.restart", "wfn": "pbe-RESTART.wfn"}, r
        # G21: the stress-cutoff warning fires for cell_opt below 800 Ry only
        stress_warn = "below 800 Ry"
        assert not any(stress_warn in w for w in r["warnings"]), r  # cell_opt at 1000 Ry
        for rt in ("sp", "geo_opt", "cell_opt"):  # each at its default cutoff
            w = hsrv.cp2k_make_input(rt, "pbe", subc, pbe, out_path=str(d / f"g21-{rt}.inp"))
            assert w["settings"]["CUTOFF"] == (600 if rt == "cell_opt" else 450), w
            assert any(stress_warn in x for x in w["warnings"]) == (rt == "cell_opt"), w
        for rt in ("sp", "geo_opt"):
            w = hsrv.cp2k_make_input(rt, "pbe", subc, pbe, out_path=str(d / f"g21-{rt}-lo.inp"),
                                     cutoff=300)
            assert not any(stress_warn in x for x in w["warnings"]), w
        assert r["opt"] == {**opt}, r

        def section(lines, name):
            i = lines.index(f"&{name}")
            return lines[i:lines.index(f"&END {name}", i) + 1]
        theirs = norm("@SET RESTART 1\n" + (src / "opt.inp").read_text().split("&FORCE_EVAL")[0]
                      .replace("@INCLUDE", "# @INCLUDE"))
        text = pathlib.Path(r["out"]).read_text()
        for restart in ("0", "1"):
            # the one named difference: we write CP2K's default unit, [bar], explicitly
            ours = [l.replace(" [BAR]", "") for l in norm(text, {"RESTART": restart})]
            assert section(ours, "CELL_OPT") == section(theirs, "CELL_OPT"), \
                (section(ours, "CELL_OPT"), section(theirs, "CELL_OPT"))
            ext = ["&EXT_RESTART", "RESTART_FILE_NAME PBE-1.RESTART", "&END EXT_RESTART"]
            assert (section(ours, "EXT_RESTART") == ext) if restart == "1" else ("&EXT_RESTART" not in ours)
            assert ("SCF_GUESS RESTART" if restart == "1" else "SCF_GUESS ATOMIC") in ours, (restart, ours)
        assert section(theirs, "EXT_RESTART") == ext, section(theirs, "EXT_RESTART")
        # from a hybrid guess: RESTART 0 reads guess_wfn, RESTART 1 the run's own wavefunction
        r = hsrv.cp2k_make_input("cell_opt", "hse06", {**sub0, "symmetry": None}, pbe0,
                                 out_path=str(d / "hco.inp"), guess_wfn="../sp/sp-RESTART.wfn")
        text = pathlib.Path(r["out"]).read_text()
        assert "WFN_RESTART_FILE_NAME ../SP/SP-RESTART.WFN" in norm(text, {"RESTART": "0"})
        assert "WFN_RESTART_FILE_NAME CELLOPT-RESTART.WFN" in norm(text, {"RESTART": "1"})
        for rt in ("geo_opt", "md_nvt"):
            r = hsrv.cp2k_make_input(rt, "revpbe", sub, out_path=str(d / f"r-{rt}.inp"))
            assert r["restart"]["file"] == f"{'opt' if rt == 'geo_opt' else 'md'}-1.restart", r
        assert hsrv.cp2k_make_input("sp", "revpbe", sub, out_path=str(d / "r-sp.inp"))["restart"] is None
        for kw, text in (({"symmetry": "CUBIC"}, "not CUBIC"),            # 60 deg primitive cell
                         ({"symmetry": "FCC"}, "symmetry is one of"),
                         ({"symmetry": "RHOMBOHEDRAL", "periodic": "NONE"}, "periodic cell")):
            try:
                hsrv.cp2k_make_subsys(str(xyz), pbe, str(d / "bad.inc"), **kw)
            except ValueError as e:
                assert text in str(e), (text, e)
            else:
                raise AssertionError(f"cp2k_make_subsys accepted {kw}")
        assert hsrv.cp2k_make_subsys(str(xyz), pbe, str(d / "rh.inc"), symmetry="RHOMBOHEDRAL")["symmetry"]
        for args, kw, text in (
                (("cell_opt", "pbe", subc, pbe), {"opt": {"rms_force": 1e-3}}, "at most max_force"),
                (("cell_opt", "pbe", subc, pbe), {"opt": {"max_force": 5}}, "Ha/bohr"),
                (("geo_opt", "pbe", subc, pbe), {"opt": {"keep_angles": True}}, "for cell_opt"),
                (("cell_opt", "revpbe", sub), {"opt": {"keep_symmetry": True}}, "symmetry=")):
            try:
                hsrv.cp2k_make_input(*args, out_path=str(d / "bad.inp"), overwrite=True, **kw)
            except ValueError as e:
                assert text in str(e), (text, e)
            else:
                raise AssertionError(f"cp2k_make_input accepted {args[:2]} {kw}")
