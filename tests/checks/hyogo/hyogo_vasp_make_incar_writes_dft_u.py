import shutil
import tempfile
from pathlib import Path

from checks._common import TESTS

LABEL = 'hyogo vasp_make_incar writes DFT+U like the campaign (G42)'

# The MXene campaign's U pilot (magmom_test/make_u_pilot.py, 2026-11) wrote
# its LDAU tags with this function; copied here (values and logic only) as
# the reference the tool must reproduce.
U_MP = {"Cr": 3.7, "Mo": 4.38, "V": 3.25, "W": 6.2}


def campaign_ldau(species):
    return {
        "LDAU": True, "LDAUTYPE": 2,
        "LDAUL": " ".join("2" if el in U_MP else "-1" for el, _ in species),
        "LDAUU": " ".join(f"{U_MP.get(el, 0.0):g}" for el, _ in species),
        "LDAUJ": " ".join("0" for _ in species),
        "LMAXMIX": 4,
    }


def poscar(species, counts):
    n = sum(counts)
    return ("stand-in\n1.0\n10 0 0\n0 10 0\n0 0 10\n"
            f"{' '.join(species)}\n{' '.join(map(str, counts))}\nDirect\n"
            + "".join(f"0.{i:03d} 0.5 0.5\n" for i in range(n)))


def potcar(labels):
    return "".join(f"  PAW_PBE {lb} 01Jan2000\n   TITEL  = PAW_PBE {lb} 01Jan2000\n" for lb in labels)


