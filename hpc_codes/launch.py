"""launch_plan: from "what to run" to "how to launch it here".

A workflow says which code, run type, system and method it wants to run;
this answers with the build, the ranks x threads layout and node count,
a job-spec fragment for submit_job, and a cost estimate, all taken from
software_registry, the benchmarks records and the facility's pack values.
Local only: it reads records and values and never contacts the cluster.

The launch line is the pack value <CODE>_LAUNCH, a mapping of templates
({R}, {T}, {N}, {env_script} and any other pack value by name), so a PBS
or PJM facility, or another code, brings its own.
"""
import math
from collections import Counter

from hpc_agent_core import config
from hpc_codes import packs, records

RUN_TYPES = ("aimd_step", "single_point", "geo_opt", "cell_opt")
SIMILAR_ATOMS = 0.30      # a "similar" system has atoms within +-30 %
MIN_EFFICIENCY = 0.7      # optimize="time" won't buy speed below this
MEM_HEADROOM = 0.9        # a rank may use this share of its slice of node memory


def _method_key(r: dict) -> tuple:
    m = r["method"]
    if m.get("model") is not None:   # an MLIP record (G37): no DFT fields
        return (r["system"]["label"], m["model"], m.get("precision"), m.get("interface"),
                m.get("device"), r["version"])
    return (r["system"]["label"], m["functional"], m["basis"], m["cutoff_ry"],
            m.get("rel_cutoff_ry"), m["hybrid"], r["version"])


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b)
    return str(a).strip().lower() == str(b).strip().lower()


def _fill(obj, subs: dict):
    if isinstance(obj, dict):
        return {k: _fill(v, subs) for k, v in obj.items()}
    return str(obj).format_map(subs)


def _duration(seconds: float) -> str:
    """Walltime with headroom: 1.5x plus 10 min, rounded up to 30 min."""
    s = math.ceil((seconds * 1.5 + 600) / 1800) * 1800
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:00"


def _per_node(group: list[dict]) -> dict[int, dict]:
    """The fastest record at each node count."""
    best = {}
    for r in group:
        n = r["nodes"]
        if n not in best or r["metric"]["value"] < best[n]["metric"]["value"]:
            best[n] = r
    return best


def _options(best: dict[int, dict], cores: int) -> list[dict]:
    one = best.get(1)
    out = []
    for n, r in sorted(best.items()):
        v = r["metric"]["value"]
        out.append({"nodes": n, "layout": f"{r['ranks_per_node']}x{r['threads_per_rank']}",
                    "value": v, "core_hours_per_unit": round(v * n * cores / 3600, 4),
                    "efficiency": round(one["metric"]["value"] / (n * v), 2) if one else None,
                    "record_id": r["id"]})
    return out


def _node_mib(facility: str, partition: str | None) -> float | None:
    """Memory per node of the partition, from the facility's bundled facts."""
    if not partition:
        return None
    try:
        parts = config.load_facts(facility).get("partitions") or []
    except (OSError, ValueError):
        return None
    for p in parts:
        if isinstance(p, dict) and p.get("name") == partition and p.get("memory_per_node_gib"):
            return float(p["memory_per_node_gib"]) * 1024
    return None


def _hfx_info(group: list[dict]) -> dict | None:
    """The exact-exchange memory of a recorded hybrid: the ERIs kept in RAM
    (a total over ranks, about the same at any layout) and what each rank
    holds besides them (peak per rank minus its share of the ERIs). From the
    newest record of the group that has both (cp2k_record_from_run writes
    them; G16)."""
    have = [r for r in group if (r.get("extra") or {}).get("hfx_eri_mib")
            and (r.get("extra") or {}).get("peak_mib_per_rank")]
    if not have:
        return None
    r = max(have, key=lambda r: str(r["date"]))
    eri, peak = r["extra"]["hfx_eri_mib"], r["extra"]["peak_mib_per_rank"]
    ranks = r["nodes"] * r["ranks_per_node"]
    return {"record_id": r["id"], "eri_total_mib": eri, "base_mib_per_rank": max(peak - eri / ranks, 0.0)}


