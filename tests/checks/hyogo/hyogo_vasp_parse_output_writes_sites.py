import gzip
import tempfile
from pathlib import Path

from checks._common import TESTS

LABEL = 'hyogo vasp_parse_output writes per-site moments and charges (G37)'


def hyogo_vasp_parse_output_writes_sites():
    """G37: sites_out writes the final frame as extxyz with every per-site
    column VASP printed (moments and charges per channel and tot), the
    final positions, forces and cell; the values are the OUTCAR's own
    rows; the tool's reply stays small; yagura's labels_compare reads the
    file (two copies compare as identical); an OUTCAR without LORBIT is
    refused before anything is written."""
    from hyogo import server as hsrv
    from yagura import server as ysrv
    fx = TESTS / "fixtures" / "vasp_sites" / "cr2co2-water"
    outcar_lines = gzip.decompress((fx / "OUTCAR.gz").read_bytes()).decode("latin-1").splitlines()

    def rows(title):
        """The last block's rows, read the plain way: [atom, s, p, d, tot]."""
        start = max(i for i, l in enumerate(outcar_lines) if l.strip() == title)
        out = []
        for l in outcar_lines[start + 4:]:
            p = l.split()
            if p and p[0].isdigit():
                out.append([float(x) for x in p[1:]])
            elif out:
                break
        return out

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        out = tmp / "sites.extxyz"
        r = hsrv.vasp_parse_output(str(fx / "OUTCAR.gz"), sites_out=str(out))
        assert r["sites"] == {"out": str(out), "natoms": 252, "columns": [
            "species", "pos", "REF_forces", "magmoms_s", "magmoms_p", "magmoms_d", "REF_magmoms",
            "charges_s", "charges_p", "charges_d", "charges"]}, r["sites"]
        assert "positions" not in r and "vectors" not in r["forces"] \
            and "site_moments" not in r["magnetization"], "sites_out must not grow the reply"
        lines = out.read_text().splitlines()
        assert lines[0] == "252" and len(lines) == 254, lines[:1]
        head = lines[1]
        for part in ('Lattice="7.951081025 0.000000000 0.000000000 0.000000000 9.181117541 '
                     '0.000000000 0.000000000 0.000000000 31.160022489"',
                     "REF_energy=-1444.0492337", "e_fermi=2.6228", "magnetization=63.9987", "converged=T"):
            assert part in head, (part, head[:200])
        mag, chg = rows("magnetization (x)"), rows("total charge")
        assert len(mag) == len(chg) == 252
        for i, line in enumerate(lines[2:]):
            p = line.split()
            assert [float(x) for x in p[7:11]] == mag[i] and [float(x) for x in p[11:15]] == chg[i], (i, line)
        assert lines[2].split()[0] == "C" and lines[2 + 36].split()[0] == "H", "species order lost"

        # yagura reads it: a copy compares as identical, moments included
        (tmp / "copy.extxyz").write_text(out.read_text())
        c = ysrv.labels_compare(str(out), str(tmp / "copy.extxyz"))
        assert c["keys"]["a"] == {"energy_key": "REF_energy", "forces_key": "REF_forces",
                                  "moment_key": "REF_magmoms"}, c["keys"]
        assert c["pairs"] == 1 and c["flagged_count"] == 0 and c["df_rms_mev_a"]["max"] == 0.0, c

        # refusals: an existing sites_out; an OUTCAR without LORBIT (no per-site blocks)
        try:
            hsrv.vasp_parse_output(str(fx / "OUTCAR.gz"), sites_out=str(out))
        except ValueError as e:
            assert "exists; pass overwrite=true" in str(e), e
        else:
            raise AssertionError("an existing sites_out was overwritten")
        bare = tmp / "bare.extxyz"
        try:
            hsrv.vasp_parse_output(str(TESTS / "fixtures" / "vasp_outputs" / "cr-nup6" / "OUTCAR.gz"),
                                   sites_out=str(bare))
        except ValueError as e:
            assert "LORBIT >= 10" in str(e), e
        else:
            raise AssertionError("an OUTCAR without per-site blocks wrote a sites file")
        assert not bare.exists()
