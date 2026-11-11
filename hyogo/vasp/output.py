"""Read a VASP OUTCAR (and its OSZICAR, if given) into run-control facts,
printed results and cost facts (G32).

Bytes in, dict out: no file writes, no cluster. It reads only what VASP
prints; physics analysis of the numbers (where a moment sits, what the
water is doing) is yagura's.

Learned the hard way, and why each check is here:
  * a disk-full period leaves NUL holes in OUTCARs: they are stripped and
    counted, and every block is then checked for completeness;
  * VASP exits 0 when the SCF hits NELM. "Converged" here needs the "EDIFF
    is reached" line for every ionic step, a TOTEN, and a complete force
    block;
  * per-site moments exist only with LORBIT >= 10;
  * NBANDS follows the band-group count, which the layout lines show.
"""
from __future__ import annotations

import re

NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[Ee][-+]?\d+)?"   # OSZICAR writes "-.14549746E+04"
ITER = re.compile(r"^-+ Iteration\s+(\d+)\(\s*(\d+)\)\s+-+")
SCF_OK = "aborting loop because EDIFF is reached"
SCF_FAIL = "aborting loop EDIFF was not reached"
TOTEN = re.compile(rf"free\s+energy\s+TOTEN\s+=\s+({NUM})\s+eV")
SIGMA0 = re.compile(rf"energy\(sigma->0\)\s+=\s+({NUM})")
NOENT = re.compile(rf"energy\s+without entropy\s*=\s+({NUM})")
EFERMI = re.compile(rf"^\s*E-fermi\s*:\s+({NUM})")
MAGTOT = re.compile(rf"number of electron\s+({NUM})\s+magnetization\s+({NUM})")
STRESS = re.compile(rf"^\s+in kB((?:\s+{NUM}){{6}})\s*$")
PRESSURE = re.compile(rf"external pressure =\s+({NUM}) kB\s+Pullay stress =\s+({NUM}) kB")
LOOP = re.compile(rf"LOOP\+:\s+cpu time\s+({NUM}):\s+real time\s+({NUM})")
RANKS = re.compile(r"running\s+(\d+)\s+mpi-ranks,?\s+(?:with\s+(\d+)\s+threads/rank,?\s+)?on\s+(\d+)\s+nodes")
RANKS_OLD = re.compile(r"running on\s+(\d+) total cores")
DISTRK = re.compile(r"distrk:\s+each k-point on\s+(\d+) cores,\s+(\d+) groups")
DISTR = re.compile(r"distr:\s+one band on NCORE=\s*(\d+) cores,\s+(\d+) groups")
POTCAR = re.compile(r"^\s*POTCAR:\s+(\S+)\s+(\S+)")
OSZ_STEP = re.compile(r"^(DAV|RMM|CG|SDA):\s+\d+")
OSZ_F = re.compile(rf"^\s*(\d+)\s+F=\s*({NUM})\s+E0=\s*({NUM})(?:.*?mag=\s*({NUM}))?")
# parameters echoed in the OUTCAR header: key -> regex group
PARAMS = {
    "NKPTS": (rf"NKPTS\s*=\s*(\d+)", int), "NBANDS": (r"NBANDS\s*=\s*(\d+)", int),
    "NIONS": (r"NIONS\s*=\s*(\d+)", int), "ISPIN": (r"^\s*ISPIN\s*=\s*(\d+)", int),
    "NELM": (r"^\s*NELM\s*=\s*(\d+);", int), "EDIFF": (rf"^\s*EDIFF\s*=\s*({NUM})", float),
    "NELECT": (rf"^\s*NELECT\s*=\s*({NUM})", float),
    "NUPDOWN": (rf"^\s*NUPDOWN\s*=\s*({NUM})", float),
    "ENCUT": (rf"^\s*ENCUT\s*=\s*({NUM}) eV", float),
    "ISMEAR": (r"^\s*ISMEAR\s*=\s*(-?\d+);", int), "SIGMA": (rf"SIGMA\s*=\s*({NUM})\s+broad", float),
    "IBRION": (r"^\s*IBRION\s*=\s*(-?\d+)", int), "NSW": (r"^\s*NSW\s*=\s*(\d+)", int),
    "ISTART": (r"^\s*ISTART\s*=\s*(\d+)", int), "LORBIT": (r"^\s*LORBIT\s*=\s*(\d+)", int),
}
LARGEST = 5
OPPOSED = 0.5   # uB: a site counts as opposed to its element's net moment above this


def _f(s: str) -> float:
    return float(s)


