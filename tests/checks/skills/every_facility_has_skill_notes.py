from checks._common import ROOT

LABEL = 'every facility has real submitting-jobs notes'


def every_facility_has_skill_notes():
    """A facility with no notes for a workflow still renders (a stub is
    substituted), but submitting-jobs is where a port's real value
    lives — an empty one means an unfinished port."""
    import hpc_mcp
    root = ROOT
    extra = [hpc_mcp.FACILITY_DIRS[s] / "facility.json" for s in hpc_mcp.EXTRA_FACILITIES]
    for manifest in sorted((root / "facilities").glob("*/facility.json")) + extra:
        notes = manifest.parent / "skill_notes" / "submitting-jobs.md"
        assert notes.exists() and notes.read_text().strip(), \
            f"{manifest.parent.name}: skill_notes/submitting-jobs.md is missing or empty"
