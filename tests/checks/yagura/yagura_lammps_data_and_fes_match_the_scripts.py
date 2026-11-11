import pathlib
import subprocess
import sys
import tempfile
from checks._common import ROOT

LABEL = 'yagura lammps data and fes match the scripts'


def yagura_lammps_data_and_fes_match_the_scripts():
    """structure_to_lammps_data writes, byte for byte, the .data, .types.in
    and read-back .xyz that HOKUSAI's prep_structures.py wrote for the
    UiO-66 primitive cell (needs ASE); colvar_fes writes what
    do_block_fes.py writes, 1D unweighted and 2D bias-weighted."""
    import numpy as np
    from yagura import server as ysrv
    root = ROOT
    st = root / "tests" / "fixtures" / "lammps" / "hokusai_bench" / "structures"
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        r = ysrv.structure_to_lammps_data(str(st / "uio66_prim_114.xyz"), str(d / "uio66-prim.data"))
        for mine, theirs in (("uio66-prim.data", "uio66-prim.data"), ("uio66-prim.types.in", "uio66-prim.types.in"),
                             ("uio66-prim.lammps.xyz", "uio66-prim.xyz")):
            assert (d / mine).read_bytes() == (st / theirs).read_bytes(), mine
        assert r["elements"] == ["H", "C", "O", "Zr"] and r["pair_coeff"] == "pair_coeff * * 1 6 8 40", r
        assert r["rotated"] and r["cell"]["triclinic"] and r["natoms"] == 114, r
        r = ysrv.structure_to_lammps_data(str(st / "uio66_prim_114.xyz"), str(d / "s.data"),
                                          specorder=["H", "C", "N", "O", "Zr"])
        assert "pair_coeff * * 1 6 7 8 40" in (d / "s.types.in").read_text() and r["warnings"][0].startswith("specorder has ['N']")
        for kw, msg in ((dict(out_path=str(d / "uio66-prim.data")), "exists"),
                        (dict(out_path=str(d / "x.lmp")), "ends in .data"),
                        (dict(out_path=str(d / "y.data"), specorder=["H", "C", "O"]), "lacks ['Zr']")):
            try:
                ysrv.structure_to_lammps_data(str(st / "uio66_prim_114.xyz"), **kw)
            except ValueError as e:
                assert msg in str(e), (msg, e)
            else:
                raise AssertionError(f"structure_to_lammps_data accepted {kw}")

        rng = np.random.default_rng(0)
        n = 5000
        phi = np.round(np.clip(rng.normal(-1.2, 0.5, n), -3.14, 3.14), 6)
        psi = np.round(np.clip(rng.normal(2.0, 0.6, n), -3.14, 3.14), 6)
        bias = np.round(rng.normal(5, 2, n), 6)
        kbt = 0.0083144626 * 300
        (d / "COLVAR").write_text("#! FIELDS time phi psi metad.bias\n" + "".join(
            f"{i} {a:.6f} {b:.6f} {c:.6f}\n" for i, (a, b, c) in enumerate(zip(phi, psi, bias))))
        w = np.exp(bias / kbt - np.max(bias / kbt))
        (d / "one.dat").write_text("".join(f"{a:.6f}\n" for a in phi))
        (d / "two.dat").write_text("".join(f"{a:.6f} {b:.6f} {float(c)!r}\n" for a, b, c in zip(phi, psi, w)))
        script = root / "tests" / "fixtures" / "plumed" / "master-ISDD-2" / "do_block_fes.py"
        g = ["-3.141593", "3.141593", "50"]
        grid = [-3.141593, 3.141593, 50]
        for name, argv, cvs, b in (("one", ["1", *g], ["phi"], None),
                                   ("two", ["2", *g, *g], ["phi", "psi"], ["metad.bias"])):
            subprocess.run([sys.executable, "-I", str(script), f"{name}.dat", *argv, repr(kbt), "500"],
                           cwd=d, check=True, capture_output=True, timeout=120)
            (d / "fes.500.dat").rename(d / f"ref-{name}.dat")
            r = ysrv.colvar_fes(str(d / "COLVAR"), cvs, [grid] * len(cvs), 300, 500, bias=b,
                                out_path=str(d / f"mine-{name}.dat"))
            assert (d / f"mine-{name}.dat").read_text() == (d / f"ref-{name}.dat").read_text(), name
            assert r["nblocks"] == 10 and r["weights"] == ("bias" if b else "uniform"), r
        for kw, msg in ((dict(cvs=["chi"], grid=[grid]), "not in the COLVAR"),
                        (dict(cvs=["phi"], grid=[[-1, 1, 50]]), "outside the grid"),
                        (dict(cvs=["phi"], grid=[grid], block_size=4000), "need at"),
                        (dict(cvs=["phi"], grid=[grid], units="hartree"), "units is one of")):
            try:
                ysrv.colvar_fes(str(d / "COLVAR"), **{"temperature": 300, "block_size": 500,
                                                      "out_path": str(d / "bad.dat"), **kw})
            except ValueError as e:
                assert msg in str(e), (msg, e)
            else:
                raise AssertionError(f"colvar_fes accepted {kw}")
