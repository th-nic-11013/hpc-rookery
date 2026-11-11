from checks._common import ROOT

LABEL = 'README requires explicit facility selection for manual install'


def readme_requires_explicit_facility_selection_for_manual_install():
    root = ROOT
    readme = (root / "README.md").read_text()
    normalized = " ".join(readme.split())
    assert "before installing any facility skills, ask the user" in normalized
    assert "Do not install all facility packs by default" in normalized
    assert "plugins/hpc-<selected-slug>/skills/" in readme
