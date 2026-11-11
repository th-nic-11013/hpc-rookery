from hpc_agent_core import config
from checks._common import ROOT

LABEL = 'facility data ships in a wheel'


def facility_data_is_declared_as_package_data():
    """Every file a facility reads at runtime must match a
    package-data glob in pyproject.toml.

    These are read via Path(__file__).parent/"data", which works
    perfectly in an editable install and silently ships nothing in a
    wheel unless declared. That is the failure this repo's own
    PORTING.md warns about, and it was real here: the first built wheel
    contained zero guides, zero docs indexes and zero facts JSON, so the
    documented `uv tool run --from git+...` install would have produced
    a server where get_facility and search_docs failed on every
    facility, while local testing looked fine.
    """
    import fnmatch
    import tomllib
    root = ROOT
    with open(root / "pyproject.toml", "rb") as fh:
        globs = tomllib.load(fh)["tool"]["setuptools"]["package-data"]["*"]

    for fac in config.list_facilities():
        wanted = [fac.data_dir / fac.docs_filename,
                  fac.data_dir / fac.facts_filename,
                  fac.data_dir / "docs_index" / "chunks.json"]
        for path in wanted:
            if not path.exists():
                continue
            rel = path.relative_to(fac.data_dir.parent).as_posix()
            assert any(fnmatch.fnmatch(rel, g) for g in globs), (
                f"{fac.slug}: {rel} matches no package-data glob in "
                f"pyproject.toml, so it would not ship in a wheel")
