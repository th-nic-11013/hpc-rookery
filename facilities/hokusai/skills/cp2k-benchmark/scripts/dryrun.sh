#!/bin/bash
# Dry run of a benchmark folder on the front end, with no Slurm and no compute.
#   dryrun.sh <benchmark folder> <empty scratch folder> [fake job id]
# Copies the folder's scripts and inputs/ (not runs/) into the scratch folder and runs
# the three stages there exactly as bench_job.sh would, with a stub `srun` first on
# PATH. The stub copies REF_OUT (required: a finished cp2k.out, e.g. the build's H2O-32 test)
# instead of running CP2K, and exits 3 for the layout named in FAIL_LAYOUT (e.g. 16x7)
# so the failure path can be tested. Pass the job's settings as environment variables:
#   SMOKE_INPUT=... SCAN_INPUT=... VALIDATE_INPUT=... BENCH_LIMIT_S=3180 dryrun.sh ...
# Checks: stage order, gates, layout picking, time guard (set BENCH_LIMIT_S small).
# JOB=<job script> runs that script (e.g. multinode_layouts_job.sh) instead of the three
# stages, as Slurm would on DRY_NODES nodes (default 4); its settings can be overridden
# the same way, e.g. REF_DIR=<absolute path> LAYOUTS="16x7 8x14".
# Timings in the output are meaningless.
set -u
SRC=${1:?benchmark folder}; SCR=${2:?scratch folder}; ID=${3:-dry$$}
REF_OUT=${REF_OUT:?set REF_OUT to a finished cp2k.out, e.g. the H2O-32 test output of the build}
[ -f "${REF_OUT}" ] || { echo "REF_OUT ${REF_OUT} not found"; exit 1; }
SRC=$(cd "${SRC}" && pwd); mkdir -p "${SCR}"; SCR=$(cd "${SCR}" && pwd)
[ "${SRC}" != "${SCR}" ] || { echo "scratch must differ from the benchmark folder"; exit 1; }

mkdir -p "${SCR}/bench/runs" "${SCR}/bin"
cp "${SRC}"/*.sh "${SRC}"/parse_bench.py "${SCR}/bench/" && cp -r "${SRC}/inputs" "${SCR}/bench/" || exit 1
# scripts that hard-code the benchmark folder (source .../run_layouts.sh, BENCH=...) would
# otherwise write the stub runs into the real runs/
sed -i "s|${SRC}|${SCR}/bench|g" "${SCR}/bench/"*.sh || exit 1
! grep -l "${SRC}" "${SCR}/bench/"*.sh || { echo "copies still point at ${SRC}, abort"; exit 1; }
cat > "${SCR}/bin/srun" <<EOF
#!/bin/bash
# stub srun written by dryrun.sh: no MPI, no CP2K
for a in "\$@"; do case \$a in --ntasks=*) R=\${a#*=};; --cpus-per-task=*) T=\${a#*=};; esac; done
echo "cpu-bind=MASK - stub, \${R}x\${T}" >&2
[ "\${R}x\${T}" = "\${FAIL_LAYOUT:-none}" ] && exit 3
cp "${REF_OUT}" cp2k.out
EOF
chmod +x "${SCR}/bin/srun"

export PATH=${SCR}/bin:${PATH}
# a real srun must never run from here: on the front end it would try to start a job
[ "$(command -v srun)" = "${SCR}/bin/srun" ] || { echo "stub srun is not first on PATH, abort"; exit 9; }
export BENCH=${SCR}/bench SLURM_JOB_ID=${ID} BENCH_START=$(date +%s) BENCH_LIMIT_S=${BENCH_LIMIT_S:-3180}

if [ -n "${JOB:-}" ]; then
    export SLURM_SUBMIT_DIR=${BENCH} SLURM_JOB_NUM_NODES=${DRY_NODES:-4} SLURM_JOB_NODELIST=stub[1-${DRY_NODES:-4}]
    ( cd "${BENCH}" && bash "${BENCH}/${JOB}" ); rc=$?
    echo "##### ${JOB} exit ${rc} (dry run ${ID}, results in ${BENCH}/runs/${ID}) #####"
    exit ${rc}
fi
for S in stage1_smoke stage2_scan stage3_validate; do
    echo "##### ${S} #####"
    bash "${BENCH}/${S}.sh" || { echo "##### ${S} failed: later stages skipped #####"; exit 1; }
done
echo "##### all stages passed (dry run ${ID}, results in ${BENCH}/runs/${ID}) #####"
