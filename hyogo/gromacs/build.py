"""Frame (extxyz) -> GAFF/AM1-BCC topology for GROMACS, charge-scaled and replicated."""
import json
import re
import shutil
import subprocess
from pathlib import Path

from . import env

def _run(cmd, cwd, e=None, log=None):
    r = subprocess.run(cmd, cwd=cwd, env=e or None, capture_output=True, text=True)
    if log:
        (Path(cwd) / log).write_text(r.stdout + r.stderr)
    if r.returncode:
        raise RuntimeError(f"{' '.join(map(str, cmd))} failed ({r.returncode}):\n{(r.stdout + r.stderr)[-1500:]}")
    return r.stdout

def tleap_pdb(src: Path, dst: Path):
    """Li is renamed atom/residue 'LI' (unit from atomic_ions.lib); TER after every residue."""
    out = []
    for l in src.read_text().splitlines():
        if l.startswith("ATOM") and l[12:16].strip().startswith("Li"):
            l = l[:12] + " LI  " + l[17:17] + " LI" + l[20:]
        out.append(l)
    dst.write_text("\n".join(out) + "\n")

TLEAP = """source leaprc.gaff
loadOff atomic_ions.lib
loadamberparams frcmod.ionsjc_tip3p
loadamberparams tfsi.frcmod
loadamberparams meoh.frcmod
TFS = loadmol2 tfsi.mol2
MOH = loadmol2 meoh.mol2
sys = loadpdb system_tleap.pdb
set sys box {{{b} {b} {b}}}
check sys
saveamberparm sys system.prmtop system.inpcrd
quit
"""

def build_system(workdir, labels, charge_scale=0.8, replicate=2, frame="median", energy_key="REF_energy"):
    wd = Path(workdir).expanduser().resolve()
    b = wd / "build"
    b.mkdir(parents=True, exist_ok=True)
    aenv = env.amber_env()
    log = []
    out = _run([env.PY, env.SCRIPTS / "make_molecules.py", Path(labels).expanduser().resolve(),
                "--frame", str(frame), "--replicate", str(replicate), "--energy-key", energy_key], b, log="01_make_molecules.log")
    log.append(out.strip().splitlines()[0])
    box_A = float(re.search(r"box ([\d.]+)", out).group(1))
    _run(["antechamber", "-i", "meoh_template.pdb", "-fi", "pdb", "-o", "meoh_raw.mol2", "-fo", "mol2", "-c", "bcc",
          "-nc", "0", "-at", "gaff", "-rn", "MOH", "-pf", "y", "-s", "0"], b, aenv, "ac_meoh.log")
    _run(["antechamber", "-i", "tfsi_template.pdb", "-fi", "pdb", "-o", "tfsi_raw.mol2", "-fo", "mol2", "-c", "bcc",
          "-nc", "-1", "-at", "gaff", "-rn", "TFS", "-pf", "y", "-s", "0"], b, aenv, "ac_tfsi.log")
    sym = _run([env.PY, env.SCRIPTS / "symmetrize_charges.py", "--scale", str(charge_scale)], b, log="02_symmetrize.log")
    for m in ("meoh", "tfsi"):
        _run(["parmchk2", "-i", f"{m}.mol2", "-f", "mol2", "-o", f"{m}.frcmod", "-s", "gaff"], b, aenv)
    tleap_pdb(b / "system_replicated.pdb", b / "system_tleap.pdb")
    (b / "tleap.in").write_text(TLEAP.format(b=f"{box_A:.2f}"))
    _run(["tleap", "-f", "tleap.in"], b, aenv, "tleap.log")
    tl = (b / "tleap.log").read_text()
    if "Errors = 0" not in tl:
        raise RuntimeError("tleap reported errors:\n" + tl[-1500:])
    conv = _run([env.AMBER_PY, env.SCRIPTS / "to_gromacs.py", "--scale", str(charge_scale), "--box-nm-a", f"{box_A:.2f}"],
                b, aenv, "03_to_gromacs.log")
    mols = dict(re.findall(r"^(TFS|MOH|LI)\s+(\d+)", (b / "system.top").read_text(), re.M))
    info = {"workdir": str(wd), "charge_scale": charge_scale, "replicate": replicate, "box_nm": box_A / 10,
            "molecules": {k: int(v) for k, v in mols.items()}, "tfsi_charges": sym.strip().splitlines()[0],
            "conversion": conv.strip().splitlines(), "top": str(b / "system.top"), "gro": str(b / "system.gro")}
    n_s, n_m = int(mols["TFS"]), int(mols["MOH"])
    info["molality_mol_per_kg"] = round(n_s / (n_m * 32.042e-3), 2)
    info["tleap_warnings"] = re.search(r"Warnings = (\d+)", tl).group(1)
    (wd / "build_info.json").write_text(json.dumps(info, indent=1))
    return info
