import importlib.util
import json
import re
import tomllib

from checks._common import ROOT

LABEL = 'install sources follow the one setting'

# where users are told to install or clone this code from; history and
# credit pages (docs/) name upstream on purpose and are not scanned
SCANNED = ("plugins", "templates", "README.md", "AGENTS.md", "PORTING.md",
           "pyproject.toml", "tests/notebook_client.py")
GIT_SOURCE = re.compile(r"git\+https?://[^\s\"'`]+")
REPO_LINK = re.compile(r"https?://github\.com/[^/\s\"'`]+/hpc-rookery(?:\.git)?\b")


def install_sources_follow_the_one_setting():
    """G34: every repository field and install source names REPO_URL and
    INSTALL_REF from scripts/render_plugins.py, so switching between this
    fork and upstream is one line and a hard-coded URL cannot come back."""
    spec = importlib.util.spec_from_file_location(
        "render_plugins", ROOT / "scripts" / "render_plugins.py")
    plugins = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugins)
    repo, source = plugins.REPO_URL, plugins.INSTALL_SOURCE
    assert source == f"git+{repo}.git@{plugins.INSTALL_REF}", source

    manifests = [p for p in (ROOT / "plugins").glob("*/plugin.json")]
    manifests += (ROOT / "plugins").glob("*/.*-plugin/plugin.json")
    assert len(manifests) >= 3, "no plugin manifests found"
    for path in manifests:
        got = json.loads(path.read_text()).get("repository")
        assert got == repo, f"{path.relative_to(ROOT)}: repository {got!r}, not {repo!r}"

    for name in ("mcp.json", ".mcp.json"):
        servers = json.loads((ROOT / "plugins" / "hpc" / name).read_text())["mcpServers"]
        assert servers, f"plugins/hpc/{name} has no servers"
        for server, entry in servers.items():
            args = entry["args"]
            pinned = args[args.index("--from") + 1]
            # "git+<url>@<ref>" or "hpc-rookery[extra] @ git+<url>@<ref>"
            assert pinned.split(" @ ")[-1] == source, f"plugins/hpc/{name} {server}: {pinned}"

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["urls"]["Repository"] == repo, project["urls"]

    stale = []
    for top in SCANNED:
        base = ROOT / top
        for path in ([base] if base.is_file() else sorted(base.rglob("*"))):
            if not path.is_file():
                continue
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                bad = [m for m in GIT_SOURCE.findall(line) if m != source]
                bad += [m for m in REPO_LINK.findall(line) if m not in (repo, f"{repo}.git")]
                stale += [f"{path.relative_to(ROOT)}:{line_no}: {m}" for m in bad]
    assert not stale, ("install/clone sources that do not follow REPO_URL, INSTALL_REF in "
                       "scripts/render_plugins.py:\n  " + "\n  ".join(stale))
