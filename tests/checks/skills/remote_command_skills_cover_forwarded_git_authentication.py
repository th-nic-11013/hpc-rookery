from checks._common import ROOT, public_facilities

LABEL = 'remote-command skills diagnose forwarded Git authentication'


def remote_command_skills_cover_forwarded_git_authentication():
    """Generated command skills must not guess a generic SSH key path."""
    root = ROOT
    source = (root / "templates" / "skills" / "remote-command.md.tmpl").read_text()
    assert "ssh -G <host>" in source
    assert "forwardagent" in source
    assert "ssh-add <their-facility-key>" in source
    assert "never invent a path" in source
    for facility in public_facilities():
        rendered = (root / "plugins" / f"hpc-{facility.slug}" / "skills" /
                    f"{facility.slug}-remote-command" / "SKILL.md").read_text()
        assert "Git-over-SSH authentication failures" in rendered
