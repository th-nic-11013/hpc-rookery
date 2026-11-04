"""File-backed records adapter: the one place that knows where records live.

Implements the `adapter:` contract of schemas/records.yaml (next to this
file, in schemas/): reads return both scopes, queries match exactly, writes only append,
records refer to each other by id. A later backend (the lab notebook)
replaces this module and keeps the tool signatures in server.py.

Two scopes per facility:
- facility: facilities/<dir>/records/<type>.yaml, shipped in the package
  (shared, versioned, read-only here);
- user: ~/.hpc-agent/records/<slug>/<type>.yaml, or $HPC_RECORDS_USER_DIR
  when set (tests). Every write lands here, also for facility-scope types:
  the installed package is a copy, not the repository.

Files are read at call time, never at import, so the server always starts.
"""
import os
import re
from functools import lru_cache
from pathlib import Path

import yaml

import hpc_mcp  # noqa: F401 -- registers every facility, so slugs resolve without the server
from hpc_agent_core import config

SCHEMA_PATH = Path(__file__).parent / "schemas" / "records.yaml"


class RecordsError(ValueError):
    """A request or a record file the adapter refuses; the message says why."""


@lru_cache(maxsize=1)
def schema() -> dict:
    return yaml.safe_load(SCHEMA_PATH.read_text())


def types() -> dict:
    return schema()["record_types"]


def _type(rtype: str) -> dict:
    if rtype not in types():
        raise RecordsError(f"Unknown record type {rtype!r}. "
                           f"Valid types: {', '.join(types())}")
    return types()[rtype]


def facility_dir(facility: str) -> Path:
    """Shared records of a facility (raises ValueError for an unknown slug)."""
    return config.get_facility(facility).data_dir.parent / "records"


def user_dir(facility: str) -> Path:
    config.get_facility(facility)
    override = os.environ.get("HPC_RECORDS_USER_DIR")
    return Path(override) if override else Path.home() / ".hpc-agent" / "records" / facility


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    doc = yaml.safe_load(path.read_text()) or {}
    if doc.get("schema_version") != schema()["schema_version"]:
        raise RecordsError(f"{path}: schema_version {doc.get('schema_version')!r}, "
                           f"expected {schema()['schema_version']}")
    return doc.get("records") or []


def _sources(facility: str, rtype: str) -> list[tuple[str, Path]]:
    out = []
    if _type(rtype)["scope"] == "facility":
        out.append(("facility", facility_dir(facility) / f"{rtype}.yaml"))
    out.append(("user", user_dir(facility) / f"{rtype}.yaml"))
    return out


def _read(facility: str, rtype: str) -> list[dict]:
    return [dict(r, _scope=scope)
            for scope, path in _sources(facility, rtype) for r in _load(path)]


def _get(rec, dotted: str):
    for part in dotted.split("."):
        if not isinstance(rec, dict) or part not in rec:
            return None
        rec = rec[part]
    return rec


def _matches(rec: dict, filters: dict) -> bool:
    # Equal as a value (nodes=2, valid=True) or as text (version="2026.2"),
    # so a caller need not know how YAML typed the stored field.
    for key, want in filters.items():
        have = _get(rec, key)
        if have != want and str(have) != str(want):
            return False
    return True


def _type_errors(t: str, v, where: str) -> list[str]:
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
        return [e for i, x in enumerate(v) for e in _type_errors(t[5:-1], x, f"{where}[{i}]")]
    if t.startswith("map["):
        kt, vt = t[4:-1].split(",")
        if not isinstance(v, dict):
            return [f"{where}: not a mapping"]
        return [e for k, x in v.items()
                for e in _type_errors(kt, k, f"{where} key") + _type_errors(vt, x, f"{where}.{k}")]
    raise RecordsError(f"schema has unknown type {t}")


def _field_errors(spec: dict, rec: dict, where: str) -> list[str]:
    errs = [f"{where}: unknown field {k}" for k in rec if k not in spec and k != "_scope"]
    for name, f in spec.items():
        v = rec.get(name)
        if v is None:
            if f.get("required"):
                errs.append(f"{where}: missing required {name}")
        elif "fields" in f:
            errs += (_field_errors(f["fields"], v, f"{where}.{name}") if isinstance(v, dict)
                     else [f"{where}.{name}: not a mapping"])
        else:
            errs += _type_errors(f["type"], v, f"{where}.{name}")
    return errs


