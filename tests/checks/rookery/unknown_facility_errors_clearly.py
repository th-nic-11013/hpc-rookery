from facilities.registry import get_backend
from hpc_agent_core import config

LABEL = 'unknown facility errors clearly, listing valid slugs'


def unknown_facility_errors_clearly():
    for label, fn in (("config", config.get_facility), ("registry", get_backend)):
        try:
            fn("definitely-not-a-facility")
        except ValueError as e:
            assert "Unknown facility" in str(e), f"{label}: {e}"
            assert "Valid facilities:" in str(e), f"{label}: no slug list in {e}"
        else:
            raise AssertionError(f"{label}: expected ValueError")
