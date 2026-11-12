---
name: {{SLUG}}-campaign
description: Run and track a many-frame campaign on {{DISPLAY_NAME}} - hundreds or thousands of independent calculations (MLIP training-set labels, single points of MD frames, a screening set) worked through by one restartable "fleet" job, one lane per node - and answer what is done, what is left and why. Use whenever the user wants to label or compute many frames or structures as one job, asks how far a campaign or fleet has got, which frames failed, are held or keep retrying, why a frame failed, or wants a snapshot of a campaign's state kept, on {{DISPLAY_NAME}}.
---

# Many-frame campaigns on {{DISPLAY_NAME}}

Every `hpc` tool call uses `facility="{{SLUG}}"`. One frame is one
calculation in its own folder. `submit_job` runs one job; a campaign of
many frames runs as a **fleet**: one job whose nodes each run a lane that
claims the next frame, runs it, and retires it. Resubmitting the same
job continues where it stopped. `campaign_status` reads where it stands.

## The layout

`scripts/run_fleet.sh` (beside this file) writes this tree under the
campaign root, and `campaign_status` reads it with the same defaults:

| folder or file | meaning |
|---|---|
| `<Group>/<frame>/` | to do (`[A-Z]*` / `f[0-9][0-9][0-9]`, e.g. `Mo2CO2/f003`) |
| `<Group>/POTCAR` | a shared file, copied into each frame for its run |
| `<frame>/.running/` (`jobid` inside) | claimed by a running lane |
| `<frame>/.attempts`, `.lastjob` | tries so far; the job that tried last |
| `<frame>/NEEDS_POTCAR` | the group's shared file is missing; the frame waits |
| `<frame>/vasp.out` (`.1`, `.2`, ...) | the run's output (and earlier tries') |
| `completed/<Group>/<frame>/` | done: the result check passed |
| `failed/<Group>/<frame>/REASON` | failed `MAX_ATTEMPTS` times; the fleet never retries it |
| `scf_hold/<Group>/<frame>/` | parked by hand; the fleet ignores it |

A campaign with other names passes them as `layout` to `campaign_status`
(e.g. `{"done_dir": "done", "frame_glob": "frame_*", "log_file": "run.log"}`).

## 1. Build the tree

Write each frame's inputs into `<root>/<Group>/<frame>/` on this computer
(the input-writing skill of the code) and upload the root with
`fs_upload`, or write them on the cluster. Keep large shared files (POTCAR)
once per group. Upload `scripts/run_fleet.sh` into the root.

## 2. Submit the fleet

The driver needs `FLEET_RUN`, the command that runs one frame from inside
its folder. It gets `$FLEET_NODE` (this lane's node), `$FLEET_RANKS` and
`$FLEET_HOSTFILE`. For one node per frame under Slurm:
`srun -N1 -n "$FLEET_RANKS" -w "$FLEET_NODE" --exact <code binary>`; an
Intel MPI build may need `mpirun -np "$FLEET_RANKS" -hostfile "$FLEET_HOSTFILE" <binary>`.
The result check defaults to a VASP single point (forces and energy in
OUTCAR, SCF steps under NELM); set `FLEET_CHECK` for another code. The
other knobs (`MAX_ATTEMPTS`, `JOB_EST_SECONDS`, `FLEET_SHARED`,
`FLEET_CLEAN`, `FLEET_GZIP`, `order.txt`) are documented at the top of
the script.

Submit with `submit_job`, one node per lane:

- `executable`: `bash run_fleet.sh` with `exec_command: true`, so the
  walltime signal reaches the driver, not the batch shell;
- `directory`: the campaign root;
- `environment`: `FLEET_DIR` (the root), `FLEET_RUN`, and any knob;
- `resources`: N nodes = N lanes, ranks per node = `$FLEET_RANKS`;
- `attributes.custom_attributes`: `{"signal": "B:USR1@600"}`, so the
  driver stops its runs 10 minutes before the walltime and leaves them
  for the next submission without counting a try.

Show the user the spec and the cost (frames x time per frame / lanes) and
wait for permission. To continue after the job ends, submit the same spec
again. More nodes = more lanes; two jobs on one root are safe.

## 3. Read where it stands

```text
campaign_status(facility="{{SLUG}}", root="~/agent/work/<campaign>")
```

One read-only command on the login node, however many frames; no
permission step. It returns counts per state (pending, running, stale,
done, failed, held), per group, a `summary` line, and the frames that need
attention, most urgent first, each with `why`:

- **stale**: claimed by a job no longer in the queue. The next submission
  removes the claim and runs the frame; nothing to do.
- **failed**: `reason` (the REASON file), `log_tail` (the log's last line)
  and "SCF hit its limit (n/NELM steps)" say why. Fix the cause before
  moving frames back.
- **flagged**: a marker such as `NEEDS_POTCAR`; supply the file.
- **retried**: not done after `many_attempts` tries.
- **held**: parked by hand; the user decides.

`done_wall_s_median` per group (from file times) times the frames left,
divided by the lanes, estimates the time to finish.

Moving frames between state folders (retrying a failed frame, holding
one) is `mv` on the cluster: give the user the commands, or use `fs_mv`
after their permission. Never move a frame that is running.

{{CODE_NOTES}}

## 4. Keep a snapshot

`campaign_status(..., record=true)` also appends a `campaign` record
(every frame's state, attempts, SCF steps, wall time, reason; see the
{{SLUG}}-records skill) and returns its id. Take one at milestones (before
changing the method for the failed frames, when a round ends), not on
every check: a snapshot holds every frame.

**Done when:** the user knows how many frames are done, how many are
left, and why each frame needing attention is there.
