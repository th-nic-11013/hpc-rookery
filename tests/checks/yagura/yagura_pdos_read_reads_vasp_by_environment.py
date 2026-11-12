import tempfile
from pathlib import Path

import numpy as np

from checks._common import TESTS

LABEL = 'yagura pdos_read reads a VASP vasprun by environment (G37)'


def _vasprun(path, partial=True, lm=True):
    """A small vasprun.xml in VASP's layout: Cr at the origin, O at the
    cube's centre (10 A cell), ISPIN 1, E_F 1.0 eV, a 10 meV grid. O's py
    is 1 state/eV for E - E_F in [-3, -1.5]; Cr's dxy (or d) is 2 states/eV
    in [-0.3, 0.3]. Bands: 0.0 and 0.5 eV full, 2.0 and 2.5 eV empty."""
    ef = 1.0
    e = np.round(np.arange(-500, 501) * 0.01, 2) + ef
    rel = e - ef
    chans = ["s", "py", "pz", "px", "dxy", "dyz", "dz2", "dxz", "x2-y2"] if lm else ["s", "p", "d"]
    cr = np.zeros((len(e), len(chans)))
    cr[(rel >= -0.3 - 1e-9) & (rel <= 0.3 + 1e-9), chans.index("dxy" if lm else "d")] = 2.0
    o = np.zeros((len(e), len(chans)))
    o[(rel >= -3 - 1e-9) & (rel <= -1.5 + 1e-9), chans.index("py" if lm else "p")] = 1.0
    tot = cr.sum(1) + o.sum(1)

    def rows(a):
        return "".join("<r>" + " ".join(f"{x:.4f}" for x in r) + "</r>\n" for r in a)

    pdos = ""
    if partial:
        fields = "".join(f"<field>{c}</field>" for c in ["energy", *chans])
        ions = "".join(f'<set comment="ion {i + 1}"><set comment="spin 1">'
                       + rows(np.column_stack([e, a])) + "</set></set>" for i, a in enumerate((cr, o)))
        pdos = f"<partial><array>{fields}<set>{ions}</set></array></partial>"
    xml = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<modeling>
 <incar><i type="int" name="LORBIT">{11 if lm else 10}</i></incar>
 <atominfo><array name="atoms"><set><rc><c>Cr</c><c>1</c></rc><rc><c>O </c><c>2</c></rc></set></array></atominfo>
 <calculation>
  <eigenvalues><array><set><set comment="spin 1"><set comment="kpoint 1">
   <r>0.0 1.0</r><r>0.5 1.0</r><r>2.0 0.0</r><r>2.5 0.0</r>
  </set></set></set></array></eigenvalues>
  <dos><i name="efermi">{ef}</i>
   <total><array><set><set comment="spin 1">{rows(np.column_stack([e, tot, np.cumsum(tot) * 0.01]))}</set></set></array></total>
   {pdos}
  </dos>
  <projected><eigenvalues><array><set><set comment="spin 1"><set comment="kpoint 1"><r>0 1</r></set></set></set></array></eigenvalues></projected>
 </calculation>
 <structure name="finalpos"><crystal><varray name="basis"><v>10 0 0</v><v>0 10 0</v><v>0 0 10</v></varray></crystal>
  <varray name="positions"><v>0 0 0</v><v>0.5 0.5 0.5</v></varray></structure>
