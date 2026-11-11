import pathlib
import tempfile
from checks._common import ROOT

LABEL = 'hyogo chains one run into the next (G27)'


def hyogo_chains_one_run_into_the_next():
    """G27: cp2k_parse_output names the files a follow-up run needs (the
    final xyz/cif CP2K printed, the wfn and restart), and cp2k_make_input
    turns a previous run's parse into guess_wfn (guess_from, guess_dir),
    refusing a wavefunction from other atoms, electrons, basis or
    potential files, and warning on an unfinished or unconverged run."""
    import copy
    import gzip
    from hyogo import server as hsrv
    root = ROOT
    fx = root / "tests" / "fixtures" / "cp2k_outputs"
    q = {"H": 1, "C": 4, "O": 6, "Zr": 12}
    kinds = {"files": {"basis": ["BASIS_MOLOPT_UZH_2026.2", "BASIS_ADMM_UZH"],
                       "potential": "POTENTIAL_UZH_2026.2"},
             "kinds": {e: {"q": n, "basis": f"DZVP-MOLOPT-PBE0-GTH-q{n}",
                           "potential": f"GTH-PBE0-q{n}", "aux_basis": f"admm-dzp-q{n}"}
                       for e, n in q.items()},
             "errors": {}}
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        parsed = {}
        for name in ("hse06-cellopt", "hse06-sp"):
            out = d / f"{name}.out"
            out.write_bytes(gzip.decompress((fx / f"{name}.out.gz").read_bytes()))
            parsed[name] = hsrv.cp2k_parse_output(str(out))
        f = parsed["hse06-cellopt"]["files"]
        assert f == {"final_xyz": "hse06-cellopt-FINAL-1_21.xyz",
                     "final_cif": "hse06-cellopt-FINAL-1_21.cif",
                     "basis": "BASIS_MOLOPT_UZH_2026.2", "potential": "POTENTIAL_UZH_2026.2",
                     "wfn": "hse06-cellopt-RESTART.wfn", "restart": "hse06-cellopt-1.restart"}, f
        f = parsed["hse06-sp"]["files"]
        assert f["final_xyz"] is None and f["final_cif"] is None and f["restart"] is None, f
        assert f["wfn"] == "hse06-sp-RESTART.wfn", f

        # a subsys standing in for the 456-atom cell: the primitive fixture,
        # with the conventional cell's atom and electron counts
        xyz = d / "uio66.xyz"
        xyz.write_text((root / "tests" / "fixtures" / "cp2k_inputs" / "uio66"
                        / "uio66_prim_114_ch108.xyz").read_text())
        sub = hsrv.cp2k_make_subsys(str(xyz), kinds, str(d / "subsys.inc"))
        sub = {**sub, "natoms": 456, "electrons": 1936}
        prev = parsed["hse06-cellopt"]
        r = hsrv.cp2k_make_input("sp", "pbe0", sub, kinds, out_path=str(d / "pbe0.inp"),
                                 stress=True, guess_from=prev, guess_dir="../hse06-cellopt/")
        wfn = "../hse06-cellopt/hse06-cellopt-RESTART.wfn"
        text = (d / "pbe0.inp").read_text()
        assert f"WFN_RESTART_FILE_NAME {wfn}" in text and "SCF_GUESS RESTART" in text, text
        assert r["guess"] == {"wfn": wfn, "from_project": "hse06-cellopt",
                              "checked": ["natoms", "electrons", "basis", "potential"],
                              "final_xyz": "hse06-cellopt-FINAL-1_21.xyz"}, r["guess"]
        assert not any("guess_from" in w for w in r["warnings"]), r["warnings"]
        assert not any("atomic guess" in w for w in r["warnings"]), r["warnings"]
        # same folder by default; an opt keeps the wfn as its @SET WFN
        r = hsrv.cp2k_make_input("cell_opt", "hse06", sub, kinds, out_path=str(d / "co.inp"),
                                 guess_from=parsed["hse06-sp"])
        assert r["guess"]["wfn"] == "hse06-sp-RESTART.wfn", r["guess"]
        assert r["settings"]["WFN"] == "hse06-sp-RESTART.wfn", r["settings"]
        # warnings: unfinished, unconverged SCF, unconverged optimisation
        bad = copy.deepcopy(prev)
        bad["finished"] = False
        bad["scf"]["not_converged"] = 2
        bad["opt"]["converged"] = False
        r = hsrv.cp2k_make_input("sp", "pbe0", sub, kinds, out_path=str(d / "w.inp"),
                                 guess_from=bad, guess_dir="../x")
        ws = [w for w in r["warnings"] if w.startswith("guess_from")]
        assert len(ws) == 3, r["warnings"]

        def other(**kv):
            p = copy.deepcopy(prev)
            for k, v in kv.items():
                if k in ("basis", "potential"):
                    p["files"][k] = v
                else:
                    p[k] = v
            return p
        other_kinds = copy.deepcopy(kinds)
        refusals = (
            (dict(guess_from=other(natoms=114)), "114 atoms"),
            (dict(guess_from=other(electrons=1932)), "1932 electrons"),
            (dict(guess_from=other(basis="BASIS_MOLOPT")), "can't move between bases"),
            (dict(guess_from=other(potential="GTH_POTENTIALS")), "other potentials"),
            (dict(guess_from=prev, guess_wfn="x.wfn"), "not both"),
            (dict(guess_dir="../x"), "goes with guess_from"),
            (dict(guess_from={"natoms": 456}), "cp2k_parse_output"),
            (dict(guess_from=prev, guess_dir="../a b"), "without spaces"),
        )
        for i, (kw, msg) in enumerate(refusals):
            dst = d / f"refused{i}.inp"
            try:
                hsrv.cp2k_make_input("sp", "pbe0", sub, other_kinds, out_path=str(dst), **kw)
            except ValueError as e:
                assert msg in str(e), (msg, e)
            else:
                raise AssertionError(f"cp2k_make_input accepted {kw}")
            assert not dst.exists(), dst
