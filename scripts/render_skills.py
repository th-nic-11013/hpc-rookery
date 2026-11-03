"""Render one SKILL.md per (facility, workflow) from a shared template plus
that facility's own hand-written notes — the placeholder mechanism for the
genuinely universal parts of a skill (tool names, caching-mode tables,
"show before you run"), while every facility's real cluster-specific
knowhow (GPU dialect, module tables, MPI gotchas, failure modes, SSH portal
name) stays freely-authored prose in its own file, not squeezed into a
rigid multi-slot template.

    python scripts/render_skills.py            # rewrite in place
    python scripts/render_skills.py --check     # exit 1 if stale (CI)

Inputs (per facility, from facilities/<dir>/):
  facility.json           slug, display_name (also used by
                           render_facility_tables.py)
  skill_notes/<workflow>.md   freely-authored markdown dropped verbatim into
                           that workflow's {{FACILITY_NOTES}} placeholder.
                           Optional — a missing file falls back to a short
                           generic pointer at get_facility/search_docs, so a
                           facility PR can add real notes incrementally.
  skills/<name>/SKILL.md  a workflow only this facility has, copied (with
                           scalar placeholders resolved) to <slug>-<name>/.
  skills/<name>/**        any other files beside it (scripts/, references/)
                           are mirrored into the same output folder; text
                           files get the scalar placeholders too.

Inputs (code packs, from codes/<code>/, rendered only for facilities that
opt in with facilities/<dir>/codes/<code>/values.json):
  pack.json                {"code": ..., "stages": [...], "values": {NAME: doc}}:
                           every {{NAME}} a stage may use, all required.
  <stage>/SKILL.md, <stage>/**   the facility-neutral component, rendered to
                           <slug>-<code>-<stage>/ with the scalar tokens,
                           {{NAME}} from values.json and {{CODE_NOTES}} from
                           facilities/<dir>/codes/<code>/<stage>/notes.md.
  facilities/<dir>/codes/<code>/<stage>/**   facility files added to that
                           output (e.g. references/facility.md); a path the
                           pack also ships is an error, not an override.
  An unknown or missing value, or a {{TOKEN}} left in the output, fails the
  render: a facility never ships a half-filled component.

Inputs (shared, from templates/skills/):
  <workflow>.md.tmpl       one per workflow (configuring, submitting-jobs,
                           monitoring-jobs, demo, reproducing). Editing a
                           template is a deliberate, cross-cutting change
                           that affects every facility at once — not
                           something a single facility's PR should need to
                           touch; see PORTING.md.

Output: plugins/hpc-<slug>/skills/<slug>-<workflow>/SKILL.md, one directory
per (facility, workflow) pair.  ``plugins/hpc`` stays small: it owns the
shared MCP servers and the one universal discovery skill, while a user only
installs a facility pack after choosing that facility. A facility PR that
adds/edits skill_notes/*.md must re-run this (no --check) and commit the
result; CI's --check run catches a PR that forgot to. Plugin skill folders
may also contain hand-authored custom skills; this renderer never treats
those as stale or tries to manage them.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import hpc_mcp  # noqa: E402,F401 -- import registers every facility
from hpc_agent_core import config as _config  # noqa: E402

TEMPLATES_DIR = ROOT / "templates" / "skills"
CODES_DIR = ROOT / "codes"
PLUGINS_DIR = ROOT / "plugins"

_FALLBACK_NOTES = (
    "_No facility-specific notes yet for this workflow — see "
    "`get_facility(facility=\"{slug}\")` and `search_docs(facility=\"{slug}\", "
    "query=...)` on the `hpc-docs` server for details._"
)


def load_facilities() -> list[dict]:
    facs = []
    for manifest in sorted((ROOT / "facilities").glob("*/facility.json")):
        data = json.loads(manifest.read_text())
        data["_dir"] = manifest.parent
        facs.append(data)
    return sorted(facs, key=lambda f: f["slug"])


def skills_dir(fac: dict) -> Path:
    """The isolated skill-pack directory for one facility."""
    return PLUGINS_DIR / f"hpc-{fac['slug']}" / "skills"


def render_one(template_text: str, fac: dict, workflow: str) -> str:
    slug = fac["slug"]
    notes_path = fac["_dir"] / "skill_notes" / f"{workflow}.md"
    notes = notes_path.read_text().rstrip() if notes_path.exists() else _FALLBACK_NOTES.format(slug=slug)
    # {{FACILITY_NOTES}} is substituted FIRST, before the scalar placeholders
    # below — so {{SLUG}}/{{DISPLAY_NAME}}/{{ENV_PREFIX}}/{{CONFIG_STEM}}
    # also resolve if a facility's own skill_notes/*.md writes them (natural
    # for DRY-ness, e.g. `facility="{{SLUG}}"` instead of a hardcoded slug).
    # Reversing this order would silently leave those tokens unreplaced
    # inside notes content, since the scalar pass would already be done by
    # the time notes text enters the string.
    # CONFIG_EXAMPLE and SETUP_HELP come from the live registration rather
    # than facility.json, so the configuring skill shows byte-for-byte the
    # same JSON and prerequisites that an unconfigured tool call returns at
    # runtime (config.setup_instructions). Hardcoding them in the template
    # is how those two drift apart.
    registered = _config.get_facility(slug)
    config_example = json.dumps(registered.config_example, indent=2)
    setup_help = registered.setup_help or (
        "_No machine-specific setup notes registered for this facility._"
    )

    text = template_text.replace("{{FACILITY_NOTES}}", notes)
    text = text.replace("{{CONFIG_EXAMPLE}}", config_example)
    text = text.replace("{{SETUP_HELP}}", setup_help)
    return substitute_scalars(text, fac)


def substitute_scalars(text: str, fac: dict) -> str:
    """Resolve the four scalar tokens, which mean the same thing anywhere."""
    env_prefix = fac["slug"].upper().replace("-", "_")
    text = text.replace("{{SLUG}}", fac["slug"])
    text = text.replace("{{DISPLAY_NAME}}", fac["display_name"])
    text = text.replace("{{ENV_PREFIX}}", env_prefix)
    text = text.replace("{{CONFIG_STEM}}", env_prefix.lower())
    return text


def _is_ignored(rel: Path) -> bool:
    # .DS_Store, editor swap files, __pycache__: never part of a skill.
    return any(part.startswith(".") or part == "__pycache__" for part in rel.parts)


def render_supporting_file(source: Path, fac: dict, values: dict | None = None) -> bytes:
    """A file shipped beside a facility-unique SKILL.md (a script, a
    reference page). Text gets the scalar tokens (and, for a code pack, its
    values); anything that isn't UTF-8 is copied byte-for-byte."""
    data = source.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return substitute_values(substitute_scalars(text, fac), values).encode("utf-8")


