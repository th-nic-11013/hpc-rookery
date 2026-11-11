from hpc_agent_core import config
from checks._common import no_user_config

LABEL = 'setup directions are machine-specific and actionable'


def setup_directions_are_actionable(slug, fac, backend):
    """What a user gets when they say "I want to use this machine" and
    nothing is configured. It has to stand on its own: the agent may
    not be able to open any other file, so the directions must name the
    config file, show what to put in it, and cover this machine's own
    prerequisites."""
    assert fac.setup_help.strip(), (
        "no setup_help registered — an unconfigured user would get generic "
        "directions with nothing machine-specific (how to get a key "
        "registered, the real login hostname, any required account)"
    )
    assert fac.config_example, "no config_example registered"
    assert "ssh" in fac.config_example, "config_example has no ssh section"

    with no_user_config(slug):
        text = config.setup_instructions(slug)
        expected_path = str(config.config_path(slug))
    assert expected_path in text, "directions omit the config file path"
    assert f"{slug}-configuring" in text, "directions don't name the configuring skill"
    assert "hpc-doctor" in text, "directions don't say how to verify"
    assert fac.setup_help.splitlines()[0] in text, "setup_help didn't reach the directions"
    # The example must be valid, copy-pasteable JSON.
    import json as _json
    _json.loads(_json.dumps(fac.config_example))
