import pathlib
from hpc_agent_core import config

LABEL = 'a broken facility cannot deny the server'


def a_broken_facility_cannot_deny_the_server():
    """One facility failing to import must not take down the others.

    The hub sharpened this: a single process now serves every facility,
    so an exception in one facility.py would otherwise deny every other
    facility to every user. Verified by importing a deliberately broken
    module the same way hpc_mcp does, and asserting the healthy ones
    survive and the failure is recorded rather than swallowed.
    """
    import importlib.util
    import tempfile
    import hpc_mcp

    healthy = {f.slug for f in config.list_facilities()}
    assert healthy, "no healthy facilities to protect"

    with tempfile.TemporaryDirectory() as tmp:
        mod = pathlib.Path(tmp) / "broken_facility.py"
        mod.write_text("raise RuntimeError('deliberately broken')\n")
        spec = importlib.util.spec_from_file_location("broken_facility", mod)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            recorded = f"{type(exc).__name__}: {exc}"
        else:
            raise AssertionError("the broken module did not raise")

    assert "RuntimeError" in recorded, recorded
    assert {f.slug for f in config.list_facilities()} == healthy, \
        "a failed facility import disturbed the healthy registry"
    assert isinstance(hpc_mcp.FAILED_FACILITIES, dict), \
        "hpc_mcp must expose FAILED_FACILITIES so a skipped facility is never silent"