def substitute_values(text: str, values: dict | None) -> str:
    for name, value in (values or {}).items():
        text = text.replace("{{" + name + "}}", str(value))
    return text


def sync_supporting_files(skill_dir: Path, out_dir: Path, fac: dict,
                          check: bool, overlays: tuple[Path, ...] = (),
                          values: dict | None = None) -> list[Path]:
    """Mirror everything in skill_dir except SKILL.md into out_dir, keeping
    the executable bit, and remove files that no longer have a source.
    out_dir is generated from skill_dir in full, so a file there with no
    source is a leftover from a rename or deletion, not hand-authored work.
    Returns every path that was (or, under check, would be) changed.
    overlays are further source folders merged in (a code pack's facility
    files); notes.md there is not a file to ship, it fills {{CODE_NOTES}}."""
    changed = []
    expected = set()
    sources = [(skill_dir, s) for s in sorted(skill_dir.rglob("*"))]
    sources += [(d, s) for d in overlays for s in sorted(d.rglob("*"))]
    for base, source in sources:
        rel = source.relative_to(base)
        if not source.is_file() or rel == Path("SKILL.md") or _is_ignored(rel):
            continue
        if base in overlays and rel == Path("notes.md"):
            continue
        if rel in expected:
            raise SystemExit(f"{source}: {rel} is already shipped by {skill_dir}")
        expected.add(rel)
        out_path = out_dir / rel
        rendered = render_supporting_file(source, fac, values)
        executable = bool(source.stat().st_mode & 0o111)
        if (out_path.exists() and out_path.read_bytes() == rendered
                and bool(out_path.stat().st_mode & 0o111) == executable):
            continue
        changed.append(out_path)
        if not check:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(rendered)
            mode = out_path.stat().st_mode
            out_path.chmod(mode | 0o111 if executable else mode & ~0o111)

    if out_dir.exists():
        for out_path in sorted(out_dir.rglob("*"), reverse=True):
            rel = out_path.relative_to(out_dir)
            if out_path.is_dir():
                if not check and not any(out_path.iterdir()):
                    out_path.rmdir()
                continue
            if rel == Path("SKILL.md") or _is_ignored(rel) or rel in expected:
                continue
            changed.append(out_path)
            if not check:
                out_path.unlink()
    return changed


_TOKEN = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")


def code_pack_stages(fac: dict) -> list[tuple[str, Path, Path, dict, str]]:
    """(output name, stage dir, facility overlay dir, values, notes) for every
    code-pack stage this facility opts into. Raises SystemExit on a values
    file that does not match its pack, so a broken pack fails CI loudly."""
    stages = []
    for values_path in sorted((fac["_dir"] / "codes").glob("*/values.json")):
        code = values_path.parent.name
        pack_path = CODES_DIR / code / "pack.json"
        if not pack_path.exists():
            raise SystemExit(f"{values_path}: no code pack {pack_path}")
        pack = json.loads(pack_path.read_text())
        values = json.loads(values_path.read_text())
        missing = sorted(set(pack["values"]) - set(values))
        unknown = sorted(set(values) - set(pack["values"]))
        if missing or unknown:
            raise SystemExit(f"{values_path}: missing {missing}, unknown {unknown} "
                             f"(the names are declared in {pack_path})")
        for stage in pack["stages"]:
            overlay = values_path.parent / stage
            notes_path = overlay / "notes.md"
            notes = notes_path.read_text().rstrip() if notes_path.exists() else ""
            stages.append((f"{fac['slug']}-{code}-{stage}", CODES_DIR / code / stage,
                           overlay, values, notes))
    return stages


