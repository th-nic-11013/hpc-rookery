import pathlib
import sys
import tempfile
from checks._common import ROOT

LABEL = 'hyogo checks INCARs through lit-search'


def hyogo_checks_incars_through_lit_search():
    """vasp_make_incar and vasp_check_incar hand the INCAR to lit-search
    (`reference check VASP FILE --json`) as a separate program, pass
    --library and --vasp-version through, turn its findings into
    warnings, and say so, without failing, when lit-search is absent or
    not set up. A stand-in lit-search keeps this test independent of it."""
    import os
    import shutil
    from hyogo import server as hsrv
    root = ROOT
    fi = root / "tests" / "fixtures" / "vasp_inputs"
    keep = {k: os.environ.get(k) for k in ("HYOGO_LITSEARCH", "HYOGO_LITSEARCH_LIBRARY", "PATH")}
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        shutil.copy(fi / "POSCAR", d / "POSCAR")
        stub = d / "lit-search"
        stub.write_text(f"#!{sys.executable}\n" + """import json, sys
a = sys.argv[1:]
open(sys.argv[0] + ".args", "a").write(" ".join(a) + "\\n")
if "--library" in a and a[a.index("--library") + 1].endswith("unset"):
    print("no VASP reference: run `lit-search reference set VASP ...`"); sys.exit(2)
bad = [{"line": n, "tag": l.split("=")[0].strip(), "value": l.split("=")[1].strip(),
        "level": "error", "message": "not an INCAR tag in the reference"}
       for n, l in enumerate(open(a[a.index("VASP") + 1]), 1) if l.startswith("ENCUTT")]
print(json.dumps(bad)); sys.exit(1 if bad else 0)
""")
        stub.chmod(0o755)
        try:
            os.environ["PATH"] = str(d / "empty")       # no lit-search on PATH
            os.environ.pop("HYOGO_LITSEARCH", None)
            os.environ.pop("HYOGO_LITSEARCH_LIBRARY", None)
            r = hsrv.vasp_check_incar(str(fi / "expected_rel.INCAR"))
            assert not r["ran"] and "HYOGO_LITSEARCH" in r["why"], r
            os.environ["HYOGO_LITSEARCH"] = str(stub)
            os.environ["HYOGO_LITSEARCH_LIBRARY"] = str(d / "lib")
            r = hsrv.vasp_make_incar(str(d / "POSCAR"), str(fi / "INCAR.label"),
                                     settings={"ENCUTT": 400}, magmom={"Cr": 3.0},
                                     pre={"nupdown": 6}, vasp_version="6.4.3",
                                     out_path=str(d / "INCAR"))
            assert set(r["incar_check"]) == {"INCAR", "INCAR.pre"}, r["incar_check"]
            assert all(c["ran"] and c["errors"] == 1 for c in r["incar_check"].values()), r
            assert any(w.startswith("INCAR:") and "ENCUTT = 400" in w for w in r["warnings"]), r["warnings"]
            calls = (d / "lit-search.args").read_text().splitlines()
            assert calls[-1].startswith(f"--library {d / 'lib'} reference check VASP "), calls
            assert calls[-1].endswith("--json --vasp-version 6.4.3"), calls
            r = hsrv.vasp_check_incar(str(fi / "expected_rel.INCAR"), "6.4.3")
            assert r["ran"] and r["findings"] == [] and r["errors"] == 0, r
            os.environ["HYOGO_LITSEARCH_LIBRARY"] = str(d / "unset")
            r = hsrv.vasp_check_incar(str(fi / "expected_rel.INCAR"))
            assert not r["ran"] and "no VASP reference" in r["why"], r
            r = hsrv.vasp_make_incar(str(d / "POSCAR"), str(fi / "INCAR.label"),
                                     magmom={"Cr": 3.0}, out_path=str(d / "x.INCAR"))
            assert not any("not checked" in w for w in r["warnings"]), r["warnings"]
        finally:
            for k, v in keep.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
