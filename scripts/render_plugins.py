"""Render marketplace and plugin metadata for Codex, Claude Code, OpenPlugin.

The shared ``hpc`` plugin owns the two MCP servers and only universal skills.
Each facility instead has a small ``hpc-<slug>`` plugin containing its own
generated workflow skills.  This keeps a user from loading guidance for
machines they do not use while keeping all plugin metadata derived from the
same ``facilities/*/facility.json`` source as the skill generator.

    python scripts/render_plugins.py
    python scripts/render_plugins.py --check
    python scripts/render_plugins.py --bump [REF]   # once per merge, see below

Plugin versions are cache-busters: an installed plugin updates only when its
version changes. Gap branches never edit them (that made every branch
conflict on the same lines). Once per merge into the integration branch, run
--bump with that branch as it was before the merge (default origin/main): it
raises the minor version of every plugin whose files differ from REF, unless
its version already differs from REF's, so a second run bumps nothing.
"""
import argparse
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
CODEX_MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"
CLAUDE_MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
OWNER = {"name": "William Dawson", "email": "william.dawson@riken.jp"}
OPEN_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
OPEN_PLUGIN_MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"


def facilities() -> list[dict]:
    return sorted(
        (json.loads(path.read_text()) for path in (ROOT / "facilities").glob("*/facility.json")),
        key=lambda facility: facility["slug"],
    )


#: plugin name -> version set by --bump for this run
BUMPED: dict[str, str] = {}
MANIFESTS = ("plugin.json", ".codex-plugin/plugin.json", ".claude-plugin/plugin.json")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def bump_versions(ref: str, names: list[str]) -> dict[str, str]:
    """Minor-bump each plugin in names whose files changed since ref."""
    if git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode:
        raise SystemExit(f"--bump: {ref!r} is not a commit (fetch it, or pass the branch to compare with)")
    bumped = {}
    for name in names:
        base = f"plugins/{name}"
        old = git("show", f"{ref}:{base}/.codex-plugin/plugin.json")
        if old.returncode:
            continue  # new plugin: keeps its initial version
        old_version = json.loads(old.stdout)["version"]
        current = manifest_version(PLUGINS / name / ".codex-plugin" / "plugin.json", old_version)
        if current != old_version:
            continue  # already bumped since ref
        changed = git("diff", "--name-only", ref, "--", base).stdout.split()
        changed += git("ls-files", "--others", "--exclude-standard", "--", base).stdout.split()
        changed = [f for f in changed if not any(f == f"{base}/{m}" for m in MANIFESTS)]
        if changed:
            major, minor, *_ = (old_version.split(".") + ["0", "0"])[:3]
            bumped[name] = f"{major}.{int(minor) + 1}.0"
    return bumped


def manifest_version(path: Path, initial: str) -> str:
    """Keep an explicit plugin cache-buster when regenerating metadata.

    Plugin versions are release inputs, not derived facility data.  The
    renderer owns the rest of the manifest but must not silently reset a
    version bumped for an update.
    """
    name = path.parent.parent.name
    if name in BUMPED:
        return BUMPED[name]
    if path.exists():
        try:
            value = json.loads(path.read_text()).get("version")
        except json.JSONDecodeError:
            value = None
        if isinstance(value, str) and value.strip():
            return value
    return initial


def skill_plugin(facility: dict, version: str) -> dict:
    slug = facility["slug"]
    name = f"hpc-{slug}"
    display = f"HPC — {facility['display_name']}"
    return {
        "name": name,
        "version": version,
        "description": (
            f"Facility-specific HPC workflow skills for {facility['display_name']}. "
            "Install the hpc base plugin first for MCP tools."
        ),
        "author": OWNER,
        "repository": "https://github.com/william-dawson/hpc-rookery",
        "keywords": ["hpc", slug, facility["scheduler"]],
        "skills": "./skills/",
        "interface": {
            "displayName": display,
            "shortDescription": f"Workflow skills for {facility['display_name']}.",
            "longDescription": (
                f"Adds only the configuration, reference, submission, monitoring, "
                f"demo, and reproducibility skills for {facility['display_name']}. "
                "Requires the hpc base plugin for its MCP servers."
            ),
            "developerName": "William Dawson",
            "category": "Productivity",
            "capabilities": ["Interactive"],
            "defaultPrompt": [
                f"Show me how to configure access to {facility['display_name']}.",
                f"Help me submit a job on {facility['display_name']}.",
            ],
        },
    }


def base_plugin(version: str) -> dict:
    return {
        "name": "hpc",
        "version": version,
        "description": "Shared HPC MCP servers and facility discovery. Install only the facility skill packs you use.",
        "author": OWNER,
        "repository": "https://github.com/william-dawson/hpc-rookery",
        "keywords": ["hpc", "slurm", "supercomputer", "multi-facility"],
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
        "interface": {
            "displayName": "HPC",
            "shortDescription": "Shared HPC MCP servers and facility discovery.",
            "longDescription": "Adds shared HPC MCP servers and discovery. Install an hpc-<facility> skill pack only for a facility you choose to use.",
            "developerName": "William Dawson",
            "category": "Productivity",
            "capabilities": ["Interactive", "Write"],
            "defaultPrompt": [
                "List the available HPC facilities.",
                "Which facility skill pack should I install?",
                "Show me how to configure access to RIKYU.",
            ],
        },
    }