def _box_kind(art: str) -> str:
    if "ADVICE" in art:
        return "advice"
    if "W    W" in art:
        return "warning"
    if "DDDDD" in art or "V     V" in art:
        return "advice"
    if "EEEEEEE" in art or "RRRRRR" in art:
        return "error"
    return "notice"


def parse_oszicar(text: str) -> dict:
    """Electronic steps per ionic step, and the last F= line's values."""
    steps, cur, last = [], 0, None
    for line in text.splitlines():
        if OSZ_STEP.match(line):
            cur += 1
        m = OSZ_F.match(line)
        if m:
            steps.append(cur)
            cur = 0
            last = m
    if cur:
        steps.append(cur)   # an ionic step that never reached its F= line
    return {"steps": steps,
            "F": _f(last.group(2)) if last else None,
            "E0": _f(last.group(3)) if last else None,
            "mag": _f(last.group(4)) if last and last.group(4) else None}


def parse(raw: bytes | str, oszicar: str | None = None, poscar: str | None = None,
          arrays: bool = False) -> dict:
    """Parse an OUTCAR. Raises ValueError if it isn't one.

    poscar (the run's POSCAR text) supplies species and counts when NUL
    holes ate the OUTCAR header lines that carry them."""
    if isinstance(raw, str):
        raw = raw.encode("latin-1", errors="replace")
    nul = raw.count(b"\x00")
    text = raw.replace(b"\x00", b"").decode("latin-1")
    lines = text.splitlines()
    head = "\n".join(lines[:40])
    m = re.search(r"^\s*vasp\.(\S+)\s.*?(\S+)\s*$", head, re.M)
    header_lost = False
    if not m:
        # a NUL hole can take the first lines; the body still says what it is
        if nul and any(k in text for k in ("TOTAL-FORCE", "POTCAR:", "General timing",
                                          "Iteration", "TOTEN")):
            header_lost = True
        else:
            raise ValueError("not a VASP OUTCAR: no 'vasp.<version>' line at the top")
    r = {"version": m.group(1) if m else None, "flavour": m.group(2) if m else None,
         "finished": False, "nul_bytes": nul}

    # header: layout and parameters
    layout = {"ranks": None, "nodes": None, "threads": None, "kpar": None, "ncore": None,
              "band_groups": None}
    m = RANKS.search(head)
    if m:
        layout.update(ranks=int(m.group(1)), threads=int(m.group(2)) if m.group(2) else 1,
                      nodes=int(m.group(3)))
    else:
        m = RANKS_OLD.search(head)
        if m:
            layout["ranks"] = int(m.group(1))
    m = DISTRK.search(head)
    if m:
        layout["kpar"] = int(m.group(2))
    m = DISTR.search(head)
    if m:
        layout["ncore"], layout["band_groups"] = int(m.group(1)), int(m.group(2))

    params = {}
    for key, (pat, conv) in PARAMS.items():
        m = re.search(pat, text, re.M)
        params[key] = conv(m.group(1)) if m else None
    m = re.search(r"ions per type =\s+([\d\s]+)$", text, re.M)
    counts = [int(x) for x in m.group(1).split()] if m else []
    natoms = params["NIONS"] or (sum(counts) if counts else None)
    pots = []
    for line in lines[:4000]:
        mm = POTCAR.match(line)
        if mm:
            pots.append(f"{mm.group(1)} {mm.group(2)}")
            if len(pots) == len(counts):
                break
    species = [p.split()[1].split("_")[0] for p in pots] if len(pots) == len(counts) else []
    from_poscar = False
    if (not species or natoms is None) and poscar:
        pl = poscar.splitlines()
        try:
            names, nums = pl[5].split(), [int(x) for x in pl[6].split()]
            if len(names) == len(nums) and names[0][0].isalpha():
                species = [re.match(r"[A-Z][a-z]?", x).group(0) for x in names]
                counts, natoms, from_poscar = nums, sum(nums), True
                header_lost = True
        except (IndexError, ValueError, AttributeError):
            pass
    elements = [el for el, n in zip(species, counts) for _ in range(n)]

    # INCAR as given (the echo near the top), for what the user set
    incar = {}
    try:
        i = next(k for k, l in enumerate(lines[:400]) if l.strip() == "INCAR:")
        for l in lines[i + 1:i + 300]:
            if not l.strip():
                break
            if "=" in l:
                k, v = l.split("=", 1)
                incar[k.strip()] = v.strip()
    except StopIteration:
        pass

    # body
    ionic, cur_ion, cur_max, cur_ok = [], 0, 0, None
    toten = sigma0 = noent = efermi = None
    mag_total = None
    mom_rows, cur_mom, in_mom = None, [], False
    forces, cur_f, in_f = None, [], False
    drift = None
    stress = pressure = None
    loops = []
    elapsed = memory = None
    boxes, box, art = {}, None, ""

    for line in lines:
        mi = ITER.match(line)
        if mi:
            n = int(mi.group(1))
            if n != cur_ion:                     # a new ionic step began
                if cur_max:
                    ionic.append({"steps": cur_max, "converged": cur_ok})
                cur_ion, cur_max, cur_ok = n, 0, None
            cur_max = max(cur_max, int(mi.group(2)))
            continue
        if SCF_OK in line:
            cur_ok = True
        elif SCF_FAIL in line:
            cur_ok = False
        m = TOTEN.search(line)
        if m:
            toten = _f(m.group(1))
        m = SIGMA0.search(line)
        if m:
            sigma0 = _f(m.group(1))
            mm = NOENT.search(line)
            if mm:
                noent = _f(mm.group(1))
        m = EFERMI.match(line)
        if m:
            efermi = _f(m.group(1))
        m = MAGTOT.search(line)
        if m:
            mag_total = _f(m.group(2))

        # site moments: the last "magnetization (x)" block, column "tot"
        if line.strip() == "magnetization (x)":
            in_mom, cur_mom = True, []
            continue
        if in_mom:
            p = line.split()
            if p and p[0].isdigit() and len(p) >= 3:
                try:
                    cur_mom.append(_f(p[-1]))
                except ValueError:
                    in_mom = False
                continue
            # header, blank and dashed lines pass; "tot" (or anything else
            # after the rows, e.g. a NUL hole) ends the block
            if cur_mom and not (p and set(p[0]) == {"-"}):
                mom_rows, in_mom = list(cur_mom), False
            continue

        # forces: the last TOTAL-FORCE block
        if "TOTAL-FORCE (eV/Angst)" in line:
            in_f, cur_f = True, []
            continue
        if in_f:
            s = line.strip()
            if s.startswith("---"):
                if cur_f:
                    forces, in_f = list(cur_f), False
                continue
            p = s.split()
            try:
                if len(p) != 6:
                    raise ValueError
                cur_f.append([_f(x) for x in p])
            except ValueError:
                forces, in_f = list(cur_f), False   # cut block (NUL hole, killed run)
            continue
        if "total drift:" in line:
            drift = [_f(x) for x in line.split()[-3:]]

        m = STRESS.match(line)
        if m:
            stress = [_f(x) for x in m.group(1).split()]
        m = PRESSURE.search(line)
        if m:
            pressure = _f(m.group(1))
        m = LOOP.search(line)
        if m:
            loops.append(_f(m.group(2)))
        if "General timing and accounting" in line:
            r["finished"] = True
        if "Elapsed time (sec):" in line:
            elapsed = _f(line.split()[-1])
        if "Maximum memory used (kb):" in line:
            memory = _f(line.split()[-1].rstrip("."))

        # VASP's message boxes: "|" lines between dashed borders; the big
        # letters (no lower case) say which kind, the rest is the message
        if line.startswith("|"):
            body = line.strip().strip("|").strip()
            if box is None:
                box, art = [], ""
            if body and not re.search(r"[a-z]", body):
                art += body
            elif "ADVICE to this user" in body:
                art += " ADVICE"
            elif body:
                box.append(body)
        elif box is not None:
            if box:
                key = (_box_kind(art), " ".join(box))
                boxes[key] = boxes.get(key, 0) + 1
            box = None
    if cur_max:
        ionic.append({"steps": cur_max, "converged": cur_ok})
    if in_f and cur_f:
        forces = list(cur_f)
    if in_mom and cur_mom and mom_rows is None:
        mom_rows = list(cur_mom)

    nelm = params["NELM"]
    if nelm is None and incar and re.match(r"\d+", incar.get("NELM", "")):
        nelm = int(re.match(r"\d+", incar["NELM"]).group(0))   # header lost to a NUL hole
    for st in ionic:
        if st["converged"] is None and nelm:   # VASP versions without the abort line
            st["converged"] = st["steps"] < nelm
    r.update(natoms=natoms, species=[[el, n] for el, n in zip(species, counts)] or None,
             potcars=pots or None, params=params, incar=incar or None, layout=layout)

    forces_ok = forces is not None and natoms is not None and len(forces) == natoms
    scf = {"ionic_steps": len(ionic), "steps": [s["steps"] for s in ionic],
           "converged": [s["converged"] for s in ionic],
           "nelm": nelm, "nelm_hit": sum(1 for s in ionic if s["converged"] is False)}
    r["scf"] = scf
    # not r["finished"]: a run cut after its last force block (disk full) is
    # still a complete result; the warning says it was cut
    r["converged"] = bool(ionic) and all(s["converged"] for s in ionic) and toten is not None \
        and forces_ok
    r["energy"] = {"toten": toten, "sigma0": sigma0, "without_entropy": noent,
                   "per_atom_toten": toten / natoms if toten is not None and natoms else None}
    r["e_fermi"] = efermi

    osz = parse_oszicar(oszicar) if oszicar is not None else None
    mags = None
    spin = params["ISPIN"] == 2 or (params["ISPIN"] is None and (
        mag_total is not None or (osz and osz["mag"] is not None)))   # header lost
    if spin:
        # OSZICAR's mag= is the final density's; the OUTCAR's last "number of
        # electron ... magnetization" line is one mixing step earlier (~0.01 uB off)
        final = osz["mag"] if osz and osz["mag"] is not None else None
        mags = {"total": final if final is not None else mag_total,
                "source": "OSZICAR" if final is not None else "OUTCAR (last SCF step)",
                "outcar_last_step": mag_total, "sites": None}
        if mom_rows is not None and natoms and len(mom_rows) == natoms:
            by = {}
            for i, (el, m) in enumerate(zip(elements or ["?"] * natoms, mom_rows)):
                by.setdefault(el, []).append((i + 1, m))
            summary = {}
            for el, rows in by.items():
                ms = [m for _, m in rows]
                net = sum(ms)
                summary[el] = {"n": len(ms), "min": min(ms), "max": max(ms),
                               "sum": round(net, 4),
                               "opposed": sum(1 for m in ms if abs(m) >= OPPOSED
                                              and (m > 0) != (net > 0))}
            top = sorted(range(natoms), key=lambda i: -abs(mom_rows[i]))[:LARGEST]
            mags["sites"] = {"by_element": summary,
                             "largest": [[i + 1, elements[i] if elements else None, mom_rows[i]]
                                         for i in top]}
            if arrays:
                mags["site_moments"] = mom_rows
    r["magnetization"] = mags

    if forces:
        fv = [row[3:] for row in forces]
        mag_f = [(x * x + y * y + z * z) ** 0.5 for x, y, z in fv]
        imax = max(range(len(mag_f)), key=mag_f.__getitem__)
        r["forces"] = {"complete": forces_ok, "rows": len(forces), "max": mag_f[imax],
                       "max_atom": imax + 1,
                       "rms": (sum(v * v for v in mag_f) / len(mag_f)) ** 0.5, "drift": drift}
        if arrays:
            r["positions"] = [row[:3] for row in forces]
            r["forces"]["vectors"] = fv
    else:
        r["forces"] = None
    r["stress_kb"] = ({"xx_yy_zz_xy_yz_zx": stress, "pressure": pressure}
                      if stress is not None else None)
    r["cost"] = {"elapsed_s": elapsed, "loop_s": loops or None,
                 "max_memory_mb": memory / 1024 if memory else None}

    if osz is not None:
        r["oszicar"] = osz
    w = []
    if nul:
        w.append(f"{nul} NUL bytes stripped (holes from a full disk or a killed write): "
                 "values were read around them; check `converged` and `forces.complete`")
    if natoms is None and (toten is not None or ionic):
        w.append("the atom count and species were lost to NUL holes in the header: pass the "
                 "run's POSCAR to read the moments and the force block")
    elif natoms is None:
        w.append(f"the OUTCAR stops in its header ({len(lines)} lines): VASP died while "
                 "starting (read the job's stdout and stderr: MPI, POTCAR, memory)")
    elif header_lost:
        w.append("header lines were lost to NUL holes" + (
            "; species and atom count taken from the POSCAR" if from_poscar else ""))
    if not r["finished"] and natoms is not None:
        w.append("no 'General timing' block: the run is still going, was killed, or its "
                 "OUTCAR was cut (disk full). " + ("The last ionic step's result is complete."
                 if r["converged"] else "Values are from the last complete step"))
    if scf["nelm_hit"]:
        w.append(f"SCF hit NELM ({nelm or 'unknown'}) in {scf['nelm_hit']} ionic step(s): VASP still exits 0 "
                 "and prints energies and forces, but they are not converged. Don't use them as "
                 "a result")
    if forces is not None and not forces_ok:
        w.append(f"the last TOTAL-FORCE block has {len(forces)} rows for {natoms} atoms: cut off")
    if forces is None and r["finished"]:
        w.append("no TOTAL-FORCE block")
    if toten is None:
        w.append("no TOTEN: no ionic step finished")
    if spin and mags["sites"] is None:
        w.append("no per-site moments: they need LORBIT >= 10" if (params["LORBIT"] or 0) < 10
                 else "the magnetization (x) block is missing or incomplete")
    if osz is not None:
        if osz["F"] is not None and toten is not None and abs(osz["F"] - toten) > 1e-3:
            w.append(f"OSZICAR's F {osz['F']} differs from the OUTCAR's TOTEN {toten}: "
                     "the two files come from different runs (a restart overwrote one?)")
        if osz["steps"] and scf["steps"] and osz["steps"] != scf["steps"]:
            w.append(f"OSZICAR has {osz['steps']} electronic steps per ionic step, the OUTCAR "
                     f"{scf['steps']}")
    for (kind, msg), n in boxes.items():
        if kind in ("warning", "error") and not (scf["nelm_hit"] and "(NELM)" in msg):
            w.append(f"VASP {kind} ({n}x): {msg}")
    advice = sum(n for (kind, _), n in boxes.items() if kind not in ("warning", "error"))
    if advice:
        w.append(f"VASP printed {advice} advice box(es): see messages")
    r["messages"] = [{"kind": k, "text": t, "count": n} for (k, t), n in boxes.items()]
    r["warnings"] = w
    return r


