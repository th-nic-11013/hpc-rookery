import importlib.util
import json

from checks._common import ROOT

LABEL = 'sibling plugins are listed from their repositories'


def sibling_plugins_are_listed_from_their_repositories():
    """yagura and hyogo are Claude Code plugins of their own: the marketplace
    lists each by its GitHub repository (not a copy under plugins/), and, where
    the sibling is checked out next to this repo, its root plugin.json has the
    same name and starts that sibling's server."""
    spec = importlib.util.spec_from_file_location(
        "render_plugins", ROOT / "scripts" / "render_plugins.py")
    plugins = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugins)
    siblings = plugins.SIBLING_PLUGINS
    assert set(siblings) == {"yagura", "hyogo"}, sorted(siblings)

    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    entries = {e["name"]: e for e in market["plugins"]}
    for name, (repo, _description) in siblings.items():
        entry = entries.get(name)
        assert entry, f"marketplace.json does not list {name}"
        assert entry["source"] == {"source": "github", "repo": repo}, entry["source"]
        assert not (ROOT / "plugins" / name).exists(), f"plugins/{name} would shadow {repo}"

        checkout = ROOT.parent / name
        manifest = checkout / ".claude-plugin" / "plugin.json"
        if not manifest.exists():
            continue  # sibling not checked out here, or not yet a plugin there
        got = json.loads(manifest.read_text())
        assert got["name"] == name, f"{manifest}: name {got['name']!r}"
        servers = got.get("mcpServers", {})
        assert list(servers) == [name], f"{manifest}: servers {list(servers)}"
        assert servers[name]["args"][-1] == f"{name}-mcp", servers[name]["args"]
