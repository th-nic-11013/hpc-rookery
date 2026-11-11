from hpc_agent_core import config

LABEL = 'slugs are lowercase and safe'


def slugs_are_url_and_identifier_safe():
    for fac in config.list_facilities():
        assert fac.slug == fac.slug.lower(), f"{fac.slug}: not lowercase"
        assert " " not in fac.slug, f"{fac.slug}: contains a space"
        assert fac.slug.replace("-", "").isalnum(), f"{fac.slug}: unexpected characters"
