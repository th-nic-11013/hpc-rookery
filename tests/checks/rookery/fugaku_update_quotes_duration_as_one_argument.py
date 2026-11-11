import shlex
from hpc_agent_core.models import JobAttributes, JobSpec, ResourceSpec
from unittest.mock import patch

LABEL = 'Fugaku update quotes duration as one argument'


def fugaku_update_quotes_duration_as_one_argument():
    from facilities.fugaku.compute import PJMBackend

    backend = PJMBackend("fugaku")
    unsafe = '01:02:03"; touch /tmp/pwned; #'
    # Bypass the generic validator deliberately to verify the backend's
    # independent defense in depth. Normal MCP input cannot construct
    # this model after the duration fix above.
    spec = JobSpec.model_construct(
        executable="unused",
        resources=ResourceSpec(),
        attributes=JobAttributes.model_construct(duration=unsafe),
    )
    commands = []

    def record(_facility, command):
        commands.append(command)
        return ""

    with patch("facilities.fugaku.compute.run_command", record), \
            patch.object(backend, "get_statuses", return_value=[]):
        try:
            backend.update("123", spec)
        except ValueError:
            pass  # expected: the mocked status lookup finds no job
    assert len(commands) == 1, commands
    assert shlex.split(commands[0]) == ["pjalter", "-L", f"elapse={unsafe}", "123"]
