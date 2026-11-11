LABEL = 'yagura gap_compare judges by functional class (G46)'


def yagura_gap_compare_judges_by_functional_class():
    """gap_compare on the UiO-66 gaps (2026-11-07) reproduces the hand
    judgement against the 4.07 eV DRS optical gap: the screened and 10 %
    hybrids agree, the 25 % global hybrids disagree, r2SCAN is the
    expected semilocal bias (not scored), with the KS-vs-optical and
    Gamma caveats; against B3LYP only the same functional is scored and
    the direction follows exact exchange; bad input is refused (G46)."""
    from yagura import server as ysrv
    gaps = [{"functional": "HSE06-D3(BJ)", "gap_ev": 4.04},
            {"functional": "r²SCANh-D3(BJ)", "gap_ev": 4.03},
            {"functional": "PBE0-D3(BJ)", "gap_ev": 4.72},
            {"functional": "r2SCAN0-D3(BJ)", "gap_ev": 4.93},
            {"functional": "r2SCAN-D3(BJ)", "gap_ev": 3.37},
            {"functional": "B3LYP", "gap_ev": 4.60, "label": "b3lyp-mine"}]
    refs = [{"value": 4.07, "kind": "optical", "source": "valenzano2011:h-drs-gap"},
            {"value": 4.56, "kind": "calculated", "functional": "B3LYP",
             "source": "valenzano2011:h-b3lyp-gap"}]
    r = ysrv.gap_compare(gaps, refs)
    v = {g["label"]: [row["verdict"] for row in g["rows"]] for g in r["gaps"]}
    assert v["HSE06-D3(BJ)"] == ["agree", "info"], v
    assert v["r²SCANh-D3(BJ)"] == ["agree", "info: consistent"], v
    assert v["PBE0-D3(BJ)"] == ["disagree", "info: consistent"], v
    assert v["r2SCAN0-D3(BJ)"] == ["disagree", "info: consistent"], v
    assert v["r2SCAN-D3(BJ)"] == ["expected bias", "info: consistent"], v
    assert v["b3lyp-mine"] == ["disagree", "agree"], v
    assert r["closest"]["valenzano2011:h-drs-gap"][:2] == ["HSE06-D3(BJ)", "r²SCANh-D3(BJ)"], r["closest"]
    assert "r2SCAN-D3(BJ)" not in r["closest"]["valenzano2011:h-drs-gap"]
    cav = " ".join(r["caveats"])
    assert "exciton" in cav and "Gamma" in cav, cav
    assert not any("Gamma" in c for c in ysrv.gap_compare(gaps[:1], refs[:1], kpoints="mesh")["caveats"])
    # a semilocal gap above experiment is flagged, not scored
    hi = ysrv.gap_compare([{"functional": "PBE", "gap_ev": 4.3}], refs[:1])
    assert hi["gaps"][0]["rows"][0]["verdict"] == "unexpected", hi
    # an unlisted functional needs its exact exchange; a listed one can't be contradicted
    ok = ysrv.gap_compare([{"functional": "myhyb", "gap_ev": 4.1, "exact_exchange": 0.15}], refs[:1])
    assert ok["gaps"][0]["class"] == "hybrid" and ok["gaps"][0]["rows"][0]["verdict"] == "agree", ok
    for bad, text in (([{"functional": "myhyb", "gap_ev": 4.0}], "unknown functional"),
                      ([{"functional": "PBE0", "gap_ev": 4.0, "exact_exchange": 0.1}], "not 0.1"),
                      ([{"functional": "PBE0", "gap_ev": -1}], "positive"),
                      ([{"functional": "PBE0", "gap": 4.0}], "unknown gap field"),
                      (gaps[:1] * 2, "labelled")):
        try:
            ysrv.gap_compare(bad, refs[:1])
        except ValueError as e:
            assert text in str(e), (text, e)
        else:
            raise AssertionError(f"gap_compare did not refuse ({text})")
    for bad, text in (([{"value": 4.0, "kind": "tauc"}], "kind is one of"),
                      ([{"value": 4.0, "kind": "calculated"}], "needs its functional"),
                      ([], "references is empty")):
        try:
            ysrv.gap_compare(gaps[:1], bad)
        except ValueError as e:
            assert text in str(e), (text, e)
        else:
            raise AssertionError(f"gap_compare did not refuse ({text})")
