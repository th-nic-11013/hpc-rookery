import json
import os
import tempfile
from unittest.mock import patch

LABEL = 'records_query is lean without fields'


def records_query_is_lean_without_fields():
    """kairo live run 1, gap 17: records_query("hokusai", "benchmarks")
    with no fields gave the planner all 42 CP2K records in full. More
    than 5 hits without fields come back as summary rows (id, _scope,
    key fields, _summary); 5 or fewer, fields, or full=true are as
    before."""
    from hpc_codes import server
    with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}):
        lean = server.records_query("hokusai", "benchmarks")
        full = server.records_query("hokusai", "benchmarks", full=True)
        assert len(lean) == len(full) > 5, (len(lean), len(full))
        assert [r["id"] for r in lean] == [r["id"] for r in full]
        assert all(r["_summary"] is True and "_scope" in r for r in lean), lean[:1]
        assert "system" in full[0] and "_summary" not in full[0], "full=true gives whole records"
        assert len(json.dumps(lean)) * 3 < len(json.dumps(full)), "summary rows are not much smaller"
        d3 = next(r for r in lean if r.get("method.dispersion"))
        assert d3["method.dispersion"] == "D3" and "system.label" in d3, d3
        assert all(None not in r.values() for r in lean), "unset fields are left out"

        one = server.records_query("hokusai", "benchmarks", {"id": full[0]["id"]})
        assert one == [full[0]], "a few hits are still whole records"
        some = server.records_query("hokusai", "benchmarks", None, ["metric.value"])
        assert len(some) == len(full) and set(some[0]) == {"id", "_scope", "metric.value"}, some[:1]
