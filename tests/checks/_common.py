"""What several checks share. Everything else a check needs is in its own file.

ROOT is the repo root and TESTS the tests/ folder (the old checks' Path(__file__) roots).
"""
import contextlib
import os
import pathlib
import subprocess

from hpc_agent_core import config
from hpc_agent_core.models import JobAttributes, JobSpec, ResourceSpec

ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"

# the code-pack and docs tools the hpc server serves (hpc-codes-mcp and
# hpc-docs-mcp, their old standalone servers, are removed)
CODES_TOOLS = ['cp2k_bench_dryrun', 'cp2k_bench_input', 'cp2k_bench_parse', 'cp2k_bench_records',
               'cp2k_check_build', 'cp2k_check_input', 'cp2k_kinds', 'cp2k_record_from_run',
               'launch_plan', 'records_append', 'records_check', 'records_query']
DOCS_TOOLS = ['list_doc_sections', 'read_doc_section', 'search_docs']


@contextlib.contextmanager
def no_user_config(slug: str):
    """Point a facility's config at a path that doesn't exist.

    Without this, checks covering "the user hasn't configured X yet" only
    run on a machine that genuinely hasn't — so they silently pass on a
    developer's fully-configured laptop and on nobody's CI. Forcing the
    unconfigured state makes those paths deterministic everywhere.
    """
    var = f"{config.get_facility(slug).env_prefix}_CONFIG"
    previous = os.environ.get(var)
    os.environ[var] = "/nonexistent/conformance-forced-missing.json"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = previous


def bare_spec() -> JobSpec:
    """A minimal spec with everything optional left blank — what
    apply_defaults() is supposed to complete."""
    return JobSpec(
        name="conformance",
        executable="echo ok",
        resources=ResourceSpec(node_count=1, processes_per_node=1),
        attributes=JobAttributes(duration=300),
    )


def run_defaults_validate_render(backend):
    """The exact sequence submit_job runs. Either it produces a usable
    script, or it raises a ValueError that tells the user what to set —
    never an AttributeError/TypeError from a half-completed spec."""
    spec = bare_spec()
    try:
        backend.apply_defaults(spec)
        backend.validate_spec(spec)
    except ValueError as e:
        msg = str(e)
        assert len(msg) > 40, f"error too terse to act on: {msg!r}"
        # Must tell the user what to do about it, not just what's wrong.
        assert any(w in msg.lower() for w in ("set ", "configure", "use ")), \
            f"error doesn't tell the user what to do: {msg!r}"
        return
    script = backend.render_script(spec)
    assert script.startswith("#!"), "rendered script has no shebang"
    assert "echo ok" in script, "rendered script lost the executable"


def local_run_command(facility, cmd, timeout=None):
    """middleware.run_command's contract, run in a local bash: the same
    payload (cd "$HOME" first), stdout back, RuntimeError on failure.
    Only read-only commands reach it (the cp2k_* tools)."""
    out = subprocess.run(["bash", "-c", 'cd "$HOME" && ' + cmd],
                         capture_output=True, text=True, timeout=120)
    if out.returncode:
        raise RuntimeError(out.stderr or out.stdout)
    return out.stdout
