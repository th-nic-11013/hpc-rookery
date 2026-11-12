import gzip
import json
import pathlib
import subprocess
import sys
import tempfile
from checks._common import ROOT

LABEL = 'yagura reads LAMMPS dumps and checks the framework'

SPEC = ["H", "C", "O", "Zr"]


def _dump_text(frames, style="ortho", cols="id type x y z"):
    """A LAMMPS dump of frames [(timestep, cell rows, origin, symbols-as-types, positions)]."""
    out = []
    for ts, H, o, types, X, ids in frames:
        out += ["ITEM: TIMESTEP", str(ts), "ITEM: NUMBER OF ATOMS", str(len(X))]
        if style == "ortho":
            out += ["ITEM: BOX BOUNDS pp pp pp"] + [f"{o[k]:.10f} {o[k] + H[k][k]:.10f}" for k in range(3)]
        elif style == "tri":
            xy, xz, yz = H[1][0], H[2][0], H[2][1]
            xlo, ylo, zlo = o
            xhi, yhi, zhi = xlo + H[0][0], ylo + H[1][1], zlo + H[2][2]
            out += ["ITEM: BOX BOUNDS xy xz yz pp pp pp",
                    f"{xlo + min(0, xy, xz, xy + xz):.10f} {xhi + max(0, xy, xz, xy + xz):.10f} {xy:.10f}",
                    f"{ylo + min(0, yz):.10f} {yhi + max(0, yz):.10f} {xz:.10f}",
                    f"{zlo:.10f} {zhi:.10f} {yz:.10f}"]
        else:                                           # general triclinic, LAMMPS 2024+
            out += ["ITEM: BOX BOUNDS abc origin pp pp pp"] + \
                   [" ".join(f"{v:.10f}" for v in H[k]) + f" {o[k]:.10f}" for k in range(3)]
        out.append("ITEM: ATOMS " + cols)
        for i in ids:
            row = {"id": i + 1, "type": types[i], "element": SPEC[types[i] - 1]}
            pos = cols.split()[2:]
            if "xs" in cols:
                import numpy as np
                v = (np.asarray(X[i]) - o) @ np.linalg.inv(np.asarray(H))
            else:
                v = X[i]
            row.update(zip(pos, map(float, v)))
            out.append(" ".join(f"{row[c]:.8f}" if isinstance(row[c], float) else str(row[c])
                                for c in cols.split()))
    return "\n".join(out) + "\n"


