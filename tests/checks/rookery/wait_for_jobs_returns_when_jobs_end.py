LABEL = 'wait_for_jobs returns when jobs end, times out, and survives a dropped connection'


def wait_for_jobs_returns_when_jobs_end():
    """wait_for_jobs replaces the hand-written `until ssh ... sacct` loops of
    the UiO-66 campaign (at least 10, one of which never ended because the
    HOKUSAI login banner hid the state). Run against a fake backend with an
    injected clock: it returns once every job has ended ("all") or the first
    has ("any"), stops at timeout_s with the IDs still waiting, retries a
    failed status query, gives up on an ID never found, and refuses bad
    bounds."""
    from hpc_agent_core.models import Job, JobState, JobStatus
    import hpc_mcp.hpc_server as server

    class Fake:
        def __init__(self, timeline, fail_at=()):
            self.timeline, self.fail_at, self.calls = timeline, set(fail_at), 0

        def get_statuses(self, ids):
            n, self.calls = self.calls, self.calls + 1
            if n in self.fail_at:
                raise ConnectionError("ssh: connection reset")
            out = []
            for i in ids:
                states = self.timeline.get(i)
                if states:
                    s = states[min(n, len(states) - 1)]
                    out.append(Job(id=i, status=JobStatus(
                        state=s, exit_code=0 if s == JobState.COMPLETED else None,
                        meta_data={"native_state": s.value.upper(), "workdir": f"/w/{i}"})))
            return out

    def run(fake, ids, timeout=600, poll=60, until="all"):
        t = [0.0]
        return server._wait_for_jobs(fake, ids, timeout, poll, until,
                                     sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])

    Q, A, C, F = JobState.QUEUED, JobState.ACTIVE, JobState.COMPLETED, JobState.FAILED
    timeline = {"1": [Q, A, A, C], "2": [A, F]}

    r = run(Fake(timeline), ["1", "2"])
    assert r["done"] and not r["timed_out"] and r["polls"] == 4, r
    assert r["failed"] == ["2"] and r["still_waiting"] == [], r
    assert r["jobs"][0]["workdir"] == "/w/1" and "failed" in r["next"], r

    r = run(Fake(timeline), ["1", "2"], until="any")
    assert r["done"] and r["polls"] == 2 and r["still_waiting"] == ["1"], r

    r = run(Fake(timeline), ["1"], timeout=150)
    assert not r["done"] and r["timed_out"] and r["still_waiting"] == ["1"], r
    assert r["waited_s"] <= 150 and "wait_for_jobs again" in r["next"], r

    r = run(Fake(timeline, fail_at={0, 1}), ["1"])
    assert r["done"], r
    try:
        run(Fake(timeline, fail_at={0, 1, 2}), ["1"])
    except RuntimeError as e:
        assert "3 status queries" in str(e), e
    else:
        raise AssertionError("three failed queries in a row did not stop the wait")

    r = run(Fake(timeline), ["1", "999"])
    assert not r["done"] and r["not_found"] == ["999"] and r["polls"] == 3, r

    # the tool's own argument checks, before any connection (facility guard aside)
    for kwargs, msg in ((dict(job_ids=[]), "at least one"),
                        (dict(job_ids=["1"], timeout_s=0), "timeout_s"),
                        (dict(job_ids=["1"], timeout_s=7200), "timeout_s"),
                        (dict(job_ids=["1"], poll_s=5), "poll_s")):
        try:
            server.wait_for_jobs.__wrapped__("hokusai", **kwargs)
        except ValueError as e:
            assert msg in str(e), (kwargs, e)
        else:
            raise AssertionError(f"{kwargs} was accepted")
