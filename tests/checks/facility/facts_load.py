from hpc_agent_core import config

LABEL = 'facts JSON loads'


def facts_load(slug, fac, backend):
    assert config.load_facts(slug), "facts JSON is empty"
