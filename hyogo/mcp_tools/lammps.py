"""hyogo MCP tools: lammps.

hyogo/server.py loads every module in this folder. A new lammps tool goes here;
a new group of tools is a new module beside this one.
"""
from pathlib import Path
from hyogo.lammps import input as lammps_input, log as lammps_log
from hyogo.mcp_tools import mcp


#: this module's rows of hyogo/INVENTORY.md (scripts/render_inventory.py renders the
#: table; a live row must be a tool served here). Plain literals only: the renderer
#: reads it without importing this module.
TOOL_META = {
    "lammps_make_input": {
        "method": "md",
        "stage": "input",
        "code": "lammps",
        "module": "`hyogo/lammps/input.py`",
        "status": "live",
        "source": "HOKUSAI `mlip/2026.10/bench/inputs/*.in`, `tests/smoke/in.melt_plumed`",
        "notes": "MLMD input with `pair_style metatomic` (MACE); modes nvt, npt, valid (NVT→NVE), time; dump/restart strides; optional `fix plumed` (written before the thermostat, as LAMMPS requires); optional D3 via `hybrid/overlay … dispersion/d3` (G41, reproduces the UiO-66 A2 inputs). Data file and element order come from yagura `structure_to_lammps_data`",
    },
    "lammps_parse_log": {
        "method": "md",
        "stage": "parse",
        "code": "lammps",
        "module": "`hyogo/lammps/log.py`",
        "status": "live",
        "source": "HOKUSAI `bench/parse_mlmd.py` (`parse_log`, `run_info`)",
        "notes": "finished/error, thermo blocks, Loop time → s/step and ns/day, NVE drift and fluctuation, T range, step-0 PE. Layout planning and records stay rookery's (G38)",
    },
}


@mcp.tool()
def lammps_make_input(data: dict, model: str, mode: str, steps: dict,
                      temperature: float = 300.0, timestep_fs: float = 0.5,
                      tdamp_fs: float | None = None, seed: int = 4928459, device: str = "cpu",
                      pressure_bar: float = 1.0, barostat: str = "iso",
                      pdamp_fs: float | None = None, thermo_every: int | None = None,
                      dump_every: int = 0, restart_every: int = 0,
                      restart_from: str | None = None, plumed_file: str | None = None,
                      data_name: str | None = None, dispersion: dict | None = None,
                      out_path: str | None = None, overwrite: bool = False) -> dict:
    """Write a LAMMPS input for MD with a machine-learned potential
    through pair_style metatomic (MACE and other metatomic exports), metal
    units, as HOKUSAI's MLMD benchmark ran it. Local only; fs_upload it,
    the .data file and (with plumed_file) the PLUMED input to one run
    folder. The model path is on the cluster.

    Modes: "valid" (NVT then NVE: is the model stable, and does it conserve
    energy?), "time" (untimed warm-up, then timed NVT), "nvt", "npt" and
    "nve" (production; may dump traj.xyz, write restart files, and couple
    PLUMED). Every fresh start prints the step-0 energy (`run 0`) before
    velocities are drawn, so it can be checked against an ASE reference.

    dispersion adds D3 to the MLIP (pair_style hybrid/overlay metatomic +
    dispersion/d3), for models trained without dispersion (MACE-MP-0,
    MACE-MATPES-*); use the model's own functional's D3 parameters. The
    default range, 10.5835 A (20 bohr), cost nothing measurable on UiO-66;
    longer ranges slow every step because the MLIP's neighbour list grows
    too. CP2K's D3 reaches 2 x R_CUTOFF = 40 A by default, so a LAMMPS D3
    energy sits a near-constant shift above CP2K's: compare forces and
    pressures, not absolute D3 energies.

    Before it: yagura structure_to_lammps_data (pass its whole result as
    data); for a bias, plumed_make_input. After the run: fs_download
    log.lammps and lammps_parse_log.

    Refuses: a timestep above 1 fs with hydrogen (0.5 fs is benchmarked),
    a model that isn't a .pt export, steps that don't match the mode,
    dumps/restarts/PLUMED in the benchmark modes; a dispersion with
    unknown keys, a damping other than zero/zerom/bj/bjm, a non-positive
    cutoff, or a CN cutoff beyond the cutoff.

    Returns {"out", "mode", "steps", "total_steps", "simulated_ps",
    "timestep_fs", "temperature", "elements", "dispersion" (as written,
    or None), "files" (what the run folder
    needs), "outputs" ({log, traj, restart, plumed}), "warnings"}; read the
    warnings to the user.

    Args:
        data: yagura structure_to_lammps_data's result (data, elements,
            atomic_numbers, cell).
        model: the metatomic model (.pt) on the cluster, absolute path
            (HOKUSAI: ~/software/mlip/2026.10/models/mace-mp-0-medium-f32.pt).
        mode: "valid", "time", "nvt", "npt" or "nve".
        steps: valid {"nvt": n, "nve": n}; time {"warm": n, "timed": n};
            otherwise {"run": n}.
        temperature: K, for the thermostat and the initial velocities.
        timestep_fs: fs; 0.5 is what the benchmark validated.
        tdamp_fs: thermostat damping; default 100 timesteps.
        seed: velocity seed (the benchmark's 4928459 by default).
        device: "cpu" or "cuda" (a GPU build of LAMMPS-metatomic).
        pressure_bar, barostat ("iso", "aniso", "tri", "z"), pdamp_fs
            (default 1000 timesteps): npt only.
        thermo_every: thermo stride; default 10 (valid) or 100.
        dump_every: write traj.xyz every n steps (0: none); xyz has no cell.
        restart_every: write md.restart.a/.b every n steps, and
            md.restart.final at the end.
        restart_from: continue from this restart file (no new velocities,
            no step-0 check).
        plumed_file: PLUMED input name in the run folder, coupled with fix plumed.
        data_name: the data file's name in the run folder; default its basename.
        dispersion: {functional, damping, cutoff_A, cn_cutoff_A}: D3 on
            top of the MLIP. functional is LAMMPS' D3 parameter name in
            lower case (run on HOKUSAI: pbe, hse06, r2scan); damping
            "bj" (default), "zero", "zerom" or "bjm"; cutoff_A default
            10.5835442; cn_cutoff_A default cutoff_A. Needs a LAMMPS build
            with EXTRA-PAIR (HOKUSAI: lammps-cpu-d3).
        out_path: default in.<mode>.lmp beside the data file.
        overwrite: replace out_path if it exists.
    """
    text, info = lammps_input.make_input(data, model, mode, steps, temperature, timestep_fs,
                                         tdamp_fs, seed, device, pressure_bar, barostat,
                                         pdamp_fs, thermo_every, dump_every, restart_every,
                                         restart_from, plumed_file, data_name, dispersion)
    dst = (Path(out_path).expanduser() if out_path
           else Path(data["data"]).expanduser().with_name(f"in.{mode}.lmp"))
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} exists; pass overwrite=true to replace it")
    dst.write_text(text)
    return {"out": str(dst), **info}