</modeling>
"""
    Path(path).write_text(xml)
    return str(path)


def yagura_pdos_read_reads_vasp_by_environment():
    """G37 part 3: pdos_read on a vasprun.xml(.gz). On a real MXene + water
    frame (tests/fixtures/vasp_sites/README.md) the water-O states per atom
    either side of E_F and the top of the water-O PDOS are what the
    campaign's water_pdos.py printed, the environment classes are
    sites_by_environment's, the metal gets no gap and the 191 meV grid is
    warned about. On a small vasprun in VASP's layout: lm-resolved channels
    fold into s/p/d, the numbers near E_F and the edge are exact, the gap
    comes from the eigenvalues, named groups work, a file without LORBIT
    gives the total DOS only; mixing in CP2K files, or CP2K files with
    group_by, is refused."""
    from yagura import server as ysrv
    fx = TESTS / "fixtures" / "vasp_sites" / "cr2co2-water"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "vasprun.xml.gz"
        src.write_bytes((fx / "vasprun.xml.gz").read_bytes())
        r = ysrv.pdos_read([str(src)], group_by="environment")
        assert Path(r["out"]).name == "vasprun-pdos.dat" and r["code"] == "vasp", r["out"]
        assert r["e_fermi_ev"] == 2.6228 and r["grid_mev"] == 191.0 and r["natoms"] == 252, r
        assert r["environment"]["metals"] == ["Cr"] and r["gap_ev"] is None, (r["environment"], r["gap_ev"])
        assert any("191 meV" in w and "NEDOS" in w for w in r["warnings"]), r["warnings"]
        assert any("partly occupied" in w for w in r["warnings"]), r["warnings"]
        a, b = r["spins"]["alpha"]["groups"], r["spins"]["beta"]["groups"]
        assert {k: v["atoms"] for k, v in a.items()} == {"C": 12, "H": 128, "metal": 24,
                                                          "slab_O": 33, "water_O": 55}, a.keys()
        # water_pdos.py: spin 1 [-0.5,0]=0.0003 [0,+0.5]=0.0005; spin 2 0.0000, 0.0004; top -3.44 eV
        for g, below, above in ((a["water_O"], 0.0003, 0.0005), (b["water_O"], 0.0000, 0.0004)):
            assert abs(g["below_ef"] - below) <= 5e-5 and abs(g["above_ef"] - above) <= 5e-5, g
            assert abs(g["edge_ev"] + 3.44) <= 5e-3, g
        assert abs(sum(a["water_O"]["channels_near_ef"].values())
                   - a["water_O"]["below_ef"] - a["water_O"]["above_ef"]) < 1e-5, a["water_O"]
        dat = Path(r["out"]).read_text().splitlines()
        assert dat[1].split()[2:] == ["alpha:total", "alpha:C", "alpha:H", "alpha:metal", "alpha:slab_O",
                                      "alpha:water_O", "beta:total", "beta:C", "beta:H", "beta:metal",
                                      "beta:slab_O", "beta:water_O"], dat[1]
        by_el = ysrv.pdos_read([str(src)], overwrite=True)["spins"]["alpha"]["groups"]
        assert {k: v["atoms"] for k, v in by_el.items()} == {"C": 12, "Cr": 24, "H": 128, "O": 88}, by_el

        # small vasprun: exact numbers, lm channels, gap, named groups
        (tmp / "small").mkdir()
        r = ysrv.pdos_read([_vasprun(tmp / "small" / "vasprun.xml")], groups={"first": [1]})
        s = r["spins"]["none"]
        assert r["channels"][:2] == ["s", "py"] and r["gap_ev"] == 1.5 and not r["warnings"], r
        assert (s["homo_ev"], s["lumo_ev"], s["partly_occupied_states"]) == (0.5, 2.0, 0), s
        cr, o = s["groups"]["Cr"], s["groups"]["O"]
        assert abs(cr["below_ef"] - 0.62) < 1e-6 and abs(cr["above_ef"] - 0.60) < 1e-6, cr
        assert cr["channels_near_ef"] == {"s": 0.0, "p": 0.0, "d": 1.22}, cr["channels_near_ef"]
        assert cr["edge_ev"] == 0.3 and o["edge_ev"] == -1.5 and o["below_ef"] == 0.0, (cr, o)
        assert s["groups"]["first"] == cr, s["groups"]["first"]
        env = ysrv.pdos_read([str(tmp / "small" / "vasprun.xml")], group_by="environment", overwrite=True,
                             environment={"metals": ["Cr"]})
        assert set(env["spins"]["none"]["groups"]) == {"metal", "other_O"}, env["spins"]["none"]["groups"]

        # no LORBIT: total only
        (tmp / "nolorbit").mkdir()
        r = ysrv.pdos_read([_vasprun(tmp / "nolorbit" / "vasprun.xml", partial=False)])
        assert "groups" not in r["spins"]["none"] and any("LORBIT" in w for w in r["warnings"]), r

        # refusals
        cp2k = tmp / "x-k1-1.pdos"
        cp2k.write_text("# not read\n")
        for args, kw, msg in (([str(tmp / "small" / "vasprun.xml"), str(cp2k)], {}, "pass it alone"),
                              ([str(cp2k)], {"group_by": "environment"}, "are for a vasprun"),
                              ([str(tmp / "small" / "vasprun.xml")], {"group_by": "kind", "overwrite": True}, "'element' or"),
                              ([str(tmp / "small" / "vasprun.xml")], {"groups": {"x": [3]}, "overwrite": True}, "run 1..2"),
                              ([str(tmp / "nolorbit" / "vasprun.xml")], {"groups": {"x": [1]}, "overwrite": True},
                               "LORBIT >= 10")):
            try:
                ysrv.pdos_read(args, **kw)
            except ValueError as e:
                assert msg in str(e), (msg, e)
            else:
                raise AssertionError(f"accepted: {kw or args}")
