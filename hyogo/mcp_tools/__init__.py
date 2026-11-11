"""The hyogo server's tools, one module per group: hyogo/mcp_tools/<group>.py.

hyogo/server.py imports every module here (load()) and serves their tools;
a new tool goes in its group's module, or a new module, and nothing else
is edited. This file holds the server object and what several groups share.
"""
import importlib
import pkgutil
import gzip
from pathlib import Path
from hyogo.mcp_server import MCPServer

mcp = MCPServer("hyogo-mcp")


def load() -> list:
    """Import every tool module in this folder, in name order, and return them."""
    return [importlib.import_module(f"{__name__}.{m.name}")
            for m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name)]


def _read_local(path: str, what: str) -> tuple[Path, bytes]:
    """A local file's bytes (gunzipped if it ends in .gz)."""
    src = Path(path).expanduser()
    if not src.is_file():
        raise ValueError(f"{src} ({what}) is not a file on this machine. For a file on a "
                         "cluster, download it first (fs_download).")
    raw = src.read_bytes()
    if src.suffix == ".gz":
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError) as e:
            raise ValueError(f"{src} is not a readable gzip file ({e})") from None
    return src, raw
