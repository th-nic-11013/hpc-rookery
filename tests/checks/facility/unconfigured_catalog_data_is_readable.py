from hpc_agent_core import config
from checks._common import no_user_config

LABEL = 'bundled catalog data remains readable without user config'


def unconfigured_catalog_data_is_readable(slug, fac, backend):
    """Bundled data remains readable so setup directions can be built."""
    with no_user_config(slug):
        assert config.load_facts(slug), "get_facility would fail unconfigured"
        assert config.docs_source(slug).exists(), "guide unreadable unconfigured"
        assert fac.display_name and fac.description, "get_facilities would be degraded"
