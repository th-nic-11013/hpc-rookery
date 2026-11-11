from hpc_agent_core import config

LABEL = 'at least one facility is registered'


def facilities_exist():
    assert config.list_facilities(), "no facilities registered"