def portable_manifest(manifest: dict) -> dict:
    """OpenPlugin has fixed component locations, so keep its root manifest metadata-only."""
    return {
        "$schema": OPEN_PLUGIN_SCHEMA,
        **{key: manifest[key] for key in
           ("name", "version", "description", "author", "repository", "keywords")},
    }


def claude_manifest(manifest: dict) -> dict:
    """Claude discovers skills/MCP from fixed root paths, just like Codex."""
    return {
        "name": manifest["name"],
        "description": manifest["description"],
        "version": manifest["version"],
        "author": manifest["author"],
        "repository": manifest["repository"],
        "keywords": manifest["keywords"],
    }


def marketplace_entry(name: str) -> dict:
    return {
        "name": name,
        "source": {"source": "local", "path": f"./plugins/{name}"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Productivity",
    }


def codex_marketplace(facs: list[dict]) -> dict:
    names = ["hpc", *(f"hpc-{facility['slug']}" for facility in facs)]
    return {
        "name": "hpc-marketplace",
        "interface": {"displayName": "HPC Agent Hub"},
        "owner": OWNER,
        "plugins": [marketplace_entry(name) for name in names],
    }


def claude_marketplace(manifests: list[dict]) -> dict:
    return {
        "name": "hpc-marketplace",
        "owner": OWNER,
        "plugins": [
            {
                "name": manifest["name"],
                "source": f"./plugins/{manifest['name']}",
                "description": manifest["description"],
            }
            for manifest in manifests
        ],
    }


def portable_mcp() -> dict:
    args_prefix = [
        "tool", "run", "--quiet", "--from",
        "git+https://github.com/william-dawson/hpc-rookery.git@main",
    ]
    return {
        "$schema": OPEN_PLUGIN_MCP_SCHEMA,
        "mcpServers": {
            # hpc-mcp serves the scheduler, docs-search and code-pack tools
            "hpc": {"type": "stdio", "command": "uv", "args": [*args_prefix, "hpc-mcp"], "env": {}},
            # the [yagura] extra brings ASE for the CIF tools
            "yagura": {"type": "stdio", "command": "uv", "env": {},
                       "args": [*args_prefix[:-1], f"hpc-rookery[yagura] @ {args_prefix[-1]}", "yagura-mcp"]},
            # the [gromacs] extra brings ASE for build_system
            "hyogo": {"type": "stdio", "command": "uv", "env": {},
                      "args": [*args_prefix[:-1], f"hpc-rookery[gromacs] @ {args_prefix[-1]}", "hyogo-mcp"]},
        },
    }


def write_or_check(path: Path, payload: dict, check: bool, stale: list[Path]) -> None:
    expected = json.dumps(payload, indent=2) + "\n"
    current = path.read_text() if path.exists() else None
    if current == expected:
        return
    stale.append(path)
    if not check:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 when generated plugin files are stale.")
    parser.add_argument("--bump", nargs="?", const="origin/main", metavar="REF",
                        help="minor-bump plugins changed since REF (default origin/main); once per merge")
    args = parser.parse_args()
    facs = facilities()
    if args.bump and args.check:
        parser.error("--bump and --check do not go together")
    if args.bump:
        BUMPED.update(bump_versions(args.bump, ["hpc", *(f"hpc-{f['slug']}" for f in facs)]))
        for name, version in sorted(BUMPED.items()):
            print(f"bumped {name} to {version}")
        if not BUMPED:
            print(f"no plugin changed since {args.bump}, or each is already bumped")
    stale: list[Path] = []
    manifests: list[dict] = []
    base_dir = PLUGINS / "hpc"
    base = base_plugin(manifest_version(
        base_dir / ".codex-plugin" / "plugin.json", "0.2.0"))
    manifests.append(base)
    write_or_check(PLUGINS / "hpc" / ".codex-plugin" / "plugin.json",
                   base, args.check, stale)
    write_or_check(PLUGINS / "hpc" / ".claude-plugin" / "plugin.json",
                   claude_manifest(base), args.check, stale)
    write_or_check(PLUGINS / "hpc" / "plugin.json", portable_manifest(base), args.check, stale)
    write_or_check(PLUGINS / "hpc" / "mcp.json", portable_mcp(), args.check, stale)
    for facility in facs:
        plugin_dir = PLUGINS / f"hpc-{facility['slug']}"
        manifest = skill_plugin(facility, manifest_version(
            plugin_dir / ".codex-plugin" / "plugin.json", "0.1.0"))
        manifests.append(manifest)
        write_or_check(
            plugin_dir / ".codex-plugin" / "plugin.json", manifest, args.check, stale,
        )
        write_or_check(plugin_dir / ".claude-plugin" / "plugin.json",
                       claude_manifest(manifest), args.check, stale)
        write_or_check(plugin_dir / "plugin.json", portable_manifest(manifest), args.check, stale)
    write_or_check(CODEX_MARKETPLACE, codex_marketplace(facs), args.check, stale)
    write_or_check(CLAUDE_MARKETPLACE, claude_marketplace(manifests), args.check, stale)
    if stale:
        rel = [str(path.relative_to(ROOT)) for path in stale]
        if args.check:
            print("Generated plugin metadata is stale:", *rel, sep="\n  ")
            print("\nRun: python scripts/render_plugins.py")
            return 1
        print("Rendered plugin metadata:", *rel, sep="\n  ")
    else:
        print(f"Plugin metadata up to date ({len(facs)} facility skill packs).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
