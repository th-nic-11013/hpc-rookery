from checks._common import ROOT, public_facilities

LABEL = 'facility skill packs are isolated and discoverable'


def facility_skill_packs_are_isolated_and_discoverable():
    """The same base/pack split is valid for all three plugin formats.
    A facility from HPC_ROOKERY_FACILITY_PATH has no pack here, by design."""
    import json
    root = ROOT
    facilities = public_facilities()
    base_skills = sorted(p.name for p in (root / "plugins" / "hpc" / "skills").iterdir()
                         if p.is_dir())
    assert base_skills == ["hpc-facilities"], (
        "the base plugin must not preload facility workflow skills")

    marketplace = json.loads((root / ".agents" / "plugins" / "marketplace.json").read_text())
    names = [entry["name"] for entry in marketplace["plugins"]]
    assert names == ["hpc", *(f"hpc-{fac.slug}" for fac in facilities)]
    claude_marketplace = json.loads(
        (root / ".claude-plugin" / "marketplace.json").read_text())
    assert [entry["name"] for entry in claude_marketplace["plugins"]] == names

    base_portable = json.loads((root / "plugins" / "hpc" / "plugin.json").read_text())
    assert base_portable["$schema"].endswith("plugin.schema.json")
    portable_mcp = json.loads((root / "plugins" / "hpc" / "mcp.json").read_text())
    assert portable_mcp["$schema"].endswith("mcp.schema.json")
    assert set(portable_mcp["mcpServers"]) == {"hpc", "yagura", "hyogo"}
    # .mcp.json (the harnesses load it) is derived from mcp.json; it must
    # name the same servers, or one harness silently lacks a server
    harness_mcp = json.loads((root / "plugins" / "hpc" / ".mcp.json").read_text())
    assert set(harness_mcp["mcpServers"]) == set(portable_mcp["mcpServers"])
    for fac in facilities:
        plugin = root / "plugins" / f"hpc-{fac.slug}"
        manifest = json.loads((plugin / ".codex-plugin" / "plugin.json").read_text())
        assert manifest["name"] == f"hpc-{fac.slug}"
        assert "mcpServers" not in manifest, f"{fac.slug} pack must not duplicate MCP"
        claude = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())
        portable = json.loads((plugin / "plugin.json").read_text())
        assert claude["name"] == manifest["name"] == portable["name"]
        assert portable["$schema"].endswith("plugin.schema.json")
        assert list((plugin / "skills").glob(f"{fac.slug}-*/SKILL.md")), \
            f"{fac.slug} pack has no generated skills"
