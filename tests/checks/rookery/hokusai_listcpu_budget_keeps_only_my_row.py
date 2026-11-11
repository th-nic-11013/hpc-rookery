from checks._common import TESTS

LABEL = 'HOKUSAI listcpu budget keeps only my row (G35)'


def hokusai_listcpu_budget_keeps_only_my_row():
    """G35: listcpu -p prints the per-subsystem budget and every
    member's usage. The parser keeps the budget and the caller's own
    row only; other members never appear in what it returns."""
    import json
    from pathlib import Path
    from facilities.hokusai.facility import parse_listcpu
    text = (TESTS / "fixtures" / "hokusai" / "listcpu-p.txt").read_text()
    r = parse_listcpu(text, "thnic123")
    assert r["project"] == "RB230090", r
    mpc = r["subsystems"][0]
    assert (mpc["subsystem"], mpc["used_h"], mpc["limit_h"], mpc["used_pct"], mpc["expiry"]) \
        == ("mpc", 2320866.2, 3019161.6, 76.9, "2027-03-31"), mpc
    assert [x["subsystem"] for x in r["subsystems"]] == ["mpc", "lmc", "gpu", "fpga"]
    assert r["subsystems"][3]["limit_h"] is None, "fpga has no limit ('-')"
    assert r["user"] == {"user": "thnic123", "used_h": 8745.7, "expiry": "2027-03-31",
                         "marked": False}, r["user"]
    assert "member" not in json.dumps(r), "another member's row leaked"
    assert parse_listcpu(text, "member2")["user"]["marked"] is True
    assert parse_listcpu(text, "nobody")["user"] is None
    try:
        parse_listcpu("listcpu: command not found", "thnic123")
    except RuntimeError as e:
        assert "not recognised" in str(e)
    else:
        raise AssertionError("unrecognised listcpu text was parsed")
