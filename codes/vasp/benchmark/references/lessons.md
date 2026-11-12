# Why the VASP benchmark looks the way it does

Learned on a 1700-frame VASP labelling campaign (M2XO2 MXene slabs under
water, 252 atoms, PBE, ISPIN 2, 5 irreducible k-points, VASP 6.4.3, one
48-core node per frame, pure MPI). The numbers are that system's; the
reasons carry over.

1. **The SCF step count decides the cost, not the layout.** Across the
   campaign the time per SCF step varied 1.4x between compositions, while the
   median number of SCF steps per composition ranged from 24 to 174 (and the
   magnetic frames often hit NELM 200 after hours). A layout benchmark saves a
   fixed fraction; the spin and mixing protocol decides how many steps you pay
   for. Fix convergence first (`{{SLUG}}-vasp-single-point`, section 4).
2. **Time a fixed number of SCF steps.** With NELMIN = NELM = N every layout
   does exactly the same work, so the per-step time compares layouts directly.
   Nine steps were enough; the repeat of the baseline at the end of the
   job agreed to 0.7 %. These runs stop at NELM on purpose, so
   `vasp_parse_output` reports them unconverged; that is expected here and
   only here.
3. **Pin NBANDS.** VASP rounds NBANDS up to a multiple of the band groups,
   ranks / (KPAR x NCORE). NCORE 4 and NCORE 12 on 48 ranks gave different
   default band counts, so unpinned layouts would do different work.
   Pinned, a rerun at the new layout reproduced its label exactly. Any
   change of layout for production is followed by one full, converged
   frame compared with its label (`labels_compare`).
4. **NCORE matters most.** 48 ranks: NCORE 12 took 56.6 s per SCF step,
   NCORE 6 71.1 s, NCORE 4 75.5 s (the old production setting). NCORE 12
   was a free 1.33x. Davidson (EDDAV) was 93 % of the SCF time.
5. **KPAR > 1 was slower** for this system: KPAR 2 0.93-0.95x, KPAR 4
   0.80-0.87x of the baseline. 40 ranks with KPAR 5 (one k-point per group)
   reached 1.07x but leaves cores idle. Test KPAR, don't assume it.
6. **Fewer ranks per run, more runs per node?** 48 ranks were only 7 %
   faster than 24, so two 24-rank frames side by side might raise throughput
   up to ~1.8x. Unmeasured: two runs share memory bandwidth. Measure it as
   two concurrent runs before relying on it (`extra.concurrent_runs`), and
   note that `launch_plan` only plans full-node layouts for this pack.
7. **OpenMP may do nothing.** That build had no OpenMP and linked sequential
   MKL, so `OMP_NUM_THREADS` changed nothing and every record has
   `threads_per_rank` 1. Check the build before scanning threads.
8. **Check the build's CPU flags.** Its makefile had `-O2 -mtune=native` and
   no `-march`, so it probably used no AVX-512 on AVX-512 CPUs. A rebuild of
   the same VASP version is worth a benchmark, checked against existing
   labels before any production use.
9. **Disk space is part of the method.** A full disk killed about 990 runs
   (POTCAR copies failed) and left NUL holes in OUTCARs that were being
   written. Keep `LWAVE` and `LCHARG` off, gzip `vasprun.xml`, and delete the
   benchmark's run folders once the records are written.