def _memory_at(info: dict, nodes: int, rpn: int, node_mib: float | None) -> dict:
    per_rank = info["eri_total_mib"] / (nodes * rpn)
    peak = info["base_mib_per_rank"] + per_rank
    limit = node_mib / rpn * MEM_HEADROOM if node_mib else None
    return {"eri_per_rank_mib": round(per_rank), "est_peak_mib_per_rank": round(peak),
            "limit_mib_per_rank": round(limit) if limit else None,
            "fits": None if limit is None else peak <= limit}


def plan(facility: str, code: str, run_type: str, system: dict, method: dict | None = None,
         build_id: str | None = None, nodes: int | None = None, optimize: str = "cost",
         steps: int | None = None, hfx_memory_mb: int | None = None) -> dict:
    if run_type not in RUN_TYPES:
        raise ValueError(f"run_type must be one of {', '.join(RUN_TYPES)}")
    if optimize not in ("cost", "time"):
        raise ValueError('optimize must be "cost" or "time"')
    if not isinstance(system, dict) or not isinstance(system.get("atoms"), int):
        raise ValueError('system needs at least {"atoms": <int>}; also give "label" when known')
    if nodes is not None and nodes < 1:
        raise ValueError("nodes must be at least 1")
    if hfx_memory_mb is not None and hfx_memory_mb < 1:
        raise ValueError("hfx_memory_mb is the input's HFX MAX_MEMORY per MPI rank, in MiB")
    vals = packs.pack_values(facility, code)
    prefix = code.upper().replace("-", "_")
    launch = vals.get(f"{prefix}_LAUNCH")
    if not isinstance(launch, dict):
        raise ValueError(f"{facility}'s {code} pack values have no {prefix}_LAUNCH template")
    cores = int(vals[f"{prefix}_CORES_PER_NODE"])
    partition = vals.get(f"{prefix}_PARTITION")
    build = packs.resolve_build(facility, code, build_id)
    warnings = []

    replaced = records.superseded(facility, "benchmarks")   # G33: corrected records
    every = [r for r in records.query(facility, "benchmarks", {"code": code})
             if r.get("valid") and r["id"] not in replaced and r["ranks_per_node"] * r["threads_per_rank"] == cores
             and (partition is None or r["partition"] == partition)]
    label = system.get("label")
    newest = lambda g: max(str(r["date"]) for r in g)

    def groups_of(rt: str) -> list[list[dict]]:
        groups: dict[tuple, list[dict]] = {}
        for r in every:
            if r["run_type"] == rt and all(_same(r["method"].get(k), v) for k, v in (method or {}).items()):
                groups.setdefault(_method_key(r), []).append(r)
        return list(groups.values())

    def exact_of(groups: list[list[dict]]) -> list[list[dict]]:
        return [g for g in groups if label and _same(g[0]["system"]["label"], label)]

    def distance(g):
        s, key = g[0]["system"], ("electrons" if system.get("electrons") and g[0]["system"].get("electrons")
                                  else "atoms")
        return abs(math.log(s[key] / system[key]))

    groups = groups_of(run_type)
    exact = exact_of(groups)
    timing_run_type = run_type
    if not exact:
        near = [g for g in groups if distance(g) <= math.log(1 + SIMILAR_ATOMS)]
        if not near and run_type in ("geo_opt", "cell_opt"):
            # An optimisation step is one energy+forces (+stress) evaluation
            # restarting from the previous wavefunction, so a single point of
            # the same system and method bounds it from above (HOKUSAI, HSE06
            # UiO-66-conv: 423 s per cell_opt step vs 464 s single point).
            exact = exact_of(groups_of("single_point"))
            if exact:
                timing_run_type = "single_point"
                warnings.append(f"no {run_type} timing for {label}; the per-step time is its "
                                "single_point time (one energy+forces evaluation), which is "
                                "conservative: an optimisation step restarts from the previous "
                                "wavefunction and was 0.91x the single point for HSE06 UiO-66")
    if exact:
        match, group = "exact", max(exact, key=newest)
        if len(exact) > 1:
            warnings.append(f"{len(exact)} method variants of {label} are recorded; used the newest. "
                            "Pass method (functional, cutoff_ry, ...) to choose.")
    else:
        # newest first, so min() breaks a distance tie towards the newest
        group = min(sorted(near, key=newest, reverse=True), key=distance) if near else None
        match = "similar" if group else "none"

    memory = None
    if group:
        best = _per_node(group)
        options = _options(best, cores)
        # G7: a hybrid's exchange integrals must fit in each rank's memory
        hfx = _hfx_info(group) if match == "exact" else None
        node_mib = _node_mib(facility, partition) if hfx else None
        if hfx:
            for o in options:
                o["memory"] = _memory_at(hfx, o["nodes"], best[o["nodes"]]["ranks_per_node"], node_mib)
            fit = [o for o in options if o["memory"]["fits"] is not False]
            if nodes is None and fit and len(fit) < len(options):
                skipped = [o["nodes"] for o in options if o["memory"]["fits"] is False]
                warnings.append(f"memory: {skipped} node(s) would not hold this hybrid's exchange "
                                f"integrals ({hfx['eri_total_mib']} MiB in all); chose among the rest")
                options_for_choice = fit
            else:
                options_for_choice = options
        else:
            options_for_choice = options
        if nodes is not None:
            n = min(best, key=lambda k: (abs(k - nodes), k))
            if n != nodes:
                warnings.append(f"no timing on {nodes} node(s); layout taken from {n} node(s), no estimate")
        elif optimize == "cost":
            n = min(options_for_choice, key=lambda o: (o["core_hours_per_unit"], o["nodes"]))["nodes"]
        else:
            if 1 not in best:
                warnings.append("no 1-node timing, so efficiency is unknown; chose by cost instead")
                n = min(options_for_choice, key=lambda o: (o["core_hours_per_unit"], o["nodes"]))["nodes"]
            else:
                ok = [o for o in options_for_choice
                      if o["efficiency"] is not None and o["efficiency"] >= MIN_EFFICIENCY]
                n = min(ok or options_for_choice, key=lambda o: o["value"])["nodes"]
        chosen = best[n]
        R, T = chosen["ranks_per_node"], chosen["threads_per_rank"]
        nodes = nodes or n
        s = chosen["system"]
        from_sp = timing_run_type != run_type
        basis = {"match": match, "run_type": timing_run_type, "system": s["label"], "atoms": s["atoms"],
                 "method": chosen["method"], "record_ids": [chosen["id"]], "options": options,
                 "why": (f"fastest layout at {n} node(s) of the recorded {s['label']} "
                         f"({s['atoms']} atoms, {chosen['method'].get('functional') or chosen['method'].get('model')})"
                         + ("" if match == "exact" else "; a similar system, so the time is rough")
                         + (f"; no {run_type} record, so its single_point time stands in for one "
                            "step (rough, conservative)" if from_sp else ""))}
        if chosen["build_id"] != build["build_id"]:
            warnings.append(f"timing measured with {chosen['build_id']}, plan uses {build['build_id']}"
                            + ("" if str(chosen["version"]) == str(build["version"]) else
                               " (a different version: time may differ)"))
        if hfx:
            m = _memory_at(hfx, nodes, R, node_mib)
            headroom = (m["limit_mib_per_rank"] - hfx["base_mib_per_rank"]
                        if m["limit_mib_per_rank"] else None)
            want = math.ceil(m["eri_per_rank_mib"] * 1.2 / 100) * 100
            memory = {**m, "eri_total_mib": hfx["eri_total_mib"], "record_id": hfx["record_id"],
                      "rough": True,
                      # CP2K's HFX MAX_MEMORY per rank (cp2k_make_input hfx.memory_mb):
                      # the ERIs plus 20 %, never past what the rank can spare
                      "suggested_hfx_memory_mb": (int(min(want, headroom)) if headroom and headroom > 0
                                                  else want)}
            if m["fits"] is False:
                warnings.append(f"memory: at {nodes} node(s) x {R} ranks each rank needs about "
                                f"{m['est_peak_mib_per_rank']} MiB (exchange integrals "
                                f"{m['eri_per_rank_mib']} MiB/rank) but can use about "
                                f"{m['limit_mib_per_rank']} MiB: use more nodes, or expect an "
                                "out-of-memory kill")
            if hfx_memory_mb is not None and m["eri_per_rank_mib"] > hfx_memory_mb:
                warnings.append(f"memory: the exchange integrals need about {m['eri_per_rank_mib']} "
                                f"MiB per rank at this layout but HFX MAX_MEMORY is {hfx_memory_mb}: "
                                "CP2K computes the rest on the fly, so each step costs more than "
                                f"the estimate; set hfx memory_mb to {memory['suggested_hfx_memory_mb']} "
                                "or use more nodes")
            if node_mib is None:
                warnings.append(f"memory: no memory_per_node_gib for partition {partition} in "
                                f"{facility}'s facts, so only the HFX MAX_MEMORY check ran")
        elif group and match == "exact" and chosen["method"].get("hybrid"):
            warnings.append("memory: this hybrid timing has no recorded HFX memory "
                            "(hfx_eri_mib, peak_mib_per_rank), so the memory check was skipped; "
                            "record hybrid runs with cp2k_record_from_run")
        estimate = None
        if nodes == n:
            v = chosen["metric"]["value"]
            unit_ch = v * n * cores / 3600
            total = v if run_type == "single_point" else (v * steps if steps else None)
            estimate = {"metric": "s_per_step" if from_sp else chosen["metric"]["name"], "value": v,
                        "rough": match != "exact" or from_sp,
                        "core_hours_per_unit": round(unit_ch, 4),
                        "total_wall_s": round(total, 1) if total is not None else None,
                        "total_core_hours": round(total * n * cores / 3600, 2) if total is not None else None,
                        "suggested_duration": _duration(total) if total is not None else None}
    else:
        fallback = [r for r in every if r["run_type"] == run_type] or every
        if not fallback:
            raise ValueError(f"No {code} timings at all on {facility}; run {facility}-{code}-benchmark "
                             "first, it is what fills these records.")
        firsts = Counter()
        for g in {_method_key(r) + (r["nodes"],): None for r in fallback}:
            rows = [r for r in fallback if _method_key(r) + (r["nodes"],) == g]
            f = min(rows, key=lambda r: r["metric"]["value"])
            firsts[(f["ranks_per_node"], f["threads_per_rank"])] += 1
        (R, T), wins = firsts.most_common(1)[0]
        nodes = nodes or 1
        common = (f"{R}x{T} is the fastest layout in {wins} of {sum(firsts.values())} "
                  "recorded cases")
        rts = (run_type, "single_point") if run_type in ("geo_opt", "cell_opt") else (run_type,)
        labelled = [r for r in every if label and r["run_type"] in rts
                    and _same(r["system"]["label"], label)]
        if labelled and method:
            wanted = ", ".join(f"{k}={v}" for k, v in method.items())
            recorded = "; ".join(
                f"{k}: " + ", ".join(sorted({str(r["method"].get(k)) for r in labelled}))
                for k in method)
            why = (f"{label} is recorded, but not with {wanted}; recorded for {label}: "
                   f"{recorded}. No timing is scaled across methods; {common}")
            warnings.append(f"no timing for {label} with {wanted}; run {facility}-{code}-benchmark "
                            "first if the job is long")
        else:
            why = f"no recorded system like this; {common}"
            warnings.append(f"no timing for a system like this; run {facility}-{code}-benchmark "
                            "first if the job is long")
        basis = {"match": "none", "run_type": None, "record_ids": [], "options": [], "why": why}
        estimate = None

    subs = {k: v for k, v in vals.items() if not isinstance(v, (dict, list))}
    subs.update(R=R, T=T, N=nodes, env_script=build["env_script"])
    job_spec = {**_fill(launch, subs),
                "resources": {"node_count": nodes, "processes_per_node": R, "cpu_cores_per_process": T},
                "attributes": {"queue_name": partition} if partition else {}}
    return {"build": {k: build.get(k) for k in ("build_id", "version", "env_script", "data_dir")},
            "layout": {"nodes": nodes, "ranks_per_node": R, "threads_per_rank": T},
            "basis": basis, "job_spec": job_spec, "estimate": estimate, "memory": memory,
            "warnings": warnings}
