from checks._common import no_user_config

LABEL = 'no-config tools still answer (never-fail invariant)'


def unconfigured_tools_still_answer(slug, fac, backend):
    """The never-fail invariant, at tool granularity: every tool that
    does not need the cluster must keep working with no config at all,
    so an agent can orient the user *before* setup.

    Checked through the MCP entry points, not the helpers underneath —
    the regression this guards against was a decorator applied to the
    whole tool surface, which the helpers would not have noticed. An
    earlier version of this file asserted only on config.load_facts()
    and so passed while get_facility itself was raising.
    """
    from hpc_mcp.hpc_server import get_facility, render_job_script
    from hpc_agent_core import docs_server
    from hpc_agent_core.models import JobSpec

    with no_user_config(slug):
        facts = get_facility(slug)
        assert facts, "get_facility returned nothing unconfigured"

        # Packaged index: searching a guide is how an agent answers
        # "what partitions does this machine have" before SSH exists.
        index = docs_server._index(slug)
        hits = index.search("storage quota partition", top_k=2)
        assert hits, f"{slug}: docs search returned nothing unconfigured"
        sections = index.chunks
        assert sections, f"{slug}: no doc sections unconfigured"

        # Local-only rendering. Supplying an account keeps facilities
        # that mandate one on the happy path; the point is that nothing
        # here reaches for a connection.
        spec = JobSpec.model_validate({
            "name": "offline-render", "executable": "hostname",
            "attributes": {"account": "conformance-probe",
                           "duration": 600},
        })
        try:
            script = render_job_script(slug, spec)
        except ValueError:
            pass          # a facility may reject this minimal spec; fine
        else:
            assert script.strip(), f"{slug}: empty script unconfigured"
