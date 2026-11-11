import os
import pathlib
import subprocess
import sys
import tempfile
from checks._common import ROOT

LABEL = 'hyogo make_subsys matches the script and stays separate'


def hyogo_make_subsys_matches_the_script_and_stays_separate():
    """hyogo's cp2k_make_subsys writes what make_subsys.py (the by-hand
    fallback) writes, from cp2k_kinds-shaped kinds; it refuses missing
    kinds; its server starts with an empty HOME; and the three staged
    groups (rookery, yagura, hyogo) import nothing from each other."""
    import ast
    import re
    from hyogo import server as hsrv
    root = ROOT
    fx = root / "tests" / "fixtures" / "structures" / "clean.xyz"
    data = root / "tests" / "fixtures" / "cp2k_data"
    kinds = {"build_id": "x", "kinds": {
        "O": {"q": 6, "basis": "DZVP-MOLOPT-SR-GTH", "potential": "GTH-PBE-q6"},
        "H": {"q": 1, "basis": "DZVP-MOLOPT-SR-GTH", "potential": "GTH-PBE-q1"}}, "errors": {}}
    with tempfile.TemporaryDirectory() as d:
        for periodic in ("XYZ", "NONE"):
            old, new = pathlib.Path(d) / f"old-{periodic}", pathlib.Path(d) / f"new-{periodic}"
            subprocess.run([sys.executable, "-I",
                            str(root / "codes/common/structure-making/cp2k/make_subsys.py"),
                            str(fx), "--data-dir", str(data), "--periodic", periodic,
                            "--out", str(old)], check=True, capture_output=True, timeout=60)
            r = hsrv.cp2k_make_subsys(str(fx), kinds, str(new), periodic=periodic)
            a, b = old.read_text().splitlines(), new.read_text().splitlines()
            assert a[:1] + a[2:] == b[:1] + b[2:], (a, b)
            assert r["electrons"] == 8 and r["formula"] == "O1H2", r
        assert any("below 10 A" in w for w in
                   hsrv.cp2k_make_subsys(str(fx), kinds["kinds"], str(pathlib.Path(d) / "k"))["warnings"])
        for args, text in (((str(fx), {"O": kinds["kinds"]["O"]}, str(pathlib.Path(d) / "m")), "no kind for ['H']"),
                           ((str(fx), kinds, str(pathlib.Path(d) / "new-XYZ")), "exists"),
                           ((str(fx), {"O": {"q": 6}, "H": {"q": 1}}, str(pathlib.Path(d) / "q")), "needs q")):
            try:
                hsrv.cp2k_make_subsys(*args)
            except ValueError as e:
                assert text in str(e), e
            else:
                raise AssertionError(f"cp2k_make_subsys accepted {args[1:]}")

    probe = ("import asyncio; from hyogo.server import mcp; "
             "print(sorted(t.name for t in asyncio.run(mcp.list_tools())))")
    with tempfile.TemporaryDirectory() as home:
        out = subprocess.run([sys.executable, "-c", probe], cwd=root,
                             env={**os.environ, "HOME": home},
                             capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    live = sorted(re.findall(r"^\| `(\w+)` \|(?:[^|]*\|){4} live \|",
                             (root / "hyogo" / "INVENTORY.md").read_text(), re.M))
    assert out.stdout.strip() == str(live), (
        f"hyogo serves {out.stdout.strip()}, INVENTORY.md lists {live} as live")

    groups = {"rookery": ("hpc_agent_core", "hpc_mcp", "hpc_codes", "facilities"),
              "yagura": ("yagura",), "hyogo": ("hyogo",)}
    for name, pkgs in groups.items():
        others = {p for g, ps in groups.items() if g != name for p in ps}
        for pkg in pkgs:
            for f in (root / pkg).rglob("*.py"):
                for node in ast.walk(ast.parse(f.read_text())):
                    mods = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                            [node.module] if isinstance(node, ast.ImportFrom) and node.module
                            and not node.level else [])
                    bad = [m for m in mods if m.split(".")[0] in others]
                    assert not bad, f"{f.relative_to(root)} ({name}) imports {bad}"
