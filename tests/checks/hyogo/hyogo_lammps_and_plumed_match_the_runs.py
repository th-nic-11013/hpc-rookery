import pathlib
import tempfile
from checks._common import ROOT

LABEL = 'hyogo lammps and plumed tools match the runs'


def hyogo_lammps_and_plumed_match_the_runs():
    """lammps_make_input writes HOKUSAI's MLMD benchmark inputs (valid,
    time) after expanding includes and variables; lammps_parse_log gives
    parse_mlmd.py's numbers on two benchmark logs and reads the PLUMED
    logs; plumed_make_input writes the smoke-test and masterclass PLUMED
    inputs (differences named below); each refusal fires."""
    import importlib.util
    import re
    from hyogo import server as hsrv
    root = ROOT
    fx = root / "tests" / "fixtures"
    bench = fx / "lammps" / "hokusai_bench"

    def lnorm(path, var):
        out = []
        for line in pathlib.Path(path).read_text().splitlines():
            s = line.split("#", 1)[0].strip()
            if not s:
                continue
            w = re.sub(r"\$\{(\w+)\}", lambda m: var[m.group(1)], s).split()
            out += lnorm(w[1], var) if w[0] == "include" else [" ".join(w)]
        return out

    far = "/lustre/home/thnic123/software/mlip/2026.10/bench"
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "h2o-64.data").write_text("")
        data = {"data": str(d / "h2o-64.data"), "elements": ["H", "O"],
                "atomic_numbers": [1, 8], "natoms": 192}
        var = {"inputs": str(bench / "inputs"), "data": f"{far}/structures/h2o-64.data",
               "types": str(bench / "structures" / "h2o-64.types.in"),
               "model": f"{far}/models/mace-mp-0-medium-f32.pt", "seed": "4928459",
               "nnvt": "196", "nnve": "588", "nwarm": "10", "nsteps": "36"}
        for mode, steps in (("valid", {"nvt": 196, "nve": 588}), ("time", {"warm": 10, "timed": 36})):
            r = hsrv.lammps_make_input(data, var["model"], mode, steps, data_name=var["data"])
            assert r["out"] == str(d / f"in.{mode}.lmp"), r
            got, want = lnorm(r["out"], {}), lnorm(bench / "inputs" / f"md_{mode}.in", var)
            assert got == want, (mode, [l for l in got if l not in want], [l for l in want if l not in got])
        r = hsrv.lammps_make_input(data, var["model"], "nvt", {"run": 400000}, dump_every=50,
                                   restart_every=20000, plumed_file="plumed.dat")
        text = pathlib.Path(r["out"]).read_text()
        for line in ("fix             plumed all plumed plumedfile plumed.dat outfile plumed.out",
                     "dump_modify     traj element H O sort id", "write_restart   md.restart.final"):
            assert line in text, line
        assert r["simulated_ps"] == 200.0 and r["files"][-1] == "plumed.dat", r
        r = hsrv.lammps_make_input(data, var["model"], "nvt", {"run": 10}, restart_from="md.restart.final",
                                   out_path=str(d / "cont.lmp"))
        text = pathlib.Path(r["out"]).read_text()
        assert "read_restart    md.restart.final" in text and "velocity" not in text and "run             0" not in text

        # G41: MACE + D3 writes A2's pair and neighbour lines (fixtures/lammps/uio66_d3),
        # and fix plumed comes before the thermostat (O1 failed on the reverse order)
        d3fx = fx / "lammps" / "uio66_d3"
        uio = {"data": str(d / "uio66.data"), "elements": ["H", "C", "O", "Zr"],
               "atomic_numbers": [1, 6, 8, 40], "natoms": 456}
        mp0 = f"{far}/models/mace-mp-0-medium-f32.pt"
        a2var = {"data": "uio66.data", "pair": str(d3fx / "pair_both.in"), "model": mp0,
                 "func": "pbe", "rc": "10.5835442", "rcn": "10.5835442", "temp": "300.0",
                 "seed": "4928759", "every": "50", "nsteps": "40000"}
        r = hsrv.lammps_make_input(uio, mp0, "npt", {"run": 40000}, restart_every=10000,
                                   dispersion={"functional": "pbe"}, plumed_file="plumed.dat",
                                   out_path=str(d / "npt.lmp"))
        got = lnorm(r["out"], {})
        want = lnorm(d3fx / "npt.in", a2var)
        keep = ("pair_style", "pair_coeff", "neighbor", "neigh_modify")
        assert [l for l in got if l.startswith(keep)] == [l for l in want if l.startswith(keep)], \
            ([l for l in got if l.startswith(keep)], [l for l in want if l.startswith(keep)])
        npt = lambda lines: next(l.split()[2:] for l in lines if l.startswith("fix") and " npt " in l)
        assert npt(got) == npt(want), (npt(got), npt(want))
        fixes = [l.split()[1] for l in got if l.startswith("fix ")]
        assert fixes == ["plumed", "thermostat"], fixes
        assert r["dispersion"] == {"type": "d3", "functional": "pbe", "damping": "bj",
                                   "cutoff_A": 10.5835442, "cn_cutoff_A": 10.5835442}, r
        assert not r["warnings"], r["warnings"]
        # a restart re-declares the pair style (restart files don't keep it)
        r = hsrv.lammps_make_input(uio, mp0, "npt", {"run": 10}, restart_from="a2.restart",
                                   dispersion={"functional": "pbe"}, out_path=str(d / "c.lmp"))
        got = lnorm(r["out"], {})
        assert got.index("read_restart a2.restart") < next(
            i for i, l in enumerate(got) if l.startswith("pair_style hybrid/overlay")), got
        # warnings fire only on their case
        matpes = f"{far}/../models/mace-matpes-r2scan-0/mace-matpes-r2scan-0-f32.pt"
        for model, disp, want_w in ((matpes, {"functional": "r2scan"}, None),
                                    (matpes, {"functional": "pbe"}, "trained on r2scan"),
                                    (mp0, {"functional": "pbe", "cutoff_A": 21.17}, "+48 %"),
                                    ("/x/finetuned.pt", {"functional": "b97-d"}, "has not run on HOKUSAI")):
            r = hsrv.lammps_make_input(uio, model, "nvt", {"run": 10}, dispersion=disp,
                                       out_path=str(d / "w.lmp"), overwrite=True)
            w = [x for x in r["warnings"] if "D3" in x]
            assert (not w) if want_w is None else (len(w) == 1 and want_w in w[0]), (disp, w)
        for disp, msg in (({"functional": "pbe", "damping": "bk"}, "damping is one of"),
                          ({"functional": "pbe", "rc": 10}, "unknown keys"),
                          ({"functional": "pbe", "cutoff_A": 10, "cn_cutoff_A": 20}, "beyond cutoff_A"),
                          ({"functional": "pbe", "cutoff_A": 0}, "above 0"),
                          ({"damping": "bj"}, "needs functional"),
                          ({"functional": "PBE"}, "lower case")):
            try:
                hsrv.lammps_make_input(uio, mp0, "nvt", {"run": 10}, dispersion=disp,
                                       out_path=str(d / "x.lmp"), overwrite=True)
            except ValueError as e:
                assert msg in str(e), (msg, e)
            else:
                raise AssertionError(f"lammps_make_input accepted dispersion={disp}")

        for kw, msg in ((dict(mode="nvt", steps={"run": 10}, timestep_fs=1.5), "hydrogen"),
                        (dict(mode="valid", steps={"run": 10}), "steps for mode 'valid'"),
                        (dict(mode="time", steps={"warm": 1, "timed": 2}, dump_every=5), "benchmark run"),
                        (dict(mode="nvt", steps={"run": 10}, model="m.model"), ".pt"),
                        (dict(mode="nvt", steps={"run": 10}, data={"data": "x"}), "structure_to_lammps_data"),
                        (dict(mode="nvt", steps={"run": 10}), "exists"),
                        (dict(mode="md", steps={"run": 10}), "mode is one of")):
            args = {"data": data, "model": var["model"], **kw}
            try:
                hsrv.lammps_make_input(**args)
            except ValueError as e:
                assert msg in str(e), (msg, e)
            else:
                raise AssertionError(f"lammps_make_input accepted {kw}")

    spec = importlib.util.spec_from_file_location("parse_mlmd", bench / "parse_mlmd.py")
    pm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pm)
    stage = bench / "runs" / "9619420" / "stage3"
    for run in ("h2o-64_f32_1x56_valid_r1", "h2o-64_f32_1x56_time_r1"):
        ref = pm.run_info(stage / run)
        r = hsrv.lammps_parse_log(str(stage / run / "log.lammps"))
        assert r["finished"] and r["natoms"] == ref["atoms"] == 192 and r["threads"] == 56, r
        assert r["pe0"] == ref["pe0"] and abs(r["s_per_step"] - ref["t_step"]) < 1e-12, (r, ref)
        assert abs(r["ns_per_day"] - ref["ns_day"]) < 1e-12, (r, ref)
        if "valid" in run:
            assert abs(r["T_tail_mean"] - ref["T_nvt"]) < 1e-9 and r["T_range"] == list(ref["T_range"])
            assert abs(r["nve"]["nve_drift_meV_atom_ps"] - ref["drift"]) < 1e-12, (r["nve"], ref)
            assert abs(r["nve"]["nve_fluct_meV_atom"] - ref["e_std"]) < 1e-12
            assert abs(r["nve"]["nve_ps"] - ref["nve_ps"]) < 1e-12
            assert [x["ensemble"] for x in r["runs"]] == ["none", "nvt", "nve"], r["runs"]
        else:
            assert r["nve"] is None and [x["ensemble"] for x in r["runs"]] == ["none", "nvt", "nvt"]
    r = hsrv.lammps_parse_log(str(fx / "lammps" / "lugano-6a" / "log.lammps"))
    assert r["finished"] and r["natoms"] == 1000 and r["ns_per_day"] is None, r  # lj units: no time
    assert [(x["ensemble"], x["fixes"], x["target_T"]) for x in r["runs"]] == [("nvt", ["nvt", "plumed"], 1.0)]
    r = hsrv.lammps_parse_log(str(fx / "lammps" / "hokusai_smoke" / "log.melt_plumed"))
    assert r["finished"] and r["runs"][0]["fixes"] == ["nve", "plumed"], r
    with tempfile.TemporaryDirectory() as d:
        cut = pathlib.Path(d) / "log.lammps"
        cut.write_text("".join((stage / "h2o-64_f32_1x56_valid_r1" / "log.lammps")
                               .read_text().splitlines(True)[:150]) + "ERROR: Lost atoms: original 192 current 190\n")
        r = hsrv.lammps_parse_log(str(cut))
        assert not r["finished"] and r["error"].startswith("ERROR: Lost atoms"), r
        assert any("did not finish" in w for w in r["warnings"]), r["warnings"]

    def pnorm(path):
        """(label, action) and the set of KEY=VALUE per action, comments dropped,
        '...' continuations joined, numbers compared as numbers."""
        out, buf = [], None
        for line in pathlib.Path(path).read_text().splitlines():
            s = line.split("#", 1)[0].strip()
            if not s:
                continue
            if buf is not None:
                if s == "...":
                    out.append(buf)
                    buf = None
                else:
                    buf += " " + s
            elif s.endswith("..."):
                buf = s[:-3].strip()
            else:
                out.append(s)

        def tok(t):
            k, _, v = t.partition("=")
            try:
                return f"{k}={float(v)!r}"
            except ValueError:
                return t
        res = []
        for s in out:
            w = s.split()
            head = (w.pop(0),) if w[0].endswith(":") else ()
            res.append((head + (w.pop(0),), frozenset(tok(t) for t in w)))
        return res

    ca = [int(x) for x in re.search(r"GYRATION ATOMS=(\S+)", (fx / "plumed" / "master-ISDD-1" / "plumed.dat")
                                    .read_text()).group(1).split(",")]
    tors = [{"label": "phi", "type": "TORSION", "atoms": "@phi-2"},
            {"label": "psi", "type": "TORSION", "atoms": "@psi-2"}]
    metad = [{"label": "metad", "type": "METAD", "arg": "phi", "pace": 500, "height": 1.2,
              "biasfactor": 8, "sigma": 0.35, "grid_min": "-pi", "grid_max": "pi"}]
    temp_added = (("metad:", "METAD"), "TEMP=300.0")
    enevol = [{"label": "ene", "type": "ENERGY"}, {"label": "vol", "type": "VOLUME"}]
    ecv = [{"label": "ecv", "type": "ECV_MULTITHERMAL_MULTIBARIC", "arg": ["ene", "vol"],
            "temp_min": 300, "temp_max": 600, "pressure_bar": 1, "pressure_min_bar": 1,
            "pressure_max_bar": 10000}]
    opes_x = [{"label": "opes", "type": "OPES_EXPANDED", "arg": "ecv", "pace": 50,
               "observation_steps": 10, "print_stride": 500}]
    cases = (
        (fx / "lammps" / "hokusai_smoke" / "plumed.dat",
         dict(cvs=[{"label": "d", "type": "DISTANCE", "atoms": [1, 2]}],
              bias=[{"label": "r", "type": "RESTRAINT", "arg": "d", "at": 1.0, "kappa": 1.0}]), None, 0),
        (fx / "plumed" / "master-ISDD-1" / "plumed.dat",
         dict(cvs=[{"label": "r", "type": "GYRATION", "atoms": ca},
                   {"label": "co", "type": "COORDINATION", "groupa": ca, "r_0": 0.80}], print_stride=1), None, 0),
        # the masterclass ran GROMACS, which passes the temperature to PLUMED;
        # LAMMPS does not, so the tool writes TEMP
        (fx / "plumed" / "master-ISDD-2" / "plumed.dat",
         dict(cvs=tors, bias=metad, molinfo="diala.pdb", temperature=300, print_args=["phi", "psi"]),
         temp_added, 0),
        # the reweighting input also histograms and writes F(s) in PLUMED (7 lines:
        # REWEIGHT_BIAS, HISTOGRAM x2, CONVERT_TO_FES x2, DUMPGRID x2); here that
        # step is yagura's colvar_fes, which adds block errors
        (fx / "plumed" / "master-ISDD-2" / "plumed_reweight.dat",
         dict(cvs=tors, bias=metad, molinfo="diala.pdb", temperature=300, mode="reweight"), None, 7),
        # G40: the hand-written OPES_EXPANDED input of the UiO-66 O1 run; pressures
        # given in bar and converted to eV/A^3
        (fx / "plumed" / "o1-opes-expanded" / "plumed.dat",
         dict(cvs=enevol, ecvs=ecv, bias=opes_x, temperature=300, print_stride=50,
              units={"length": "A", "energy": "eV", "time": "ps"}), None, 0))
    with tempfile.TemporaryDirectory() as d:
        rs = []
        for i, (ref, kw, added, dropped) in enumerate(cases):
            r = hsrv.plumed_make_input(out_path=str(pathlib.Path(d) / f"p{i}.dat"), **kw)
            rs.append(r)
            got, want = pnorm(r["out"]), pnorm(ref)
            if added:
                got = [(h, t - {added[1]}) if h == added[0] else (h, t) for h, t in got]
            missing = [x for x in want if x not in got]
            assert [x for x in got if x not in want] == [] and len(missing) == dropped, (ref, got, missing)
        assert rs[3]["print"] == {"file": "COLVAR_REWEIGHT", "stride": 1, "args": ["phi", "psi", "metad.bias"]}
        r = rs[4]
        assert r["outputs"] == ["COLVAR", "DELTAFS"] and any("25000 steps" in w for w in r["warnings"]), r
        # 1 GPa in kJ/mol/nm^3 (PLUMED's default units): 10^9 Pa x 1e-27 m^3 x N_A / 1000
        assert abs(hsrv.plumed_input.bar_to_plumed(1e4, None) - 602.214076) < 1e-6
        assert abs(hsrv.plumed_input.bar_to_plumed(1, {"length": "A", "energy": "eV"}) - 6.241509e-7) < 1e-12
        # OPES_METAD and _EXPLORE, and the single-temperature ECV
        r = hsrv.plumed_make_input([{"label": "v", "type": "VOLUME"}], str(pathlib.Path(d) / "om.dat"),
                                   bias=[{"label": "o", "type": "OPES_METAD_EXPLORE", "arg": "v",
                                          "pace": 500, "barrier": 0.5}],
                                   units={"length": "A", "energy": "eV"}, temperature=300)
        text = pathlib.Path(r["out"]).read_text()
        assert "o: OPES_METAD_EXPLORE ARG=v PACE=500 BARRIER=0.5 FILE=KERNELS TEMP=300" in text, text
        assert r["outputs"] == ["COLVAR", "KERNELS"] and any("no sigma" in w for w in r["warnings"]), r
        r = hsrv.plumed_make_input(enevol[:1], str(pathlib.Path(d) / "mt.dat"), temperature=300,
                                   ecvs=[{"label": "ecv", "type": "ECV_MULTITHERMAL", "arg": "ene",
                                          "temp_min": 300, "temp_max": 500, "temp_steps": 8}],
                                   bias=[{**opes_x[0], "print_stride": 10}])
        assert "ecv: ECV_MULTITHERMAL ARG=ene TEMP=300 TEMP_MIN=300 TEMP_MAX=500 TEMP_STEPS=8" \
            in pathlib.Path(r["out"]).read_text(), r
        mr = {"label": "m", "type": "MOVINGRESTRAINT", "arg": "d", "step": [0, 50000, 100000],
              "at": [2.3, 4.5, 2.3], "kappa": [100, 100, 100]}
        r = hsrv.plumed_make_input([{"label": "d", "type": "DISTANCE", "atoms": [1, 2]}],
                                   str(pathlib.Path(d) / "mr.dat"), bias=[mr], units={"length": "A"})
        assert "m: MOVINGRESTRAINT ARG=d AT0=2.3 KAPPA0=100 STEP0=0 AT1=4.5 KAPPA1=100 STEP1=50000" \
            in pathlib.Path(r["out"]).read_text() and r["warnings"] == [], r
        d1 = {"label": "d", "type": "DISTANCE", "atoms": [1, 2]}
        for args, kw, msg in (
                (([{"label": "d", "type": "RMSD"}],), {}, "type is one of"),
                (([d1, d1],), {}, "used twice"),
                (([{"label": "d.x", "type": "DISTANCE", "atoms": [1, 2]}],), {}, "must start"),
                (([{"label": "d", "type": "DISTANCE", "atoms": [0, 2]}],), {}, "1-based"),
                (([{"label": "d", "type": "DISTANCE", "atoms": [1, 2, 3]}],), {}, "takes 2 atoms"),
                (([{"label": "d", "type": "DISTANCE", "atoms": [1, 9]}],), {"natoms": 4}, "beyond"),
                (([d1],), {"bias": [{"label": "b", "type": "RESTRAINT", "arg": "e", "at": 1, "kappa": 1}]}, "not a label"),
                (([d1],), {"bias": [{"label": "b", "type": "RESTRAINT", "arg": "d.x", "at": 1, "kappa": 1}]}, "no component"),
                (([d1],), {"bias": [{"label": "b", "type": "METAD", "arg": "d", "sigma": 0.1}]}, "missing"),
                (([d1],), {"bias": [{**metad[0], "arg": "d", "biasfactor": 1}]}, "biasfactor > 1"),
                (([d1],), {"bias": [{**metad[0], "arg": "d"}]}, "needs temperature"),
                (([d1],), {"mode": "reweight", "temperature": 300}, "exactly one METAD"),
                (([{"label": "t", "type": "TORSION", "atoms": "@phi-2"}],), {}, "needs molinfo"),
                # G40 refusals
                ((enevol,), {"ecvs": [{**ecv[0], "arg": ["vol", "ene"]}], "bias": opes_x,
                             "temperature": 300}, "in that order"),
                ((enevol,), {"ecvs": ecv, "bias": opes_x}, "needs temperature"),
                ((enevol,), {"ecvs": [{k: v for k, v in ecv[0].items() if k != "pressure_bar"}],
                             "bias": opes_x, "temperature": 300}, "missing ['pressure_bar']"),
                ((enevol,), {"ecvs": ecv, "temperature": 300}, "not used by an OPES_EXPANDED"),
                ((enevol,), {"bias": [{**opes_x[0], "arg": "ene"}], "temperature": 300},
                 "names ECVs"),
                ((enevol,), {"ecvs": ecv, "bias": opes_x + [{"label": "r", "type": "RESTRAINT",
                             "arg": "ecv", "at": 1, "kappa": 1}], "temperature": 300}, "only OPES_EXPANDED"),
                ((enevol,), {"ecvs": ecv, "bias": [{**opes_x[0], "temp": 300}], "temperature": 300},
                 "unknown ['temp']"),
                ((enevol,), {"ecvs": ecv, "bias": opes_x, "temperature": 300, "units": "natural"},
                 "need real units"),
                ((enevol,), {"bias": [{"label": "o", "type": "OPES_METAD", "arg": "vol", "pace": 500}],
                             "temperature": 300}, "missing ['barrier']"),
                ((enevol,), {"bias": [{"label": "o", "type": "OPES_METAD", "arg": "vol", "pace": 500,
                                       "barrier": 40}]}, "needs temperature")):
            try:
                hsrv.plumed_make_input(*args, str(pathlib.Path(d) / "bad.dat"), **kw)
            except ValueError as e:
                assert msg in str(e), (msg, e)
            else:
                raise AssertionError(f"plumed_make_input accepted {args} {kw}")
