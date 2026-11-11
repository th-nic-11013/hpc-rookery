import os
import pathlib
import tempfile
from unittest.mock import patch

LABEL = 'HPC_RECORDS_ROOT moves the user scope'


def records_root_moves_the_user_scope():
    """HPC_RECORDS_ROOT puts each facility's user records under
    <root>/<slug>/; HPC_RECORDS_USER_DIR still wins, and with neither set
    the default under ~/.hpc-agent is unchanged."""
    from hpc_codes import records
    env = {k: v for k, v in os.environ.items()
           if k not in ("HPC_RECORDS_USER_DIR", "HPC_RECORDS_ROOT")}
    with patch.dict(os.environ, env, clear=True):
        assert records.user_dir("hokusai") == \
            pathlib.Path.home() / ".hpc-agent" / "records" / "hokusai"
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as only, \
            patch.dict(os.environ, {**env, "HPC_RECORDS_ROOT": root}, clear=True):
        assert records.user_dir("hokusai") == pathlib.Path(root) / "hokusai"
        shared = records.query("hokusai", "benchmarks")
        good = dict({k: v for k, v in shared[0].items() if k != "_scope"},
                    id="records-root-probe", job_id="1", build_external=True)
        out = records.append("hokusai", "benchmarks", good)
        assert out["path"] == str(pathlib.Path(root) / "hokusai" / "benchmarks.yaml"), out
        assert [r["_scope"] for r in records.query("hokusai", "benchmarks",
                                                   {"id": "records-root-probe"})] == ["user"]
        with patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": only}):
            assert records.user_dir("hokusai") == pathlib.Path(only)