# ---------------------------------------------------------------------------
# Per-site blocks (G37): moments and charges per atom and orbital channel,
# written as a one-frame extxyz for yagura's sites_by_environment.
# ---------------------------------------------------------------------------

SITE_BLOCKS = (("magnetization (x)", "REF_magmoms", "magmoms"), ("total charge", "charges", "charges"))


def site_blocks(raw: bytes | str) -> dict:
    """The last "magnetization (x)" and "total charge" blocks (LORBIT >= 10)
    and the last direct lattice vectors: {"magmoms" / "charges": {column:
    [one value per atom]} with VASP's columns (s, p, d, [f,] tot), absent
    when the OUTCAR has no such block; "cell": 3 rows in A, or None}."""
    if isinstance(raw, bytes):
        raw = raw.replace(b"\x00", b"").decode("latin-1")
    lines = raw.splitlines()
    out = {}
    for title, _, name in SITE_BLOCKS:
        starts = [i for i, l in enumerate(lines) if l.strip() == title]
        if not starts:
            continue
        i = starts[-1] + 1
        while i < len(lines) and not lines[i].lstrip().startswith("# of ion"):
            i += 1
        if i >= len(lines):
            continue
        cols = lines[i].split()[3:]
        rows = []
        for l in lines[i + 1:]:
            p = l.split()
            if p and p[0].isdigit() and len(p) == 1 + len(cols):
                rows.append([_f(x) for x in p[1:]])
            elif rows or (p and not set(p[0]) <= {"-"}):
                break
        if rows:
            out[name] = {c: [r[k] for r in rows] for k, c in enumerate(cols)}
    cell = None
    for i, l in enumerate(lines):
        if "direct lattice vectors" in l and i + 3 < len(lines):
            try:
                cell = [[_f(x) for x in lines[i + k].split()[:3]] for k in (1, 2, 3)]
            except (ValueError, IndexError):
                pass
    out["cell"] = cell
    return out


