import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from checks._common import ROOT

LABEL = 'every code pack renders for a facility and calls only real tools'

# `name(` with an underscore in the name: how skills write a tool call
CALL = re.compile(r"\b([a-z][a-z0-9]*_[a-z0-9_]+)\(")
KWARG = re.compile(r"(?:^|[,\s(])([a-z_][a-z0-9_]*)\s*=(?!=)")


def _tool_arguments() -> dict[str, set[str]]:
    """{tool: its argument names} for every tool the hpc, hyogo and yagura
    servers serve, listed in a child process with an empty HOME."""
    probe = ("import asyncio, json; from hpc_mcp.hpc_server import mcp as h; "
             "from hyogo.server import mcp as hy; from yagura.server import mcp as ya; "
             "print(json.dumps({t.name: sorted(t.input_schema.get('properties', {})) "
             "for m in (h, hy, ya) for t in asyncio.run(m.list_tools())}))")
    with tempfile.TemporaryDirectory() as home:
        env = {k: v for k, v in os.environ.items()
               if k not in ("HPC_RECORDS_USER_DIR", "HPC_RECORDS_ROOT")}
        env["HOME"] = home
        out = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    return {k: set(v) for k, v in json.loads(out.stdout.strip().splitlines()[-1]).items()}


def _call_args(text: str, open_paren: int) -> str:
    """The argument text of the call whose "(" is at open_paren, with every
    nested bracket's contents blanked, so only top-level keywords remain."""
    depth, out = 0, []
    for c in text[open_paren:]:
        if c in "([{":
            depth += 1
            out.append(c if depth == 1 else " ")
            continue
        if c in ")]}":
            depth -= 1
            if depth == 0:
                break
        out.append(c if depth == 1 else " ")
    return "".join(out)


def code_packs_render_for_any_facility_and_call_real_tools():
    """A code pack renders only for facilities that opt in, so a pack no
    shipped facility opts into (VASP, whose values a private facility repo
    supplies, G32) is never rendered by render_skills.py --check. This
    renders every pack's every stage for one facility with a stub value for
    each declared name: each stage listed in pack.json has a SKILL.md (and
    each folder with a SKILL.md is listed), the output's frontmatter names
    its folder, and no {{TOKEN}} is left in any rendered file. It also
    reads every Markdown file of every pack: each `snake_case(` call names a
    tool the hpc, hyogo or yagura server serves, and each keyword argument
    of it is one of that tool's arguments, so a skill can't point at an
    invented tool or argument."""
    spec = importlib.util.spec_from_file_location(
        "render_skills", ROOT / "scripts" / "render_skills.py")
    render_skills = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render_skills)
    token = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")

    packs = {}
    for pack_path in sorted((ROOT / "codes").glob("*/pack.json")):
        pack = json.loads(pack_path.read_text())
        code_dir = pack_path.parent
        assert pack["code"] == code_dir.name, f"{pack_path}: code {pack['code']!r}"
        listed = set(pack["stages"])
        has_skill = {p.parent.name for p in code_dir.glob("*/SKILL.md")}
        assert listed <= has_skill, f"{pack_path}: stages without SKILL.md: {sorted(listed - has_skill)}"
        assert has_skill <= listed, f"{pack_path}: SKILL.md folders not in stages: {sorted(has_skill - listed)}"
        packs[code_dir.name] = pack
    assert "vasp" in packs, "the VASP pack is gone"

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        fac = {"slug": "hokusai", "display_name": "Stub Machine", "_dir": tmp / "fac"}
        for code, pack in packs.items():
            d = fac["_dir"] / "codes" / code
            d.mkdir(parents=True)
            (d / "values.json").write_text(json.dumps(
                {name: f"stub-{name.lower()}" for name in pack["values"]}))
        stages = render_skills.code_pack_stages(fac)
        want = {"-".join(p for p in ("hokusai", pack.get("skill_prefix", code), stage) if p)
                for code, pack in packs.items() for stage in pack["stages"]}
        assert {s[0] for s in stages} == want, sorted(want ^ {s[0] for s in stages})
        for name, stage_dir, overlays, values, notes in stages:
            out = tmp / "out" / name
            text = stage_dir.joinpath("SKILL.md").read_text().replace("{{CODE_NOTES}}", notes)
            rendered = render_skills.substitute_values(
                render_skills.render_one(text, fac, "__unique__"), values)
            assert rendered.startswith("---\n"), f"{name}: no YAML frontmatter"
            front = rendered.split("---", 2)[1]
            assert f"name: {name}\n" in front, f"{name}: frontmatter name is not the folder"
            assert "description:" in front, f"{name}: no description"
            left = sorted(set(token.findall(rendered)))
            assert not left, f"{name}/SKILL.md: unresolved {left}"
            render_skills.sync_supporting_files(stage_dir, out, fac, False, overlays, values)
            for f in sorted(out.rglob("*")):
                if f.is_file():
                    try:
                        body = f.read_text()
                    except UnicodeDecodeError:
                        continue
                    left = sorted(set(token.findall(body)))
                    assert not left, f"{name}/{f.relative_to(out)}: unresolved {left}"

    tools = _tool_arguments()
    bad = []
    for md in sorted((ROOT / "codes").rglob("*.md")):
        text = md.read_text()
        for m in CALL.finditer(text):
            tool = m.group(1)
            where = f"{md.relative_to(ROOT)}:{text.count(chr(10), 0, m.start()) + 1}"
            if tool not in tools:
                bad.append(f"{where}: {tool}( is not a tool of hpc, hyogo or yagura")
                continue
            for kw in KWARG.findall(_call_args(text, m.end() - 1)):
                if kw not in tools[tool]:
                    bad.append(f"{where}: {tool} has no argument {kw!r}")
    assert not bad, "\n".join(bad)
