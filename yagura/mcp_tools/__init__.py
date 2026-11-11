"""The yagura server's tools, one module per group: yagura/mcp_tools/<group>.py.

yagura/server.py imports every module here (load()) and serves their tools;
a new tool goes in its group's module, or a new module, and nothing else
is edited. This file holds the server object and what several groups share.
"""
import importlib
import pkgutil
from pathlib import Path
from yagura.mcp_server import MCPServer

mcp = MCPServer("yagura-mcp")


def load() -> list:
    """Import every tool module in this folder, in name order, and return them."""
    return [importlib.import_module(f"{__name__}.{m.name}")
            for m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name)]


def _ase(module: str):
    """Import a yagura module that needs ASE, or say how to get it."""
    try:
        return importlib.import_module(f"yagura.{module}")
    except ModuleNotFoundError as e:
        if e.name and e.name.split(".")[0] == "ase":
            raise RuntimeError("this tool needs ASE, which is not installed: install the "
                               "optional extra (pip install 'hpc-rookery[yagura]', or "
                               "pip install ase) and restart the yagura server") from e
        raise


def _local_file(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"{p} is not a file on this machine. For a file on a "
                         "cluster, download it first (fs_download).")
    return p