@mcp.tool()
def lammps_parse_log(log_path: str) -> dict:
    """Read a LAMMPS log (log.lammps) on this machine: did it finish, what
    ran, how fast, and is the dynamics sane. Writes nothing. Use it instead
    of grepping a log. Each `run` is labelled by the fixes LAMMPS echoed
    before it, so an NVT-then-NVE validation gives a thermostat tail
    temperature and an NVE energy drift.

    Before it: get_job_status, then fs_download log.lammps.

    Returns {"path", "finished", "error", "version", "ranks", "threads"
    (per rank), "natoms", "pe0" (first PotEng: the step-0 energy),
    "s_per_step", "ns_per_day" (of the last run with steps), "T_tail_mean"
    (mean of the second half of the last thermostatted run), "T_range"
    (over runs with steps), "nve": {nve_drift_meV_atom_ps,
    nve_fluct_meV_atom, nve_ps} (the last NVE run, metal units; per-atom
    energy x1000 otherwise), "runs": [{index, fixes, ensemble, steps, rows,
    first, last (thermo rows as dicts), s_per_step, ns_per_day, target_T,
    T_tail_mean, T_range, ...}], "warnings"}. Read the warnings to the
    user: an unfinished log, a LAMMPS error, non-finite thermo, dangerous
    neighbour builds, a temperature far above target. Judging the drift
    (e.g. against the 0.005 meV/atom/ps of MACE-MP-0 on water at 0.5 fs)
    is yours to explain.

    Args:
        log_path: the log on this machine; ~ is expanded.
    """
    src = Path(log_path).expanduser()
    if not src.is_file():
        raise ValueError(f"{src} is not a file on this machine. For a log on a cluster, "
                         "download it first (fs_download).")
    return {"path": str(src), **lammps_log.parse(src.read_text(errors="replace"))}
