import tempfile
from pathlib import Path

import numpy as np

from checks._common import TESTS

LABEL = 'yagura labels_compare finds the frame in another state (G38)'

SYMBOLS = ["Cr", "Cr", "C", "O", "O", "H", "H", "O"]


def frame(pos, energy, forces, moments, config_type):
    head = (f'{len(pos)}\nLattice="9 0 0 0 9 0 0 0 9" '
            'Properties=species:S:1:pos:R:3:REF_forces:R:3:REF_magmoms:R:1 '
            f'REF_energy={energy:.8f} config_type={config_type} pbc="T T T"\n')
    return head + "".join(f"{s} {p[0]:.8f} {p[1]:.8f} {p[2]:.8f} {f[0]:.8f} {f[1]:.8f} {f[2]:.8f} {m:.4f}\n"
                          for s, p, f, m in zip(SYMBOLS, pos, forces, moments))


def campaign_metrics(fa, fb, ea, eb, n):
    """compare_magmom_test.py's numbers: dE meV/atom, dF RMS and max meV/A."""
    df = np.linalg.norm(fb - fa, axis=1) * 1000
    return (eb - ea) / n * 1000, np.sqrt((df ** 2).mean()), df.max()


def yagura_labels_compare_finds_the_odd_state():
    """G38: labels_compare pairs two label sets by geometry (b reordered, a
    subset, plus a frame a lacks), reports dE per atom against the median
    method shift and the force difference the way the MXene campaign's
    compare_magmom_test.py did, flags only the frame in another spin state
    (forces, energy and a flipped Cr moment), writes one TSV row per pair,
    refuses index pairing of reordered sets, and on two real VASP runs of one
    Cr atom (NUPDOWN 6 and 4) gives their energy difference."""
    from yagura import server as ysrv
    rng = np.random.default_rng(7)
    n, nf = len(SYMBOLS), 6
    P = rng.uniform(0, 9, (nf, n, 3))
    E = rng.uniform(-80, -70, nf)
    F = rng.normal(0, 0.5, (nf, n, 3))
    M = np.array([3.0, 3.0, 0.0, -0.1, -0.1, 0.0, 0.0, 0.0])
    a_text = "".join(frame(P[i], E[i], F[i], M, "mxene") for i in range(nf))

    # b: frames 4, 0, 2, 1, 3 (frame 5 not relabelled), +10 meV/atom method
    # shift, small noise; frame 2 in another spin state; one structure a lacks
    shift, odd = 0.010, 2
    b_frames, want = [], {}
    for i in (4, 0, 2, 1, 3):
        e = E[i] + shift * n + rng.normal(0, 1e-4)
        f = F[i] + rng.normal(0, 0.005, (n, 3))
        m = M.copy()
        if i == odd:
            e += 0.020 * n
            f = f + rng.normal(0, 0.2, (n, 3))
            m[1] = -2.8
        pos = P[i] + rng.uniform(-2e-4, 2e-4, (n, 3))   # rewritten coordinates, within tol
        b_frames.append(frame(pos, e, f, m, "mxene"))
        want[i] = campaign_metrics(F[i], f, E[i], e, n)
    b_frames.append(frame(rng.uniform(0, 9, (n, 3)), -75.0, F[0], M, "extra"))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "a.extxyz").write_text(a_text)
        (tmp / "b.extxyz").write_text("".join(b_frames))
        r = ysrv.labels_compare(str(tmp / "a.extxyz"), str(tmp / "b.extxyz"))
        assert r["keys"]["a"] == r["keys"]["b"] == {"energy_key": "REF_energy", "forces_key": "REF_forces",
                                                     "moment_key": "REF_magmoms"}, r["keys"]
        assert r["pairs"] == 5 and r["frames"] == {"a": 6, "b": 6}, (r["pairs"], r["frames"])
        assert abs(r["median_shift_mev_atom"] - 10.0) < 0.1, r["median_shift_mev_atom"]
        assert r["flagged_count"] == 1, r["flagged"]
        fl = r["flagged"][0]
        de, rms, mx = want[odd]
        assert fl["a"] == odd and fl["b"] == 2, fl
        assert abs(fl["de_mev_atom"] - de) < 1e-3 and abs(fl["df_rms_mev_a"] - rms) < 1e-3 \
            and abs(fl["df_max_mev_a"] - mx) < 1e-3, (fl, want[odd])
        assert fl["flipped_sites"] == [2] and fl["opposed"] == {"Cr": [0, 1]}, fl
        assert len(fl["reasons"]) == 3, fl["reasons"]
        assert any("frames of b.extxyz have no match" in w for w in r["warnings"]), r["warnings"]
        assert any("1 frames of a.extxyz were not compared" in w for w in r["warnings"]), r["warnings"]
        rows = Path(r["out"]).read_text().splitlines()
        assert Path(r["out"]).name == "b_vs_a.tsv" and len(rows) == 6, rows[:2]
        cols = rows[0].split("\t")
        for line in rows[1:]:
            row = dict(zip(cols, line.split("\t")))
            de, rms, mx = want[int(row["a"])]
            assert abs(float(row["de_mev_atom"]) - de) < 1e-3 and abs(float(row["df_rms_mev_a"]) - rms) < 1e-3, row

        # refusals: reordered sets by index, the same file twice, an existing TSV
        for kw, msg in [({"pair": "index", "overwrite": True}, "differ in atoms or positions"),
                        ({"b_path": str(tmp / "a.extxyz")}, "the same file"),
                        ({}, "exists; pass overwrite=true")]:
            try:
                ysrv.labels_compare(**{"a_path": str(tmp / "a.extxyz"), "b_path": str(tmp / "b.extxyz"), **kw})
            except ValueError as e:
                assert msg in str(e), (kw, str(e))
            else:
                raise AssertionError(f"labels_compare accepted {kw}")

        # real data: one Cr atom, NUPDOWN 6 and NUPDOWN 4 (tests/fixtures/vasp_outputs)
        from hyogo import server as hsrv
        energies = {}
        for run in ("cr-nup6", "cr-nup4"):
            out = hsrv.vasp_parse_output(str(TESTS / "fixtures" / "vasp_outputs" / run / "OUTCAR.gz"))
            energies[run] = out["energy"]["toten"]
            (tmp / f"{run}.extxyz").write_text(
                f'1\nProperties=species:S:1:pos:R:3 REF_energy={energies[run]!r}\nCr 8.0 8.25 8.5\n')
        r = ysrv.labels_compare(str(tmp / "cr-nup6.extxyz"), str(tmp / "cr-nup4.extxyz"))
        want_de = (energies["cr-nup4"] - energies["cr-nup6"]) * 1000
        assert r["pairs"] == 1 and abs(r["de_mev_atom"]["mean"] - want_de) < 1e-3, (r["de_mev_atom"], want_de)
        assert r["keys"]["a"]["forces_key"] is None and r["df_rms_mev_a"] is None, r
        assert any("median shift is not a reference" in w for w in r["warnings"]), r["warnings"]
