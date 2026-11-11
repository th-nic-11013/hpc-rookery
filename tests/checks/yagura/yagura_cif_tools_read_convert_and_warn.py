import pathlib
import tempfile
from checks._common import TESTS

LABEL = 'yagura cif tools read, convert and warn'


def yagura_cif_tools_read_convert_and_warn():
    """cif_inspect and structure_to_extxyz on a small disordered CIF and a
    molecule with no cell (needs ASE, the [yagura] extra CI installs)."""
    from yagura import server as ysrv
    fx = TESTS / "fixtures" / "structures"
    r = ysrv.cif_inspect(str(fx / "disordered.cif"))
    assert r["cell"] == {"lengths": [6.0, 6.0, 6.0], "angles": [90.0, 90.0, 90.0]}, r
    assert r["partial"] == ["H1A", "H1B"] and r["partial_groups"] == {"1": ["H1A"], "2": ["H1B"]}, r
    assert r["keys"]["_space_group_it_number"] == "1" and len(r["sites"]) == 4, r
    with tempfile.TemporaryDirectory() as d:
        out = pathlib.Path(d) / "cell.xyz"
        c = ysrv.structure_to_extxyz(str(fx / "disordered.cif"), str(out))
        assert c["formula"] == "CH2O" and c["electrons"] == 16, c
        assert c["multiplicities"] == "1, 3, 5, ..." and "partial sites (H1A, H1B)" in c["warnings"][0], c
        assert ysrv.structure_check(str(out))["natoms"] == 4
        box = ysrv.structure_to_extxyz(str(fx / "h2_nocell.xyz"), str(pathlib.Path(d) / "h2.xyz"), vacuum=6)
        assert box["cell"]["lengths"][:2] == [12.0, 12.0] and box["warnings"] == [], box
        for args, kw, text in (((str(fx / "h2_nocell.xyz"), str(pathlib.Path(d) / "x.xyz")), {}, "no complete cell"),
                               ((str(fx / "disordered.cif"), str(out)), {}, "exists"),
                               ((str(fx / "disordered.cif"), str(fx / "disordered.cif")), {}, "must differ")):
            try:
                ysrv.structure_to_extxyz(*args, **kw)
            except ValueError as e:
                assert text in str(e), e
            else:
                raise AssertionError(f"structure_to_extxyz accepted {args}")
