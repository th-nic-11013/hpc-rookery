import os
import tempfile
from unittest.mock import patch

LABEL = 'records tools follow the adapter contract'


def records_tools_follow_the_adapter_contract():
    """Both scopes on read, exact-match filters, append-only writes to
    the user scope. A temporary HPC_RECORDS_USER_DIR keeps the runner's
    own ~/.hpc-agent/records out of it, in both directions."""
    from hpc_codes import server
    from hpc_codes.records import RecordsError
    with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}):
        shared = server.records_query("hokusai", "benchmarks")
        assert shared and {r["_scope"] for r in shared} == {"facility"}, "no shared benchmarks"
        assert server.records_query("hokusai", "software_registry") == []
        assert server.records_query("rikyu", "benchmarks") == [], "a facility with no records"
        hits = server.records_query("hokusai", "benchmarks",
                                    {"nodes": "1", "valid": True}, ["system.label"])
        assert hits and set(hits[0]) == {"id", "_scope", "system.label"}, hits[:1]

        good = dict({k: v for k, v in shared[0].items() if k != "_scope"},
                    id="conformance-probe", job_id="1",
                    build_id="conformance-no-such-build", build_external=None)
        for bad in ({**good, "nodes": "two"}, {**good, "id": shared[0]["id"]},
                    {**good, "facility": "rikyu"}, {**good, "valid": False}):
            try:
                server.records_append("hokusai", "benchmarks", bad)
            except RecordsError:
                pass
            else:
                raise AssertionError(f"appended a bad record: {bad}")
        assert not os.listdir(tmp), "a refused append wrote a file"

        out = server.records_append("hokusai", "benchmarks", good)
        assert out["scope"] == "user" and out["path"].startswith(tmp), out
        both = server.records_query("hokusai", "benchmarks", {"id": "conformance-probe"})
        assert [r["_scope"] for r in both] == ["user"], both
        try:
            server.records_append("hokusai", "benchmarks", good)
        except RecordsError:
            pass
        else:
            raise AssertionError("appended the same id twice")

        # extra: the hybrid / optimisation fields are accepted with their
        # types; a wrong type and an unknown key are still refused
        extra = {"first_step_s": 12.5, "scf_steps": 23, "hfx_eri_mib": 18952,
                 "peak_mib_per_rank": 3120, "forces_s": 410.2, "opt_steps": 21,
                 "final_pressure_bar": 7.34}
        for bad_extra in ({**extra, "opt_steps": "21"}, {**extra, "hfx_eri_mib": 1.5},
                          {**extra, "forces_s": "fast"}, {**extra, "eri_mib": 18952}):
            try:
                server.records_append("hokusai", "benchmarks",
                                      dict(good, id="conformance-probe-extra", extra=bad_extra))
            except RecordsError as e:
                assert "extra" in str(e), e
            else:
                raise AssertionError(f"appended a bad extra: {bad_extra}")
        server.records_append("hokusai", "benchmarks",
                              dict(good, id="conformance-probe-extra", extra=extra))
        back = server.records_query("hokusai", "benchmarks", {"id": "conformance-probe-extra"})
        assert len(back) == 1 and back[0]["extra"] == extra, back

        # the probe's build_id resolves nowhere and is not build_external
        report = server.records_check("hokusai")
        assert report["counts"]["benchmarks"] == len(shared) + 2, report["counts"]
        assert any("conformance-no-such-build" in e for e in report["errors"]), report
        for bad_type in ("benchmark", ""):
            try:
                server.records_query("hokusai", bad_type)
            except RecordsError as e:
                assert "Valid types" in str(e)
            else:
                raise AssertionError(f"accepted record type {bad_type!r}")
        try:
            server.records_query("definitely-not-a-facility", "benchmarks")
        except ValueError as e:
            assert "Valid facilities:" in str(e)
        else:
            raise AssertionError("accepted an unknown facility")
