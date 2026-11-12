import json
import os
import pathlib
import subprocess
import sys
import tempfile
from checks._common import ROOT

LABEL = 'yagura structure_check flags and stays separate'


def yagura_structure_check_flags_and_stays_separate():
    """yagura's structure_check on the fixtures, its server starting with
    an empty HOME, and the boundary that keeps it splittable: yagura
    imports nothing from the rookery packages, and they import nothing
    from yagura."""
    import ast
    from yagura import server as ysrv
    root = ROOT
    fx = root / "tests" / "fixtures" / "structures"

    r = ysrv.structure_check(str(fx / "flagged.xyz"))
    assert not r["ok"] and r["natoms"] == 5, r
    assert {(f["pair"], f["kind"]) for f in r["flags"]} == {("C-H", "short_xh"), ("O-O", "overlap")}, r
    # plain Python types only, so the result is the same after JSON
    assert all(type(k) is str for k in r["composition"]), r["composition"]
    assert json.loads(json.dumps(r)) == r
    # minimum image: N at x=9.6 is 1.4 A from C at x=1.0 across the boundary
    assert {"pair": "C-N", "d": 1.4, "count": 1} in r["min_pair_distances"], r
    r = ysrv.structure_check(str(fx / "clean.xyz"))
    assert r["ok"] and r["composition"] == {"O": 1, "H": 2} and r["cell"]["volume"] == 512.0, r
    for bad, text in (("plain.xyz", "need extended xyz"), ("missing.xyz", "not a file")):
        try:
            ysrv.structure_check(str(fx / bad))
        except ValueError as e:
            assert text in str(e), e
        else:
            raise AssertionError(f"structure_check accepted {bad}")

    # stretch: the short C-H becomes 1.08 A, matching make_subsys.py's
    # stretch_ch (the by-hand fallback), and the input is never overwritten
    import importlib.util
    import numpy as np
    spec = importlib.util.spec_from_file_location(
        "make_subsys", root / "codes/common/structure-making/cp2k/make_subsys.py")
    ms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ms)
    cell, el, xyz = ms.read_extxyz(fx / "flagged.xyz")
    expected, _ = ms.stretch_ch(cell, el, xyz.copy(), 1.08)
    with tempfile.TemporaryDirectory() as d:
        out = pathlib.Path(d) / "fixed.xyz"
        r = ysrv.structure_stretch_xh(str(fx / "flagged.xyz"), str(out))
        assert r["moved"] == 1 and r["shortest_before"] == 0.9, r
        lines = out.read_text().splitlines()
        got = np.array([[float(v) for v in l.split()[1:4]] for l in lines[2:]])
        assert np.allclose(got, expected, atol=1e-7), got
        assert lines[1] == (fx / "flagged.xyz").read_text().splitlines()[1], lines[1]
        assert [f["kind"] for f in ysrv.structure_check(str(out))["flags"]] == ["overlap"]
        for args, text in (((str(fx / "flagged.xyz"), str(fx / "flagged.xyz")), "must differ"),
                           ((str(fx / "flagged.xyz"), str(out)), "exists"),
                           ((str(fx / "flagged.xyz"), str(out), 1.08, ["C-O"], True), "X-H")):
            try:
                ysrv.structure_stretch_xh(*args)
            except ValueError as e:
                assert text in str(e), e
            else:
                raise AssertionError(f"structure_stretch_xh accepted {args}")

    probe = ("import asyncio; from yagura.server import mcp; "
             "print(sorted(t.name for t in asyncio.run(mcp.list_tools())))")
    with tempfile.TemporaryDirectory() as home:
        out = subprocess.run([sys.executable, "-c", probe], cwd=root,
                             env={**os.environ, "HOME": home},
                             capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == ("['analyze_density', 'cif_inspect', 'colvar_fes', 'export_frames', "
                                  "'framework_check', 'gap_compare', 'labels_compare', 'pdos_read', 'sites_by_environment', 'stage_energies', 'structure_check', "
                                  "'structure_stretch_xh', 'structure_to_extxyz', "
                                  "'structure_to_lammps_data', 'trajectory_msd', "
                                  "'trajectory_rdf', 'trajectory_vdos']"), out.stdout

    # without ASE: the server still starts with every tool, the numpy
    # tools (checks and trajectory analysis) work, and the CIF tools say
    # how to install it
    import shutil
    walk = pathlib.Path(tempfile.mkdtemp()) / "walk.xyz"
    rng = np.random.default_rng(1)
    X = np.cumsum(rng.normal(0, 0.05, (64, 4, 3)), axis=0) % 8.0
    walk.write_text("".join(f"4\nLattice=\"8 0 0 0 8 0 0 0 8\"\n"
                            + "".join(f"O {x:.5f} {y:.5f} {z:.5f}\n" for x, y, z in f) for f in X))
    no_ase = ("import sys, importlib.abc\n"
              "class Block(importlib.abc.MetaPathFinder):\n"
              "    def find_spec(self, name, path=None, target=None):\n"
              "        if name == 'ase' or name.startswith('ase.'):\n"
              "            raise ModuleNotFoundError(f'No module named {name!r}', name=name)\n"
              "sys.meta_path.insert(0, Block())\n"
              "import asyncio; from yagura import server\n"
              "print(len(asyncio.run(server.mcp.list_tools())))\n"
              f"print(server.structure_check({str(fx / 'clean.xyz')!r})['ok'])\n"
              f"print(len(server.trajectory_rdf({str(walk)!r}, ['O-O'], 3.0, 10)['r']),\n"
              f"      server.trajectory_msd({str(walk)!r}, ['O'], 1.0)['elements'][0]['natoms'],\n"
              f"      server.trajectory_vdos({str(walk)!r}, 1.0, points=8)['natoms'])\n"
              "try:\n"
              f"    server.cif_inspect({str(fx / 'disordered.cif')!r})\n"
              "except RuntimeError as e:\n"
              "    print('needs ASE' in str(e))\n"
              "try:\n"
              "    server.export_frames('/nonexistent')\n"
              "except RuntimeError as e:\n"
              "    print('needs ASE' in str(e))\n")
    out = subprocess.run([sys.executable, "-c", no_ase], cwd=root, capture_output=True,
                         text=True, timeout=120)
    shutil.rmtree(walk.parent)
    assert out.stdout.split() == ["17", "True", "10", "4", "4", "True", "True"], (out.stdout, out.stderr[-2000:])

    def imports(folder):
        for f in (root / folder).rglob("*.py"):
            for node in ast.walk(ast.parse(f.read_text())):
                if isinstance(node, ast.Import):
                    yield f, [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    yield f, [node.module]
    rookery = ("hpc_agent_core", "hpc_mcp", "hpc_codes", "facilities")
    for f, names in imports("yagura"):
        bad = [n for n in names if n.split(".")[0] in rookery]
        assert not bad, f"{f.relative_to(root)} imports {bad}: yagura must stay separate"
    for pkg in rookery:
        for f, names in imports(pkg):
            bad = [n for n in names if n.split(".")[0] == "yagura"]
            assert not bad, f"{f.relative_to(root)} imports {bad}: rookery must not depend on yagura"
