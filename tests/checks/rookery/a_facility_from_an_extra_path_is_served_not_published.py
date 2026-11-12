import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from checks._common import ROOT

LABEL = 'a facility from HPC_ROOKERY_FACILITY_PATH is served, not published'

#: the in-tree facility copied to make the throwaway one
SOURCE = "rikyu"
SLUG = "extralab"
RENDERS = ("render_facility_tables.py", "render_plugins.py", "render_skills.py")

OWN_CHECK = '''LABEL = "extralab brings its own check"


def extralab_brings_its_own_check(slug, fac, backend):
    assert slug == "extralab" and fac.display_name == "Extra Lab"
'''

PROBE = '''
import asyncio, json, sys
sys.path.insert(0, "tests")
import hpc_mcp, live_smoke
from hpc_agent_core import config
from facilities.registry import get_backend
from hpc_mcp.hpc_server import mcp
from checks.skills.facility_skill_packs_are_isolated_and_discoverable import \\
    facility_skill_packs_are_isolated_and_discoverable as isolated
from checks.skills.generated_skills_are_wellformed import generated_skills_are_wellformed
from checks.skills.remote_command_skills_cover_forwarded_git_authentication import \\
    remote_command_skills_cover_forwarded_git_authentication as remote
from checks.skills.facility_data_is_declared_as_package_data import \\
    facility_data_is_declared_as_package_data as wheel
from checks.skills.every_facility_has_skill_notes import every_facility_has_skill_notes as notes
for check in (isolated, generated_skills_are_wellformed, remote, wheel, notes):
    check()
get_backend("extralab")
listed = asyncio.run(mcp.call_tool("get_facilities", {}))
facts = asyncio.run(mcp.call_tool("get_facility", {"facility": "extralab"}))
print(json.dumps({"slugs": [f.slug for f in config.list_facilities()],
                  "extra": hpc_mcp.EXTRA_FACILITIES, "failed": hpc_mcp.FAILED_FACILITIES,
                  "listed": "extralab" in str(listed), "facts": bool(str(facts)),
                  "job_defaults": live_smoke.job_defaults("extralab")}))
'''


def _private_facility(folder: Path) -> None:
    """facilities/rikyu copied under a new slug, with its own conformance
    check and live-smoke defaults, as a private repository would hold it."""
    fac = folder / SLUG
    shutil.copytree(ROOT / "facilities" / SOURCE, fac,
                    ignore=shutil.ignore_patterns("__pycache__"))
    for kind in ("config.json", "guide.md"):
        (fac / "data" / f"{SOURCE}_{kind}").rename(fac / "data" / f"{SLUG}_{kind}")
    py = (fac / "facility.py").read_text()
    assert f'SLUG = "{SOURCE}"' in py, f"facilities/{SOURCE}/facility.py changed shape; pick another source"
    py = py.replace(f'SLUG = "{SOURCE}"', f'SLUG = "{SLUG}"')
    py = py.replace('display_name="RIKYU (RIKEN AI4S / GB200)"', 'display_name="Extra Lab"')
    (fac / "facility.py").write_text(py)
    manifest = json.loads((fac / "facility.json").read_text())
    manifest.update(slug=SLUG, display_name="Extra Lab")
    (fac / "facility.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (fac / "checks").mkdir()
    (fac / "checks" / "extralab_brings_its_own_check.py").write_text(OWN_CHECK)
    (fac / "live_smoke.json").write_text('{"queue_name": "tiny", "duration": 120}\n')


def _snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts}


def _run_all(cmds: list[list[str]], cwd: Path, env: dict) -> list[subprocess.CompletedProcess]:
    procs = [subprocess.Popen(c, cwd=cwd, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True) for c in cmds]
    out = []
    for c, p in zip(cmds, procs):
        stdout, _ = p.communicate(timeout=300)
        out.append(subprocess.CompletedProcess(c, p.returncode, stdout))
    return out


def a_facility_from_an_extra_path_is_served_not_published():
    """G39. A facility kept in a private repository is named by
    HPC_ROOKERY_FACILITY_PATH instead of being symlinked into facilities/.
    A copy of an in-tree facility under a new slug, in a temporary folder,
    must be loaded and served, run the per-facility checks plus the one it
    ships itself, give live_smoke its own job defaults, leave every
    published generated file (tables, plugins, marketplaces) exactly as
    it was, render its skills to --out only, and not break the checks of
    those published files (the skill-pack isolation check failed while a
    private facility was symlinked in)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "ext").mkdir()
        _private_facility(tmp / "ext")
        base = {k: v for k, v in os.environ.items() if k != "HPC_ROOKERY_FACILITY_PATH"}
        (tmp / "home").mkdir()
        base["HOME"] = str(tmp / "home")
        env = {**base, "HPC_ROOKERY_FACILITY_PATH": os.pathsep.join(
            [str(tmp / "ext"), str(tmp / "missing")])}

        out = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=300)
        assert out.returncode == 0, out.stderr[-3000:]
        got = json.loads(out.stdout.strip().splitlines()[-1])
        assert got["slugs"][-1] == SLUG and got["extra"] == [SLUG], got
        assert got["listed"] and got["facts"], got
        assert list(got["failed"]) == [str(tmp / "missing")], \
            f"a missing HPC_ROOKERY_FACILITY_PATH folder must be reported: {got['failed']}"
        assert got["job_defaults"] == {"queue_name": "tiny", "duration": 120}, got

        # The renders write, so they run in a copy of this checkout: first
        # without the variable (so a stale tree isn't blamed on it), then with.
        repo = tmp / "repo"
        shutil.copytree(ROOT, repo, symlinks=True, ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__"))
        renders = [[sys.executable, f"scripts/{r}"] for r in RENDERS]
        for r in _run_all(renders, repo, base):
            assert r.returncode == 0, r.stdout[-2000:]
        before = _snapshot(repo)
        skills = tmp / "skills"
        runs = _run_all(renders + [
            [sys.executable, "scripts/render_skills.py", "--out", str(skills)],
            [sys.executable, "scripts/render_skills.py", "--out", str(repo / "plugins" / "x")],
        ], repo, env)
        for r in runs[:-1]:
            assert r.returncode == 0, (r.args, r.stdout[-2000:])
        assert runs[-1].returncode == 2 and "inside this checkout" in runs[-1].stdout, \
            runs[-1].stdout[-2000:]
        assert SLUG in runs[2].stdout, "render_skills must name the facility it skips"
        after = _snapshot(repo)
        changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
        assert not changed, f"a render published the extra-path facility: {changed[:10]}"

        templates = {p.name.removesuffix(".md.tmpl")
                     for p in (ROOT / "templates" / "skills").glob("*.md.tmpl")}
        rendered = {p.parent.name for p in skills.glob("*/SKILL.md")}
        assert rendered == {f"{SLUG}-{t}" for t in templates}, sorted(rendered)
        text = (skills / f"{SLUG}-submitting-jobs" / "SKILL.md").read_text()
        assert text.startswith("---\n") and f'facility="{SLUG}"' in text, text[:500]

        conf = subprocess.run([sys.executable, "tests/conformance.py", "--only", SLUG],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
        assert conf.returncode == 0, conf.stdout[-3000:]
        assert f"=== {SLUG} (Extra Lab) ===" in conf.stdout, conf.stdout[-2000:]
        assert "✓ extralab brings its own check" in conf.stdout, conf.stdout[-2000:]
