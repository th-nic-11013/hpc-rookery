from checks._common import no_user_config

LABEL = 'unconfigured account query returns setup directions first'


def unconfigured_account_query_returns_setup(slug, fac, backend):
    """Capability errors must not mask the cold-start configuration path."""
    from hpc_mcp.hpc_server import get_projects
    with no_user_config(slug):
        try:
            get_projects(slug)
        except RuntimeError as exc:
            text = str(exc)
            assert "not configured yet" in text
            assert f"{slug}-configuring" in text
        else:
            raise AssertionError(
                "unconfigured get_projects did not return setup instructions"
            )
