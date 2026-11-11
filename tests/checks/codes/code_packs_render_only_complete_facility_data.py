import json
import pathlib
import tempfile
from checks._common import ROOT

LABEL = 'code packs render only with complete facility data'


def code_packs_render_only_complete_facility_data():
    """A code pack's stage is rendered with one facility's values, notes
    and extra files, and anything that would ship half-filled fails.

    The real packs are covered by CI's render_skills.py --check; this
    exercises the failure paths on a throwaway pack, since a correct
    repository never hits them."""
    import importlib.util
    root = ROOT
    spec = importlib.util.spec_from_file_location(
        "render_skills", root / "scripts" / "render_skills.py")
    render_skills = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render_skills)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        render_skills.CODES_DIR = tmp / "codes"
        stage = tmp / "codes" / "demo" / "run"
        (stage / "scripts").mkdir(parents=True)
        (tmp / "codes" / "demo" / "pack.json").write_text(json.dumps(
            {"code": "demo", "stages": ["run"], "values": {"DEMO_CORES": "cores"}}))
        (stage / "SKILL.md").write_text("{{SLUG}} {{DEMO_CORES}}\n{{CODE_NOTES}}\n")
        (stage / "scripts" / "x.sh").write_text("N={{DEMO_CORES}}\n")
        fac_dir = tmp / "fac"
        overlay = fac_dir / "codes" / "demo" / "run"
        (overlay / "references").mkdir(parents=True)
        (overlay / "notes.md").write_text("site quirk on {{SLUG}}")
        (overlay / "references" / "site.md").write_text("{{DEMO_CORES}} cores\n")
        values_path = fac_dir / "codes" / "demo" / "values.json"
        fac = {"slug": "demo-fac", "display_name": "Demo", "_dir": fac_dir}

        for bad in ({}, {"DEMO_CORES": 8, "DEMO_TYPO": 1}):
            values_path.write_text(json.dumps(bad))
            try:
                render_skills.code_pack_stages(fac)
            except SystemExit:
                pass
            else:
                raise AssertionError(f"values {bad} were accepted")

        values_path.write_text(json.dumps({"DEMO_CORES": 8}))
        [(name, stage_dir, ovs, values, notes)] = render_skills.code_pack_stages(fac)
        assert (name, stage_dir, ovs) == ("demo-fac-demo-run", stage, (overlay,))
        assert notes == "site quirk on {{SLUG}}"

        out = tmp / "out"
        render_skills.sync_supporting_files(stage, out, fac, False, ovs, values)
        assert (out / "scripts" / "x.sh").read_text() == "N=8\n"
        assert (out / "references" / "site.md").read_text() == "8 cores\n"
        assert not (out / "notes.md").exists(), "notes.md fills {{CODE_NOTES}}, not a file"

        (overlay / "scripts").mkdir()
        (overlay / "scripts" / "x.sh").write_text("override\n")
        try:
            render_skills.sync_supporting_files(stage, out, fac, True, ovs, values)
        except SystemExit:
            pass
        else:
            raise AssertionError("a facility file shadowed a pack file")

        common = tmp / "codes" / "common" / "notes"
        common.mkdir(parents=True)
        (tmp / "codes" / "common" / "pack.json").write_text(json.dumps(
            {"code": "common", "skill_prefix": "", "stages": ["notes"],
             "values": {}, "facility_dirs": {"notes": "records"}}))
        (common / "SKILL.md").write_text("{{DEMO_CORES}}\n")
        (fac_dir / "codes" / "common").mkdir()
        (fac_dir / "codes" / "common" / "values.json").write_text("{}")
        (fac_dir / "records").mkdir()
        (fac_dir / "records" / "r.yaml").write_text("x: 1\n")
        by_name = {s[0]: s for s in render_skills.code_pack_stages(fac)}
        assert set(by_name) == {"demo-fac-demo-run", "demo-fac-notes"}, by_name
        _, _, ovs, values, _ = by_name["demo-fac-notes"]
        assert ovs == (fac_dir / "records",) and values["DEMO_CORES"] == 8

        assert render_skills.unresolved_tokens({root / "a": "ok {{SLUG}}"}) != []
        assert render_skills.unresolved_tokens({root / "a": "${HOME} {x}"}) == []