def _record_errors(facility: str, rtype: str, rec: dict, scope: str) -> list[str]:
    rid = rec.get(_type(rtype)["id_field"])
    errs = _field_errors(_type(rtype)["fields"], rec, f"{rtype}[{rid}]")
    if "facility" in rec and rec["facility"] != facility:
        errs.append(f"{rtype}[{rid}]: facility {rec['facility']!r} in {facility}'s records")
    if rtype == "benchmarks":
        if rec.get("valid") is False and not rec.get("reason"):
            errs.append(f"benchmarks[{rid}]: valid is false but there is no reason")
        if rec.get("build_external") and not rec.get("build_desc"):
            errs.append(f"benchmarks[{rid}]: build_external needs build_desc")
        if scope == "facility" and rec.get("job_id") is not None:
            errs.append(f"benchmarks[{rid}]: shared records keep job_id null")
    return errs


def _link_errors(recs: dict) -> list[str]:
    errs = []
    builds = {r.get("build_id") for r in recs["software_registry"]}
    bench = {r.get("id") for r in recs["benchmarks"]}
    for r in recs["benchmarks"]:
        if r.get("build_id") not in builds and not r.get("build_external"):
            errs.append(f"benchmarks[{r.get('id')}]: build_id {r.get('build_id')} is not in "
                        "software_registry and not build_external")
    for p in recs["benchmarks_provenance"]:
        if p.get("benchmark_id") not in bench:
            errs.append(f"benchmarks_provenance[{p.get('id')}]: unknown benchmark")
    return errs


def query(facility: str, rtype: str, filters: dict | None = None,
          fields: list[str] | None = None) -> list[dict]:
    hits = [r for r in _read(facility, rtype) if _matches(r, filters or {})]
    if fields:
        keep = [_type(rtype)["id_field"], "_scope", *fields]
        hits = [{f: _get(r, f) for f in keep} for r in hits]
    return hits


def errors(facility: str, rtype: str, record: dict) -> list[str]:
    """Why append() would refuse this record; empty if it would accept it."""
    if not isinstance(record, dict):
        raise RecordsError("the record must be one mapping")
    rec = {k: v for k, v in record.items() if k != "_scope"}
    idf = _type(rtype)["id_field"]
    errs = _record_errors(facility, rtype, rec, "user")
    if any(r.get(idf) == rec.get(idf) for r in _read(facility, rtype)):
        errs.append(f"{rtype}: {idf} {rec.get(idf)!r} already exists (append-only: "
                    "use a new id with supersedes)")
    return errs


def append(facility: str, rtype: str, record: dict) -> dict:
    errs = errors(facility, rtype, record)
    if errs:
        raise RecordsError("not appended:\n  " + "\n  ".join(errs))
    rec = {k: v for k, v in record.items() if k != "_scope"}
    idf = _type(rtype)["id_field"]
    path = user_dir(facility) / f"{rtype}.yaml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rtype} records for {facility}, written by hpc-codes\n"
                        f"schema_version: {schema()['schema_version']}\nrecords:\n")
    with path.open("a") as fh:
        fh.write(yaml.safe_dump([rec], sort_keys=False, allow_unicode=True, width=100))
    return {"id": rec.get(idf), "scope": "user", "path": str(path)}


def check(facility: str) -> dict:
    recs, counts, errs = {}, {}, []
    for rtype in types():
        recs[rtype] = []
        for scope, path in _sources(facility, rtype):
            try:
                loaded = _load(path)
            except (RecordsError, yaml.YAMLError) as exc:
                errs.append(str(exc))
                continue
            for r in loaded:
                errs += _record_errors(facility, rtype, r, scope)
                recs[rtype].append(r)
        ids = [r.get(_type(rtype)["id_field"]) for r in recs[rtype]]
        errs += [f"{rtype}: duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
        counts[rtype] = len(recs[rtype])
    errs += _link_errors(recs)
    return {"counts": counts, "errors": errs}
