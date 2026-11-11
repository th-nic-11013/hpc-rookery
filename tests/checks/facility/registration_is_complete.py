LABEL = 'registration is complete'


def registration_is_complete(slug, fac, backend):
    assert fac.display_name.strip(), "display_name is empty"
    assert fac.description.strip(), "description is empty"
    assert (fac.data_dir / fac.docs_filename).exists(), "guide file missing"
    assert (fac.data_dir / fac.facts_filename).exists(), "facts JSON missing"
    assert (fac.data_dir / "docs_index" / "chunks.json").exists(), "docs index missing"