def yagura_lammps_dump_and_framework_check():
    """structure_to_extxyz on LAMMPS dumps (type map, frame ranges, info keys,
    orthogonal / restricted / general triclinic boxes, scaled coordinates,
    unsorted ids, .gz) and framework_check on excerpts of the real UiO-66
    runs: A2 600 K (frame 534 has 4 of 192 Zr-O > 2.7 A, the run's maximum;
    no frame dropped) and O2 1 GPa collapse (Zr CN down to 6, a shared
    mu3-OH proton, no frame dropped), then synthetic broken frames that
    must be dropped, and the persistent/transient Zr-O split."""
    import numpy as np
    from yagura import framework, lammps_dump
    from yagura import server as ysrv
    fx = ROOT / "tests" / "fixtures" / "lammps" / "uio66_dumps"
    a2, o2 = fx / "a2_600K_excerpt.dump.gz", fx / "o2_1gpa_excerpt.dump.gz"
    tmp = pathlib.Path(tempfile.mkdtemp())

    def refused(fn, text, *a, **k):
        try:
            fn(*a, **k)
        except ValueError as e:
            assert text in str(e), (text, str(e))
        else:
            raise AssertionError(f"{fn.__name__} accepted {a} {k}")

    # --- the reader on the real excerpt (7 frames, original timesteps kept)
    assert lammps_dump.is_dump(a2) and not lammps_dump.is_dump(ROOT / "tests/fixtures/structures/clean.xyz")
    assert lammps_dump.count_frames(a2) == 7
    steps = [f["timestep"] for f in lammps_dump.frames(a2, SPEC, ":")]
    assert steps == [0, 26650, 26700, 26750, 26800, 26850, 40000], steps
    assert [f["timestep"] for f in lammps_dump.frames(a2, SPEC, "1:6:2")] == [26650, 26750, 26850]
    assert [f["frame"] for f in lammps_dump.frames(a2, SPEC, "-2:")] == [5, 6]
    last = next(lammps_dump.frames(a2, SPEC, "-1"))
    assert last["frame"] == 6 and last["timestep"] == 40000
    assert dict(zip(*np.unique(last["symbols"], return_counts=True))) == {"C": 192, "H": 112, "O": 128, "Zr": 24}
    F = last["positions"] @ np.linalg.inv(last["cell"])
    assert F.min() >= 0 and F.max() < 1, "positions are wrapped into the cell"

    out = tmp / "a2.xyz"
    r = ysrv.structure_to_extxyz(str(a2), str(out), index="::3", specorder=SPEC, dt_fs=0.5,
                                 source="A2-600K", config_type="P1-600K")
    assert r["frames_written"] == 3 and r["natoms"] == 456 and r["electrons"] == 3248, r
    assert r["first"] == {"frame": 0, "timestep": 0, "volume": 9040.27, "time_ps": 0.0}, r
    assert r["last"]["timestep"] == 40000 and r["last"]["time_ps"] == 20.0, r
    assert json.loads(json.dumps(r)) == r
    lines = out.read_text().splitlines()
    assert len(lines) == 3 * 458 and lines[458] == "456"
    c = lines[459]
    for key in ("Lattice=", "timestep=26750", "time_ps=13.375000", " frame=3 ", "source=A2-600K ",
                f'source_path="{a2.resolve()}"', "config_type=P1-600K", 'pbc="T T T"'):
        assert key in c, (key, c)
    assert ysrv.structure_check(str(out))["natoms"] == 456
    rows = ysrv.framework_check(str(out), table_path=str(tmp / "a2.tsv"))["frames"]
    assert [(f["frame"], f["source"], f["source_frame"]) for f in rows] == \
        [(0, "A2-600K", 0), (1, "A2-600K", 3), (2, "A2-600K", 6)], rows
    head = (tmp / "a2.tsv").read_text().splitlines()[0].split("\t")
    assert head[:5] == ["frame", "source", "source_frame", "timestep", "volume"], head
    r = ysrv.structure_to_extxyz(str(a2), str(out), specorder=SPEC, dt_fs=0.5,
                                 time_range_ps=[13.3, 13.4], index=":", overwrite=True)
    assert [r["first"]["timestep"], r["last"]["timestep"], r["frames_written"]] == [26650, 26800, 4], r
    try:
        from ase.io import read
    except ModuleNotFoundError:
        pass
    else:
        at = read(out, index=":")
        i = at[1].info
        assert len(at) == 4 and i["timestep"] == 26700 and i["frame"] == 2, i
        assert i["source"] == "a2_600K_excerpt" and i["source_path"] == str(a2.resolve()), i
        assert abs(at[1].info["volume"] - at[1].cell.volume) < 1e-3

    refused(ysrv.structure_to_extxyz, "give specorder", str(a2), str(tmp / "n.xyz"))
    refused(ysrv.structure_to_extxyz, "specorder names 3", str(a2), str(tmp / "n.xyz"), specorder=SPEC[:3])
    refused(ysrv.structure_to_extxyz, "unknown element", str(a2), str(tmp / "n.xyz"),
            specorder=["H", "C", "O", "Q"])
    refused(ysrv.structure_to_extxyz, "exists", str(a2), str(out), specorder=SPEC)
    refused(ysrv.structure_to_extxyz, "no spaces", str(a2), str(tmp / "n.xyz"), specorder=SPEC,
            source="A3 300K")
    refused(ysrv.structure_to_extxyz, "needs dt_fs", str(a2), str(tmp / "n.xyz"), specorder=SPEC,
            time_range_ps=[1, 2])
    refused(ysrv.structure_to_extxyz, "select no frame", str(a2), str(tmp / "n.xyz"), specorder=SPEC,
            index="20:")
    assert not (tmp / "n.xyz").exists()
    refused(ysrv.structure_to_extxyz, "outside the 7 frames", str(a2), str(tmp / "n.xyz"),
            specorder=SPEC, index="-9")
    refused(ysrv.structure_to_extxyz, "for a LAMMPS dump",
            str(ROOT / "tests/fixtures/structures/clean.xyz"), str(tmp / "n.xyz"), specorder=SPEC)

    # --- box styles: one sheared cell written three ways, shuffled ids, an
    # origin off zero and some atoms an image away, must read back identically
    f0 = next(lammps_dump.frames(a2, SPEC, "0"))
    H = np.array([[20.9, 0, 0], [1.3, 20.7, 0], [-0.9, 0.8, 20.5]])
    Xs = (f0["positions"] @ np.linalg.inv(f0["cell"])) @ H
    origin = np.array([-1.5, 2.0, 0.5])
    shift = np.zeros_like(Xs)
    shift[::7] += H[0] + H[2]
    shift[3::11] -= H[1]
    Xd = Xs + origin + shift
    types = [SPEC.index(s) + 1 for s in f0["symbols"]]
    ids = np.random.default_rng(3).permutation(len(Xs))
    want_D, _ = framework._distances({"cell": H, "positions": Xs})
    for style, cols in (("tri", "id type x y z"), ("abc", "id type x y z"),
                        ("tri", "id element xs ys zs"), ("tri", "id type xu yu zu")):
        p = tmp / f"{style}.dump"
        p.write_text(_dump_text([(100, H.tolist(), origin, types, Xd.tolist(), ids)], style, cols))
        fr = next(lammps_dump.frames(p, SPEC, "0"))
        assert np.allclose(fr["cell"], H, atol=1e-8), (style, fr["cell"])
        assert list(fr["symbols"]) == list(f0["symbols"]), style
        got_D, _ = framework._distances(fr)
        near = want_D < 6.0       # bonds; beyond half a skewed cell's width the rounded image is not unique
        assert np.allclose(got_D[near], want_D[near], atol=2e-4), (style, cols)
    gz = tmp / "tri.dump.gz"
    gz.write_bytes(gzip.compress((tmp / "tri.dump").read_bytes()))
    assert next(lammps_dump.frames(gz, SPEC, "0"))["timestep"] == 100
    cut = tmp / "cut.dump"
    cut.write_text("".join((tmp / "tri.dump").read_text().splitlines(keepends=True)[:-5]))
    refused(lambda: list(lammps_dump.frames(cut, SPEC)), "cut short")

    # --- framework_check on the real excerpts
    r = ysrv.framework_check(str(a2), specorder=SPEC, dt_fs=0.5)
    assert r["ok"] and r["frames_dropped"] == 0 and r["frames_analysed"] == 7, r
    assert [f["metal_o_long"] for f in r["frames"]] == [0, 0, 4, 3, 1, 2, 1], r["frames"]
    mo = r["metal_o"]
    assert (mo["reference_bonds"], mo["max_long_at_once"], mo["frames_with_long"]) == (192, 4, 5), mo
    assert mo["persistent_count"] == 0 and mo["cn_histogram"].get("8", 0) > 150, mo
    assert {t: b["n"] for t, b in r["bonds"].items()} == {"C-H": 96, "C-C": 192, "C-O": 96, "O-H": 16}, r["bonds"]
    assert all(b["frames_broken"] == 0 and b["max"] < b["cutoff"] for b in r["bonds"].values()), r["bonds"]
    assert r["frames"][2]["time_ps"] == 13.35 and r["frames"][2]["metal_cn_min"] == 7
    assert json.loads(json.dumps(r)) == r

    r = ysrv.framework_check(str(o2), specorder=SPEC, table_path=str(tmp / "o2.tsv"))
    assert r["ok"] and r["frames_dropped"] == 0, r
    assert [f["metal_o_long"] for f in r["frames"]] == [0, 14, 12, 12], r["frames"]
    assert r["metal_o"]["cn_histogram"].get("6", 0) > 0 and r["metal_o"]["max_length"] > 5.5, r["metal_o"]
    assert r["shared_h_frames"] == 1 and r["shared_h_first"] == [2], r
    assert r["volume_range"] == [4831.93, 9040.27], r["volume_range"]
    rows = (tmp / "o2.tsv").read_text().splitlines()
    head = rows[0].split("\t")
    assert len(rows) == 5 and head[:3] == ["frame", "timestep", "volume"] and head[-2:] == ["drop", "reasons"], head
    assert "C-O_broken" in head and rows[2].split("\t")[head.index("metal_o_long")] == "14"
    refused(ysrv.framework_check, "exists", str(o2), specorder=SPEC, table_path=str(tmp / "o2.tsv"))

    # --- synthetic frames that must be dropped, against the A2 frame 0 reference
    el, X0, H0 = f0["symbols"], f0["positions"], f0["cell"]
    D0, _ = framework._distances(f0)
    hyd = np.nonzero(el == "H")[0]
    oh = [h for h in hyd if el[np.argmin(np.where(el == "H", np.inf, D0[h]))] == "O"]
    ch = [h for h in hyd if h not in oh]
    co = np.argwhere((D0 < 1.75) & (el[:, None] == "C") & (el[None, :] == "O"))[0]
    carb_o = co[1]
    cc = np.argwhere((D0 < 1.85) & (el[:, None] == "C") & (el[None, :] == "C"))
    ring_c = cc[0][0]
    far_c = int(np.argmax(np.where((el == "C") & np.isfinite(D0[ring_c]), D0[ring_c], -1)))

    def frame(X):
        return f"{len(el)}\nLattice=\"{' '.join(map(str, H0.ravel()))}\"\n" + \
            "".join(f"{s} {x:.6f} {y:.6f} {z:.6f}\n" for s, (x, y, z) in zip(el, X))
    bad = {}
    X = X0.copy(); X[ch[0]] = X[ch[1]] + [0.3, 0, 0]; bad["overlap"] = X            # noqa: E702
    X = X0.copy(); X[carb_o] = X[co[0]] + 2.0 * (X[carb_o] - X[co[0]]) / D0[co[0], co[1]]   # noqa: E702
    bad["broken C-O"] = X
    X = X0.copy(); other_o = int(np.argmin(np.where((el == "O") & (D0[oh[0]] > 2.0), D0[oh[0]], np.inf)))  # noqa
    v = X[oh[0]] - X[other_o]; X[oh[0]] = X[other_o] + 0.98 * v / np.linalg.norm(v); bad["proton transfer"] = X  # noqa
    X = X0.copy(); v = X[ring_c] - X[far_c]; X[far_c] = X[ring_c] - 1.5 * v / np.linalg.norm(v)  # noqa
    bad["new C-C"] = X
    traj = tmp / "bad.xyz"
    traj.write_text(frame(X0) + "".join(frame(X) for X in bad.values()))
    ref = tmp / "ref.xyz"
    ref.write_text(frame(X0))
    r = ysrv.framework_check(str(traj), reference_path=str(ref))
    assert r["frames_dropped"] == 4 and r["drop_frames"] == [1, 2, 3, 4], r
    for row, why in zip(r["frames"][1:], bad):
        assert why in row["reasons"], (why, row["reasons"])
    assert r["frames"][0]["reasons"] == [] and r["bonds"]["C-O"]["frames_broken"] == 1, r
    nan = tmp / "nan.xyz"
    Xn = X0.copy()
    Xn[5] = np.nan
    nan.write_text(frame(X0) + frame(Xn))
    refused(ysrv.framework_check, "non-finite", str(nan))
    one = ysrv.framework_check(str(ref))
    assert one["ok"] and "one frame and no reference_path" in one["warnings"][-1], one["warnings"]
    shuffled = tmp / "shuffled.xyz"
    shuffled.write_text(frame(X0).replace("\nZr ", "\nQq ", 1))
    refused(ysrv.framework_check, "different atoms", str(traj), reference_path=str(shuffled))

    # --- persistent vs transient: one Zr-O stretched for 12 frames, then for 3
    mu3 = [i for i in np.nonzero(el == "O")[0]
           if not ((D0[i] < 1.75) & (el == "C")).any() and not ((D0[i] < 1.35) & (el == "H")).any()]
    o = mu3[0]
    zr = int(np.argmin(np.where(el == "Zr", D0[o], np.inf)))
    v = X0[o] - X0[zr]
    Xs = X0.copy()
    Xs[o] = X0[zr] + (D0[zr, o] + 0.9) * v / np.linalg.norm(v)
    long_ = set(range(1, 13)) | {15, 16, 17}
    traj = tmp / "zro.xyz"
    traj.write_text("".join(frame(Xs if k in long_ else X0) for k in range(20)))
    r = ysrv.framework_check(str(traj), max_transient_frames=10)
    mo = r["metal_o"]
    assert r["ok"] and mo["excursions"] >= 2 and mo["longest_frames"] == 12, mo
    pers = [p for p in mo["persistent"] if p["o"] == f"O{o}" and p["metal"] == f"Zr{zr}"]
    assert pers == [{"metal": f"Zr{zr}", "o": f"O{o}", "frames": 12, "first_frame": 1, "last_frame": 12}], mo
    r = ysrv.framework_check(str(traj), index="::2")
    assert r["frames_analysed"] == 10 and r["metal_o"]["longest_frames"] == 6, r["metal_o"]

    # --- without ASE: the dump tools still work (numpy only)
    no_ase = ("import sys, importlib.abc\n"
              "class Block(importlib.abc.MetaPathFinder):\n"
              "    def find_spec(self, name, path=None, target=None):\n"
              "        if name == 'ase' or name.startswith('ase.'):\n"
              "            raise ModuleNotFoundError(f'No module named {name!r}', name=name)\n"
              "sys.meta_path.insert(0, Block())\n"
              "from yagura import server\n"
              f"r = server.structure_to_extxyz({str(o2)!r}, {str(tmp / 'noase.xyz')!r}, "
              f"index=':', specorder={SPEC!r})\n"
              "print(r['frames_written'], server.framework_check(r['out'])['metal_o']['max_long_at_once'])\n")
    out = subprocess.run([sys.executable, "-c", no_ase], cwd=ROOT, capture_output=True,
                         text=True, timeout=120)
    assert out.stdout.split() == ["4", "14"], (out.stdout, out.stderr[-2000:])

    import shutil
    shutil.rmtree(tmp)
