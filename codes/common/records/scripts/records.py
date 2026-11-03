"""File-backed records adapter for {{DISPLAY_NAME}} (schema: ../schema.yaml).

    records.py query <type> [field.path=value ...] [--fields=a,b.c,...]
    records.py append <type> <record.yaml | ->
    records.py check

query   prints the matching records of both scopes as YAML, each tagged with
        `_scope`. A filter matches when the field equals the value, either as
        YAML (nodes=2, valid=true) or as text (version=2026.2).
append  validates one record and appends it to the user's file. It never
        edits or removes a record: a correction is a new record with
        `supersedes`.
check   validates every record file and the links between them.

Shared (facility-scope) records ship beside this script. Records written
here go to the user's directory, also for facility-scope types: the
installed skill is a copy, not the repository. Set HPC_RECORDS_USER_DIR to
override the user directory (tests).
"""
import os
import re
import sys
from pathlib import Path

import yaml

FACILITY = "{{SLUG}}"
SKILL_DIR = Path(__file__).resolve().parent.parent
SCHEMA = yaml.safe_load((SKILL_DIR / "schema.yaml").read_text())
USER_DIR = Path(os.environ.get("HPC_RECORDS_USER_DIR")
                or Path.home() / ".hpc-agent" / "records" / FACILITY)
TYPES = SCHEMA["record_types"]


def die(msg):
    sys.exit(f"records.py: {msg}")


def load(path):
    if not path.exists():
        return []
    doc = yaml.safe_load(path.read_text()) or {}
    if doc.get("schema_version") != SCHEMA["schema_version"]:
        die(f"{path}: schema_version {doc.get('schema_version')!r}, "
            f"expected {SCHEMA['schema_version']}")
    return doc.get("records") or []


def sources(rtype):
    """(scope, path) pairs a record type is read from."""
    out = []
    if TYPES[rtype]["scope"] == "facility":
        out.append(("facility", SKILL_DIR / f"{rtype}.yaml"))
    out.append(("user", USER_DIR / f"{rtype}.yaml"))
    return out


def read(rtype):
    recs = []
    for scope, path in sources(rtype):
        for r in load(path):
            recs.append(dict(r, _scope=scope))
    return recs


def get(rec, dotted):
    for part in dotted.split("."):
        if not isinstance(rec, dict) or part not in rec:
            return None
        rec = rec[part]
    return rec


def matches(rec, filters):
    for key, raw in filters:
        v = get(rec, key)
        if v != yaml.safe_load(raw) and str(v) != raw:
            return False
    return True


def type_errors(t, v, where):
    if v is None:
        return []
    ok = {"str": lambda: isinstance(v, str),
          "int": lambda: isinstance(v, int) and not isinstance(v, bool),
          "float": lambda: isinstance(v, (int, float)) and not isinstance(v, bool),
          "bool": lambda: isinstance(v, bool),
          "date": lambda: isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)}
    if t in ok:
        return [] if ok[t]() else [f"{where}: {v!r} is not {t}"]
    if t.startswith("enum["):
        return [] if v in t[5:-1].split("|") else [f"{where}: {v!r} not in {t}"]
    if t.startswith("list["):
        if not isinstance(v, list):
            return [f"{where}: not a list"]
        return [e for i, x in enumerate(v) for e in type_errors(t[5:-1], x, f"{where}[{i}]")]
    if t.startswith("map["):
        kt, vt = t[4:-1].split(",")
        if not isinstance(v, dict):
            return [f"{where}: not a mapping"]
        return [e for k, x in v.items()
                for e in type_errors(kt, k, f"{where} key") + type_errors(vt, x, f"{where}.{k}")]
    die(f"schema has unknown type {t}")


def field_errors(spec, rec, where):
    errs = [f"{where}: unknown field {k}" for k in rec if k not in spec and k != "_scope"]
    for name, f in spec.items():
        v = rec.get(name)
        if v is None:
            if f.get("required"):
                errs.append(f"{where}: missing required {name}")
        elif "fields" in f:
            errs += (field_errors(f["fields"], v, f"{where}.{name}") if isinstance(v, dict)
                     else [f"{where}.{name}: not a mapping"])
        else:
            errs += type_errors(f["type"], v, f"{where}.{name}")
    return errs


