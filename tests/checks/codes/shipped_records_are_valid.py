import os
import tempfile
from hpc_agent_core import config
from unittest.mock import patch

LABEL = 'shipped records are valid'


def shipped_records_are_valid():
    from hpc_codes import records
    for fac in config.list_facilities():
        if not records.facility_dir(fac.slug).is_dir():
            continue
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}):
            errs = records.check(fac.slug)["errors"]
        # with no user registry, unresolved build_ids are expected and
        # only those: every other error is a defect in the shipped file
        errs = [e for e in errs if "is not in software_registry" not in e]
        assert not errs, f"{fac.slug}: {errs[:5]}"
