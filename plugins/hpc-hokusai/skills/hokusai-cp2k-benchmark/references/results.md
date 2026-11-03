# Measured CP2K benchmark results on HOKUSAI

> From the rccs-agentic-ai `hokusai-hpc-cp2k-benchmark` skill, where these
> benchmarks were run. Paths such as `~/software/cp2k/bench/...` are on HOKUSAI;
> the scripts it names are in `../scripts/` here, except `geomcheck.py`
> (`hokusai-structure-making`). Other rccs-agentic-ai skills it names map to
> `hokusai-cp2k-build`, `hokusai-submitting-jobs` and `hokusai-reference`.

One section per finished benchmark: date, build, input, the key rows, the
recommended layout, anything odd. Keep superseded results, marked as such. Full
tables live in the benchmark folder's `results.md` / `results.csv`.

## 2026-10-17: single-node layout, CP2K 2026.2 (`~/software/cp2k/bench`)

Build `~/software/cp2k/cp2k-2026.2` (GCC 13.2, Open MPI 4.1.6, MKL); one exclusive
MPC node; mean MD step time over steps >= 3.

| System | Fastest | then | 112 x 1 | slowest | spread |
|---|---|---|---|---|---|
| H2O-64, 192 atoms, TZV2P 280 Ry | 16x7 0.94 s | 8x14 1.11 | 1.23 | 4x28 1.87 | 2.0x |
| H2O-128, 384 atoms (stage 3) | 16x7 2.14 s (repeat 2.16) | 8x14 2.85 | 2.87 | n/a | |
| UiO-66 primitive, 114 atoms, revPBE-D3 DZVP-MOLOPT-SR 450 Ry | 16x7 5.14 s | 8x14 5.22, 28x4 5.31 | 5.52 | 56x2 6.11 | 1.19x |

- **Default for CP2K GGA AIMD on MPC: 16 ranks x 7 threads per node** (two ranks
  per NUMA domain). 8 x 14 is a fair second; 4 x 28 and 56 x 2 are poor.
- Water is DBCSR-bound: pure MPI spends about twice as long in
  `dbcsr_multiply_generic`, which is where threads help. UiO-66 at 450 Ry is
  grid-bound (collocate/integrate about 2/3 of the time), so the layout matters
  less.
- Noise: two identical UiO-66 jobs agree to < 1 %; the H2O-128 repeat to 1 %.
- First MD step: 7x (UiO-66) to 10x (H2O-32) a steady step (initial SCF guess).
- Run time per scan run: H2O-64 17-32 s, UiO-66 86-103 s; whole jobs 5.4 min
  (water, 3 stages) and 11.7 min (UiO-66, 2 stages).
- Memory per rank stayed below 3.1 GB (4 x 28, UiO-66), far below the ~16 GB per
  NUMA domain.

## 2026-10-17: 4 nodes, UiO-66 conventional cell: invalid

456 atoms, 64 x 7 (16 x 7 per node), 20.6 min, 153.6 core-h. Every SCF stopped
at 330 iterations without converging (OT DIIS stagnates at gradient 1-3e-5,
target 1e-6), so 106 s/step is not a timing. The cell is exactly the primitive
cell tiled four times; energy per Zr6 unit ended 0.07 Ha above the primitive
cell's.

Resolved by single-point SCF tests (2026-10-18, one node, 16 x 7,
12.7 core-h; `~/software/cp2k/bench/make_scf_tests.py`, `scf_summary.py`): every
changed setting converges the 456-atom cell from the atomic guess. OT DIIS +
FULL_ALL (ENERGY_GAP 0.05): 55 steps, 66 s, the best OT choice. OT CG + 2PNT
with FULL_SINGLE_INVERSE: 126 steps, 91 s. CG + FULL_ALL: 106 steps, 89 s.
Diagonalisation + Broyden: 23 steps, 53 s (about 2.3 s per step). Gap 3.16 eV,
the same as the 114-atom cell (3.163 eV); energy per Zr6 unit within 0.5 mHa of
it, so the 114-atom cell's Gamma point is adequate. The stalled MD run's first
step was 279 mHa above the converged energy: the failure was the combination OT
DIIS + FULL_SINGLE_INVERSE (MAX_SCF 30) on the big cell, not the structure or
the physics.

With FULL_ALL the 456-atom MD is clean on one node (2026-10-19, 16 x 7, 6.0 min, 11.1 core-h): 9.34 s per steady MD step, first step 120 s, 5 SCF steps per MD step after the first, conserved quantity stable; FULL_ALL preconditioner builds take 16 % of the time. 4x the atoms of the 114-atom cell cost only 1.8x per step. 4 nodes, 64 x 7 (2026-10-19, 2.6 min, 19.5 core-h): 4.11 s per step, 2.27x the 1-node speed, 57 % parallel efficiency (first step 2.11x); energies match to 8e-11 Ha, all SCF converged; grid and FFT scale well, DBCSR only 2x. Run 456-atom cells on 1 node for throughput, 4 only when wall time matters. The first 4-node run's predicted run time (482 s) was 2.6x short only
because of the non-convergence. `parse_bench.py` now counts unconverged SCF runs
after the first force evaluation (`SCF nc`) and fails the gate on any.

