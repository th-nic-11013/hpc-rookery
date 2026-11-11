"""mdp files for the equilibration stages (settings validated on the 20 m LiTFSI/MeOH box)."""

COMMON = """; common settings
integrator          = md
constraints         = h-bonds
constraint-algorithm= lincs
lincs-order         = 4
lincs-iter          = 1
cutoff-scheme       = Verlet
nstlist             = 20
verlet-buffer-tolerance = 0.005
coulombtype         = PME
rcoulomb            = 1.0
fourierspacing      = 0.12
pme-order           = 4
vdwtype             = cutoff
vdw-modifier        = potential-shift
rvdw                = 1.0
DispCorr            = EnerPres
pbc                 = xyz
tcoupl              = v-rescale
tc-grps             = System
nstcomm             = 100
comm-mode           = Linear
nstenergy           = 500
nstlog              = 5000
nstcalcenergy       = 100
compressed-x-grps   = System
"""

EM = """integrator = steep
emtol      = 100.0
emstep     = 0.001
nsteps     = 5000
cutoff-scheme = Verlet
coulombtype = PME
rcoulomb   = 1.0
rvdw       = 1.0
vdw-modifier = potential-shift
DispCorr   = EnerPres
constraints = none
pbc        = xyz
"""

def render(st: dict) -> str:
    """st: kind em|nvt|npt, time_ps, dt_fs, gen_vel, continuation, tau_t, ref_t, ref_p, tau_p, seed, xtc_ps"""
    if st["kind"] == "em":
        return EM
    dt = st.get("dt_fs", 2.0) / 1000.0
    nsteps = int(round(st["time_ps"] / dt))
    xtc = max(1, int(round(st.get("xtc_ps", 10.0) / dt)))
    s = f"title = {st['name']}: {st['time_ps']} ps {st['kind'].upper()}, {st.get('dt_fs', 2.0)} fs\n"
    s += f"dt        = {dt}\nnsteps    = {nsteps}\n"
    gen = st.get("gen_vel", False)
    s += f"gen_vel   = {'yes' if gen else 'no'}\n"
    if gen:
        s += f"gen_temp  = {st.get('ref_t', 300)}\ngen_seed  = {st.get('seed', 20260102)}\n"
    s += f"continuation = {'yes' if st.get('continuation', True) else 'no'}\n"
    if st["kind"] == "npt":
        s += (f"pcoupl    = C-rescale\npcoupltype= isotropic\ntau-p     = {st.get('tau_p', 5.0)}\n"
              f"ref-p     = {st.get('ref_p', 1.0)}\ncompressibility = 4.5e-5\nrefcoord-scaling = com\n")
    else:
        s += "pcoupl    = no\n"
    s += f"tau-t     = {st.get('tau_t', 0.1 if gen else 0.5)}\nref-t     = {st.get('ref_t', 300)}\n"
    s += COMMON + f"nstxout-compressed  = {xtc}\n"
    return s

def default_protocol(nvt1_ps=1000, npt_ps=1000, nvt2_ps=10000, ref_t=300, ref_p=1.0, avg_last_ps=500):
    p = [
        {"name": "em", "kind": "em"},
        {"name": "nvt0", "kind": "nvt", "time_ps": 100, "dt_fs": 1.0, "gen_vel": True, "continuation": False, "ref_t": ref_t},
        {"name": "nvt1", "kind": "nvt", "time_ps": nvt1_ps, "ref_t": ref_t},
        {"name": "npt", "kind": "npt", "time_ps": npt_ps, "ref_t": ref_t, "ref_p": ref_p},
    ]
    if nvt2_ps:
        p.append({"name": "nvt2", "kind": "nvt", "time_ps": nvt2_ps, "ref_t": ref_t, "continuation": False,
                  "box_from": {"stage": "npt", "last_ps": avg_last_ps}})
    return p