def sites_extxyz(elements: list[str], positions: list, cell: list, forces: list | None,
                 blocks: dict, info: dict) -> str:
    """One extxyz frame: species, pos, REF_forces, REF_magmoms (tot) and
    magmoms_<channel>, charges (tot) and charges_<channel>; info as given."""
    n = len(elements)
    cols, data = [], []
    if forces is not None:
        cols.append("REF_forces:R:3")
        data.append([f"{v[0]:.8f} {v[1]:.8f} {v[2]:.8f}" for v in forces])
    for _, tot_name, name in SITE_BLOCKS:
        b = blocks.get(name)
        if not b:
            continue
        for c, values in b.items():
            if len(values) != n:
                raise ValueError(f"the OUTCAR's {name} block has {len(values)} rows for {n} atoms")
            key = tot_name if c == "tot" else f"{name}_{c}"
            cols.append(f"{key}:R:1")
            data.append([f"{v:.4f}" for v in values])
    head = [f'Lattice="{" ".join(f"{x:.9f}" for row in cell for x in row)}"',
            "Properties=" + ":".join(["species:S:1", "pos:R:3", *cols])]
    head += [f"{k}={v}" for k, v in info.items() if v is not None]
    head.append('pbc="T T T"')
    body = [" ".join([f"{el:<2}", *(f"{x:.8f}" for x in p), *(d[i] for d in data)])
            for i, (el, p) in enumerate(zip(elements, positions))]
    return f"{n}\n{' '.join(head)}\n" + "\n".join(body) + "\n"
