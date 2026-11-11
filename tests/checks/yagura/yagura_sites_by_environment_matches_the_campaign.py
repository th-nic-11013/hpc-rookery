import tempfile
from pathlib import Path

import numpy as np

from checks._common import TESTS

LABEL = 'yagura sites_by_environment matches the campaign scripts (G37)'


def yagura_sites_by_environment_matches_the_campaign():
    """G37: on a real MXene + water frame (tests/fixtures/vasp_sites/README.md)
    sites_by_environment gives what the campaign's stray_moment.py,
    water_charge.py and label_spins.py printed; the nearest periodic image
    is right in a skewed (hexagonal) cell where rounding fractional
    coordinates is not; metals can be named; a frame without a cell is
    refused."""
    from hyogo import server as hsrv
    from yagura import server as ysrv
    from yagura.analysis import sites as sites_mod
    fx = TESTS / "fixtures" / "vasp_sites" / "cr2co2-water"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        hsrv.vasp_parse_output(str(fx / "OUTCAR.gz"), sites_out=str(tmp / "sites.extxyz"))
        r = ysrv.sites_by_environment(str(tmp / "sites.extxyz"))
        c = r["classes"]
        assert {k: v["count"] for k, v in c.items()} == {"C": 12, "H": 128, "metal": 24,
                                                          "slab_O": 33, "water_O": 55}, c.keys()
        assert r["metals"] == ["Cr"] and r["cell_lengths"] == [7.9511, 9.1811, 31.16], r
        # water_charge.py: Ow tot 5.2222, Ow p 3.6474, Os tot 5.0809, sum|m| Ow 0.012, E_F 2.623, |mag| 64.00
        assert abs(c["water_O"]["charges"]["mean"] - 5.2222) < 5e-5, c["water_O"]["charges"]
        assert abs(c["water_O"]["charges_p"]["mean"] - 3.6474) < 5e-5, c["water_O"]["charges_p"]
        assert abs(c["slab_O"]["charges"]["mean"] - 5.0809) < 5e-5, c["slab_O"]["charges"]
        assert abs(c["water_O"]["REF_magmoms"]["sum_abs"] - 0.012) < 5e-4, c["water_O"]["REF_magmoms"]
        assert abs(float(r["info"]["e_fermi"]) - 2.623) < 5e-4 and abs(float(r["info"]["magnetization"]) - 64.00) < 5e-3
        # stray_moment.py: Cr30:+3.08(nH=0,dM=0.00)  Cr14:+3.07(...)  Cr27:+2.98(...)
        top = [(d["element"], d["atom"], round(d["REF_magmoms"], 2), d["nH"], round(d["dM"], 2))
               for d in r["largest"][:3]]
        assert top == [("Cr", 30, 3.08, 0, 0.0), ("Cr", 14, 3.07, 0, 0.0), ("Cr", 27, 2.98, 0, 0.0)], top
        # label_spins.py: anti 0, mean+ 2.68
        ms = r["metal_spin"]["all"]
        assert ms["antiparallel"] == 0 and abs(ms["mean_positive"] - 2.68) < 5e-3 and ms["n"] == 24, ms
        tsv = Path(r["out"]).read_text().splitlines()
        assert Path(r["out"]).name == "sites.environments.tsv" and len(tsv) == 253, tsv[:1]
        assert tsv[0].split("\t")[:5] == ["atom", "element", "class", "nH", "dM"], tsv[0]

        # metals named: with none, nothing is water or slab O
        r2 = ysrv.sites_by_environment(str(tmp / "sites.extxyz"), metals=[], out_path=str(tmp / "x.tsv"))
        assert "metal" not in r2["classes"] and r2["metal_spin"] is None, r2["classes"].keys()
        assert any("no metal atoms" in w for w in r2["warnings"]), r2["warnings"]

        # no cell: refused
        (tmp / "nocell.extxyz").write_text("2\nProperties=species:S:1:pos:R:3\nCr 0 0 0\nO 1 0 0\n")
        try:
            ysrv.sites_by_environment(str(tmp / "nocell.extxyz"))
        except ValueError as e:
            assert "no Lattice" in str(e), e
        else:
            raise AssertionError("a frame without a cell was accepted")

    # a hexagonal cell (gamma 120 deg): Cr at 0,0 and O at fractional 0.45, 0.55.
    # Rounding the fractional difference gives 2.34 A; the nearest image is 1.52 A.
    cell = np.array([[3.0, 0, 0], [-1.5, 1.5 * 3 ** 0.5, 0], [0, 0, 10.0]])
    pos = np.array([[0.0, 0, 0], [0.45, 0.55, 0]]) @ cell
    brute = min(np.linalg.norm(pos[1] - pos[0] + np.array([i, j, 0]) @ cell)
                for i in range(-3, 4) for j in range(-3, 4))
    sites, _ = sites_mod.classify(["Cr", "O"], pos, cell)
    d = np.array([0.45, 0.55, 0]); d -= np.round(d)
    assert abs(np.linalg.norm(d @ cell) - 2.338) < 1e-3, "the rounding case is not set up"
    assert abs(sites[1]["dM"] - brute) < 1e-9 and abs(brute - 1.522) < 1e-3, (sites[1], brute)
    assert sites[1]["class"] == "slab_O" and sites[0]["class"] == "metal", sites
