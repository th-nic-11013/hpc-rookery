from unittest.mock import patch

LABEL = 'Fugaku PJM accounting exposes groups and points'


def fugaku_pjm_accounting_exposes_groups_and_points():
    """Fugaku's IRI accounting shape from `id -Gn` + `accountj_pt -c`.

    Fixture strings are the exact formats observed live on 2026-08-30:
    the `id` group list and one GROUP_POINT CSV row.
    """
    from facilities.fugaku.compute import PJMBackend

    backend = PJMBackend("fugaku")
    pt_csv = (
        'INFO_H,COLLECT_DATE\n'
        '"INFO","2026-08-30 22:08:01"\n'
        'GROUP_POINT_H,GROUP,POINT,LIMIT(N),USAGE(N),AVAILABLE(N)\n'
        '"GROUP_POINT","ra000009","1110550","466431087","291635579","174795508"\n'
    )

    def fake(_facility, command):
        if command == "id -Gn":
            return "fugaku ra000009 ra250029 hp250291 trial\n"
        if " trial " in command:
            raise RuntimeError("no GROUP_POINT row for trial")
        return pt_csv

    with patch("facilities.fugaku.compute.run_command", fake):
        rows = backend.get_projects()
    accounts = [row["account"] for row in rows]
    assert accounts == ["hp250291", "ra000009", "ra250029", "trial"], accounts
    assert "fugaku" not in accounts  # shared group cannot submit
    assert rows[0]["points"]["point_total"] == 1110550
    assert "f-pt" in rows[0]["resource_groups"]
    assert rows[-1]["resource_groups"] == [
        "spot-small", "spot-large", "spot-int", "spot-middle"]
    assert "points" not in rows[-1]
    for row in rows:
        assert row["qos"] == []

    with patch("facilities.fugaku.compute.run_command",
               return_value=pt_csv):
        alloc = backend.get_project_allocations("ra000009")
    assert alloc == {
        "project_id": "ra000009", "point_total": 1110550,
        "limit_n": 466431087, "usage_n": 291635579,
        "available_n": 174795508,
    }

    with patch("facilities.fugaku.compute.run_command",
               side_effect=RuntimeError("connection failed")):
        try:
            backend.get_project_allocations("trial")
        except RuntimeError as exc:
            assert "connection failed" in str(exc)
        else:
            raise AssertionError(
                "a failed read was masked as 'project has no points'")