def hyogo_vasp_make_incar_writes_dft_u():
    """G42: vasp_make_incar's ldau writes LDAU, LDAUTYPE, LDAUL, LDAUU and
    LDAUJ in POSCAR species order and LMAXMIX from the highest l; the "mp"
    preset gives the campaign's U pilot INCAR on the same files; LDAU lists
    in a base INCAR are checked; potcar_path checks the TITEL order; and
    each refusal comes before anything is written."""
    from hyogo import server as hsrv
    from hyogo.vasp import incar
    fi = TESTS / "fixtures" / "vasp_inputs"
    base_text = (fi / "INCAR.label").read_text()
    pos_text = (fi / "POSCAR").read_text()
    species = incar.read_poscar_species(pos_text)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        pos, base = tmp / "POSCAR", tmp / "INCAR.label"
        shutil.copy(fi / "POSCAR", pos)
        shutil.copy(fi / "INCAR.label", base)

        # the tool, preset "mp", against the campaign's builder on the same files
        common = {"ENCUT": 520, "LORBIT": 11, "NELM": 300}
        want_text, _, _ = incar.make_incar(base_text, pos_text, settings={**common, **campaign_ldau(species)},
                                           magmom={"Cr": 3})
        (tmp / "POTCAR").write_text(potcar(["C", "Cr_pv", "H", "O"]))
        r = hsrv.vasp_make_incar(str(pos), str(base), settings=common, magmom={"Cr": 3}, ldau="mp",
                                 potcar_path=str(tmp / "POTCAR"), out_path=str(tmp / "INCAR"))
        got = incar.parse(Path(r["out"]).read_text())
        assert got == incar.parse(want_text), {k: (got.get(k), incar.parse(want_text).get(k))
                                               for k in set(got) | set(incar.parse(want_text))
                                               if got.get(k) != incar.parse(want_text).get(k)}
        assert r["ldau"] == {"LDAU": ".TRUE.", "LDAUTYPE": "2", "LDAUL": "-1 2 -1 -1",
                             "LDAUU": "0 3.7 0 0", "LDAUJ": "0 0 0 0", "LMAXMIX": "4",
                             "preset": "mp"}, r["ldau"]
        assert r["potcar"] == ["C", "Cr_pv", "H", "O"], r["potcar"]
        assert not [w for w in r["warnings"] if "LDAU" in w or "LMAXMIX" in w], r["warnings"]

        # a POTCAR in another order is refused, and nothing is written
        (tmp / "POTCAR.bad").write_text(potcar(["Cr_pv", "C", "H", "O"]))
        try:
            hsrv.vasp_make_incar(str(pos), str(base), magmom={"Cr": 3}, ldau="mp",
                                 potcar_path=str(tmp / "POTCAR.bad"), out_path=str(tmp / "INCAR.b"))
        except ValueError as e:
            assert "POTCAR order ['Cr', 'C', 'H', 'O'] != POSCAR species ['C', 'Cr', 'H', 'O']" in str(e), e
        else:
            raise AssertionError("a POTCAR in the wrong order was accepted")
        assert not (tmp / "INCAR.b").exists()

    def tags(pos_species, counts, base="", **kw):
        text, _, info = incar.make_incar(base, poscar(pos_species, counts), **kw)
        p = incar.parse(text)
        return {k: p.get(k) for k in ("LDAU", "LDAUTYPE", "LDAUL", "LDAUU", "LDAUJ", "LMAXMIX")}, info

    # explicit U; repeated species blocks get one value each
    t, _ = tags(["O", "Cr", "O"], [2, 1, 2], ldau={"Cr": 3.0})
    assert t == {"LDAU": ".TRUE.", "LDAUTYPE": "2", "LDAUL": "-1 2 -1", "LDAUU": "0 3 0",
                 "LDAUJ": "0 0 0", "LMAXMIX": "4"}, t
    # f shell: LMAXMIX 6; a higher LMAXMIX in the base is kept
    t, _ = tags(["Ce", "O"], [1, 2], ldau={"Ce": {"u": 5, "l": 3}})
    assert (t["LDAUL"], t["LDAUU"], t["LMAXMIX"]) == ("3 -1", "5 0", "6"), t
    t, _ = tags(["Cr", "O"], [1, 2], base="LMAXMIX = 6\n", ldau={"Cr": 3.7})
    assert t["LMAXMIX"] == "6", t

    # warnings: MP without O or F; Dudarev with J; a base +U INCAR with low LMAXMIX
    _, info = tags(["Cr", "C"], [2, 1], ldau="mp")
    assert any("only to structures with O or F" in w for w in info["warnings"]), info["warnings"]
    _, info = tags(["Cr", "O"], [1, 2], ldau={"Cr": {"u": 4, "j": 1}})
    assert any("only U - J" in w for w in info["warnings"]), info["warnings"]
    _, info = tags(["Cr", "O"], [1, 2], base="LDAU = .TRUE.\nLDAUL = 2 -1\nLDAUU = 3.7 0\nLDAUJ = 0 0\n")
    assert any("LMAXMIX 2 (default) with a U on l = 2" in w for w in info["warnings"]), info["warnings"]

    # refusals
    for kw, base, msg in [
        ({"ldau": {"Fe": 5.3}}, "", "ldau names ['Fe'], which the POSCAR doesn't have"),
        ({"ldau": "mp", "settings": {"LDAUU": "1 0"}}, "", "LDAUU comes from ldau"),
        ({"ldau": "mp", "settings": {"LMAXMIX": 4}}, "", "LMAXMIX comes from ldau"),
        ({"ldau": "nope"}, "", "no U preset 'nope'"),
        ({"ldau": {"Cr": 3.7}, "ldautype": 3}, "", "LDAUTYPE 3"),
        ({}, "LDAU = .TRUE.\nLDAUU = 3.7 0 0\n", "LDAUU = 3.7 0 0 has 3 values, the POSCAR 2 species blocks"),
    ]:
        try:
            tags(["Cr", "O"], [1, 2], base=base, **kw)
        except ValueError as e:
            assert msg in str(e), (kw, str(e))
        else:
            raise AssertionError(f"make_incar accepted {kw} with base {base!r}")
    try:
        tags(["Zr", "C", "O"], [2, 1, 2], ldau="mp")
    except ValueError as e:
        assert "has a U in preset 'mp'" in str(e), e
    else:
        raise AssertionError("preset mp on a structure with no U species was accepted")
