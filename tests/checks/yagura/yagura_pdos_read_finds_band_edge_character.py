import pathlib
import tempfile
from checks._common import TESTS

LABEL = 'yagura pdos_read finds band-edge character (G46)'


def yagura_pdos_read_finds_band_edge_character():
    """pdos_read on .pdos files written in CP2K's own Fortran formats, the
    2026.2 header (E(HOCO), Total column) and the older one: the same
    HOMO, LUMO, gap and edge character (valence and conduction on C p,
    Zr above), a broadened DOS written; a missing kind file, a run with
    no LUMO and an APPEND file are reported; files of different runs, a
    kind twice and a non-.pdos file are refused; spin files give one gap
    across both spins. hyogo's pdos option writes the 2026.2 &DOS/&PDOS
    layout (cp2k --check'd), single points only (G46)."""
    import numpy as np
    from yagura import server as ysrv
    H = 27.211386245988
    E = np.array([-0.40, -0.35, -0.30, -0.28, -0.26, -0.25, -0.10, -0.095, -0.05, 0.00])
    occ = np.array([2.0] * 6 + [0.0] * 4)
    frac = {"Zr": [.3, .2, .1, .05, .05, .02, .05, .05, .9, .8],
            "O": [.5, .5, .3, .25, .15, .08, .05, .05, .05, .1],
            "C": [.15, .25, .5, .6, .7, .85, .85, .85, .05, .08],
            "H": [.05, .05, .1, .1, .1, .05, .05, .05, 0, .02]}
    chans = {"Zr": ["s", "p", "d"], "O": ["s", "p"], "C": ["s", "p"], "H": ["s"]}
    split = {"Zr": [.05, .05, .9], "O": [.2, .8], "C": [.1, .9], "H": [1.0]}

    def write(d, stem, new=True, shift=0.0, occ_=occ, spin="", blocks=1):
        paths = []
        for k, (kind, f) in enumerate(frac.items(), 1):
            out = []
            for _ in range(blocks):
                if new:  # qs_pdos.F, v2026.2: (A42, n(10X,A8)) and (I8,2X,2F16.6, n(2X,F16.8))
                    out += [f"# Projected DOS for atomic kind {kind} at iteration step i = 0",
                            f"# E(Fermi) = {-0.25:12.6f} a.u. = {-0.25 * H:12.6f} eV",
                            f"# E(HOCO)  = {-0.25:12.6f} a.u. = {-0.25 * H:12.6f} eV",
                            f"{'#          MO Energy[a.u.]      Occupation':42s}"
                            + "".join(f"{'':10s}{c:>8s}" for c in ["Total"] + chans[kind])]
                else:
                    out += [f"# Projected DOS for atomic kind {kind} at iteration step i = 0, "
                            f"E(Fermi) = {-0.25:12.6f} a.u.",
                            f"{'#     MO Eigenvalue [a.u.]      Occupation':42s}"
                            + "".join(f"{'':10s}{c:>8s}" for c in chans[kind])]
                for i in range(len(E)):
                    w = [f[i] * x for x in split[kind]]
                    out.append(f"{i + 1:8d}  {E[i] + shift:16.6f}{occ_[i]:16.6f}"
                               + "".join(f"  {v:16.8f}" for v in ([sum(w)] if new else []) + w))
            path = d / f"{stem}-{spin}k{k}-1.pdos"
            path.write_text("\n".join(out) + "\n")
            paths.append(str(path))
        return paths

    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        res = [ysrv.pdos_read(write(d, stem, new)) for stem, new in (("new", True), ("old", False))]
        for r in res:
            sp = r["spins"]["none"]
            assert r["warnings"] == [], r["warnings"]
            assert sp["homo_ev"] == round(-0.25 * H, 4) and sp["lumo_ev"] == round(-0.10 * H, 4), sp
            assert r["gap_ev"] == round(0.15 * H, 4), r
            assert sp["valence"]["states"] == 2 and next(iter(sp["valence"]["kinds"])) == "C", sp
            assert next(iter(sp["conduction"]["channels"])) == "C p", sp
            assert sp["conduction"]["kinds"]["Zr"] < 0.1, sp
        assert res[0]["spins"] == res[1]["spins"]
        assert res[0]["out"] == str(d / "new-pdos.dat"), res[0]["out"]
        dat = pathlib.Path(res[0]["out"]).read_text().splitlines()
        assert dat[1].split() == ["#", "E", "total", "Zr", "O", "C", "H"], dat[1]
        rows = np.array([l.split() for l in dat[2:]], float)
        assert abs(np.trapezoid(rows[:, 1], rows[:, 0]) - len(E)) < 0.05, "DOS integrates to the MO count"

        r = ysrv.pdos_read(write(d, "new", True)[:3], overwrite=True)
        assert any("missing" in w for w in r["warnings"]), r["warnings"]
        r = ysrv.pdos_read(write(d, "occ", True, occ_=np.full(len(E), 2.0)))
        assert r["gap_ev"] is None and any("NLUMO" in w for w in r["warnings"]), r
        r = ysrv.pdos_read(write(d, "app", True, blocks=2))
        assert any("APPEND" in w for w in r["warnings"]), r["warnings"]
        a = write(d, "sp", True, spin="ALPHA_")
        b = write(d, "sp", True, spin="BETA_", shift=0.01)
        r = ysrv.pdos_read(a + b)
        assert set(r["spins"]) == {"alpha", "beta"}, r
        assert r["gap_ev"] == round((-0.10 - (-0.25 + 0.01)) * H, 4), r
        other = write(d, "oth", True, shift=0.02)
        junk = d / "junk.pdos"
        junk.write_text("1 2 3\n")
        for paths, text in ((write(d, "new", True)[:2] + other[2:], "different MOs"),
                            ([str(d / "new-k1-1.pdos"), str(d / "app-k1-1.pdos")], "given twice"),
                            ([str(junk)], "not a CP2K .pdos"),
                            (a + write(d, "cs", True), "mixed"),
                            ([], "empty")):
            try:
                ysrv.pdos_read(paths, overwrite=True)
            except ValueError as e:
                assert text in str(e), (text, e)
            else:
                raise AssertionError(f"pdos_read did not refuse ({text})")

    # hyogo writes the CP2K 2026.2 layout: NLUMO on &DOS, &PDOS inside it
    from hyogo import server as hsrv
    fx = TESTS / "fixtures/cp2k_inputs/uio66"
    q = {"Zr": 12, "O": 6, "C": 4, "H": 1}
    k = {"files": {"basis": ["BASIS_MOLOPT_UZH_2026.2", "BASIS_ADMM_UZH"],
                   "potential": "POTENTIAL_UZH_2026.2"}, "errors": {},
         "kinds": {e: {"q": n, "basis": f"DZVP-MOLOPT-PBE0-GTH-q{n}", "potential": f"GTH-PBE0-q{n}",
                       "aux_basis": f"admm-dzp-q{n}"} for e, n in q.items()}}
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "u.xyz").write_text((fx / "uio66_prim_114_ch108.xyz").read_text())
        sub = hsrv.cp2k_make_subsys(str(d / "u.xyz"), k, str(d / "subsys.inc"))
        r = hsrv.cp2k_make_input("sp", "hse06", sub, k, project="pd", cutoff=600, print_gap=True,
                                 pdos={"nlumo": 20, "components": True}, hfx={"memory_mb": 2000})
        t = [l.strip() for l in pathlib.Path(r["out"]).read_text().splitlines()]
        i = t.index("&DOS")
        assert t[i - 6:i + 7] == ["&PRINT", "&MO_CUBES", "NHOMO 1", "NLUMO 1", "WRITE_CUBE FALSE",
                                  "&END MO_CUBES", "&DOS", "NLUMO 20", "&PDOS", "COMPONENTS T",
                                  "&END PDOS", "&END DOS", "&END PRINT"], t[i - 6:i + 7]
        assert t.count("&PRINT") == t.count("&END PRINT"), "one &DFT%PRINT"
        assert r["pdos"] == "pd-k<n>-1.pdos, one per kind", r
        for kw, text in (({"run_type": "geo_opt", "pdos": {}}, "single point"),
                         ({"run_type": "sp", "pdos": {"nlumo": 0}}, "positive"),
                         ({"run_type": "sp", "pdos": {"nlum": 5}}, "unknown pdos field"),
                         ({"run_type": "sp", "pdos": {"components": "T"}}, "components is true")):
            try:
                hsrv.cp2k_make_input(kw["run_type"], "hse06", sub, k, project="x", cutoff=600,
                                     pdos=kw["pdos"], hfx={"memory_mb": 2000}, overwrite=True)
            except ValueError as e:
                assert text in str(e), (text, e)
            else:
                raise AssertionError(f"cp2k_make_input did not refuse ({text})")
