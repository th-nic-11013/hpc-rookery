from checks._common import ROOT

LABEL = 'skill descriptions parse as plain YAML scalars'


def skill_descriptions_are_valid_yaml_scalars():
    """An unquoted frontmatter value may not contain ': ' or ' #' - YAML
    reads the first as a nested mapping and the second as a comment, and
    the client then drops the description, so the skill is never offered.

    Caught a real one: hokusai-records shipped as
    'measured records for HOKUSAI BigWaterfall2 (HBW2): benchmark ...',
    which failed to parse and left the skill with a blank description.
    Checks both the generated skills and the code-pack sources they come
    from, so the error names the file to fix.
    """
    paths = sorted((ROOT / "plugins").glob("hpc-*/skills/*/SKILL.md"))
    paths += sorted((ROOT / "codes").glob("*/*/SKILL.md"))
    assert paths, "no skills found"
    bad = []
    for path in paths:
        front = path.read_text().split("---", 2)[1]
        for line in front.splitlines():
            key, sep, value = line.partition(":")
            if not sep or key.startswith(" ") or key not in ("name", "description"):
                continue
            value = value.strip()
            if value[:1] in ("'", '"', "|", ">"):
                continue
            if ": " in value or " #" in value or value.endswith(":"):
                bad.append(f"{path.relative_to(ROOT)} ({key})")
    assert not bad, "unquoted ': ' or ' #' in frontmatter: " + ", ".join(bad)
