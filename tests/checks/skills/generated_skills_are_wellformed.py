from hpc_agent_core import config
from checks._common import ROOT

LABEL = 'generated skills have matching frontmatter'


def generated_skills_are_wellformed():
    """Every generated SKILL.md needs YAML frontmatter whose name
    matches its directory, or the client cannot discover it.

    Caught a real one: Fugaku-Agent's fugaku-build skill shipped with
    no frontmatter at all — the only skill in that repo missing it, so
    it had no name or description to be found by.
    """
    import pathlib
    root = ROOT
    skills = sorted((root / "plugins").glob("hpc-*/skills/*/SKILL.md"))
    assert skills, "no generated skills found"
    for path in skills:
        text = path.read_text()
        assert text.startswith("---\n"), f"{path.parent.name}: no YAML frontmatter"
        front = text.split("---", 2)[1]
        assert f"name: {path.parent.name}\n" in front, (
            f"{path.parent.name}: frontmatter name does not match its directory")
        assert "description:" in front, f"{path.parent.name}: no description"

    remote_skills = sorted((root / "plugins").glob(
        "hpc-*/skills/*-remote-command/SKILL.md"))
    assert len(remote_skills) == len(config.list_facilities()), (
        "every facility needs a generated remote-command skill"
    )
    for path in remote_skills:
        text = path.read_text()
        assert "May I run these" in text, f"{path.parent.name}: no permission example"
        assert "Prefer multiple focused calls" in text, (
            f"{path.parent.name}: no short-command guidance"
        )
        assert "Changed state:" in text, f"{path.parent.name}: no execution recap example"
