import json
import pathlib
import subprocess
import sys
import tempfile
from checks._common import ROOT

LABEL = 'yagura trajectory analysis matches analytic cases'


def yagura_trajectory_analysis_matches_analytic_cases():
    """trajectory_rdf, _msd and _vdos on generated trajectories with known
    answers: uniformly random points (g = 1), a wrapped random walk (known
    D) and a ballistic run (log-log slope 2) read from a cell-less
    CP2K-style -pos-1.xyz, and one harmonic oscillator (its wavenumber);
    the cell-less file is refused without a cell; and the script copy in
    the structure-making skill is the module."""
    import numpy as np
    from yagura import server as ysrv
    from yagura.analysis import trajectory as tr
    root = ROOT
    assert (root / "codes/common/structure-making/scripts/trajectory.py").read_text() == \
        (root / "yagura/analysis/trajectory.py").read_text(), "scripts/trajectory.py is a copy of yagura's"
    rng = np.random.default_rng(0)
    L = 10.0

    def write(path, frames, el, cp2k_dt=None):
        out = []
        for k, X in enumerate(frames):
            out.append(f"{len(el)}\n")
            out.append(f" i = {k}, time = {k * cp2k_dt:.3f}, E = -1.0\n" if cp2k_dt else
                       f'Lattice="{L} 0 0 0 {L} 0 0 0 {L}" Properties=species:S:1:pos:R:3\n')
            out += [f"{e} {x:.6f} {y:.6f} {z:.6f}\n" for e, (x, y, z) in zip(el, X)]
        path.write_text("".join(out))

    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        write(d / "gas.xyz", [rng.uniform(0, L, (200, 3)) for _ in range(10)], ["Ar"] * 150 + ["Ne"] * 50)
        r = ysrv.trajectory_rdf(str(d / "gas.xyz"), ["Ar-Ar", "Ar-Ne"], 5.0, 50)
        assert len(r["r"]) == 50 and r["r"][-1] == 4.95 and r["cell_source"] == "Lattice= lines", r["r"]
        for p in r["pairs"]:
            g = np.array(p["g"][25:])   # r = 2.5..5 A: plenty of pairs per bin
            assert abs(g.mean() - 1) < 0.02 and g.std() < 0.05, (p["pair"], g)
        assert ysrv.trajectory_rdf(str(d / "gas.xyz"), ["Ar-Ar"], frames="::5")["frames_used"] == 2 and \
            ysrv.trajectory_rdf(str(d / "gas.xyz"), ["Ar-Ar"])["r_max"] == 5.0

        # random walk, sigma per coordinate per 2 fs frame: D = sigma^2 / (2 dt)
        sig, dt = 0.1, 2.0
        walk = np.cumsum(rng.normal(0, sig, (2000, 64, 3)), axis=0) % L
        write(d / "walk-pos-1.xyz", walk, ["O"] * 64, cp2k_dt=dt)
        m = ysrv.trajectory_msd(str(d / "walk-pos-1.xyz"), ["O"], dt, cell=[L, L, L])
        e = m["elements"][0]
        assert abs(e["D_A2_per_ps"] / (sig ** 2 / (2 * dt / 1000)) - 1) < 0.1, e
        assert abs(e["loglog_slope"] - 1) < 0.05 and e["fit"]["r2"] > 0.99, e
        assert len(m["t_ps"]) == 100 and m["t_ps"][-1] == 2.0 and m["warnings"] == [], m["t_ps"][-1]
        assert any("2 fs apart" in w for w in
                   ysrv.trajectory_msd(str(d / "walk-pos-1.xyz"), ["O"], 0.5, cell=[L] * 3)["warnings"])
        ball = np.arange(200)[:, None, None] * rng.normal(0, 0.01, (64, 3))[None]
        write(d / "ball-pos-1.xyz", ball % L, ["H"] * 64, cp2k_dt=1.0)
        b = ysrv.trajectory_msd(str(d / "ball-pos-1.xyz"), ["H"], 1.0, cell=np.diag([L] * 3).tolist(),
                                remove_drift=False)
        assert abs(b["elements"][0]["loglog_slope"] - 2) < 0.01, b["elements"][0]

        # one harmonic oscillator at 1650 cm^-1, 0.5 fs frames
        t = np.arange(4000) * 0.5
        osc = np.full((4000, 2, 3), 5.0)
        osc[:, 0, 0] += 0.05 * np.cos(2 * np.pi * 1650 * tr.C_CM_PER_FS * t)
        write(d / "osc.xyz", osc, ["H", "O"])
        v = ysrv.trajectory_vdos(str(d / "osc.xyz"), 0.5, ["H"])
        assert len(v["peaks"]) == 1 and abs(v["peaks"][0]["wavenumber"] - 1650) < 5, v["peaks"]
        assert len(v["wavenumber"]) == 400 and "central differences" in v["velocities"], v
        assert abs(v["wavenumber"][int(np.argmax(v["vdos"]))] - 1650) <= 10, v["vdos"]
        assert "Nyquist" in ysrv.trajectory_vdos(str(d / "osc.xyz"), 5.0)["warnings"][0]

        # the script runs on its own, with -I, as in a cluster job
        out = subprocess.run([sys.executable, "-I", str(root / "codes/common/structure-making/scripts/trajectory.py"),
                              "msd", str(d / "walk-pos-1.xyz"), "--elements", "O", "--dt-fs", "2",
                              "--cell", "10", "10", "10"], capture_output=True, text=True, timeout=120)
        assert out.returncode == 0 and json.loads(out.stdout)["elements"][0]["D_A2_per_ps"] == \
            e["D_A2_per_ps"], out.stderr[-2000:]

        for call, text in ((lambda: ysrv.trajectory_msd(str(d / "walk-pos-1.xyz"), ["O"], dt), "pass cell"),
                           (lambda: ysrv.trajectory_rdf(str(d / "gas.xyz"), ["Ar-Ar"], 5.5), "half the smallest"),
                           (lambda: ysrv.trajectory_rdf(str(d / "gas.xyz"), ["Ar-Kr"]), "no ['Kr'] atoms"),
                           (lambda: ysrv.trajectory_rdf(str(d / "gas.xyz"), ["Ar-Ar"], frames="3"), "START:STOP:STEP"),
                           (lambda: ysrv.trajectory_msd(str(d / "gas.xyz"), ["Ar"], 1.0), "too few"),
                           (lambda: ysrv.trajectory_msd(str(d / "walk-pos-1.xyz"), ["O"], dt, cell=[L] * 3,
                                                        fit_ps=[1, 5]), "fit_ps"),
                           (lambda: ysrv.trajectory_vdos(str(d / "missing.xyz"), 1.0), "not a file")):
            try:
                call()
            except ValueError as e:
                assert text in str(e), e
            else:
                raise AssertionError(f"trajectory tools did not refuse ({text})")
