import subprocess
from unittest.mock import patch

LABEL = 'SSH multiplexing is detected without opening a connection'


def ssh_multiplexing_detection_is_advisory_and_uses_resolved_config():
    """Doctor must inspect local OpenSSH config without connecting."""
    from hpc_agent_core.doctor import check_ssh_multiplexing

    resolved = subprocess.CompletedProcess(
        ["ssh", "-G", "example"], 0,
        "controlmaster auto\ncontrolpath /tmp/ssh-%C\ncontrolpersist 30m\n",
        "",
    )
    active = subprocess.CompletedProcess(
        ["ssh", "-O", "check", "example"], 0, "", "Master running")
    with patch("hpc_agent_core.doctor.config.ssh_host", return_value="example"), \
            patch("hpc_agent_core.doctor.subprocess.run",
                  side_effect=[resolved, active]) as run:
        assert check_ssh_multiplexing("rikyu")
    assert run.call_args_list[0].args[0] == ["ssh", "-G", "example"]
    assert run.call_args_list[1].args[0] == ["ssh", "-O", "check", "example"]

    disabled = subprocess.CompletedProcess(
        ["ssh", "-G", "example"], 0, "controlmaster no\n", "",
    )
    with patch("hpc_agent_core.doctor.config.ssh_host", return_value="example"), \
            patch("hpc_agent_core.doctor.subprocess.run",
                  return_value=disabled) as run:
        assert check_ssh_multiplexing("rikyu")
    assert run.call_count == 1, "ControlMaster no must skip socket probing"