## 2026-10-21: other layouts on 4 nodes, 456-atom UiO-66

Same FULL_ALL input, 4 nodes, 3.6 min, 26.9 core-h. Per node 8×14 (32×14): 4.84 s/step,
1.93× the 1-node 16×7, 48 % efficiency, first step 37.3 s; 28×4 (112×4): 7.07 s, 1.32×,
33 %, first step 57.5 s. 64×7 (2026-10-19 run): 4.11 s, 57 %, first step 56.7 s. All SCF
converged, energies agree < 1e-10 Ha. **16×7 per node is also the best multi-node
layout**; 28×4 loses in DBCSR (29.8 vs 13.5 s) and FFT (25.7 vs 12.4 s). 8×14 starts
fastest (initial SCF) but is 18 % slower per steady step.

## 2026-10-22: 2 nodes, 456-atom UiO-66

Same FULL_ALL input, 2 nodes, 6.0 min, 22.5 core-h. Per node 16×7 (32×7): 5.72 s/step,
1.63× the 1-node 16×7, **82 % efficiency**, first step 44.7 s; 8×14 (16×14): 6.88 s,
68 %; 28×4 (56×4): 8.15 s, 57 %. All SCF converged, energies agree < 2e-10 Ha.
16×7 per node wins on 1, 2 and 4 nodes. Steady step 9.34 -> 5.72 -> 4.11 s on 1/2/4
nodes (82 %, 57 %); core-h per step 0.29 -> 0.36 -> 0.51. 16×7 timers 1 -> 2 -> 4
nodes: grid 37.0 -> 18.9 -> 10.0 s, FFT 27.1 -> 12.5 -> 12.4 s (stops after 2),
DBCSR 26.7 -> 17.3 -> 13.5 s. **For 456 atoms: 1 node cheapest, 2 nodes the
efficient faster choice, 4 nodes only for wall time.** Where the 4-node 43 % goes:
the FULL_ALL preconditioner's ELPA/Cholesky steps (dense, small per rank), FFT
alltoall (no gain 2 -> 4 nodes), `mp_waitall` (waiting on neighbours) and small
serial parts; at 456 atoms there is too little work per rank to hide them.

## 2026-10-19: water with the UiO-66 DFT settings

`make_water_inputs.py` -> `H2O-{32,64,128}-revpbe.inp`: revPBE-D3, DZVP-MOLOPT-SR,
450/60 Ry, OT DIIS + FULL_SINGLE_INVERSE, exactly as `UiO66-prim.inp`. 9.4 min,
17.6 core-h, all gates passed (a first try stalled at launch; see lessons).

| System | Fastest | then | 112 x 1 | slowest | spread |
|---|---|---|---|---|---|
| H2O-64-revpbe, 192 atoms, 512 e | 16x7 1.80 s | 56x2 2.01, 28x4 2.04 | 2.07 | 4x28 3.07 | 1.71x |
| H2O-128-revpbe, 384 atoms | 16x7 3.56 s (repeat 3.55) | 56x2 3.87 | n/a | 28x4 4.93 | |

- 16 x 7 wins again. At 450 Ry water is grid-bound like UiO-66, and its gain over
  pure MPI drops from 1.30x (bundled LDA/280 Ry inputs) to 1.15x: the bundled
  water inputs overstate the layout effect for production-like settings.
- UiO-66 (114 atoms, 484 e) costs 2.9x H2O-64 (512 e) per MD step: 7 vs 5 SCF
  steps and about 2x per SCF step (0.73 vs 0.36 s).

## 2026-10-27: single points (ENERGY_FORCE), FULL_ALL, 1 node

`make_sp_inputs.py` -> `*-sp.inp` (atomic guess, OT DIIS + FULL_ALL, ENERGY_GAP 0.05,
EPS_SCF 1e-6); 11.4 min, 21.2 core-h, all gates passed (forces agree to 1e-10 Ha/bohr).

| System | Fastest | then | 112 x 1 | slowest |
|---|---|---|---|---|
| UiO-66 456 atoms (55 SCF it.) | 56x2 57.4 s, 16x7 58.0 s | 28x4 69.8, 8x14 72.8 | 73.2 | 4x28 114.0 |
| H2O-128-sp (53 SCF it.) | 16x7 29.4 s | 56x2 31.3 (repeat 31.6), 28x4 37.5 | n/a | |

- 16 x 7 and 56 x 2 tie for single points, both ~1.27x over pure MPI (DBCSR 11-15 s
  vs 22.7 s); keep 16 x 7 as the default. 8 x 14 gains nothing on 1 node, although
  it had the fastest first MD step on 4 nodes.

## Reference point before the benchmark

H2O-32 on 2 nodes, 4 ranks x 2 threads per node (16 cores): 28.3 s
total, 1.31 s per steady MD step, peak 397 MiB per rank.