def unresolved_tokens(rendered: dict[Path, str]) -> list[str]:
    return [f"{path.relative_to(ROOT)}: {', '.join(sorted(set(_TOKEN.findall(text))))}"
            for path, text in rendered.items() if _TOKEN.search(text)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                         help="Exit 1 if any generated skill would change, without writing.")
    args = parser.parse_args()

    facilities = load_facilities()
    templates = sorted(TEMPLATES_DIR.glob("*.md.tmpl"))
    if not templates:
        print(f"No templates found under {TEMPLATES_DIR}", file=sys.stderr)
        return 1

    stale = []
    written = []

    # Facility-unique skills: a workflow only one machine has, with no
    # shared template to render from. Fugaku's build guidance is the
    # motivating case — cross-compiling for A64FX is a real workflow there
    # and meaningless everywhere else, so adding a `build` template would
    # give every other facility an empty skill. These are copied verbatim
    # from facilities/<slug>/skills/<name>/SKILL.md, with the same scalar
    # placeholders substituted so they can still say facility="{{SLUG}}".
    # Anything else in that folder (scripts/, references/, ...) ships beside
    # it, so a long build script can live in its own file and SKILL.md can
    # point at it by relative path instead of inlining it.
    for fac in facilities:
        facility_skills = skills_dir(fac)
        for skill_dir in sorted((fac["_dir"] / "skills").glob("*/")):
            source = skill_dir / "SKILL.md"
            if not source.exists():
                continue
            out_dir = facility_skills / f"{fac['slug']}-{skill_dir.name.rstrip('/')}"
            out_path = out_dir / "SKILL.md"
            supporting = sync_supporting_files(skill_dir, out_dir, fac, args.check)
            stale.extend(supporting)
            if not args.check:
                written.extend(supporting)
            rendered = render_one(source.read_text(), fac, "__unique__")
            if out_path.exists() and out_path.read_text() == rendered:
                continue
            stale.append(out_path)
            if not args.check:
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path.write_text(rendered)
                written.append(out_path)

    # Code packs: one facility-neutral component per (code, stage) under
    # codes/<code>/<stage>/, rendered for each facility that opts in with
    # facilities/<slug>/codes/<code>/values.json. The method is written once;
    # the facility's values, notes and extra files arrive as data.
    unique_names = {f"{fac['slug']}-{d.name}" for fac in facilities
                    for d in (fac["_dir"] / "skills").glob("*/")}
    leftover = []
    for fac in facilities:
        facility_skills = skills_dir(fac)
        for name, stage_dir, overlay, values, notes in code_pack_stages(fac):
            if name in unique_names:
                raise SystemExit(f"{name}: both a facility-unique skill and a code-pack stage")
            out_dir = facility_skills / name
            out_path = out_dir / "SKILL.md"
            overlays = (overlay,) if overlay.is_dir() else ()
            text = stage_dir.joinpath("SKILL.md").read_text().replace("{{CODE_NOTES}}", notes)
            rendered = substitute_values(render_one(text, fac, "__unique__"), values)
            check_text = {out_path: rendered}
            for base in (stage_dir, *overlays):
                for src in base.rglob("*"):
                    if src.is_file() and src.name != "SKILL.md" and not _is_ignored(src.relative_to(base)):
                        try:
                            check_text[out_dir / src.relative_to(base)] = \
                                render_supporting_file(src, fac, values).decode("utf-8")
                        except UnicodeDecodeError:
                            pass
            leftover += unresolved_tokens(check_text)
            supporting = sync_supporting_files(stage_dir, out_dir, fac, args.check,
                                               overlays, values)
            stale.extend(supporting)
            if not args.check:
                written.extend(supporting)
            if out_path.exists() and out_path.read_text() == rendered:
                continue
            stale.append(out_path)
            if not args.check:
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path.write_text(rendered)
                written.append(out_path)
    if leftover:
        print("Unresolved placeholders in code-pack output:", *leftover, sep="\n  ",
              file=sys.stderr)
        return 1

    for template_path in templates:
        workflow = template_path.name.removesuffix(".md.tmpl")
        template_text = template_path.read_text()
        for fac in facilities:
            facility_skills = skills_dir(fac)
            out_dir = facility_skills / f"{fac['slug']}-{workflow}"
            out_path = out_dir / "SKILL.md"
            rendered = render_one(template_text, fac, workflow)
            current = out_path.read_text() if out_path.exists() else None
            if current == rendered:
                continue
            stale.append(out_path)
            if not args.check:
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path.write_text(rendered)
                written.append(out_path)

    rel = [str(p.relative_to(ROOT)) for p in stale]
    if args.check:
        if rel:
            print("Generated skills are stale:", *rel, sep="\n  ")
            print("\nRun: python scripts/render_skills.py")
            return 1
        print(f"Generated skills up to date ({len(facilities)} facilities x {len(templates)} workflows).")
        return 0

    print(f"Rendered {len(written)} skill file(s) across {len(facilities)} facilities.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
