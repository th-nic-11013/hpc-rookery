"""LAMMPS input for MD with a machine-learned potential (pair_style metatomic).

Taught from HOKUSAI's MLMD benchmark inputs (~/software/mlip/2026.10/bench/
inputs/common.in, md_valid.in, md_time.in; copied to tests/fixtures/lammps/
hokusai_bench/inputs/), which ran MACE-MP-0 medium on water and UiO-66
(jobs 9619420, 9695779, 9700016). Modes "valid" and "time" write those
inputs with every include and variable expanded; "nvt", "npt" and "nve"
are production runs built from the same start, with optional trajectory
dump, restart files and a PLUMED bias (as tests/smoke/in.melt_plumed).
"""
import re

MODES = ("nvt", "npt", "nve", "valid", "time")
BAROSTATS = {"iso": "iso", "aniso": "aniso", "tri": "tri", "z": "z"}


def _num(x) -> str:
    """A number as LAMMPS reads it, without float noise: 300 -> 300.0, 0.0005 -> 0.0005."""
    return repr(float(x))


def make_input(data: dict, model: str, mode: str, steps: dict, temperature: float = 300.0,
               timestep_fs: float = 0.5, tdamp_fs: float | None = None, seed: int = 4928459,
               device: str = "cpu", pressure_bar: float = 1.0, barostat: str = "iso",
               pdamp_fs: float | None = None, thermo_every: int | None = None,
               dump_every: int = 0, restart_every: int = 0, restart_from: str | None = None,
               plumed_file: str | None = None, data_name: str | None = None) -> tuple[str, dict]:
    if mode not in MODES:
        raise ValueError(f"mode is one of {', '.join(MODES)}; got {mode!r}")
    if not isinstance(data, dict) or not data.get("elements") or not data.get("data"):
        raise ValueError("data is yagura's structure_to_lammps_data result (it carries "
                         "`data` and `elements`); call it first")
    elements = list(data["elements"])
    zs = data.get("atomic_numbers")
    if not zs or len(zs) != len(elements):
        raise ValueError("data lacks atomic_numbers for its elements: pass "
                         "structure_to_lammps_data's whole result")
    if not str(model).endswith(".pt"):
        raise ValueError(f"model is the exported metatomic model (.pt) on the cluster; got "
                         f"{model!r}. Export a MACE model with metatomic first")
    if device not in ("cpu", "cuda"):
        raise ValueError("device is 'cpu' or 'cuda'")
    if temperature <= 0:
        raise ValueError("temperature is in K and positive")
    if timestep_fs <= 0 or timestep_fs > 2.0:
        raise ValueError("timestep_fs is in fs, above 0 and at most 2")
    if "H" in elements and timestep_fs > 1.0:
        raise ValueError(f"timestep_fs {timestep_fs} with hydrogen: O-H vibrations need "
                         "<= 1 fs (0.5 fs is the benchmarked value)")
    need = {"valid": ("nvt", "nve"), "time": ("warm", "timed")}.get(mode, ("run",))
    steps = dict(steps or {})
    bad = [k for k in need if not isinstance(steps.get(k), int) or steps[k] < 0]
    extra = sorted(set(steps) - set(need))
    if bad or extra:
        raise ValueError(f"steps for mode {mode!r} is {{{', '.join(f'{k!r}: int' for k in need)}}}"
                         f"; got {steps}")
    if mode in ("valid", "time") and (dump_every or restart_every or restart_from or plumed_file):
        raise ValueError(f"mode {mode!r} is the benchmark run; dumps, restarts and PLUMED "
                         "are for nvt, npt and nve")
    if mode == "npt" and barostat not in BAROSTATS:
        raise ValueError(f"barostat is one of {', '.join(BAROSTATS)}")
    warnings = []
    ts_ps = timestep_fs / 1000.0
    tdamp = (tdamp_fs if tdamp_fs is not None else 100 * timestep_fs) / 1000.0
    pdamp = (pdamp_fs if pdamp_fs is not None else 1000 * timestep_fs) / 1000.0
    name = data_name or str(data["data"]).rsplit("/", 1)[-1]
    if re.search(r"\s", name) or re.search(r"\s", model):
        raise ValueError("paths with spaces don't survive LAMMPS' parser; rename them")

    L = [f"# {mode} run with pair_style metatomic, written by hyogo lammps_make_input",
         f"# {data.get('natoms', '?')} atoms, types 1..{len(elements)} = {' '.join(elements)}",
         "units           metal",
         "atom_style      atomic",
         "boundary        p p p"]
    if restart_from:
        L.append(f"read_restart    {restart_from}")
    else:
        L.append(f"read_data       {name}")
    L += ["",
          f"pair_style      metatomic {model} device {device} uncertainty_threshold off",
          f"pair_coeff * * {' '.join(map(str, zs))}",
          "",
          "neighbor        1.0 bin",
          "neigh_modify    every 1 delay 0 check yes",
          f"timestep        {_num(ts_ps)}",
          ""]
    cols = "step temp pe ke etotal press" + (" vol density" if mode == "npt" else "")
    L += [f"thermo_style    custom {cols}",
          "thermo_modify   norm no flush yes format float %.10f", ""]
    if not restart_from:
        L += ["# step 0 energy at the input geometry", "run             0",
              f"velocity        all create {_num(temperature)} {seed} mom yes rot no dist gaussian"]
    T = _num(temperature)
    nvt = f"fix             thermostat all nvt temp {T} {T} {_num(tdamp)}"
    if mode == "valid":
        L += [nvt, f"thermo          {thermo_every or 10}", f"run             {steps['nvt']}",
              "unfix           thermostat", "fix             integrate all nve",
              "reset_timestep  0", f"run             {steps['nve']}"]
    elif mode == "time":
        L += [nvt, f"thermo          {steps['warm']}", f"run             {steps['warm']}",
              f"thermo          {steps['timed']}", f"run             {steps['timed']}"]
    else:
        if mode == "nvt":
            L.append(nvt)
        elif mode == "npt":
            P = _num(pressure_bar)
            L.append(f"fix             thermostat all npt temp {T} {T} {_num(tdamp)} "
                     f"{BAROSTATS[barostat]} {P} {P} {_num(pdamp)}")
            if barostat == "iso" and (data.get("cell") or {}).get("triclinic"):
                warnings.append("iso barostat on a triclinic cell scales it uniformly and keeps "
                                "the angles; use barostat 'tri' to relax them")
        else:
            L.append("fix             integrate all nve")
            if not restart_from:
                warnings.append("nve from fresh velocities: the temperature drops to about "
                                "half the target as kinetic energy flows into the potential; "
                                "equilibrate with nvt first")
        L.append(f"thermo          {thermo_every or 100}")
        if dump_every:
            L += [f"dump            traj all xyz {dump_every} traj.xyz",
                  f"dump_modify     traj element {' '.join(elements)} sort id"]
            if mode == "npt":
                warnings.append("traj.xyz has no cell, and npt changes it: analyse an npt "
                                "trajectory only with the average cell from the log")
        if restart_every:
            L.append(f"restart         {restart_every} md.restart.a md.restart.b")
        if plumed_file:
            L.append(f"fix             plumed all plumed plumedfile {plumed_file} outfile plumed.out")
        L.append(f"run             {steps['run']}")
        if restart_every:
            L.append("write_restart   md.restart.final")
    total = sum(steps.values())
    if total * timestep_fs / 1000 > 100 and not restart_every and mode in ("nvt", "npt", "nve"):
        warnings.append(f"{total * timestep_fs / 1000:.0f} ps with no restart files: a walltime "
                        "stop loses the run; set restart_every")
    if "f64" in str(model) and mode in ("nvt", "npt", "nve"):
        warnings.append("a float64 model is ~1.7x slower than float32 (HOKUSAI benchmarks); "
                        "float32 is enough for production MD")
    files = [name] if not restart_from else [restart_from]
    files.append(model)
    if plumed_file:
        files.append(plumed_file)
    info = {"mode": mode, "steps": steps, "total_steps": total,
            "simulated_ps": round(total * timestep_fs / 1000, 6), "timestep_fs": timestep_fs,
            "temperature": temperature, "elements": elements, "files": files,
            "outputs": {"log": "log.lammps", "traj": "traj.xyz" if dump_every else None,
                        "restart": "md.restart.final" if restart_every else None,
                        "plumed": "plumed.out" if plumed_file else None},
            "warnings": warnings}
    return "\n".join(L) + "\n", info
