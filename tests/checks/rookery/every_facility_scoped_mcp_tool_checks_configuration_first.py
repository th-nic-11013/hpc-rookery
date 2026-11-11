import pathlib
from hpc_agent_core import config
from checks._common import no_user_config

LABEL = 'every facility-scoped MCP tool checks configuration first'


def every_facility_scoped_mcp_tool_checks_configuration_first():
    """Every tool that reaches the cluster must check configuration
    first; every tool that does not must keep working without it.

    Both halves matter. Gating a cluster-bound tool stops it returning
    a misleading result; gating a packaged-data tool breaks cold start,
    which is how get_facility and docs search ended up circular with
    the <slug>-configuring skill they were supposed to support.

    The ungated set is derived from the source and compared against an
    explicit allowlist, so a new tool added without the guard fails
    here rather than silently reaching for a connection.
    """
    import re
    import hpc_mcp.hpc_server as server
    from hpc_agent_core import docs_server

    # Deliberately ungated, each for a stated reason:
    #   get_facilities    - no facility argument; discovery entry point
    #   get_facility      - returns this facility's packaged facts JSON
    #   render_job_script - pure local defaults/validate/render
    UNGATED = {"get_facilities", "get_facility", "render_job_script"}

    source = pathlib.Path(server.__file__).read_text()
    blocks = re.findall(
        r"@mcp\.tool\(\)\n(.*?)def ([a-zA-Z_][a-zA-Z0-9_]*)\(",
        source, re.S)
    assert blocks, "found no @mcp.tool() definitions to inspect"
    ungated = {name for between, name in blocks
               if "@_configured_facility_tool" not in between}
    assert ungated == UNGATED, (
        "the set of tools bypassing the configuration guard changed: "
        f"unexpectedly ungated={sorted(ungated - UNGATED)}, "
        f"newly gated={sorted(UNGATED - ungated)}"
    )

    class RecordingMCP:
        def __init__(self):
            self.functions = []

        def tool(self):
            def register(func):
                self.functions.append(func)
                return func
            return register

    docs_mcp = RecordingMCP()
    docs_server.build(docs_mcp)
    assert len(docs_mcp.functions) == 3

    for slug in (f.slug for f in config.list_facilities()):
        with no_user_config(slug):
            # Cluster-bound: must refuse with this facility's directions.
            for func, args in (
                (server.fs_ls, (slug,)),
                (server.get_projects, (slug,)),
            ):
                try:
                    func(*args)
                except RuntimeError as exc:
                    text = str(exc)
                    assert "not configured yet" in text
                    assert f"{slug}-configuring" in text
                else:
                    raise AssertionError(
                        f"{func.__name__} bypassed the config guard")

            # Packaged data / local only: must answer anyway. The docs
            # tools are included here because gating them is the exact
            # regression this check now guards against.
            assert server.get_facility(slug)
            for func, args in (
                (docs_mcp.functions[0], (slug, "queues")),
                (docs_mcp.functions[1], (slug,)),
            ):
                out = func(*args)
                assert out and out.strip(), (
                    f"{func.__name__} returned nothing for {slug} "
                    "without user config"
                )