def record_errors(rtype, rec, scope):
    rid = rec.get(TYPES[rtype]["id_field"])
    errs = field_errors(TYPES[rtype]["fields"], rec, f"{rtype}[{rid}]")
    if rtype == "benchmarks":
        if rec.get("valid") is False and not rec.get("reason"):
            errs.append(f"benchmarks[{rid}]: valid is false but there is no reason")
        if rec.get("build_external") and not rec.get("build_desc"):
            errs.append(f"benchmarks[{rid}]: build_external needs build_desc")
        if scope == "facility" and rec.get("job_id") is not None:
            errs.append(f"benchmarks[{rid}]: shared records keep job_id null")
    return errs


def link_errors(all_recs):
    errs = []
    builds = {r["build_id"] for r in all_recs["software_registry"]}
    bench = {r["id"] for r in all_recs["benchmarks"]}
    for r in all_recs["benchmarks"]:
        if r.get("build_id") not in builds and not r.get("build_external"):
            errs.append(f"benchmarks[{r['id']}]: build_id {r.get('build_id')} is not in "
                        "software_registry and not build_external")
    for p in all_recs["benchmarks_provenance"]:
        if p.get("benchmark_id") not in bench:
            errs.append(f"benchmarks_provenance[{p.get('id')}]: unknown benchmark")
    return errs


def cmd_query(rtype, args):
    fields = None
    filters = []
    for a in args:
        if a.startswith("--fields="):
            fields = a.split("=", 1)[1].split(",")
        elif "=" in a:
            filters.append(tuple(a.split("=", 1)))
        else:
            die(f"bad filter {a!r}; use field.path=value")
    hits = [r for r in read(rtype) if matches(r, filters)]
    if fields:
        hits = [{f: get(r, f) for f in [TYPES[rtype]["id_field"], "_scope"] + fields} for r in hits]
    print(f"# {len(hits)} {rtype} record(s)")
    if hits:
        print(yaml.safe_dump(hits, sort_keys=False, allow_unicode=True, width=100), end="")


def cmd_append(rtype, src):
    rec = yaml.safe_load(sys.stdin.read() if src == "-" else Path(src).read_text())
    if not isinstance(rec, dict):
        die("the record must be one YAML mapping")
    rec.pop("_scope", None)
    errs = record_errors(rtype, rec, "user")
    idf = TYPES[rtype]["id_field"]
    if any(r.get(idf) == rec.get(idf) for r in read(rtype)):
        errs.append(f"{rtype}: {idf} {rec.get(idf)!r} already exists (append-only: "
                    "use a new id with supersedes)")
    if errs:
        die("not appended:\n  " + "\n  ".join(errs))
    path = USER_DIR / f"{rtype}.yaml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rtype} records written on {FACILITY} by records.py\n"
                        f"schema_version: {SCHEMA['schema_version']}\nrecords:\n")
    with path.open("a") as fh:
        fh.write(yaml.safe_dump([rec], sort_keys=False, allow_unicode=True, width=100))
    print(f"appended {rtype} {rec.get(idf)} to the user scope")


def cmd_check():
    all_recs, errs = {}, []
    for rtype in TYPES:
        all_recs[rtype] = []
        for scope, path in sources(rtype):
            for r in load(path):
                errs += record_errors(rtype, r, scope)
                all_recs[rtype].append(r)
        ids = [r.get(TYPES[rtype]["id_field"]) for r in all_recs[rtype]]
        errs += [f"{rtype}: duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
        print(f"{rtype}: {len(all_recs[rtype])} record(s)")
    errs += link_errors(all_recs)
    if errs:
        die("\n  " + "\n  ".join(errs))
    print("all records valid")


def main(argv):
    if not argv or argv[0] not in ("query", "append", "check"):
        die(__doc__)
    if argv[0] == "check":
        return cmd_check()
    if len(argv) < 2 or argv[1] not in TYPES:
        die(f"record type must be one of {', '.join(TYPES)}")
    if argv[0] == "query":
        return cmd_query(argv[1], argv[2:])
    if len(argv) != 3:
        die("append takes one record file, or - for stdin")
    return cmd_append(argv[1], argv[2])


if __name__ == "__main__":
    main(sys.argv[1:])
