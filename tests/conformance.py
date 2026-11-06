"""Behavioral conformance checks that run against *every* registered facility.

This is the counterweight to PORTING.md §0a's "duplication between
facilities is fine". Facility code is deliberately written out per machine
rather than collapsed into shared abstractions — so the thing that keeps
those independent implementations honest is a shared *test*, not shared
code. When your facility establishes a behavior every facility should
honor, add the assertion here; it then applies to all of them, including
ones onboarded later, without any of them having to import each other.

Runs entirely offline — no SSH, no scheduler, no network. Everything here
must stay that way so CI can run it. Anything needing a live cluster
belongs in tests/live_smoke.py instead.

    .venv/bin/python tests/conformance.py
    .venv/bin/python tests/conformance.py --empty-home   # as a brand-new user (CI)
"""
import _harness  # noqa: I001 -- first: this checkout on sys.path, --empty-home (G28, G29)
_harness.setup(__file__)

import contextlib
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import traceback
from unittest.mock import patch

import hpc_mcp  # noqa: F401 -- registers every facility
from hpc_agent_core import config
from hpc_agent_core.models import Job, JobAttributes, JobSpec, ResourceSpec
from facilities.registry import get_backend

FAILURES: list[str] = []


@contextlib.contextmanager
def no_user_config(slug: str):
    """Point a facility's config at a path that doesn't exist.

    Without this, checks covering "the user hasn't configured X yet" only
    run on a machine that genuinely hasn't — so they silently pass on a
    developer's fully-configured laptop and on nobody's CI. Forcing the
    unconfigured state makes those paths deterministic everywhere.
    """
    var = f"{config.get_facility(slug).env_prefix}_CONFIG"
    previous = os.environ.get(var)
    os.environ[var] = "/nonexistent/conformance-forced-missing.json"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = previous


def check(label: str, fn) -> None:
    try:
        fn()
    except Exception:
        FAILURES.append(f"{label}\n{traceback.format_exc()}")
        print(f"  ✗ {label}")
    else:
        print(f"  ✓ {label}")


def bare_spec() -> JobSpec:
    """A minimal spec with everything optional left blank — what
    apply_defaults() is supposed to complete."""
    return JobSpec(
        name="conformance",
        executable="echo ok",
        resources=ResourceSpec(node_count=1, processes_per_node=1),
        attributes=JobAttributes(duration=300),
    )


# --- per-facility checks -------------------------------------------------

def check_facility(slug: str) -> None:
    fac = config.get_facility(slug)
    backend = get_backend(slug)

    def registration_is_complete():
        assert fac.display_name.strip(), "display_name is empty"
        assert fac.description.strip(), "description is empty"
        assert (fac.data_dir / fac.docs_filename).exists(), "guide file missing"
        assert (fac.data_dir / fac.facts_filename).exists(), "facts JSON missing"
        assert (fac.data_dir / "docs_index" / "chunks.json").exists(), "docs index missing"

    def facts_load():
        assert config.load_facts(slug), "facts JSON is empty"

    def apply_defaults_is_idempotent():
        """Running apply_defaults twice must not change the result — it's
        called by both render_job_script and submit_job, and an agent
        commonly renders then submits the same spec object."""
        first, second = bare_spec(), bare_spec()
        try:
            backend.apply_defaults(first)
        except ValueError:
            return  # a facility that requires an unset field; covered below
        backend.apply_defaults(first)          # second application
        backend.apply_defaults(second)
        assert first.attributes.queue_name == second.attributes.queue_name
        assert first.attributes.account == second.attributes.account

    def _defaults_then_validate_then_render():
        """The exact sequence submit_job runs. Either it produces a usable
        script, or it raises a ValueError that tells the user what to set —
        never an AttributeError/TypeError from a half-completed spec."""
        spec = bare_spec()
        try:
            backend.apply_defaults(spec)
            backend.validate_spec(spec)
        except ValueError as e:
            msg = str(e)
            assert len(msg) > 40, f"error too terse to act on: {msg!r}"
            # Must tell the user what to do about it, not just what's wrong.
            assert any(w in msg.lower() for w in ("set ", "configure", "use ")), \
                f"error doesn't tell the user what to do: {msg!r}"
            return
        script = backend.render_script(spec)
        assert script.startswith("#!"), "rendered script has no shebang"
        assert "echo ok" in script, "rendered script lost the executable"

    def defaults_then_validate_then_render():
        _defaults_then_validate_then_render()

    def defaults_then_validate_then_render_unconfigured():
        """Same, with the user's config forced missing — this is the path a
        brand-new user hits, and on a facility with a mandatory setting
        it's the *only* way the "you must configure X" error gets
        exercised at all."""
        with no_user_config(slug):
            _defaults_then_validate_then_render()

    def rendered_script_has_a_queue():
        """If apply_defaults supplies a queue, it must actually reach the
        script — catches a facility that sets the wrong attribute."""
        spec = bare_spec()
        try:
            backend.apply_defaults(spec)
            backend.validate_spec(spec)
        except ValueError:
            return
        if spec.attributes.queue_name:
            assert spec.attributes.queue_name in backend.render_script(spec), \
                "queue_name was defaulted but never appears in the script"

    def explicit_values_survive_defaults():
        """apply_defaults must never overwrite something the caller set."""
        spec = bare_spec()
        spec.attributes.queue_name = "caller-chosen-queue"
        spec.attributes.account = "caller-chosen-account"
        backend.apply_defaults(spec)
        assert spec.attributes.queue_name == "caller-chosen-queue", \
            "apply_defaults overwrote a caller-supplied queue_name"
        assert spec.attributes.account == "caller-chosen-account", \
            "apply_defaults overwrote a caller-supplied account"

    def backend_knows_its_facility():
        assert backend.facility == slug, \
            f"backend.facility is {backend.facility!r}, expected {slug!r}"

    def machine_resource_limits_are_enforced():
        """Exercise deterministic limits encoded by the facility hook.

        Only RIKYU currently has a complete job-resource table precise
        enough for these checks; facilities whose limits vary by project or
        live partition intentionally do not guess here.
        """
        if slug != "rikyu":
            return
        invalid_specs = [
            {"gpus": 5},
            {"gpus": 8, "node_count": 1},
            {"gpus": 1, "cpu_cores_per_process": 37},
            {"gpus": 1, "memory": 401 * 1024**3},
        ]
        for resources in invalid_specs:
            spec = JobSpec(
                executable="true", resources=resources,
                attributes={"queue_name": "gpu"},
            )
            try:
                backend.validate_spec(spec)
            except ValueError:
                continue
            raise AssertionError(f"RIKYU accepted invalid resources: {resources}")

        wrong_queue = JobSpec(
            executable="true", attributes={"queue_name": "not-gpu"},
        )
        try:
            backend.validate_spec(wrong_queue)
        except ValueError:
            return
        raise AssertionError("RIKYU accepted a nonexistent partition")

    def miyabi_pbs_shape_and_limits_are_enforced():
        """Keep the offline PBS port honest while Miyabi is inaccessible."""
        if slug != "miyabi":
            return

        spec = JobSpec(
            name="pbs-shape",
            executable="hostname",
            resources={
                "node_count": 2,
                "process_count": 8,
                "cpu_cores_per_process": 2,
                "memory": 3 * 1024**3 + 1,
            },
            attributes={
                "queue_name": "debug-c",
                "account": "caller-project",
                "duration": 300,
            },
        )
        script = backend.render_script(spec)
        assert "#PBS -q debug-c" in script
        assert "#PBS -W group_list=caller-project" in script
        assert "#PBS -l select=2:mpiprocs=4:ompthreads=2:mem=4gb" in script
        assert "#PBS -l walltime=00:05:00" in script
        assert 'cd "$PBS_O_WORKDIR"' in script

        # A PBS directive is a single line of options, so a space inside an
        # argument does not need a newline to inject a second option -- the
        # control-character validator in models.py cannot see this. Both
        # fields are checked against their real syntax instead.
        injections = [
            {"queue_name": "debug-c -l place=excl", "account": "caller-project"},
            {"queue_name": "debug-c", "account": "caller-project -l place=excl"},
            {"queue_name": "debug-c", "account": "grp; qdel 1"},
            {"queue_name": "no-such-queue", "account": "caller-project"},
            {"queue_name": "debug-", "account": "caller-project"},
        ]
        for attrs in injections:
            bad = JobSpec(executable="true", resources={"node_count": 1},
                          attributes={**attrs, "duration": 300})
            try:
                backend.apply_defaults(bad)
                backend.validate_spec(bad)
            except ValueError:
                continue
            raise AssertionError(
                f"miyabi accepted an injectable PBS directive argument: {attrs}"
            )

        # A legitimate path containing a space must survive, quoted, rather
        # than splitting into a second option on the -o/-e directive line.
        spaced = JobSpec(
            name="pbs-spaces", executable="hostname",
            resources={"node_count": 1},
            stdout_path="/home/u/out dir/job.out",
            stderr_path="/home/u/out dir/job.err",
            attributes={"queue_name": "debug-c", "account": "caller-project",
                        "duration": 300},
        )
        backend.apply_defaults(spaced)
        backend.validate_spec(spaced)
        spaced_script = backend.render_script(spaced)
        assert "#PBS -o '/home/u/out dir/job.out'" in spaced_script, spaced_script
        assert "#PBS -e '/home/u/out dir/job.err'" in spaced_script, spaced_script

        finished_without_exit = backend._job({
            "Job_Id": "123.opbs", "job_state": "F",
        })
        assert finished_without_exit.status.state.value == "completed"
        assert finished_without_exit.status.exit_code is None
        failed = backend._job({
            "Job_Id": "124.opbs", "job_state": "F", "Exit_status": "2",
        })
        assert failed.status.state.value == "failed"
        assert failed.status.exit_code == 2
        with_workdir = backend._job({
            "Job_Id": "125.opbs", "job_state": "R",
            "Variable_List": (
                "PBS_O_HOME=/home/u,PBS_O_WORKDIR=/home/u/agent/jobs,"
                "PBS_O_HOST=login"
            ),
        })
        assert with_workdir.status.meta_data["workdir"] == "/home/u/agent/jobs"
        assert backend._pbs_workdir("PBS_O_HOME=/home/u") == ""

        invalid = [
            ("debug-c", {"processes_per_node": 113}),
            ("debug-g", {"processes_per_node": 73}),
            ("debug-mig", {"processes_per_node": 19}),
            ("debug-c", {"gpus": 1}),
            ("debug-g", {"node_count": 2, "gpus": 1}),
        ]
        for queue, resources in invalid:
            bad = JobSpec(
                executable="true",
                resources=resources,
                attributes={"queue_name": queue, "account": "caller-project"},
            )
            try:
                backend.validate_spec(bad)
            except ValueError:
                continue
            raise AssertionError(
                f"Miyabi accepted invalid resources for {queue}: {resources}"
            )

    def octopus_dual_vendor_defaults_and_limits_are_enforced():
        """Exercise the dual-vendor mapping without needing either GPU."""
        if slug != "octopus":
            return

        default = bare_spec()
        backend.apply_defaults(default)
        backend.validate_spec(default)
        assert default.attributes.queue_name == "h200"
        assert default.resources.gpus == 1
        script = backend.render_script(default)
        assert "#SBATCH --partition=h200" in script
        assert "#SBATCH --gres=gpu:1" in script

        for queue, expected, forbidden in [
            ("h200", "--nv", "--rocm"),
            ("mi300x", "--rocm", "--nv"),
        ]:
            spec = JobSpec(
                executable="echo ok",
                container={"image": "$HOME/test.sif"},
                attributes={"queue_name": queue, "duration": 300},
                resources={"gpus": 2},
            )
            backend.apply_defaults(spec)
            backend.validate_spec(spec)
            rendered = backend.render_script(spec)
            assert f"#SBATCH --partition={queue}" in rendered
            assert "#SBATCH --gres=gpu:2" in rendered
            assert expected in rendered
            assert forbidden not in rendered

        invalid = [
            ({"queue_name": "gpu"}, {}),
            ({"queue_name": "h200"}, {"gpus": 9}),
            ({"queue_name": "h200"}, {"node_count": 2, "gpus": 1}),
            ({"queue_name": "h200"}, {"gpus": 1, "processes_per_node": 193}),
            ({"queue_name": "h200"}, {"gpus": 1, "memory": 2_317_611 * 1024**2}),
            ({"queue_name": "mi300x", "duration": "08:00:01"}, {"gpus": 1}),
        ]
        for attributes, resources in invalid:
            bad = JobSpec(
                executable="true", resources=resources, attributes=attributes,
            )
            backend.apply_defaults(bad)
            try:
                backend.validate_spec(bad)
            except ValueError:
                continue
            raise AssertionError(
                f"Octopus accepted invalid spec: attributes={attributes}, "
                f"resources={resources}"
            )

        long_job = JobSpec(
            executable="true", resources={"gpus": 1},
            attributes={"queue_name": "mi300x-long", "duration": "2-00:00:00"},
        )
        backend.apply_defaults(long_job)
        backend.validate_spec(long_job)

    def tsubame_resource_types_and_trial_limits_are_enforced():
        """Exercise TSUBAME's non-generic Grid Engine resource dialect."""
        if slug != "tsubame":
            return

        with no_user_config(slug), patch.dict(os.environ, {"TSUBAME_GROUP": ""}):
            trial = JobSpec(
                name="ge-shape",
                executable="hostname",
                container={"image": "$HOME/test image.sif"},
                resources={"node_count": 2, "processes_per_node": 4,
                           "cpu_cores_per_process": 2},
                attributes={
                    "duration": 180,
                    "account": "",
                    "custom_attributes": {
                        "resource_type": "gpu_1", "priority": "-5",
                        "array": "1-4:2", "gpu_compute_mode": "1",
                    },
                },
            )
            backend.apply_defaults(trial)
            script = backend.render_script(trial)
        assert "#$ -l gpu_1=2" in script
        assert "#$ -l h_rt=00:03:00" in script
        assert "#$ -p -5" in script
        assert "#$ -t 1-4:2" in script
        assert "#$ -v GPU_COMPUTE_MODE=1" in script
        assert "export OMP_NUM_THREADS=2" in script
        assert "apptainer exec -B /gs -B /apps -B /home --nv" in script
        assert '"$HOME"/' in script

        cpu_container = JobSpec(
            executable="true", container={"image": "image.sif"},
            attributes={"account": "paid", "duration": 3600,
                        "custom_attributes": {"resource_type": "cpu_40"}},
        )
        backend.apply_defaults(cpu_container)
        cpu_script = backend.render_script(cpu_container)
        assert "#$ -l cpu_40=1" in cpu_script
        assert "--nv" not in cpu_script

        invalid_specs = [
            ({"node_count": 3}, {"account": "", "duration": 180}),
            ({"node_count": 1}, {"account": "", "duration": 181}),
            ({}, {"account": "", "duration": 180,
                  "custom_attributes": {"priority": "-4"}}),
            ({}, {"account": "paid", "duration": "24:00:01"}),
            ({}, {"account": "paid", "custom_attributes": {"resource_type": "gpu_2"}}),
            ({"gpus": 1}, {"account": "paid"}),
            ({"memory": 1024}, {"account": "paid"}),
            ({"processes_per_node": 9}, {"account": "paid",
                 "custom_attributes": {"resource_type": "gpu_1"}}),
            ({}, {"account": "paid",
                  "custom_attributes": {"resource_type": "gpu_h",
                                        "gpu_compute_mode": "1"}}),
            ({}, {"account": "paid", "queue_name": "all.q"}),
            ({}, {"account": "", "queue_name": "prior"}),
            ({}, {"account": "paid", "custom_attributes": {"unknown": "x"}}),
        ]
        for resources, attributes in invalid_specs:
            bad = JobSpec(executable="true", resources=resources, attributes=attributes)
            backend.apply_defaults(bad)
            try:
                backend.validate_spec(bad)
            except ValueError:
                continue
            raise AssertionError(
                f"TSUBAME accepted invalid spec: resources={resources}, "
                f"attributes={attributes}"
            )

        assert backend._parse_qstat_line(
            "123 0.555 job user hRwq 08/19/2026 12:34:56 1"
        ).status.state.value == "held"
        assert backend._parse_qstat_line(
            "124 0.555 job user r 08/19/2026 12:34:56 all.q@node 8"
        ).status.state.value == "active"
        from facilities.tsubame.compute import _final_state
        assert _final_state("0", "0").value == "completed"
        assert _final_state("0", "2").value == "failed"
        assert _final_state(None, None).value == "unknown"

        update_spec = JobSpec(
            name="renamed", executable="true",
            attributes={"duration": 600,
                        "custom_attributes": {"priority": "-4", "hold_jid": "41"}},
        )
        updated = Job(
            id="42", status={"state": "queued", "message": None},
        )
        with patch("facilities.tsubame.compute.run_command") as command, \
                patch.object(backend, "get_statuses", return_value=[updated]):
            backend.update("42", update_spec)
        rendered_command = command.call_args.args[1]
        assert rendered_command == (
            "qalter -N renamed -l h_rt=00:10:00 -p -4 -hold_jid 41 42"
        )

    def irene_bridge_shape_and_limits_are_enforced():
        """Exercise Bridge rendering and parsers without a TGCC connection."""
        if slug != "irene":
            return

        spec = JobSpec(
            name="bridge-shape",
            executable="./solver",
            arguments=["input with spaces"],
            environment={"OMP_NUM_THREADS": "2"},
            resources={
                "node_count": 2, "process_count": 8,
                "cpu_cores_per_process": 2, "exclusive_node_use": True,
            },
            attributes={
                "queue_name": "rome", "account": "gen-test", "duration": 600,
                "custom_attributes": {"filesystems": "scratch,work", "qos": "test"},
            },
        )
        script = backend.render_script(spec)
        assert "#MSUB -q rome" in script
        assert "#MSUB -A gen-test" in script
        assert "#MSUB -m scratch,work" in script
        assert "#MSUB -n 8" in script
        assert "#MSUB -c 2" in script
        assert "#MSUB -N 2" in script
        assert "#MSUB -x" in script
        assert "#MSUB -T 600" in script
        assert "#MSUB -Q test" in script
        assert "cd ${BRIDGE_MSUB_PWD}" in script
        assert "ccc_mprun ./solver 'input with spaces'" in script

        gpu = JobSpec(
            executable="gpu-app", resources={"process_count": 4, "gpus": 4},
            attributes={"queue_name": "v100", "account": "gen-test",
                        "custom_attributes": {"filesystems": "scratch"}},
        )
        gpu_script = backend.render_script(gpu)
        assert "#MSUB -c 10" in gpu_script
        assert "ccc_mprun gpu-app" in gpu_script

        container = JobSpec(
            executable="python", arguments=["run.py"],
            container={"image": "registry/image:tag",
                       "volume_mounts": [{"source": "/work/a", "target": "/data"}]},
            attributes={"queue_name": "rome", "account": "gen-test",
                        "custom_attributes": {"filesystems": "work"}},
        )
        assert "ccc_mprun -C registry/image:tag -E" in backend.render_script(container)

        invalid_specs = [
            ({}, {"queue_name": "skylake", "account": "gen-test"}),
            ({"gpus": 1}, {"queue_name": "rome", "account": "gen-test"}),
            ({"node_count": 2}, {"queue_name": "xlarge", "account": "gen-test"}),
            ({"memory": 1024}, {"queue_name": "rome", "account": "gen-test"}),
            ({"process_count": 129}, {"queue_name": "rome", "account": "gen-test"}),
            ({"process_count": 2, "gpus": 1}, {"queue_name": "v100", "account": "gen-test"}),
        ]
        for resources, attributes in invalid_specs:
            attributes.setdefault("custom_attributes", {"filesystems": "scratch,work"})
            bad = JobSpec(executable="true", resources=resources, attributes=attributes)
            try:
                backend.validate_spec(bad)
            except ValueError:
                continue
            raise AssertionError(
                f"Irene accepted invalid spec: resources={resources}, attributes={attributes}"
            )

        from facilities.irene.compute import (
            _parse_ccc_mpp, _parse_ccc_msub_job_id, _parse_compuse_projects,
            _parse_macct,
        )
        projects = _parse_compuse_projects(
            "gen1@rome OK normal\ngen1@v100 BONUS\ngen1@rome duplicate"
        )
        assert [(p["account"], p["partition"]) for p in projects] == [
            ("gen1", "rome"), ("gen1", "v100")]
        assert _parse_ccc_msub_job_id("Submitted batch job 123456") == "123456"
        jobs = _parse_ccc_mpp(
            "user gen1 123456 8 rome batch RUN 00:10:00 now x x bridge-job node01"
        )
        assert jobs[0].status.state.value == "active"
        assert _parse_macct(
            "123456", "Date: 19/08/2026 12:00:00\nStep COMPLETED"
        ).status.state.value == "completed"

        with patch(
            "facilities.irene.compute.run_command",
            return_value="gen1@rome OK normal\ngen1@v100 BONUS",
        ) as command:
            assert backend.get_projects() == [{
                "account": "gen1", "partitions": ["rome", "v100"],
                "status": [
                    {"partition": "rome", "value": "OK normal"},
                    {"partition": "v100", "value": "BONUS"},
                ],
            }]
        assert command.call_args.args == ("irene", "ccc_compuse")

        with patch(
            "facilities.irene.compute.run_command",
            side_effect=RuntimeError("Irene connection failed"),
        ):
            try:
                backend.get_projects()
            except RuntimeError as exc:
                assert "Irene connection failed" in str(exc)
            else:
                raise AssertionError(
                    "Irene returned an empty project list after its query failed"
                )

        with patch(
            "facilities.irene.compute.run_command",
            side_effect=["gen-test@rome OK", "Submitted batch job 123456"],
        ) as command, patch(
            "facilities.irene.compute.write_remote_file",
            return_value="agent/jobs/bridge-shape.sh",
        ) as write:
            submitted = backend.submit(spec)
        assert submitted["job_id"] == "123456"
        assert write.call_args.args[0] == "irene"
        assert command.call_args_list[0].args == ("irene", "ccc_compuse")
        assert command.call_args_list[1].args == (
            "irene", "ccc_msub agent/jobs/bridge-shape.sh",
        )

        update_spec = JobSpec(executable="true", attributes={"duration": 300})
        updated = Job(id="123456", status={"state": "active"})
        with patch("facilities.irene.compute.run_command") as command, \
                patch.object(backend, "get_statuses", return_value=[updated]):
            backend.update("123456", update_spec)
        assert command.call_args.args == (
            "irene", "ccc_malter -T 300 123456",
        )

    def cell2026_dual_scheduler_routing_is_enforced():
        """Exercise both schedulers and the local routing index offline."""
        if slug != "cell2026":
            return

        # AGE may be outside PATH. Doctor must check the same configured
        # Grid Engine binaries that the runtime backend invokes.
        from facilities.cell2026.compute import Cell2026Backend
        configured = Cell2026Backend("cell2026", ge_bin_prefix="/opt/age/bin")
        with patch("hpc_agent_core.doctor.check_commands_on_path",
                   return_value=True) as check_commands:
            assert configured.check_scheduler()
        checked = check_commands.call_args.args[1]
        assert "/opt/age/bin/qsub" in checked
        assert "/opt/age/bin/qstat" in checked
        assert "sbatch" in checked

        try:
            backend.get_projects()
        except NotImplementedError as exc:
            assert "neither Grid Engine nor Slurm exposes per-project accounting" in str(exc)
        else:
            raise AssertionError(
                "cell2026 returned an empty project list despite lacking project accounting"
            )

        # A successful resource list must cover both schedulers.  An empty
        # list after failed SSH/configuration probes looks like valid empty
        # occupancy to an MCP client, which is worse than a clear error.
        with patch.object(
            backend._slurm, "get_live_resources", return_value=[],
        ), patch(
            "facilities.cell2026.compute.run_command", return_value="",
        ):
            assert backend.get_live_resources() == []

        with patch.object(
            backend._slurm, "get_live_resources",
            side_effect=RuntimeError("Slurm connection failed"),
        ), patch(
            "facilities.cell2026.compute.run_command",
            side_effect=RuntimeError("Grid Engine connection failed"),
        ):
            try:
                backend.get_live_resources()
            except RuntimeError as exc:
                message = str(exc)
                assert "incomplete" in message
                assert "Slurm, Grid Engine" in message
                assert "Slurm connection failed" in message
            else:
                raise AssertionError(
                    "cell2026 returned a resource list after both scheduler probes failed"
                )

        with patch.object(
            backend._slurm, "get_live_resources",
            return_value=[{"partition": "all", "nodes_total": 2}],
        ), patch(
            "facilities.cell2026.compute.run_command",
            side_effect=RuntimeError("Grid Engine connection failed"),
        ):
            try:
                backend.get_live_resources()
            except RuntimeError as exc:
                message = str(exc)
                assert "incomplete" in message
                assert "Grid Engine" in message
                assert "Grid Engine connection failed" in message
            else:
                raise AssertionError(
                    "cell2026 returned partial scheduler occupancy as a complete list"
                )

        default = bare_spec()
        backend.apply_defaults(default)
        assert default.attributes.queue_name == "all.q"
        ge_script = backend.render_script(JobSpec(
            name="cell-ge", executable="echo ok",
            resources={"gpus": 1, "cpu_cores_per_process": 4},
            attributes={"queue_name": "all.q", "duration": 300},
        ))
        assert "#$ -q all.q" in ge_script
        assert "#$ -l gpu=1" in ge_script
        assert "#$ -pe smp 4" in ge_script
        assert "SGE_HGR_gpu" in ge_script
        assert "CUDA_VISIBLE_DEVICES" in ge_script
        assert "export OMP_NUM_THREADS=4" in ge_script
        assert "#SBATCH" not in ge_script

        ge_host = backend.render_script(JobSpec(
            executable="true", attributes={"queue_name": "helix"},
        ))
        assert "#$ -q all.q" in ge_host
        assert "#$ -l hostname=helix" in ge_host

        slurm_host = backend.render_script(JobSpec(
            name="cell-slurm", executable="echo ok",
            resources={"cpu_cores_per_process": 2, "memory": None},
            attributes={"queue_name": "beta", "duration": 300},
        ))
        assert "#SBATCH --partition=all" in slurm_host
        assert "#SBATCH --nodelist=beta" in slurm_host
        assert "#SBATCH --cpus-per-task=2" in slurm_host
        assert "--mem" not in slurm_host
        assert "--gres" not in slurm_host
        assert "#$ " not in slurm_host

        slurm_gpu = backend.render_script(JobSpec(
            executable="true", resources={"gpus": 1},
            attributes={"queue_name": "serine"},
            container={"image": "$HOME/test.sif"},
        ))
        assert "#SBATCH --nodelist=serine" in slurm_gpu
        assert "--gres" not in slurm_gpu
        assert "singularity exec --nv" in slurm_gpu

        ge_submit_spec = JobSpec(
            name="submit-ge", executable="true", resources={"gpus": 1},
            attributes={"queue_name": "helix"},
        )
        with patch(
            "hpc_agent_core.compute.gridengine.write_remote_file",
            return_value="agent/jobs/submit-ge.sh",
        ) as ge_write, patch(
            "hpc_agent_core.compute.gridengine.run_command", return_value="301\n",
        ) as ge_command, patch(
            "facilities.cell2026.registry.record",
        ) as record:
            assert backend.submit(ge_submit_spec)["job_id"] == "301"
        assert ge_write.call_args.args[0] == "cell2026"
        assert ge_command.call_args.args[0] == "cell2026"
        assert record.call_args.args[:3] == ("301", "gridengine", "helix")

        slurm_submit_spec = JobSpec(
            name="submit-slurm", executable="true",
            attributes={"queue_name": "beta"},
        )
        with patch(
            "hpc_agent_core.compute.slurm.write_remote_file",
            return_value="agent/jobs/submit-slurm.sh",
        ) as slurm_write, patch(
            "hpc_agent_core.compute.slurm.run_command", return_value="302\n",
        ) as slurm_command, patch(
            "facilities.cell2026.registry.record",
        ) as record:
            assert backend.submit(slurm_submit_spec)["job_id"] == "302"
        assert slurm_write.call_args.args[0] == "cell2026"
        assert slurm_command.call_args.args == (
            "cell2026", "sbatch --parsable agent/jobs/submit-slurm.sh",
        )
        assert record.call_args.args[:3] == ("302", "slurm", "all")

        explicit_slurm = JobSpec(
            executable="true", attributes={"scheduler": "slurm"},
        )
        backend.apply_defaults(explicit_slurm)
        assert explicit_slurm.attributes.queue_name == "all"

        invalid_specs = [
            ({"gpus": 3}, {"queue_name": "all.q"}, {}),
            ({"process_count": 33}, {"queue_name": "all.q"}, {}),
            ({"node_count": 2}, {"queue_name": "helix"}, {}),
            ({"memory": 1024}, {"queue_name": "all.q"}, {}),
            ({"gpus": 1}, {"queue_name": "all.q"}, {"CUDA_VISIBLE_DEVICES": "0"}),
            ({}, {"queue_name": "all.q", "account": "project"}, {}),
            ({"node_count": 2}, {"queue_name": "beta"}, {}),
            ({"process_count": 9}, {"queue_name": "all"}, {}),
            ({"gpus": 1}, {"queue_name": "serine"}, {}),
            ({"gpus": 2}, {"queue_name": "all"}, {}),
            ({}, {"queue_name": "all", "custom_attributes": {"constraint": "gpu"}}, {}),
            ({}, {"queue_name": "not-a-queue"}, {}),
            ({}, {"queue_name": "all.q", "scheduler": "slurm"}, {}),
        ]
        for resources, attributes, environment in invalid_specs:
            bad = JobSpec(
                executable="true", resources=resources,
                attributes=attributes, environment=environment,
            )
            try:
                backend.validate_spec(bad)
            except ValueError:
                continue
            raise AssertionError(
                f"cell2026 accepted invalid spec: resources={resources}, "
                f"attributes={attributes}, environment={environment}"
            )

        from facilities.cell2026 import registry as cell_registry
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(cell_registry, "_REGISTRY_DIR", pathlib.Path(temporary)):
                cell_registry.record("101", "slurm", "all", "agent/jobs/a.sh")
                cell_registry.record("202", "gridengine", "all.q", "agent/jobs/b.sh")
                assert cell_registry.lookup("101") == "slurm"
                assert cell_registry.lookup("202") == "gridengine"
                assert {row["job_id"] for row in cell_registry.recent()} == {"101", "202"}
                assert cell_registry.lookup("../escape") is None
                cell_registry.record("101", "gridengine", "all.q", "agent/jobs/c.sh")
                assert cell_registry.lookup("101") is None, (
                    "overlapping IDs registered to both schedulers must stay ambiguous"
                )

        slurm_job = Job(id="101", status={"state": "active",
                                           "meta_data": {"native_state": "RUNNING"}})
        ge_job = Job(id="202", status={"state": "completed",
                                       "meta_data": {"native_state": "qacct"}})
        with patch(
            "facilities.cell2026.registry.lookup",
            side_effect=lambda job_id: {"101": "slurm", "202": "gridengine"}.get(job_id),
        ), patch.object(
            backend._slurm, "get_statuses", return_value=[slurm_job],
        ), patch.object(
            backend._gridengine, "get_statuses", return_value=[ge_job],
        ):
            routed = backend.get_statuses(["202", "101"])
        assert [job.id for job in routed] == ["202", "101"]
        assert all(job.status.meta_data["scheduler_source"] == "registry"
                   for job in routed)

        # run_command is stubbed so both liveness probes succeed: this case
        # is about a genuinely ambiguous id, not an unreachable scheduler.
        with patch("facilities.cell2026.registry.lookup", return_value=None), \
                patch("hpc_agent_core.middleware.run_command", return_value=""), \
                patch.object(backend._slurm, "get_statuses", return_value=[slurm_job]), \
                patch.object(backend._gridengine, "get_statuses", return_value=[ge_job]), \
                patch.object(backend._slurm, "cancel") as slurm_cancel, \
                patch.object(backend._gridengine, "cancel") as ge_cancel:
            try:
                backend.cancel("77")
            except ValueError as exc:
                assert "exists in both" in str(exc), exc
            else:
                raise AssertionError("cell2026 canceled an ambiguous cross-scheduler ID")
        slurm_cancel.assert_not_called()
        ge_cancel.assert_not_called()

        # An unregistered id with only ONE scheduler answering must not be
        # cancelled on that scheduler: _is_not_found() reports the same shape
        # for "absent" and "the query failed", and this facility's Slurm runs
        # has_accounting=False, which swallows both squeue and scontrol
        # errors -- so a dead link looks exactly like a clean miss. Ids can
        # collide across the two schedulers, so guessing can kill a different
        # job.
        missing = Job(id="77", status={"state": "unknown"})
        with patch("facilities.cell2026.registry.lookup", return_value=None), \
                patch("hpc_agent_core.middleware.run_command",
                      side_effect=RuntimeError("ssh: connect to host: timed out")), \
                patch.object(backend._slurm, "get_statuses", return_value=[slurm_job]), \
                patch.object(backend._gridengine, "get_statuses", return_value=[missing]), \
                patch.object(backend._slurm, "cancel") as slurm_cancel, \
                patch.object(backend._gridengine, "cancel") as ge_cancel:
            try:
                backend.cancel("77")
            except ValueError as exc:
                assert "could not be completed" in str(exc), exc
            else:
                raise AssertionError(
                    "cell2026 cancelled an unregistered id while a scheduler "
                    "lookup was inconclusive"
                )
        slurm_cancel.assert_not_called()
        ge_cancel.assert_not_called()

        # With both schedulers demonstrably answering and exactly one hit,
        # the fallback cancel still works.
        with patch("facilities.cell2026.registry.lookup", return_value=None), \
                patch("hpc_agent_core.middleware.run_command", return_value=""), \
                patch.object(backend._slurm, "get_statuses", return_value=[slurm_job]), \
                patch.object(backend._gridengine, "get_statuses", return_value=[missing]), \
                patch.object(backend._slurm, "cancel",
                             return_value=Job(id="101", status={"state": "canceled"})) as ok_cancel, \
                patch.object(backend._gridengine, "cancel") as ge_cancel2:
            cancelled = backend.cancel("101")
        ok_cancel.assert_called_once()
        ge_cancel2.assert_not_called()
        assert cancelled.status.meta_data["cancel_source"] == "slurm_fallback"

        update_spec = JobSpec(executable="true", attributes={"duration": 600})
        with patch("facilities.cell2026.registry.lookup", return_value="slurm"), \
                patch.object(backend._slurm, "update", return_value=slurm_job) as update:
            backend.update("101", update_spec)
        assert update.call_args.args == ("101", update_spec)

    def crux_pbs_shape_and_limits_are_enforced():
        """Keep the offline Crux port honest while no live access exists.

        Every fixture here is synthetic — invented rows in the shapes the
        ALCF/PBS documentation describes, never copied example output —
        and the "at request of scheduler" comment is the machine's own
        literal message text the parser must recognize.
        """
        if slug != "crux":
            return

        from facilities.crux.compute import _duration_seconds, _walltime

        def spec_with(**overrides):
            resources = overrides.pop("resources", {"node_count": 1})
            attributes = {"queue_name": "debug", "account": "Catalyst",
                          "duration": 600}
            attributes.update(overrides.pop("attributes", {}))
            return JobSpec(executable="hostname", resources=resources,
                           attributes=attributes)

        # Rendered shape: the documented ALCF directive block, whole-node
        # select, mandatory walltime/filesystems, forced -k doe, and the
        # job body starting from PBS_O_WORKDIR.
        with no_user_config(slug):
            shape = spec_with(resources={"node_count": 2})
            backend.apply_defaults(shape)
            script = backend.render_script(shape)
        assert "#!/bin/bash -l" in script
        assert "#PBS -q debug" in script
        assert "#PBS -A Catalyst" in script
        assert "#PBS -l select=2:system=crux" in script
        assert "#PBS -l place=scatter" in script
        assert "#PBS -l walltime=00:10:00" in script
        assert "#PBS -l filesystems=home:eagle" in script
        assert "#PBS -k doe" in script
        assert "#PBS -r y" not in script
        assert 'cd "$PBS_O_WORKDIR"' in script
        assert "hostname" in script
        # No mpiprocs/ompthreads: ALCF allocates whole nodes and rank
        # placement belongs to mpiexec, never to the select statement.
        assert "mpiprocs" not in script
        assert "ompthreads" not in script

        # Defaults: production queue filled when the caller is silent, a
        # reservation suppresses it, and filesystems default from facts.
        with no_user_config(slug):
            default = JobSpec(executable="true",
                              attributes={"account": "Catalyst", "duration": 600})
            backend.apply_defaults(default)
            assert default.attributes.queue_name == "workq-route"
            assert "#PBS -l filesystems=home:eagle" in backend.render_script(default)

            reserved = JobSpec(executable="true", attributes={
                "account": "Catalyst", "duration": 600,
                "reservation_id": "A123456"})
            backend.apply_defaults(reserved)
            assert reserved.attributes.queue_name == ""
            assert "#PBS -q A123456" in backend.render_script(reserved)

            preemptable = spec_with(attributes={"queue_name": "preemptable",
                                                "duration": "48:00:00"})
            backend.apply_defaults(preemptable)
            assert "#PBS -r y" in backend.render_script(preemptable)

            fs_only = spec_with(attributes={
                "custom_attributes": {"filesystems": "eagle"}})
            backend.apply_defaults(fs_only)
            assert "#PBS -l filesystems=eagle" in backend.render_script(fs_only)

        # A directive line is one line of options, so a value with a space
        # injects a second option without needing a newline — every
        # rendered field is checked against its real syntax instead.
        injections = [
            {"queue_name": "debug -l place=excl", "account": "Catalyst"},
            {"queue_name": "workq-route", "account": "Catalyst -l place=excl"},
            {"queue_name": "workq-route", "account": "grp; qdel 1"},
            {"queue_name": "no-such-queue", "account": "Catalyst"},
            {"queue_name": "workq-route", "account": "Catalyst",
             "reservation_id": "A123456"},                          # two destinations
            {"queue_name": "workq-route", "account": "Catalyst",
             "reservation_id": "A123456 -x"},                      # injectable reservation
            {"queue_name": "workq-route", "account": "Catalyst",
             "custom_attributes": {"filesystems": "home:eagle -l place=excl"}},
            {"queue_name": "workq-route", "account": "Catalyst",
             "custom_attributes": {"unknown": "value"}},           # dropped attribute
        ]
        for attrs in injections:
            bad = JobSpec(executable="true", resources={"node_count": 1},
                          attributes={"duration": 600, **attrs})
            try:
                backend.apply_defaults(bad)
                backend.validate_spec(bad)
            except ValueError:
                continue
            raise AssertionError(
                f"crux accepted an injectable or unmappable value: {attrs}")

        # Offline queue limits exist because of the routing-queue trap: a
        # job outside its queue's range is accepted and then dropped with
        # no error at all (see the compute module's docstring).
        limit_violations = [
            ("debug", {"node_count": 5}, 600),
            ("debug", {"node_count": 1}, "02:00:01"),
            ("debug", {"node_count": 1}, 240),          # below the 5-minute floor
            ("workq-route", {"node_count": 177}, 600),
            ("workq-route", {"node_count": 1}, "25:00:00"),
            ("preemptable", {"node_count": 11}, 600),
            ("preemptable", {"node_count": 1}, "73:00:00"),
            ("demand", {"node_count": 1}, "01:01:00"),
        ]
        for queue, resources, duration in limit_violations:
            bad = JobSpec(executable="true", resources=resources,
                          attributes={"queue_name": queue, "account": "Catalyst",
                                      "duration": duration})
            try:
                backend.validate_spec(bad)
            except ValueError:
                continue
            raise AssertionError(
                f"crux accepted an out-of-range request for {queue}: "
                f"resources={resources}, duration={duration}")

        # Machine facts Crux simply cannot satisfy.
        impossible = [
            {"gpus": 1},                                      # CPU-only system
            {"gpu_cores_per_process": 1},
            {"memory": 300 * 1024**3},                        # > 256 GiB node
            {"processes_per_node": 300},                      # > 256 hw threads
        ]
        for resources in impossible:
            bad = JobSpec(executable="true", resources=resources,
                          attributes={"queue_name": "workq-route",
                                      "account": "Catalyst", "duration": 600})
            try:
                backend.validate_spec(bad)
            except ValueError:
                continue
            raise AssertionError(
                f"crux accepted an unsatisfiable resource request: {resources}")

        bad = spec_with()
        bad.container = {"image": "$HOME/app.sif"}
        try:
            backend.validate_spec(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("crux accepted a container spec (no Apptainer yet)")

        bad = spec_with()
        bad.stdin_path = "/home/u/input.dat"
        try:
            backend.validate_spec(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("crux accepted an unmapped stdin_path")

        # Output/error paths: quoted so a space can't split the directive,
        # and $HOME-anchored because qsub runs from the jobs directory.
        spaced = spec_with()
        spaced.stdout_path = "results/run dir/job.out"
        spaced.stderr_path = "results/run dir/job.err"
        spaced_script = backend.render_script(spaced)
        assert "#PBS -o '../results/run dir/job.out'" in spaced_script, spaced_script
        assert "#PBS -e '../results/run dir/job.err'" in spaced_script, spaced_script
        absolute = spec_with()
        absolute.stdout_path = "/eagle/projects/Catalyst/logs/job.out"
        assert ("#PBS -o /eagle/projects/Catalyst/logs/job.out"
                in backend.render_script(absolute))
        # Explicit working directories are anchored the same way.
        workdir = spec_with()
        workdir.directory = "agent/work/run1"
        assert 'cd "$HOME"/agent/work/run1' in backend.render_script(workdir)

        # Duration helpers: the model accepts D-HH:MM:SS, PBS wants plain
        # HH:MM:SS with hours free to exceed 24.
        assert _duration_seconds("2-00:00:00") == 172800
        assert _duration_seconds("0:10:00") == 600
        assert _duration_seconds(300) == 300
        assert _walltime("2-00:00:00") == "48:00:00"
        assert _walltime(600) == "00:10:00"

        # Job record mapping from qstat -F JSON fields.
        finished = backend._job("7001.crux-pbs-0001.head.cm.crux.alcf.anl.gov", {
            "Job_Name": "demo-run", "job_state": "F", "queue": "workq",
            "Account_Name": "Catalyst", "Exit_status": 0,
        })
        assert finished.status.state.value == "completed"
        assert finished.status.exit_code == 0
        failed = backend._job("7002.crux-pbs-0001.head.cm.crux.alcf.anl.gov", {
            "job_state": "F", "Exit_status": 2, "comment": "not a preemption",
        })
        assert failed.status.state.value == "failed"
        assert failed.status.exit_code == 2
        preempted = backend._job("7003.crux-pbs-0001.head.cm.crux.alcf.anl.gov", {
            "job_state": "F", "Exit_status": 271,
            "comment": "Job to be deleted at request of Scheduler",
        })
        assert preempted.status.state.value == "failed"   # scheduler killed it, not the user
        assert "request of Scheduler" in preempted.status.message
        running = backend._job("7004.crux-pbs-0001.head.cm.crux.alcf.anl.gov", {
            "job_state": "R", "queue": "workq",
            "Variable_List": ("PBS_O_HOME=/home/u,PBS_O_QUEUE=workq-route,"
                              "PBS_O_WORKDIR=/home/u/agent/jobs"),
            "resources_used": {"walltime": "00:03:21"},
        })
        assert running.status.state.value == "active"
        assert running.status.meta_data["workdir"] == "/home/u/agent/jobs"
        assert running.status.meta_data["walltime_used"] == "00:03:21"
        assert backend._pbs_workdir("PBS_O_HOME=/home/u") == ""
        # Finished jobs carry no Variable_List at all (confirmed live); the
        # submission directory must come from Output_Path instead, and the
        # output/error destinations are surfaced as usable paths.
        historical = backend._job("7006.crux-pbs-0001.head.cm.crux.alcf.anl.gov", {
            "job_state": "F", "Exit_status": 0,
            "Output_Path": ("crux-uan-0001.head.cm.crux.alcf.anl.gov:"
                            "/home/u/agent/jobs/demo-run.o7006"),
            "Error_Path": ("crux-uan-0001.head.cm.crux.alcf.anl.gov:"
                           "/home/u/agent/jobs/demo-run.e7006"),
            "comment": "Job run at Mon Nov 02 at 11:29 on a vnode and finished",
        })
        assert historical.status.state.value == "completed"
        assert historical.status.meta_data["workdir"] == "/home/u/agent/jobs"
        assert (historical.status.meta_data["output_path"]
                == "/home/u/agent/jobs/demo-run.o7006")
        assert (historical.status.meta_data["error_path"]
                == "/home/u/agent/jobs/demo-run.e7006")
        assert backend._strip_host("crux-host:/home/u/x") == "/home/u/x"
        assert backend._strip_host("/home/u/x") == "/home/u/x"

        # JSON record parsing, including seq-only id matching.
        qstat_output = '''{
          "timestamp": 1790000000,
          "pbs_version": "2022.1",
          "pbs_server": "crux-pbs-0001.head.cm.crux.alcf.anl.gov",
          "Jobs": {
            "7004.crux-pbs-0001.head.cm.crux.alcf.anl.gov": {
              "Job_Name": "demo-run", "job_state": "R", "queue": "workq",
              "Variable_List": "PBS_O_WORKDIR=/home/u/agent/jobs"
            }
          }
        }'''
        records = backend._parse_qstat_json(qstat_output)
        assert list(records) == ["7004.crux-pbs-0001.head.cm.crux.alcf.anl.gov"]
        # A finished job's record keeps Output_Path (history drops
        # Variable_List) so workdir survives into history.
        historical_records = backend._parse_qstat_json(json.dumps({
            "Jobs": {"7006.crux-pbs-0001.head.cm.crux.alcf.anl.gov": {
                "job_state": "F", "Exit_status": 0,
                "Output_Path": ("crux-uan-0001.head.cm.crux.alcf.anl.gov:"
                                "/home/u/agent/jobs/demo-run.o7006"),
            }}
        }))
        assert backend._job(
            "7006.crux-pbs-0001.head.cm.crux.alcf.anl.gov",
            historical_records["7006.crux-pbs-0001.head.cm.crux.alcf.anl.gov"],
        ).status.meta_data["workdir"] == "/home/u/agent/jobs"

        with patch("facilities.crux.compute.run_command",
                   return_value=qstat_output) as command:
            found = backend.get_statuses(["7004"])[0]
        assert command.call_args.args[1] == "qstat -fx -F JSON 7004"
        assert found.id.endswith("crux.alcf.anl.gov")
        assert found.status.state.value == "active"

        # A missing id must explain the routing-queue trap, not just vanish.
        with patch("facilities.crux.compute.run_command",
                   side_effect=RuntimeError("qstat: Unknown Job Id 9999")):
            missing = backend.get_statuses(["9999"])[0]
        assert missing.status.state.value == "unknown"
        assert "node count and walltime" in missing.status.message

        # sbank parsing: ALCF's live layout adds a Suballocation column to
        # sbank's documented default, and both are handled by shape (the
        # start/end date pair), not by fixed position. Synthetic rows with
        # the real layouts; multi-word project names; a negative balance;
        # and a Totals section that must not become data.
        sbank_alcf_layout = '''
         Allocation  Suballocation  Start       End         Resource  Project      Jobs  Charged  Available Balance
         ----------  -------------  ----------  ----------  --------  ---------  ----  -------  -----------------
         7001        7011           2026-01-01  2026-12-31  crux      Catalyst      1,234  12,345.6  87,654.3
         7002        7012           2026-01-01  2026-12-31  crux      My Project      987  -1,234.5   1,234.5
         7003        7013           2026-01-01  2026-12-31  eagle     Catalyst         12      3.4       1.0

        Totals:
          Rows: 3
          Crux:
            Available Balance: 88,888.8 node hours
            Charged          : 13,580.1 node hours
            Jobs             : 2,221
        Date filters (UTC): none
        '''
        sbank_default_layout = '''
         Id         Start       End         Resource   Project          Jobs        Charged          Available Balance
         ---------  ----------  ----------  ---------  ---------------  ----------  ---------------  -----------------
         42         2026-01-01  2026-12-31  somehost   Defaultlayout        5         1.0               2.0
        '''
        with patch("facilities.crux.compute.run_command",
                   return_value=sbank_alcf_layout) as command:
            projects = backend.get_projects()
        assert command.call_args.args[1] == "sbank-list-allocations -r crux"
        assert [p["account"] for p in projects] == ["Catalyst", "My Project"]
        assert projects[0]["allocation_ids"] == ["7001"]    # eagle row filtered out
        assert projects[0]["jobs"] == 1234
        assert projects[0]["charged"] == 12345.6
        assert projects[1]["available_balance"] == 1234.5   # negative parsed, not rejected

        # sbank's documented default layout (no Suballocation column) is
        # the fallback shape — parsed, then resource-filtered like any other.
        with patch("facilities.crux.compute.run_command",
                   return_value=sbank_default_layout):
            assert backend._allocations() == []

        with patch("facilities.crux.compute.run_command",
                   return_value=sbank_alcf_layout):
            allocations = backend.get_project_allocations("My Project")
        assert allocations["charged_total"] == -1234.5
        assert allocations["allocations"][0]["end"] == "2026-12-31"
        assert allocations["allocations"][0]["suballocation_id"] == "7012"
        try:
            with patch("facilities.crux.compute.run_command",
                       return_value=sbank_alcf_layout):
                backend.get_project_allocations("NotAProject")
        except ValueError:
            pass
        else:
            raise AssertionError("crux invented allocations for an unknown project")

        # An empty sbank result is never a valid "no projects" answer: an
        # ALCF account always belongs to a project, so say what to check.
        with patch("facilities.crux.compute.run_command", return_value=""):
            try:
                backend.get_projects()
            except ValueError as exc:
                assert "expired" in str(exc) or "sbank" in str(exc)
            else:
                raise AssertionError(
                    "crux returned an empty project list without explaining it")

        # qstat -Q table -> queue rows (header-driven parsing).
        queue_output = '''
        Queue          Max  Tot Ena Str Que Run Hld Wat Trn Ext Type
        -------------- ---- ---- --- --- ---- ---- ---- ---- ---- ---- ----
        debug            4    2 yes yes   1   1   0   0   0   0 Exe*
        workq-route    100   42 yes yes  12  30   0   0   0   0 Rou*
        '''
        # pbsnodes summary -> machine-wide node counts, including a
        # combined state (offline,resv-exclusive) that must not be skipped.
        nodes_output = '''
                                                                        mem       ncpus   nmics   ngpus
        vnode           state           njobs   run   susp      f/t        f/t     f/t     f/t   jobs
        --------------- --------------- ------ ----- ------ ------------ ------- ------- ------- -------
        x1000c0s0b1n0   free                0     0      0  252gb/252gb   256/256     0/0     0/0 --
        x1000c0s0b1n1   job-exclusive       1     1      0  252gb/252gb   256/256     0/0     0/0 7004
        x1000c0s0b1n2   offline             0     0      0  252gb/252gb   256/256     0/0     0/0 --
        x1000c0s0b1n3   offline,resv-exclusive 0  0      0  252gb/252gb   256/256     0/0     0/0 --
        '''
        with patch("facilities.crux.compute.run_command",
                   side_effect=[queue_output, nodes_output]):
            resources_list = backend.get_live_resources()
        by_name = {row["partition"]: row for row in resources_list}
        assert set(by_name) == {"debug", "workq-route"}
        # live qstat -Q truncates the Type column ("Rou*"); the star is stripped
        assert by_name["workq-route"]["queue_type"] == "Rou"
        assert by_name["workq-route"]["running_jobs"] == 30
        assert by_name["debug"]["queued_jobs"] == 1
        nodes = by_name["debug"]["nodes"]
        assert nodes == {"allocated": 1, "idle": 1, "other": 2, "total": 4}

        # Down-node listing.
        down_output = ("x1000c0s0b1n2       offline    synthetic fixture reason\n")
        with patch("facilities.crux.compute.run_command",
                   return_value=down_output):
            drained = backend.get_drained_nodes()
        assert drained == [{"nodes": "x1000c0s0b1n2", "state": "offline",
                             "reason": "synthetic fixture reason"}]

        # Submission: script written under the jobs dir, qsub run from there
        # so streamed output lands beside the script, id parsed off the
        # server-qualified form.
        submitted = spec_with()
        with patch("facilities.crux.compute.write_remote_file",
                   return_value="/home/u/agent/jobs/crux-shape-20261101-000000.pbs") \
                as write, patch("facilities.crux.compute.run_command",
                   return_value="7005.crux-pbs-0001.head.cm.crux.alcf.anl.gov\n") \
                as command:
            result = backend.submit(submitted)
        assert result["job_id"] == "7005.crux-pbs-0001.head.cm.crux.alcf.anl.gov"
        assert write.call_args.args[1].startswith("agent/jobs/")
        qsub_command = command.call_args.args[1]
        assert "chmod 600" in qsub_command
        assert 'cd "$(dirname' in qsub_command
        assert qsub_command.endswith("qsub /home/u/agent/jobs/crux-shape-20261101-000000.pbs")

        # Recent statuses: qselect (with the ctime cutoff) then batched
        # qstat for full records.
        with patch("facilities.crux.compute.run_command",
                   side_effect=[
                       "7004.crux-pbs-0001.head.cm.crux.alcf.anl.gov\n"
                       "7005.crux-pbs-0001.head.cm.crux.alcf.anl.gov\n",
                       qstat_output,
                   ]) as command:
            recent = backend.get_recent_statuses("now-2days")
        assert command.call_args_list[0].args[1].startswith("qselect -x -u $USER -tc.gt.")
        assert "qstat -fx -F JSON" in command.call_args_list[1].args[1]
        assert [job.id for job in recent] == ["7004.crux-pbs-0001.head.cm.crux.alcf.anl.gov"]

        # Update: qalter for mutable attributes, qmove for the queue, both
        # only for fields the caller actually set.
        updated = Job(id="7004", status={"state": "active"})
        walltime_spec = JobSpec(executable="true", attributes={"duration": 300})
        with patch("facilities.crux.compute.run_command") as command, \
                patch.object(backend, "get_statuses", return_value=[updated]):
            backend.update("7004", walltime_spec)
        assert command.call_args.args[1] == "qalter -l walltime=00:05:00 7004"

        move_spec = JobSpec(executable="true",
                            attributes={"queue_name": "debug"})
        with patch("facilities.crux.compute.run_command") as command, \
                patch.object(backend, "get_statuses", return_value=[updated]):
            backend.update("7004", move_spec)
        assert command.call_args.args[1] == "qmove debug 7004"

        everything_spec = JobSpec(executable="true", name="renamed",
                                  stdout_path="logs/out.txt",
                                  attributes={"duration": 300, "account": "Catalyst"},
                                  resources={"node_count": 2})
        with patch("facilities.crux.compute.run_command") as command, \
                patch.object(backend, "get_statuses", return_value=[updated]):
            backend.update("7004", everything_spec)
        rendered = command.call_args.args[1]
        assert rendered.startswith("qalter ")
        assert "-N renamed" in rendered
        assert "-l walltime=00:05:00" in rendered
        assert "-A Catalyst" in rendered
        assert "-l select=2:system=crux" in rendered
        assert "-o logs/out.txt" in rendered

        conflicting = JobSpec(executable="true", attributes={
            "queue_name": "debug", "reservation_id": "A123456"})
        try:
            backend.update("7004", conflicting)
        except ValueError:
            pass
        else:
            raise AssertionError("crux updated toward two different queues at once")

        empty_spec = JobSpec(executable="true", environment={"A": "b"})
        try:
            backend.update("7004", empty_spec)
        except ValueError as exc:
            assert "Nothing updatable" in str(exc)
            assert "environment" in str(exc)
        else:
            raise AssertionError("crux applied an update with nothing updatable set")

    def setup_directions_are_actionable():
        """What a user gets when they say "I want to use this machine" and
        nothing is configured. It has to stand on its own: the agent may
        not be able to open any other file, so the directions must name the
        config file, show what to put in it, and cover this machine's own
        prerequisites."""
        assert fac.setup_help.strip(), (
            "no setup_help registered — an unconfigured user would get generic "
            "directions with nothing machine-specific (how to get a key "
            "registered, the real login hostname, any required account)"
        )
        assert fac.config_example, "no config_example registered"
        assert "ssh" in fac.config_example, "config_example has no ssh section"

        with no_user_config(slug):
            text = config.setup_instructions(slug)
            expected_path = str(config.config_path(slug))
        assert expected_path in text, "directions omit the config file path"
        assert f"{slug}-configuring" in text, "directions don't name the configuring skill"
        assert "hpc-doctor" in text, "directions don't say how to verify"
        assert fac.setup_help.splitlines()[0] in text, "setup_help didn't reach the directions"
        # The example must be valid, copy-pasteable JSON.
        import json as _json
        _json.loads(_json.dumps(fac.config_example))

    def unconfigured_catalog_data_is_readable():
        """Bundled data remains readable so setup directions can be built."""
        with no_user_config(slug):
            assert config.load_facts(slug), "get_facility would fail unconfigured"
            assert config.docs_source(slug).exists(), "guide unreadable unconfigured"
            assert fac.display_name and fac.description, "get_facilities would be degraded"

    def unconfigured_tools_still_answer():
        """The never-fail invariant, at tool granularity: every tool that
        does not need the cluster must keep working with no config at all,
        so an agent can orient the user *before* setup.

        Checked through the MCP entry points, not the helpers underneath —
        the regression this guards against was a decorator applied to the
        whole tool surface, which the helpers would not have noticed. An
        earlier version of this file asserted only on config.load_facts()
        and so passed while get_facility itself was raising.
        """
        from hpc_mcp.hpc_server import get_facility, render_job_script
        from hpc_agent_core import docs_server
        from hpc_agent_core.models import JobSpec

        with no_user_config(slug):
            facts = get_facility(slug)
            assert facts, "get_facility returned nothing unconfigured"

            # Packaged index: searching a guide is how an agent answers
            # "what partitions does this machine have" before SSH exists.
            index = docs_server._index(slug)
            hits = index.search("storage quota partition", top_k=2)
            assert hits, f"{slug}: docs search returned nothing unconfigured"
            sections = index.chunks
            assert sections, f"{slug}: no doc sections unconfigured"

            # Local-only rendering. Supplying an account keeps facilities
            # that mandate one on the happy path; the point is that nothing
            # here reaches for a connection.
            spec = JobSpec.model_validate({
                "name": "offline-render", "executable": "hostname",
                "attributes": {"account": "conformance-probe",
                               "duration": 600},
            })
            try:
                script = render_job_script(slug, spec)
            except ValueError:
                pass          # a facility may reject this minimal spec; fine
            else:
                assert script.strip(), f"{slug}: empty script unconfigured"

    def unconfigured_account_query_returns_setup():
        """Capability errors must not mask the cold-start configuration path."""
        from hpc_mcp.hpc_server import get_projects
        with no_user_config(slug):
            try:
                get_projects(slug)
            except RuntimeError as exc:
                text = str(exc)
                assert "not configured yet" in text
                assert f"{slug}-configuring" in text
            else:
                raise AssertionError(
                    "unconfigured get_projects did not return setup instructions"
                )

    print(f"\n=== {slug} ({fac.display_name}) ===")
    check("registration is complete", registration_is_complete)
    check("facts JSON loads", facts_load)
    check("backend is bound to this facility", backend_knows_its_facility)
    check("setup directions are machine-specific and actionable", setup_directions_are_actionable)
    check("bundled catalog data remains readable without user config",
          unconfigured_catalog_data_is_readable)
    check("no-config tools still answer (never-fail invariant)",
          unconfigured_tools_still_answer)
    check("unconfigured account query returns setup directions first",
          unconfigured_account_query_returns_setup)
    check("apply_defaults is idempotent", apply_defaults_is_idempotent)
    check("apply_defaults never overwrites caller values", explicit_values_survive_defaults)
    check("defaults -> validate -> render, or an actionable error", defaults_then_validate_then_render)
    check("same with no user config (a brand-new user)", defaults_then_validate_then_render_unconfigured)
    check("a defaulted queue reaches the script", rendered_script_has_a_queue)
    if slug == "rikyu":
        check("deterministic machine resource limits are enforced",
              machine_resource_limits_are_enforced)
    if slug == "miyabi":
        check("PBS shape and dated machine limits are enforced",
              miyabi_pbs_shape_and_limits_are_enforced)
    if slug == "octopus":
        check("dual-vendor defaults and machine limits are enforced",
              octopus_dual_vendor_defaults_and_limits_are_enforced)
    if slug == "tsubame":
        check("resource-type Grid Engine dialect and trial limits are enforced",
              tsubame_resource_types_and_trial_limits_are_enforced)
    if slug == "irene":
        check("Bridge shape, parsers, and machine limits are enforced",
              irene_bridge_shape_and_limits_are_enforced)
    if slug == "cell2026":
        check("dual-scheduler rendering, validation, and routing are enforced",
              cell2026_dual_scheduler_routing_is_enforced)
    if slug == "crux":
        check("ALCF PBS shape, limits, parsers, and sbank accounting are enforced",
              crux_pbs_shape_and_limits_are_enforced)


# --- repo-wide checks ----------------------------------------------------

def check_repo() -> None:
    def tests_import_this_checkout():
        """The packages under test, and a child Python the tests start,
        load from the checkout this file is in, not from wherever the
        venv's editable install points (a worktree would otherwise test
        the main checkout's code)."""
        import hpc_codes
        import hyogo
        import yagura
        root = pathlib.Path(__file__).resolve().parent.parent
        for mod in (hpc_codes, hyogo, yagura, config):
            assert pathlib.Path(mod.__file__).resolve().is_relative_to(root), (mod.__name__, mod.__file__)
        with tempfile.TemporaryDirectory() as elsewhere:   # not the repo: no '' on sys.path helps
            out = subprocess.run([sys.executable, "-c", "import hyogo; print(hyogo.__file__)"],
                                 cwd=elsewhere, capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr[-1000:]
        assert pathlib.Path(out.stdout.strip()).resolve().is_relative_to(root), out.stdout

    check("tests import this checkout", tests_import_this_checkout)

    def slurm_waiting_jobs_report_expected_start():
        """G31: a waiting Slurm job carries squeue's %S estimate as
        meta_data["expected_start"]; N/A / Unknown never read as a time."""
        from hpc_agent_core.compute import slurm
        from hpc_agent_core.models import JobState
        sacct = ("11|a|mpc|PENDING|00:00:00|Unknown|Unknown|0:0|None assigned|/w\n"
                 "12|b|mpc|PENDING|00:00:00|Unknown|Unknown|0:0|None assigned|/w\n"
                 "13|c|mpc|RUNNING|00:01:00|2026-11-06T10:00:00|Unknown|0:0|mpc0001|/w\n")
        squeue = "11|(Priority)|2026-11-07T03:12:00\n12|(Resources)|N/A\n"
        backend = slurm.SlurmBackend("test-slurm", has_accounting=True)
        with patch("hpc_agent_core.compute.slurm.run_command", side_effect=[sacct, squeue]):
            jobs = {j.id: j for j in backend.get_statuses(["11", "12", "13"])}
        assert jobs["11"].status.message == "(Priority)"
        assert jobs["11"].status.meta_data["expected_start"] == "2026-11-07T03:12:00"
        assert "expected_start" not in jobs["12"].status.meta_data, jobs["12"].status.meta_data
        assert jobs["13"].status.state == JobState.ACTIVE
        assert "expected_start" not in jobs["13"].status.meta_data
        # no-accounting path: the estimate comes in the primary squeue query
        live = slurm._parse_squeue("21|PENDING|(Priority)|mpc|/w|2026-11-07T03:12:00\n"
                                   "22|PENDING|(Priority)|mpc|/w|Unknown\n"
                                   "23|RUNNING|mpc0002|mpc|/w|2026-11-06T10:00:00\n"
                                   "24|PENDING|(Priority)|mpc|/w\n")   # old 5-field format
        meta = {j.id: j.status.meta_data for j in live}
        assert meta["21"]["expected_start"] == "2026-11-07T03:12:00"
        assert all("expected_start" not in meta[i] for i in ("22", "23", "24")), meta

    check("Slurm waiting jobs report expected_start (G31)", slurm_waiting_jobs_report_expected_start)

    print("\n=== repo-wide ===")

    def facilities_exist():
        assert config.list_facilities(), "no facilities registered"

    def unknown_facility_errors_clearly():
        for label, fn in (("config", config.get_facility), ("registry", get_backend)):
            try:
                fn("definitely-not-a-facility")
            except ValueError as e:
                assert "Unknown facility" in str(e), f"{label}: {e}"
                assert "Valid facilities:" in str(e), f"{label}: no slug list in {e}"
            else:
                raise AssertionError(f"{label}: expected ValueError")

    def slugs_are_url_and_identifier_safe():
        for fac in config.list_facilities():
            assert fac.slug == fac.slug.lower(), f"{fac.slug}: not lowercase"
            assert " " not in fac.slug, f"{fac.slug}: contains a space"
            assert fac.slug.replace("-", "").isalnum(), f"{fac.slug}: unexpected characters"

    def unreachable_detection_discriminates():
        """A scheduler error must not be mistaken for an SSH failure.

        Regression guard: a looser version of this check matched a bare
        "permission denied", so Slurm's own "Access/permission denied for
        job 9041823" came back buried in a wall of SSH setup directions
        instead of as the clear scheduler error it is. Caught live while
        testing update_job(hold=True) on HBW2.
        """
        from hpc_agent_core.middleware import _looks_unreachable as unreachable
        scheduler_and_command_errors = [
            ("Access/permission denied for job 9041823", None),
            ("slurm_suspend error:Job has already finished", None),
            ("ls: cannot access '/nope': No such file or directory", None),
            ("sbatch: error: Invalid account or account/partition combination", None),
            ("some remote failure", 2),
        ]
        for text, rc in scheduler_and_command_errors:
            assert not unreachable(text, rc), f"misread as an SSH failure: {text!r}"
        real_ssh_failures = [
            ("user@host: Permission denied (publickey).", None),
            ("ssh: connect to host x port 22: Connection refused", None),
            ("ssh: Could not resolve hostname bad.invalid", None),
            ("", 255),
        ]
        for text, rc in real_ssh_failures:
            assert unreachable(text, rc), f"missed a real SSH failure: {text!r} rc={rc}"

    def fs_rm_refuses_dangerous_paths():
        """fs_rm is the one irreversible tool here — these filesystems have
        no trash. Deleting a home directory or a filesystem root is far more
        likely to be a mistake (an unset variable, a stray default, a glob
        that expanded to nothing) than an intent, so those must be refused
        before any command is built, not just discouraged in a docstring.

        The guard is the code under test, so it can't also be the test's
        safety net: a regression must fail here, never reach a shell. The
        command runner is replaced with one that raises, and the host is a
        non-local, unresolvable name, so even a missed patch goes to a
        failing SSH rather than `rm -r -- ~` on the machine running this."""
        from hpc_mcp.hpc_server import fs_rm

        def must_not_run(facility, command, *args, **kwargs):
            raise AssertionError(
                f"fs_rm built and tried to run {command!r} instead of refusing")

        with patch.dict(os.environ, {"CELL2026_HOST": "fs-rm-test.invalid"}), \
                patch("hpc_mcp.hpc_server.run_command", side_effect=must_not_run):
            for bad in ["", " ", ".", "..", "/", "~", "~/", "*", "$HOME", ".//"]:
                try:
                    fs_rm("cell2026", bad)
                except ValueError as e:
                    assert "Refusing to delete" in str(e), (
                        f"{bad!r} was not refused before a command was built: {e}")
                else:
                    raise AssertionError(f"fs_rm did not refuse {bad!r}")

    def generated_skills_are_wellformed():
        """Every generated SKILL.md needs YAML frontmatter whose name
        matches its directory, or the client cannot discover it.

        Caught a real one: Fugaku-Agent's fugaku-build skill shipped with
        no frontmatter at all — the only skill in that repo missing it, so
        it had no name or description to be found by.
        """
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        skills = sorted((root / "plugins").glob("hpc-*/skills/*/SKILL.md"))
        assert skills, "no generated skills found"
        for path in skills:
            text = path.read_text()
            assert text.startswith("---\n"), f"{path.parent.name}: no YAML frontmatter"
            front = text.split("---", 2)[1]
            assert f"name: {path.parent.name}\n" in front, (
                f"{path.parent.name}: frontmatter name does not match its directory")
            assert "description:" in front, f"{path.parent.name}: no description"

        remote_skills = sorted((root / "plugins").glob(
            "hpc-*/skills/*-remote-command/SKILL.md"))
        assert len(remote_skills) == len(config.list_facilities()), (
            "every facility needs a generated remote-command skill"
        )
        for path in remote_skills:
            text = path.read_text()
            assert "May I run these" in text, f"{path.parent.name}: no permission example"
            assert "Prefer multiple focused calls" in text, (
                f"{path.parent.name}: no short-command guidance"
            )
            assert "Changed state:" in text, f"{path.parent.name}: no execution recap example"

    def facility_skill_packs_are_isolated_and_discoverable():
        """The same base/pack split is valid for all three plugin formats."""
        import json
        root = pathlib.Path(__file__).resolve().parent.parent
        facilities = config.list_facilities()
        base_skills = sorted(p.name for p in (root / "plugins" / "hpc" / "skills").iterdir()
                             if p.is_dir())
        assert base_skills == ["hpc-facilities"], (
            "the base plugin must not preload facility workflow skills")

        marketplace = json.loads((root / ".agents" / "plugins" / "marketplace.json").read_text())
        names = [entry["name"] for entry in marketplace["plugins"]]
        assert names == ["hpc", *(f"hpc-{fac.slug}" for fac in facilities)]
        claude_marketplace = json.loads(
            (root / ".claude-plugin" / "marketplace.json").read_text())
        assert [entry["name"] for entry in claude_marketplace["plugins"]] == names

        base_portable = json.loads((root / "plugins" / "hpc" / "plugin.json").read_text())
        assert base_portable["$schema"].endswith("plugin.schema.json")
        portable_mcp = json.loads((root / "plugins" / "hpc" / "mcp.json").read_text())
        assert portable_mcp["$schema"].endswith("mcp.schema.json")
        assert set(portable_mcp["mcpServers"]) == {"hpc", "hpc-docs", "hpc-codes", "yagura", "hyogo"}
        # .mcp.json is hand-maintained (the harnesses load it); it must name
        # the same servers, or one harness silently lacks a server
        harness_mcp = json.loads((root / "plugins" / "hpc" / ".mcp.json").read_text())
        assert set(harness_mcp["mcpServers"]) == set(portable_mcp["mcpServers"])
        for fac in facilities:
            plugin = root / "plugins" / f"hpc-{fac.slug}"
            manifest = json.loads((plugin / ".codex-plugin" / "plugin.json").read_text())
            assert manifest["name"] == f"hpc-{fac.slug}"
            assert "mcpServers" not in manifest, f"{fac.slug} pack must not duplicate MCP"
            claude = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())
            portable = json.loads((plugin / "plugin.json").read_text())
            assert claude["name"] == manifest["name"] == portable["name"]
            assert portable["$schema"].endswith("plugin.schema.json")
            assert list((plugin / "skills").glob(f"{fac.slug}-*/SKILL.md")), \
                f"{fac.slug} pack has no generated skills"

    def readme_requires_explicit_facility_selection_for_manual_install():
        root = pathlib.Path(__file__).resolve().parent.parent
        readme = (root / "README.md").read_text()
        normalized = " ".join(readme.split())
        assert "before installing any facility skills, ask the user" in normalized
        assert "Do not install all facility packs by default" in normalized
        assert "plugins/hpc-<selected-slug>/skills/" in readme

    def job_name_cannot_inject_into_scripts():
        """spec.name is rendered unquoted into every scheduler's directive
        line and used to build the job script's filename, so it is a real
        injection boundary — a newline puts executable lines into a script
        the scheduler runs, and a slash writes the script outside the jobs
        directory. Both were demonstrated against this code. Guarded once in
        the model so it holds for schedulers added later.
        """
        from hpc_agent_core.models import JobSpec
        for bad in ["ok\nrm -rf /", "../../../tmp/pwned", "a b",
                    'x"; rm -rf ~; #', "", "n" * 200, "foo$(id)", "a|b"]:
            try:
                JobSpec.model_validate({"name": bad, "executable": "x"})
            except Exception:
                continue
            raise AssertionError(f"job name accepted but should be rejected: {bad!r}")
        for ok in ["agent-job", "hub-smoke", "train_vit.v2", "probe360"]:
            JobSpec.model_validate({"name": ok, "executable": "x"})

        # Same primitive, every other field rendered into a directive line.
        for label, extra in [
            ("queue_name", {"attributes": {"queue_name": "gpu\nrm -rf /"}}),
            ("account", {"attributes": {"account": "a\n#SBATCH --uid=0"}}),
            ("reservation_id", {"attributes": {"reservation_id": "r\nevil"}}),
            ("custom key", {"attributes": {"custom_attributes": {"k\nevil": "v"}}}),
            ("custom value", {"attributes": {"custom_attributes": {"k": "v\nevil"}}}),
            ("directory", {"directory": "/tmp\nevil"}),
            ("stdout_path", {"stdout_path": "o\nevil"}),
        ]:
            spec = {"name": "t", "executable": "x", **extra}
            try:
                JobSpec.model_validate(spec)
            except Exception:
                continue
            raise AssertionError(f"control character accepted in {label}")

        # duration is both a directive-line field on every scheduler and a
        # login-node shell argument for Fugaku's pjalter update path.
        for bad in ["00:01:00\nrm -rf /", '00:01:00"; touch /tmp/pwned; #',
                    "not-a-duration", "00:60:00", True, 0, -1]:
            try:
                JobSpec.model_validate({
                    "name": "t", "executable": "x",
                    "attributes": {"duration": bad},
                })
            except Exception:
                continue
            raise AssertionError(f"unsafe/invalid duration accepted: {bad!r}")

    def resource_quantities_are_positive():
        invalid = [
            {"node_count": 0}, {"node_count": -1},
            {"process_count": 0}, {"process_count": -1},
            {"processes_per_node": 0}, {"cpu_cores_per_process": 0},
            {"gpu_cores_per_process": 0}, {"gpus": -1}, {"memory": 0},
        ]
        invalid += [
            {field: True} for field in (
                "node_count", "process_count", "processes_per_node",
                "cpu_cores_per_process", "gpu_cores_per_process", "gpus", "memory",
            )
        ]
        for resources in invalid:
            try:
                JobSpec.model_validate({"executable": "x", "resources": resources})
            except Exception:
                continue
            raise AssertionError(f"invalid resource quantity accepted: {resources!r}")

    def process_count_is_an_alternative_to_ppn():
        from hpc_agent_core.compute.slurm import SlurmBackend

        backend = SlurmBackend("test", nodes_always_explicit=True)
        total_only = JobSpec(
            executable="hostname", resources={"process_count": 8},
            attributes={"queue_name": "test"},
        )
        script = backend.render_script(total_only)
        assert "#SBATCH --ntasks=8" in script
        assert "#SBATCH --ntasks-per-node" not in script, script

        both = JobSpec(
            executable="hostname",
            resources={"process_count": 8, "processes_per_node": 4},
            attributes={"queue_name": "test"},
        )
        script = backend.render_script(both)
        assert "#SBATCH --ntasks=8" in script
        assert "#SBATCH --ntasks-per-node=4" in script

    def home_shorthand_is_normalized_for_scheduler_paths():
        """Scheduler directives do not expand shell variables themselves.

        The shared submitter starts in $HOME, so every supported home form
        must become a relative path before it reaches a Slurm/GE directive.
        Test both the helper and every currently supported directive path.
        """
        from hpc_agent_core.compute.gridengine import GridEngineBackend
        from hpc_agent_core.compute.slurm import SlurmBackend
        from hpc_agent_core.middleware import norm_path

        expected = {
            "~": ".",
            "~/agent/work": "agent/work",
            "$HOME": ".",
            "$HOME/agent/work": "agent/work",
            "${HOME}/agent/work": "agent/work",
        }
        for source, normalized in expected.items():
            assert norm_path(source) == normalized

        spec = JobSpec(
            executable="true",
            directory="$HOME/agent/work/run",
            stdin_path="${HOME}/inputs/in.dat",
            stdout_path="~/outputs/out.log",
            stderr_path="$HOME/outputs/err.log",
            attributes={"queue_name": "test"},
        )
        slurm = SlurmBackend("test", nodes_always_explicit=True)
        slurm_script = slurm.render_script(spec)
        assert "#SBATCH --chdir=agent/work/run" in slurm_script
        assert "#SBATCH --input=inputs/in.dat" in slurm_script
        assert "#SBATCH --output=outputs/out.log" in slurm_script
        assert "#SBATCH --error=outputs/err.log" in slurm_script

        ge = GridEngineBackend("test")
        ge_script = ge.render_script(spec)
        assert "#$ -wd agent/work/run" in ge_script
        assert "#$ -o outputs/out.log" in ge_script
        assert "#$ -e outputs/err.log" in ge_script

    def fugaku_update_quotes_duration_as_one_argument():
        from facilities.fugaku.compute import PJMBackend

        backend = PJMBackend("fugaku")
        unsafe = '01:02:03"; touch /tmp/pwned; #'
        # Bypass the generic validator deliberately to verify the backend's
        # independent defense in depth. Normal MCP input cannot construct
        # this model after the duration fix above.
        spec = JobSpec.model_construct(
            executable="unused",
            resources=ResourceSpec(),
            attributes=JobAttributes.model_construct(duration=unsafe),
        )
        commands = []

        def record(_facility, command):
            commands.append(command)
            return ""

        with patch("facilities.fugaku.compute.run_command", record), \
                patch.object(backend, "get_statuses", return_value=[]):
            try:
                backend.update("123", spec)
            except ValueError:
                pass  # expected: the mocked status lookup finds no job
        assert len(commands) == 1, commands
        assert shlex.split(commands[0]) == ["pjalter", "-L", f"elapse={unsafe}", "123"]

    def fugaku_pjm_accounting_exposes_groups_and_points():
        """Fugaku's IRI accounting shape from `id -Gn` + `accountj_pt -c`.

        Fixture strings are the exact formats observed live on 2026-08-30:
        the `id` group list and one GROUP_POINT CSV row.
        """
        from facilities.fugaku.compute import PJMBackend

        backend = PJMBackend("fugaku")
        pt_csv = (
            'INFO_H,COLLECT_DATE\n'
            '"INFO","2026-08-30 22:08:01"\n'
            'GROUP_POINT_H,GROUP,POINT,LIMIT(N),USAGE(N),AVAILABLE(N)\n'
            '"GROUP_POINT","ra000009","1110550","466431087","291635579","174795508"\n'
        )

        def fake(_facility, command):
            if command == "id -Gn":
                return "fugaku ra000009 ra250029 hp250291 trial\n"
            if " trial " in command:
                raise RuntimeError("no GROUP_POINT row for trial")
            return pt_csv

        with patch("facilities.fugaku.compute.run_command", fake):
            rows = backend.get_projects()
        accounts = [row["account"] for row in rows]
        assert accounts == ["hp250291", "ra000009", "ra250029", "trial"], accounts
        assert "fugaku" not in accounts  # shared group cannot submit
        assert rows[0]["points"]["point_total"] == 1110550
        assert "f-pt" in rows[0]["resource_groups"]
        assert rows[-1]["resource_groups"] == [
            "spot-small", "spot-large", "spot-int", "spot-middle"]
        assert "points" not in rows[-1]
        for row in rows:
            assert row["qos"] == []

        with patch("facilities.fugaku.compute.run_command",
                   return_value=pt_csv):
            alloc = backend.get_project_allocations("ra000009")
        assert alloc == {
            "project_id": "ra000009", "point_total": 1110550,
            "limit_n": 466431087, "usage_n": 291635579,
            "available_n": 174795508,
        }

        with patch("facilities.fugaku.compute.run_command",
                   side_effect=RuntimeError("connection failed")):
            try:
                backend.get_project_allocations("trial")
            except RuntimeError as exc:
                assert "connection failed" in str(exc)
            else:
                raise AssertionError(
                    "a failed read was masked as 'project has no points'")

    def docs_embedding_client_is_not_cached():
        from hpc_agent_core.docs_server import _index

        index = _index("rikyu")
        assert index._embed_client is None, (
            "the cached docs index captured an embedding client/API key"
        )
        token = object()
        # search() only reaches the vector path when embeddings are loaded.
        # A facility may legitimately ship BM25-only (no embeddings.npy, e.g.
        # after a guide edit made while the embedding endpoint was down), so
        # stand in a matrix rather than depending on what happens to be
        # committed — the invariant under test is that the client is passed
        # per call and never cached on the index.
        import numpy as _np
        with patch.object(index, "_embeddings",
                          _np.zeros((len(index.chunks), 4), dtype="float32")), \
                patch.object(index, "_vector_search", return_value=[]) as vector:
            index.search("anything", embed_client=token)
        vector.assert_called_once_with("anything", 5, token)

    def ssh_multiplexing_detection_is_advisory_and_uses_resolved_config():
        """Doctor must inspect local OpenSSH config without connecting."""
        from hpc_agent_core.doctor import check_ssh_multiplexing

        resolved = subprocess.CompletedProcess(
            ["ssh", "-G", "example"], 0,
            "controlmaster auto\ncontrolpath /tmp/ssh-%C\ncontrolpersist 30m\n",
            "",
        )
        active = subprocess.CompletedProcess(
            ["ssh", "-O", "check", "example"], 0, "", "Master running")
        with patch("hpc_agent_core.doctor.config.ssh_host", return_value="example"), \
                patch("hpc_agent_core.doctor.subprocess.run",
                      side_effect=[resolved, active]) as run:
            assert check_ssh_multiplexing("rikyu")
        assert run.call_args_list[0].args[0] == ["ssh", "-G", "example"]
        assert run.call_args_list[1].args[0] == ["ssh", "-O", "check", "example"]

        disabled = subprocess.CompletedProcess(
            ["ssh", "-G", "example"], 0, "controlmaster no\n", "",
        )
        with patch("hpc_agent_core.doctor.config.ssh_host", return_value="example"), \
                patch("hpc_agent_core.doctor.subprocess.run",
                      return_value=disabled) as run:
            assert check_ssh_multiplexing("rikyu")
        assert run.call_count == 1, "ControlMaster no must skip socket probing"

    def a_broken_facility_cannot_deny_the_server():
        """One facility failing to import must not take down the others.

        The hub sharpened this: a single process now serves every facility,
        so an exception in one facility.py would otherwise deny every other
        facility to every user. Verified by importing a deliberately broken
        module the same way hpc_mcp does, and asserting the healthy ones
        survive and the failure is recorded rather than swallowed.
        """
        import importlib.util
        import tempfile
        import hpc_mcp

        healthy = {f.slug for f in config.list_facilities()}
        assert healthy, "no healthy facilities to protect"

        with tempfile.TemporaryDirectory() as tmp:
            mod = pathlib.Path(tmp) / "broken_facility.py"
            mod.write_text("raise RuntimeError('deliberately broken')\n")
            spec = importlib.util.spec_from_file_location("broken_facility", mod)
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as exc:
                recorded = f"{type(exc).__name__}: {exc}"
            else:
                raise AssertionError("the broken module did not raise")

        assert "RuntimeError" in recorded, recorded
        assert {f.slug for f in config.list_facilities()} == healthy, \
            "a failed facility import disturbed the healthy registry"
        assert isinstance(hpc_mcp.FAILED_FACILITIES, dict), \
            "hpc_mcp must expose FAILED_FACILITIES so a skipped facility is never silent"

    def facility_data_is_declared_as_package_data():
        """Every file a facility reads at runtime must match a
        package-data glob in pyproject.toml.

        These are read via Path(__file__).parent/"data", which works
        perfectly in an editable install and silently ships nothing in a
        wheel unless declared. That is the failure this repo's own
        PORTING.md warns about, and it was real here: the first built wheel
        contained zero guides, zero docs indexes and zero facts JSON, so the
        documented `uv tool run --from git+...` install would have produced
        a server where get_facility and search_docs failed on every
        facility, while local testing looked fine.
        """
        import fnmatch
        import tomllib
        root = pathlib.Path(__file__).resolve().parent.parent
        with open(root / "pyproject.toml", "rb") as fh:
            globs = tomllib.load(fh)["tool"]["setuptools"]["package-data"]["*"]

        for fac in config.list_facilities():
            wanted = [fac.data_dir / fac.docs_filename,
                      fac.data_dir / fac.facts_filename,
                      fac.data_dir / "docs_index" / "chunks.json"]
            for path in wanted:
                if not path.exists():
                    continue
                rel = path.relative_to(fac.data_dir.parent).as_posix()
                assert any(fnmatch.fnmatch(rel, g) for g in globs), (
                    f"{fac.slug}: {rel} matches no package-data glob in "
                    f"pyproject.toml, so it would not ship in a wheel")

    def every_facility_has_skill_notes():
        """A facility with no notes for a workflow still renders (a stub is
        substituted), but submitting-jobs is where a port's real value
        lives — an empty one means an unfinished port."""
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        for manifest in sorted((root / "facilities").glob("*/facility.json")):
            notes = manifest.parent / "skill_notes" / "submitting-jobs.md"
            assert notes.exists() and notes.read_text().strip(), \
                f"{manifest.parent.name}: skill_notes/submitting-jobs.md is missing or empty"

    def remote_command_skills_cover_forwarded_git_authentication():
        """Generated command skills must not guess a generic SSH key path."""
        root = pathlib.Path(__file__).resolve().parent.parent
        source = (root / "templates" / "skills" / "remote-command.md.tmpl").read_text()
        assert "ssh -G <host>" in source
        assert "forwardagent" in source
        assert "ssh-add <their-facility-key>" in source
        assert "never invent a path" in source
        for facility in config.list_facilities():
            rendered = (root / "plugins" / f"hpc-{facility.slug}" / "skills" /
                        f"{facility.slug}-remote-command" / "SKILL.md").read_text()
            assert "Git-over-SSH authentication failures" in rendered

    def every_facility_scoped_mcp_tool_checks_configuration_first():
        """Every tool that reaches the cluster must check configuration
        first; every tool that does not must keep working without it.

        Both halves matter. Gating a cluster-bound tool stops it returning
        a misleading result; gating a packaged-data tool breaks cold start,
        which is how get_facility and docs search ended up circular with
        the <slug>-configuring skill they were supposed to support.

        The ungated set is derived from the source and compared against an
        explicit allowlist, so a new tool added without the guard fails
        here rather than silently reaching for a connection.
        """
        import re
        import hpc_mcp.hpc_server as server
        from hpc_agent_core import docs_server

        # Deliberately ungated, each for a stated reason:
        #   get_facilities    - no facility argument; discovery entry point
        #   get_facility      - returns this facility's packaged facts JSON
        #   render_job_script - pure local defaults/validate/render
        UNGATED = {"get_facilities", "get_facility", "render_job_script"}

        source = pathlib.Path(server.__file__).read_text()
        blocks = re.findall(
            r"@mcp\.tool\(\)\n(.*?)def ([a-zA-Z_][a-zA-Z0-9_]*)\(",
            source, re.S)
        assert blocks, "found no @mcp.tool() definitions to inspect"
        ungated = {name for between, name in blocks
                   if "@_configured_facility_tool" not in between}
        assert ungated == UNGATED, (
            "the set of tools bypassing the configuration guard changed: "
            f"unexpectedly ungated={sorted(ungated - UNGATED)}, "
            f"newly gated={sorted(UNGATED - ungated)}"
        )

        class RecordingMCP:
            def __init__(self):
                self.functions = []

            def tool(self):
                def register(func):
                    self.functions.append(func)
                    return func
                return register

        docs_mcp = RecordingMCP()
        docs_server.build(docs_mcp)
        assert len(docs_mcp.functions) == 3

        for slug in (f.slug for f in config.list_facilities()):
            with no_user_config(slug):
                # Cluster-bound: must refuse with this facility's directions.
                for func, args in (
                    (server.fs_ls, (slug,)),
                    (server.get_projects, (slug,)),
                ):
                    try:
                        func(*args)
                    except RuntimeError as exc:
                        text = str(exc)
                        assert "not configured yet" in text
                        assert f"{slug}-configuring" in text
                    else:
                        raise AssertionError(
                            f"{func.__name__} bypassed the config guard")

                # Packaged data / local only: must answer anyway. The docs
                # tools are included here because gating them is the exact
                # regression this check now guards against.
                assert server.get_facility(slug)
                for func, args in (
                    (docs_mcp.functions[0], (slug, "queues")),
                    (docs_mcp.functions[1], (slug,)),
                ):
                    out = func(*args)
                    assert out and out.strip(), (
                        f"{func.__name__} returned nothing for {slug} "
                        "without user config"
                    )

    check("at least one facility is registered", facilities_exist)
    check("unknown facility errors clearly, listing valid slugs", unknown_facility_errors_clearly)
    check("slugs are lowercase and safe", slugs_are_url_and_identifier_safe)
    check("fs_rm refuses home/root paths", fs_rm_refuses_dangerous_paths)
    check("job names cannot inject into scripts", job_name_cannot_inject_into_scripts)
    check("resource quantities are positive", resource_quantities_are_positive)
    check("process_count does not conflict with default tasks-per-node",
          process_count_is_an_alternative_to_ppn)
    check("scheduler directives normalize home-directory shorthand",
          home_shorthand_is_normalized_for_scheduler_paths)
    check("Fugaku update quotes duration as one argument",
          fugaku_update_quotes_duration_as_one_argument)
    check("Fugaku PJM accounting exposes groups and points",
          fugaku_pjm_accounting_exposes_groups_and_points)
    check("docs embedding credentials are refreshed per search",
          docs_embedding_client_is_not_cached)
    check("SSH multiplexing is detected without opening a connection",
          ssh_multiplexing_detection_is_advisory_and_uses_resolved_config)
    check("a broken facility cannot deny the server", a_broken_facility_cannot_deny_the_server)
    check("facility data ships in a wheel", facility_data_is_declared_as_package_data)
    check("generated skills have matching frontmatter", generated_skills_are_wellformed)
    check("facility skill packs are isolated and discoverable",
          facility_skill_packs_are_isolated_and_discoverable)
    check("README requires explicit facility selection for manual install",
          readme_requires_explicit_facility_selection_for_manual_install)
    check("scheduler errors aren't mistaken for SSH failures", unreachable_detection_discriminates)
    check("every facility has real submitting-jobs notes", every_facility_has_skill_notes)
    check("remote-command skills diagnose forwarded Git authentication",
          remote_command_skills_cover_forwarded_git_authentication)
    check("every facility-scoped MCP tool checks configuration first",
          every_facility_scoped_mcp_tool_checks_configuration_first)

    def code_packs_render_only_complete_facility_data():
        """A code pack's stage is rendered with one facility's values, notes
        and extra files, and anything that would ship half-filled fails.

        The real packs are covered by CI's render_skills.py --check; this
        exercises the failure paths on a throwaway pack, since a correct
        repository never hits them."""
        import importlib.util
        root = pathlib.Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location(
            "render_skills", root / "scripts" / "render_skills.py")
        render_skills = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(render_skills)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            render_skills.CODES_DIR = tmp / "codes"
            stage = tmp / "codes" / "demo" / "run"
            (stage / "scripts").mkdir(parents=True)
            (tmp / "codes" / "demo" / "pack.json").write_text(json.dumps(
                {"code": "demo", "stages": ["run"], "values": {"DEMO_CORES": "cores"}}))
            (stage / "SKILL.md").write_text("{{SLUG}} {{DEMO_CORES}}\n{{CODE_NOTES}}\n")
            (stage / "scripts" / "x.sh").write_text("N={{DEMO_CORES}}\n")
            fac_dir = tmp / "fac"
            overlay = fac_dir / "codes" / "demo" / "run"
            (overlay / "references").mkdir(parents=True)
            (overlay / "notes.md").write_text("site quirk on {{SLUG}}")
            (overlay / "references" / "site.md").write_text("{{DEMO_CORES}} cores\n")
            values_path = fac_dir / "codes" / "demo" / "values.json"
            fac = {"slug": "demo-fac", "display_name": "Demo", "_dir": fac_dir}

            for bad in ({}, {"DEMO_CORES": 8, "DEMO_TYPO": 1}):
                values_path.write_text(json.dumps(bad))
                try:
                    render_skills.code_pack_stages(fac)
                except SystemExit:
                    pass
                else:
                    raise AssertionError(f"values {bad} were accepted")

            values_path.write_text(json.dumps({"DEMO_CORES": 8}))
            [(name, stage_dir, ovs, values, notes)] = render_skills.code_pack_stages(fac)
            assert (name, stage_dir, ovs) == ("demo-fac-demo-run", stage, (overlay,))
            assert notes == "site quirk on {{SLUG}}"

            out = tmp / "out"
            render_skills.sync_supporting_files(stage, out, fac, False, ovs, values)
            assert (out / "scripts" / "x.sh").read_text() == "N=8\n"
            assert (out / "references" / "site.md").read_text() == "8 cores\n"
            assert not (out / "notes.md").exists(), "notes.md fills {{CODE_NOTES}}, not a file"

            (overlay / "scripts").mkdir()
            (overlay / "scripts" / "x.sh").write_text("override\n")
            try:
                render_skills.sync_supporting_files(stage, out, fac, True, ovs, values)
            except SystemExit:
                pass
            else:
                raise AssertionError("a facility file shadowed a pack file")

            common = tmp / "codes" / "common" / "notes"
            common.mkdir(parents=True)
            (tmp / "codes" / "common" / "pack.json").write_text(json.dumps(
                {"code": "common", "skill_prefix": "", "stages": ["notes"],
                 "values": {}, "facility_dirs": {"notes": "records"}}))
            (common / "SKILL.md").write_text("{{DEMO_CORES}}\n")
            (fac_dir / "codes" / "common").mkdir()
            (fac_dir / "codes" / "common" / "values.json").write_text("{}")
            (fac_dir / "records").mkdir()
            (fac_dir / "records" / "r.yaml").write_text("x: 1\n")
            by_name = {s[0]: s for s in render_skills.code_pack_stages(fac)}
            assert set(by_name) == {"demo-fac-demo-run", "demo-fac-notes"}, by_name
            _, _, ovs, values, _ = by_name["demo-fac-notes"]
            assert ovs == (fac_dir / "records",) and values["DEMO_CORES"] == 8

            assert render_skills.unresolved_tokens({root / "a": "ok {{SLUG}}"}) != []
            assert render_skills.unresolved_tokens({root / "a": "${HOME} {x}"}) == []

    check("code packs render only with complete facility data",
          code_packs_render_only_complete_facility_data)

    def records_tools_follow_the_adapter_contract():
        """Both scopes on read, exact-match filters, append-only writes to
        the user scope. A temporary HPC_RECORDS_USER_DIR keeps the runner's
        own ~/.hpc-agent/records out of it, in both directions."""
        from hpc_codes import server
        from hpc_codes.records import RecordsError
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}):
            shared = server.records_query("hokusai", "benchmarks")
            assert shared and {r["_scope"] for r in shared} == {"facility"}, "no shared benchmarks"
            assert server.records_query("hokusai", "software_registry") == []
            assert server.records_query("rikyu", "benchmarks") == [], "a facility with no records"
            hits = server.records_query("hokusai", "benchmarks",
                                        {"nodes": "1", "valid": True}, ["system.label"])
            assert hits and set(hits[0]) == {"id", "_scope", "system.label"}, hits[:1]

            good = dict({k: v for k, v in shared[0].items() if k != "_scope"},
                        id="conformance-probe", job_id="1",
                        build_id="conformance-no-such-build", build_external=None)
            for bad in ({**good, "nodes": "two"}, {**good, "id": shared[0]["id"]},
                        {**good, "facility": "rikyu"}, {**good, "valid": False}):
                try:
                    server.records_append("hokusai", "benchmarks", bad)
                except RecordsError:
                    pass
                else:
                    raise AssertionError(f"appended a bad record: {bad}")
            assert not os.listdir(tmp), "a refused append wrote a file"

            out = server.records_append("hokusai", "benchmarks", good)
            assert out["scope"] == "user" and out["path"].startswith(tmp), out
            both = server.records_query("hokusai", "benchmarks", {"id": "conformance-probe"})
            assert [r["_scope"] for r in both] == ["user"], both
            try:
                server.records_append("hokusai", "benchmarks", good)
            except RecordsError:
                pass
            else:
                raise AssertionError("appended the same id twice")

            # extra: the hybrid / optimisation fields are accepted with their
            # types; a wrong type and an unknown key are still refused
            extra = {"first_step_s": 12.5, "scf_steps": 23, "hfx_eri_mib": 18952,
                     "peak_mib_per_rank": 3120, "forces_s": 410.2, "opt_steps": 21,
                     "final_pressure_bar": 7.34}
            for bad_extra in ({**extra, "opt_steps": "21"}, {**extra, "hfx_eri_mib": 1.5},
                              {**extra, "forces_s": "fast"}, {**extra, "eri_mib": 18952}):
                try:
                    server.records_append("hokusai", "benchmarks",
                                          dict(good, id="conformance-probe-extra", extra=bad_extra))
                except RecordsError as e:
                    assert "extra" in str(e), e
                else:
                    raise AssertionError(f"appended a bad extra: {bad_extra}")
            server.records_append("hokusai", "benchmarks",
                                  dict(good, id="conformance-probe-extra", extra=extra))
            back = server.records_query("hokusai", "benchmarks", {"id": "conformance-probe-extra"})
            assert len(back) == 1 and back[0]["extra"] == extra, back

            # the probe's build_id resolves nowhere and is not build_external
            report = server.records_check("hokusai")
            assert report["counts"]["benchmarks"] == len(shared) + 2, report["counts"]
            assert any("conformance-no-such-build" in e for e in report["errors"]), report
            for bad_type in ("benchmark", ""):
                try:
                    server.records_query("hokusai", bad_type)
                except RecordsError as e:
                    assert "Valid types" in str(e)
                else:
                    raise AssertionError(f"accepted record type {bad_type!r}")
            try:
                server.records_query("definitely-not-a-facility", "benchmarks")
            except ValueError as e:
                assert "Valid facilities:" in str(e)
            else:
                raise AssertionError("accepted an unknown facility")

    def records_root_moves_the_user_scope():
        """HPC_RECORDS_ROOT puts each facility's user records under
        <root>/<slug>/; HPC_RECORDS_USER_DIR still wins, and with neither set
        the default under ~/.hpc-agent is unchanged."""
        from hpc_codes import records
        env = {k: v for k, v in os.environ.items()
               if k not in ("HPC_RECORDS_USER_DIR", "HPC_RECORDS_ROOT")}
        with patch.dict(os.environ, env, clear=True):
            assert records.user_dir("hokusai") == \
                pathlib.Path.home() / ".hpc-agent" / "records" / "hokusai"
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as only, \
                patch.dict(os.environ, {**env, "HPC_RECORDS_ROOT": root}, clear=True):
            assert records.user_dir("hokusai") == pathlib.Path(root) / "hokusai"
            shared = records.query("hokusai", "benchmarks")
            good = dict({k: v for k, v in shared[0].items() if k != "_scope"},
                        id="records-root-probe", job_id="1", build_external=True)
            out = records.append("hokusai", "benchmarks", good)
            assert out["path"] == str(pathlib.Path(root) / "hokusai" / "benchmarks.yaml"), out
            assert [r["_scope"] for r in records.query("hokusai", "benchmarks",
                                                       {"id": "records-root-probe"})] == ["user"]
            with patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": only}):
                assert records.user_dir("hokusai") == pathlib.Path(only)

    def shipped_records_are_valid():
        from hpc_codes import records
        for fac in config.list_facilities():
            if not records.facility_dir(fac.slug).is_dir():
                continue
            with tempfile.TemporaryDirectory() as tmp, \
                    patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}):
                errs = records.check(fac.slug)["errors"]
            # with no user registry, unresolved build_ids are expected and
            # only those: every other error is a defect in the shipped file
            errs = [e for e in errs if "is not in software_registry" not in e]
            assert not errs, f"{fac.slug}: {errs[:5]}"

    def codes_server_starts_with_empty_home():
        """The never-fail invariant for the second server: a brand-new user
        with no ~/.hpc-agent at all still gets all its tools listed."""
        root = pathlib.Path(__file__).resolve().parent.parent
        probe = ("import asyncio; from hpc_codes.server import mcp; "
                 "print(sorted(t.name for t in asyncio.run(mcp.list_tools())))")
        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items()
                   if k not in ("HPC_RECORDS_USER_DIR", "HPC_RECORDS_ROOT")}
            env["HOME"] = home
            out = subprocess.run([sys.executable, "-c", probe], cwd=root, env=env,
                                 capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        assert out.stdout.strip() == ("['cp2k_bench_dryrun', 'cp2k_bench_input', 'cp2k_bench_parse', "
                                      "'cp2k_bench_records', 'cp2k_check_build', 'cp2k_check_input', "
                                      "'cp2k_kinds', 'cp2k_record_from_run', 'launch_plan', 'records_append', 'records_check', "
                                      "'records_query']"), out.stdout

    def local_run_command(facility, cmd):
        """middleware.run_command's contract, run in a local bash: the same
        payload (cd "$HOME" first), stdout back, RuntimeError on failure.
        Only read-only commands reach it (the cp2k_* tools)."""
        out = subprocess.run(["bash", "-c", 'cd "$HOME" && ' + cmd],
                             capture_output=True, text=True, timeout=120)
        if out.returncode:
            raise RuntimeError(out.stderr or out.stdout)
        return out.stdout

    def cp2k_tools_parse_and_check_on_the_login_node():
        """The cp2k_* tools' real shell commands, run against fixtures: two
        scrubbed cp2k.out files from a HOKUSAI smoke stage, and a fake build
        (env script, cp2k.psmp, ldd) in a temporary folder."""
        import shutil
        from hpc_codes import server
        from hpc_codes.cp2k import tools
        real_pack_values = tools.pack_values
        root = pathlib.Path(__file__).resolve().parent.parent
        stage = root / "tests" / "fixtures" / "cp2k_bench" / "stage1"
        # HOKUSAI's own values, except the login node's Python path
        values = {**tools.pack_values("hokusai"), "CP2K_PYTHON": sys.executable}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(tools, "run_command", local_run_command), \
                patch.object(tools, "pack_values", lambda f, code="cp2k": (
                    values if f == "hokusai" else real_pack_values(f, code))), \
                patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp, "HOKUSAI_HOST": "localhost"}):
            tmp = pathlib.Path(tmp)
            r = server.cp2k_bench_parse("hokusai", [str(stage), str(tmp / "absent")])
            got = r["stages"][str(stage)]
            assert got["passed"] and got["runs"] == 2, got
            assert got["ranked"] == ["8x14", "28x4"], got
            assert all(run["steps"] == 10 and run["dE_max"] < 1e-10 for run in r["runs"]), r["runs"]
            assert r["stages"][str(tmp / "absent")] == {"found": False, "runs": 0,
                                                          "passed": False, "ranked": []}
            broken = tmp / "broken"
            shutil.copytree(stage, broken)
            out = broken / "H2O-32_8x14_r1" / "cp2k.out"
            out.write_text(out.read_text().replace("PROGRAM ENDED AT", "PROGRAM KILLED AT"))
            got = server.cp2k_bench_parse("hokusai", [str(broken)])["stages"][str(broken)]
            assert not got["passed"] and got["ranked"] == ["28x4"], got

            # a fake build: cp2k.psmp and ldd on PATH after the env script
            bin_dir = tmp / "build" / "bin"
            bin_dir.mkdir(parents=True)
            (tmp / "build" / "data").mkdir()
            (bin_dir / "cp2k.psmp").write_text(
                '#!/bin/bash\n'
                'if [ "$1" = --version ]; then echo " CP2K version 2026.2"; echo " cp2kflags: omp mkl"; exit 0; fi\n'
                # what CP2K 2026.2 printed for the input (tests/fixtures/cp2k_echo)
                'if [ "$1" = --echo ]; then [ -f "${3%.inp}.echo" ] && cat "${3%.inp}.echo"; exit 0; fi\n'
                '[ -f "${3%.inp}.abort" ] && { cat "${3%.inp}.abort"; exit 1; }\n'
                'grep -q BAD "$3" && { echo " *** [ABORT] unknown keyword BAD ***"; exit 1; }\n'
                '[ -f sub.inc ] || { echo " *** [ABORT] @INCLUDE sub.inc not found ***"; exit 1; }\n'
                'exit 0\n')
            (bin_dir / "ldd").write_text('#!/bin/bash\n[ -n "$FAKE_MISSING" ] && echo "\tlibxc.so.6 => not found"; true\n')
            # coreutils timeout is on every Linux login node, not on macOS
            (bin_dir / "timeout").write_text('#!/bin/bash\nshift\nexec "$@"\n')
            for f in bin_dir.iterdir():
                f.chmod(0o755)
            env = tmp / "build" / "env.sh"
            env.write_text(f'export PATH={bin_dir}:$PATH\n')
            build = {"build_id": "cp2k-2026.2-fake", "facility": "hokusai", "code": "cp2k",
                     "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"},
                     "toolchain": "fake", "install_path": str(tmp / "build"),
                     "env_script": str(env), "data_dir": str(tmp / "build" / "data"),
                     "test_status": {"level": "none", "result": "pass"},
                     "default": True, "date": "2026-11-03"}
            server.records_append("hokusai", "software_registry", build)

            res = server.cp2k_check_build("hokusai")
            assert res["ok"] and res["version"] == "2026.2" and res["cp2kflags"] == ["omp", "mkl"], res
            with patch.dict(os.environ, {"FAKE_MISSING": "1"}):
                res = server.cp2k_check_build("hokusai", "cp2k-2026.2-fake")
            assert not res["ok"] and res["missing_libs"] == ["libxc.so.6"], res

            run = tmp / "run dir"         # a space: the folder must be quoted
            run.mkdir()
            (run / "sub.inc").write_text("")
            (run / "good.inp").write_text("&GLOBAL\n&END\n")
            (run / "bad.inp").write_text("BAD\n")
            res = server.cp2k_check_input("hokusai", str(run / "good.inp"))
            assert res["ok"] and res["build_id"] == "cp2k-2026.2-fake", res
            assert res["files"] == [] and res["warnings"] == [], res
            res = server.cp2k_check_input("hokusai", str(run / "bad.inp"))
            assert not res["ok"] and any("unknown keyword BAD" in e for e in res["errors"]), res

            # the files the input reads at run time (G23), from CP2K's own
            # --echo of cp2k_make_input-shaped inputs; existence is tested
            # for real, from the input's folder
            echo_src = root / "tests" / "fixtures" / "cp2k_echo"
            wfn_run = tmp / "wfn run"
            wfn_run.mkdir()
            for f in echo_src.iterdir():
                if f.suffix in (".inp", ".inc", ".echo"):
                    shutil.copy(f, wfn_run / f.name)
            (wfn_run / "sub.inc").write_text("")
            check = lambda name: server.cp2k_check_input("hokusai", str(wfn_run / name))

            res = check("atomic.inp")       # SCF_GUESS ATOMIC: nothing to read
            assert res["ok"] and res["files"] == [] and res["warnings"] == [], res
            assert not any(l.startswith("HPC_ECHO") for l in res["tail"]), res["tail"]

            guess = "../pbe-guess/pbe-guess-RESTART.wfn"
            res = check("guess_wfn.inp")    # RESTART 0: the @SET WFN guess, missing
            assert res["ok"], res           # a missing wfn warns; it does not fail the parse
            assert res["files"] == [{"keyword": "WFN_RESTART_FILE_NAME", "path": guess,
                                     "exists": False}], res
            assert len(res["warnings"]) == 1 and guess in res["warnings"][0] \
                and "atomic guess" in res["warnings"][0], res
            (tmp / "pbe-guess").mkdir()
            (tmp / "pbe-guess" / "pbe-guess-RESTART.wfn").write_text("")
            res = check("guess_wfn.inp")    # present: no warning
            assert res["ok"] and res["warnings"] == [], res
            assert res["files"] == [{"keyword": "WFN_RESTART_FILE_NAME", "path": guess,
                                     "exists": True}], res

            res = check("restart1.inp")     # RESTART 1: the @IF branch's own files
            assert res["ok"], res
            assert res["files"] == [
                {"keyword": "EXT_RESTART/RESTART_FILE_NAME", "path": "hse06-geo-1.restart", "exists": True},
                {"keyword": "WFN_RESTART_FILE_NAME", "path": "hse06-geo-RESTART.wfn", "exists": False}], res
            assert len(res["warnings"]) == 1 and "hse06-geo-RESTART.wfn" in res["warnings"][0], res
            (wfn_run / "hse06-geo-RESTART.wfn").write_text("")
            res = check("restart1.inp")
            assert res["ok"] and res["warnings"] == [] and res["files"][1]["exists"], res

            res = check("restart_noguess.inp")   # no WFN keyword: CP2K's default name
            assert res["ok"], res
            assert [(f["path"], f["exists"]) for f in res["files"]] == [
                ("pbe-geo-1.restart", True), ("pbe-geo-RESTART.wfn", False)], res
            assert "default" in res["files"][1]["keyword"] and len(res["warnings"]) == 1, res

            # the .restart itself missing: --check already stops on it
            shutil.copy(wfn_run / "restart1.inp", wfn_run / "restart1_gone.inp")
            shutil.copy(echo_src / "restart1.abort", wfn_run / "restart1_gone.abort")
            res = check("restart1_gone.inp")
            assert not res["ok"], res
            assert res["errors"][0].startswith("[ABORT] The specified file <hse06-geo-1.restart>"), res
            assert res["files"] == [{"keyword": "EXT_RESTART/RESTART_FILE_NAME",
                                     "path": "hse06-geo-1.restart", "exists": False}], res
            assert len(res["warnings"]) == 1 and "RESTART 0" in res["warnings"][0], res

            # a long name is hard-wrapped over CP2K's box lines (2026.2 output)
            stars = " " + "*" * 79
            wrapped = [stars,
                       " * [ABORT]                          The specified file                         *",
                       " *  \\___/   <../a-rather-long-folder-name/and-a-long-project-name-for-wrapping *",
                       " *    |       -tests-1.restart> cannot be opened with STATUS=OLD. It does not  *",
                       " *  O/|           exist. Data directory path: /usr/local/share/cp2k/data       *",
                       " * / \\                                                   common/cp_files.F:441 *",
                       stars, "            2 handle_ext_restart"]
            long_name = "../a-rather-long-folder-name/and-a-long-project-name-for-wrapping-tests-1.restart"
            assert tools._abort_missing_file(wrapped) == long_name
            assert tools._abort_missing_file(wrapped[:-1]) is None   # not the EXT_RESTART handler
            info = [stars, " *                            RESTART INFORMATION                              *", stars,
                    " *    RESTART FILE NAME: ../a-rather-long-folder-name/and-a-long-project-name- *",
                    " *                       for-wrapping-tests-1.restart                          *",
                    " *                                                                             *",
                    " * RESTARTED QUANTITIES:                                                       *",
                    " *                       CELL                                                  *", stars]
            assert tools._ext_restart_read(info) == long_name

            # a removed default is never used; an unknown id lists what exists
            server.records_append("hokusai", "software_registry",
                                  {**build, "build_id": "cp2k-gone", "removed": "2026-11-03"})
            for bad in ("cp2k-gone", "no-such-build"):
                try:
                    server.cp2k_check_build("hokusai", bad)
                except ValueError as e:
                    assert "cp2k-2026.2-fake" in str(e) and "cp2k-gone" not in str(e).split("Available:")[1], e
                else:
                    raise AssertionError(f"used build {bad}")
            try:
                server.cp2k_check_build("rikyu")
            except ValueError as e:
                assert "has not opted into the cp2k code pack" in str(e), e
            else:
                raise AssertionError("cp2k tool ran on a facility without the pack")

    def cp2k_bench_dryrun_runs_the_stage_logic_with_a_stub_srun():
        """cp2k_bench_dryrun's real command: a benchmark folder rendered from
        the pack's scripts, the scrubbed smoke-stage cp2k.out as REF_OUT, all
        three cases; the benchmark folder is left as it was."""
        import re
        from hpc_codes import server
        from hpc_codes.cp2k import tools
        real_pack_values = tools.pack_values
        root = pathlib.Path(__file__).resolve().parent.parent
        ref = root / "tests" / "fixtures" / "cp2k_bench" / "stage1" / "H2O-32_8x14_r1" / "cp2k.out"
        values = {**tools.pack_values("hokusai"), "CP2K_PYTHON": sys.executable}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            bench = tmp / "bench dir"     # a space: every path must be quoted
            (bench / "inputs").mkdir(parents=True)
            for f in (root / "codes" / "cp2k" / "benchmark" / "scripts").iterdir():
                text = re.sub(r"\{\{(\w+)\}\}", lambda m: str(values[m[1]]), f.read_text())
                (bench / f.name).write_text(text)
            for name in ("smoke.inp", "scan.inp", "big.inp"):
                (bench / "inputs" / name).write_text("&GLOBAL\n&END\n")
            before = sorted(p.relative_to(bench) for p in bench.rglob("*"))
            # login-node GNU tools the scripts use, on a macOS test machine
            shims = tmp / "shims"
            shims.mkdir()
            if sys.platform == "darwin":
                (shims / "sed").write_text('#!/bin/bash\n[ "$1" = -i ] && { shift; exec /usr/bin/sed -i "" "$@"; }\n'
                                           'exec /usr/bin/sed "$@"\n')
                (shims / "timeout").write_text('#!/bin/bash\nwhile [[ $1 = -* ]]; do shift; done\nshift\nexec "$@"\n')
            for f in shims.iterdir():
                f.chmod(0o755)
            settings = {"SMOKE_INPUT": "smoke.inp", "SCAN_INPUT": "scan.inp", "VALIDATE_INPUT": "big.inp",
                        "SMOKE_LAYOUTS": "28x4 8x14", "SCAN_LAYOUTS": "112x1 28x4 8x14"}
            with patch.object(tools, "run_command", local_run_command), \
                    patch.object(tools, "pack_values", lambda f, code="cp2k": (
                        values if f == "hokusai" else real_pack_values(f, code))), \
                    patch.dict(os.environ, {"HOME": str(tmp), "HOKUSAI_HOST": "localhost",
                                            "PATH": f"{shims}:{os.environ['PATH']}"}):
                r = server.cp2k_bench_dryrun("hokusai", str(bench), str(ref), settings)
                assert r["ok"], json.dumps(r, indent=1)[-3000:]
                case = {c["case"]: c for c in r["cases"]}
                assert case["pass"]["exit_code"] == 0 and case["pass"]["stages_run"] == [
                    "stage1_smoke", "stage2_scan", "stage3_validate"], case["pass"]
                # the last scan layout not in the smoke stage: stage 2 stops, stage 3 never runs
                fl = case["fail_layout"]
                assert fl["fail_layout"] == "112x1" and fl["exit_code"] == 1, fl
                assert fl["stages_run"] == ["stage1_smoke", "stage2_scan"], fl
                assert fl["failed"] == ["scan.inp 112x1"], fl
                tl = case["time_limit"]
                assert tl["not_started"] == ["smoke 28x4 r1"] and tl["stages_run"] == ["stage1_smoke"], tl
                # a dry run that passes when it should fail is reported, not hidden
                r = server.cp2k_bench_dryrun("hokusai", str(bench), str(ref), settings,
                                             cases=["time_limit"], time_limit_s=100000)
                assert not r["ok"] and r["cases"][0]["why"], r
                kept = server.cp2k_bench_dryrun("hokusai", str(bench), str(ref), settings,
                                                cases=["pass"], keep_scratch=True)["cases"][0]["scratch"]
                assert (pathlib.Path(kept) / "bench" / "runs" / "dry-pass" / "stage2").is_dir(), kept
                left = list((tmp / "agent" / "work" / "cp2k-dryrun").iterdir())
                assert [str(p) for p in left] == [kept], left
                assert sorted(p.relative_to(bench) for p in bench.rglob("*")) == before
                for bad in ({"PATH": "/x"}, {"SLURM_JOB_ID": "1"}, {"a b": "1"}):
                    try:
                        server.cp2k_bench_dryrun("hokusai", str(bench), str(ref), bad)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError(f"cp2k_bench_dryrun accepted {bad}")
                (bench / "dryrun.sh").write_text("#!/bin/bash\nexit 0\n")
                try:
                    server.cp2k_bench_dryrun("hokusai", str(bench), str(ref), settings)
                except RuntimeError as e:
                    assert "stub-srun check" in str(e), e
                else:
                    raise AssertionError("ran a dryrun.sh without the stub-srun check")

    def cp2k_bench_records_appends_benchmarks_and_provenance():
        """cp2k_bench_records on the scrubbed smoke-stage runs, laid out as a
        job's stage folder with a repeat and a killed run: one record per
        layout, the killed one valid: false, job IDs only in provenance, and
        nothing written when any record is refused."""
        import shutil
        import yaml
        from hpc_codes import records, server
        from hpc_codes.cp2k import tools
        real_pack_values = tools.pack_values
        root = pathlib.Path(__file__).resolve().parent.parent
        fixture = root / "tests" / "fixtures" / "cp2k_bench" / "stage1"
        values = {**tools.pack_values("hokusai"), "CP2K_PYTHON": sys.executable}
        system = {"label": "H2O-32", "atoms": 96, "elements": {"H": 64, "O": 32},
                  "periodic": "xyz", "kpoints": "gamma"}
        method = {"functional": "PADE (LDA)", "basis": "DZVP-GTH", "cutoff_ry": 280,
                  "hybrid": False, "scf": "OT DIIS"}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(tools, "run_command", local_run_command), \
                patch.object(tools, "pack_values", lambda f, code="cp2k": (
                    values if f == "hokusai" else real_pack_values(f, code))), \
                patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp, "HOKUSAI_HOST": "localhost"}):
            tmp = pathlib.Path(tmp)
            stage = tmp / "runs" / "9000001" / "stage2"
            shutil.copytree(fixture, stage)
            shutil.copytree(stage / "H2O-32_8x14_r1", stage / "H2O-32_8x14_r2")
            killed = stage / "H2O-32_112x1_r1"
            shutil.copytree(stage / "H2O-32_28x4_r1", killed)
            out = killed / "cp2k.out"
            out.write_text(out.read_text().replace("PROGRAM ENDED AT", "PROGRAM KILLED AT"))
            server.records_append("hokusai", "software_registry", {
                "build_id": "cp2k-2026.2-fake", "facility": "hokusai", "code": "cp2k",
                "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"},
                "toolchain": "fake", "install_path": str(tmp), "env_script": str(tmp / "env.sh"),
                "test_status": {"level": "none", "result": "pass"}, "default": True,
                "date": "2026-11-04"})

            parsed = server.cp2k_bench_parse("hokusai", [str(stage)])
            r = server.cp2k_bench_records("hokusai", system, method, "aimd_step", parsed=parsed,
                                          date="2026-11-04")
            base = "hokusai-cp2k-2026-11-04-h2o32-aimdstep-1n-"
            assert r["appended"] == [base + l for l in ("8x14", "28x4", "112x1")], r["appended"]
            assert r["valid"] == [base + "8x14", base + "28x4"], r
            assert "did not finish" in r["invalid"][base + "112x1"], r
            assert not r["check_errors"], r["check_errors"]
            by_id = {x["id"]: x for x in r["records"]}
            rec = by_id[base + "8x14"]
            assert rec["metric"]["name"] == "s_per_step" and rec["metric"]["value"] > 0, rec
            assert rec["ranks_per_node"] == 8 and rec["threads_per_rank"] == 14, rec
            assert rec["job_id"] is None and "2 repeats (2 usable)" in rec["notes"], rec
            # the shared HOKUSAI records' label, so launch_plan's queries line up
            assert rec["node_type"] == "MPC (bwmpc), 112 cores", rec
            assert rec["build_id"] == "cp2k-2026.2-fake" and rec["version"] == "2026.2", rec
            prov = server.records_query("hokusai", "benchmarks_provenance")
            assert {p["benchmark_id"] for p in prov} == set(r["appended"]), prov
            assert all(p["job_ids"] == ["9000001"] for p in prov), prov
            assert "9000001" not in (tmp / "benchmarks.yaml").read_text()
            assert not server.records_check("hokusai")["errors"]

            # an id that exists refuses the whole call; a tag makes new ids
            count = len(yaml.safe_load((tmp / "benchmarks.yaml").read_text())["records"])
            try:
                server.cp2k_bench_records("hokusai", system, method, "aimd_step", parsed=parsed,
                                          date="2026-11-04")
            except records.RecordsError as e:
                assert "already exists" in str(e) and "pass a tag" in str(e), e
            else:
                raise AssertionError("appended a benchmark id twice")
            assert len(yaml.safe_load((tmp / "benchmarks.yaml").read_text())["records"]) == count
            # parsing on the login node, and job IDs given explicitly
            r = server.cp2k_bench_records("hokusai", system, method, "aimd_step",
                                          stage_dirs=[str(stage)], date="2026-11-04", tag="lda",
                                          job_ids=["9000002"])
            assert all(i.endswith("-lda") for i in r["appended"]), r["appended"]
            assert server.records_query("hokusai", "benchmarks_provenance",
                                        {"id": r["appended"][0]})[0]["job_ids"] == ["9000002"]

            # refused before anything is written
            dry = tmp / "runs" / "dry-pass" / "stage2"
            shutil.copytree(fixture, dry)
            other = {**parsed, "runs": parsed["runs"] + [{**parsed["runs"][0], "input": "H2O-64"}]}
            for call, why in (
                    (lambda: server.cp2k_bench_records("hokusai", system, method, "aimd_step",
                                                       stage_dirs=[str(dry)]), "dry-run"),
                    (lambda: server.cp2k_bench_records("hokusai", system, method, "aimd_step",
                                                       parsed=other), "give input_name"),
                    (lambda: server.cp2k_bench_records("hokusai", system, method, "single_point",
                                                       parsed=parsed), "is run_type right"),
                    (lambda: server.cp2k_bench_records("hokusai", {"label": "H2O-32"}, method,
                                                       "aimd_step", parsed=parsed, tag="x"),
                     "missing required atoms")):
                try:
                    call()
                except ValueError as e:
                    assert why in str(e), (why, e)
                else:
                    raise AssertionError(f"cp2k_bench_records did not refuse ({why})")
            assert len(yaml.safe_load((tmp / "benchmarks.yaml").read_text())["records"]) == count + 3

    def cp2k_tools_check_configuration_first():
        from hpc_codes import server
        from hpc_codes.cp2k import tools

        def must_not_run(*_):
            raise AssertionError("an unconfigured facility reached run_command")
        env = {k: v for k, v in os.environ.items() if k != "HOKUSAI_HOST"}
        # clear the environment first: no_user_config's override must survive it
        with patch.dict(os.environ, env, clear=True), no_user_config("hokusai"), \
                patch.object(tools, "run_command", must_not_run):
            for call in (lambda: server.cp2k_bench_parse("hokusai", ["x"]),
                         lambda: server.cp2k_check_input("hokusai", "x.inp"),
                         lambda: server.cp2k_check_build("hokusai"),
                         lambda: server.cp2k_kinds("hokusai", ["H"]),
                         lambda: server.cp2k_bench_input("hokusai", "x.xyz", "x", "out"),
                         lambda: server.cp2k_bench_dryrun("hokusai", "x", "y"),
                         lambda: server.cp2k_bench_records("hokusai", {}, {}, "aimd_step",
                                                           stage_dirs=["x"])):
                try:
                    call()
                except RuntimeError as e:
                    assert "not configured yet" in str(e), e
                else:
                    raise AssertionError("unconfigured cp2k tool did not stop")

    def cp2k_kinds_reads_the_build_data_folder():
        """cp2k_kinds' real command against a fixture data folder: q from the
        basis alias, checked against the potentials; a missing alias or
        potential is reported per element; bad input never reaches a shell."""
        from hpc_codes import server
        from hpc_codes.cp2k import tools
        real_pack_values = tools.pack_values
        data = pathlib.Path(__file__).resolve().parent / "fixtures" / "cp2k_data"
        values = {**tools.pack_values("hokusai"), "CP2K_PYTHON": sys.executable}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(tools, "run_command", local_run_command), \
                patch.object(tools, "pack_values", lambda f, code="cp2k": (
                    values if f == "hokusai" else real_pack_values(f, code))), \
                patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp, "HOKUSAI_HOST": "localhost"}):
            build = {"build_id": "cp2k-2026.2-fake", "facility": "hokusai", "code": "cp2k",
                     "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"},
                     "toolchain": "fake", "install_path": tmp, "env_script": tmp + "/env.sh",
                     "data_dir": str(data), "test_status": {"level": "none", "result": "pass"},
                     "default": True, "date": "2026-11-04"}
            server.records_append("hokusai", "software_registry", build)
            r = server.cp2k_kinds("hokusai", ["Zr", "O", "C", "H", "H", "Xx", "N"])
            assert r["kinds"] == {e: {"q": q, "basis": "DZVP-MOLOPT-SR-GTH", "potential": f"GTH-PBE-q{q}"}
                                  for e, q in (("O", 6), ("C", 4), ("H", 1))}, r
            assert set(r["errors"]) == {"Zr", "Xx", "N"}, r
            assert "GTH-PBE-q12" in r["errors"]["Zr"] and "no -qN alias" in r["errors"]["Xx"], r
            assert "no DZVP-MOLOPT-SR-GTH for N" in r["errors"]["N"], r
            assert r["files"] == {"basis": ["BASIS_MOLOPT"], "potential": "GTH_POTENTIALS"}, r
            # UZH: the newest versioned file wins (the unversioned one has no O),
            # q comes from the basis name, the potential must be the same family
            r = server.cp2k_kinds("hokusai", ["Zr", "O", "H"], family="uzh-scan")
            assert r["files"] == {"basis": ["BASIS_MOLOPT_UZH_2026.2"],
                                  "potential": "POTENTIAL_UZH_2026.2"}, r
            assert r["kinds"]["Zr"] == {"q": 12, "basis": "DZVP-MOLOPT-SCAN-GTH-q12",
                                        "potential": "GTH-SCAN-q12"}, r
            assert not r["errors"], r
            r = server.cp2k_kinds("hokusai", ["Zr", "O", "H"], family="uzh-pbe0", aux_basis="admm-dzp")
            assert r["files"]["basis"] == ["BASIS_MOLOPT_UZH_2026.2", "BASIS_ADMM_UZH"], r
            assert r["kinds"]["O"]["aux_basis"] == "admm-dzp-q6", r
            assert set(r["errors"]) == {"Zr"} and "admm-dzp-q12" in r["errors"]["Zr"], r
            for kw in ({"family": "gth"}, {"aux_basis": "admm-dzp"}):
                try:
                    server.cp2k_kinds("hokusai", ["H"], **kw)
                except ValueError:
                    pass
                else:
                    raise AssertionError(f"cp2k_kinds accepted {kw}")
            for bad in (["H; rm -rf ~"], []):
                try:
                    server.cp2k_kinds("hokusai", bad)
                except ValueError:
                    pass
                else:
                    raise AssertionError(f"cp2k_kinds accepted {bad}")
            try:
                server.cp2k_kinds("hokusai", ["H"], basis="X $(id)")
            except ValueError:
                pass
            else:
                raise AssertionError("cp2k_kinds accepted a shell word as a basis")

    def cp2k_bench_input_matches_the_script():
        """cp2k_bench_input writes what make_cp2k_md_input.py (the by-hand
        fallback) writes for the same structure and data folder, apart from
        the "Written by" line; kinds come through cp2k_kinds' real command;
        a missing kind or an existing input stops it before writing."""
        from hpc_codes import server
        from hpc_codes.cp2k import tools
        real_pack_values = tools.pack_values
        root = pathlib.Path(__file__).resolve().parent.parent
        script = root / "codes" / "cp2k" / "benchmark" / "scripts" / "make_cp2k_md_input.py"
        fx = root / "tests" / "fixtures" / "structures"
        data = root / "tests" / "fixtures" / "cp2k_data"
        values = {**tools.pack_values("hokusai"), "CP2K_PYTHON": sys.executable}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(tools, "run_command", local_run_command), \
                patch.object(tools, "pack_values", lambda f, code="cp2k": (
                    values if f == "hokusai" else real_pack_values(f, code))), \
                patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp, "HOKUSAI_HOST": "localhost"}):
            build = {"build_id": "cp2k-2026.2-fake", "facility": "hokusai", "code": "cp2k",
                     "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"},
                     "toolchain": "fake", "install_path": tmp, "env_script": tmp + "/env.sh",
                     "data_dir": str(data), "test_status": {"level": "none", "result": "pass"},
                     "default": True, "date": "2026-11-04"}
            server.records_append("hokusai", "software_registry", build)
            tmp = pathlib.Path(tmp)
            cases = [("clean.xyz", {}), ("ch2o.xyz", {"xc": "PBE", "d3": "bj", "charge": 1,
                                                         "steps": 20, "cutoff": 500})]
            for xyz, kw in cases:
                old, new = tmp / f"old-{xyz}", tmp / f"new-{xyz}"
                flags = [f"--{k.replace('_', '-')}={v}" for k, v in kw.items()]
                subprocess.run([sys.executable, "-I", str(script), str(fx / xyz), "--name", "sys",
                                "--data-dir", str(data), "--out-dir", str(old), *flags],
                               check=True, capture_output=True, timeout=60)
                r = server.cp2k_bench_input("hokusai", str(fx / xyz), "sys", str(new), **kw)
                assert r["inputs"] == [str(new / "sys.inp"), str(new / "sys-smoke.inp")], r
                for f in ("sys.inp", "sys-smoke.inp"):
                    a, b = (old / f).read_text().splitlines(), (new / f).read_text().splitlines()
                    assert a[:2] + a[3:] == b[:2] + b[3:], (xyz, f, a, b)
                assert r["build_id"] == "cp2k-2026.2-fake", r
            assert r["electrons"] == 11 and r["formula"] == "C1O1H2", r
            assert len(r["warnings"]) == 1 and "odd" in r["warnings"][0], r
            r = server.cp2k_bench_input("hokusai", str(fx / "ch2o.xyz"), "even", str(tmp / "e"))
            assert r["electrons"] == 12 and r["warnings"] == [] and min(r["widths"]) > 10, r
            assert any("below 10 A" in w for w in
                       server.cp2k_bench_input("hokusai", str(fx / "clean.xyz"), "t", str(tmp / "t"))["warnings"])
            zr = tmp / "zr.xyz"
            zr.write_text((fx / "clean.xyz").read_text().replace("\nO ", "\nZr "))
            for args, kw, text in (((str(zr), "z", str(tmp / "z")), {}, "GTH-PBE-q12"),
                                   ((str(fx / "clean.xyz"), "sys", str(tmp / "new-clean.xyz")), {}, "exist"),
                                   ((str(fx / "clean.xyz"), "a b", str(tmp / "n")), {}, "name"),
                                   ((str(fx / "clean.xyz"), "x", str(tmp / "x")), {"d3": "d4"}, "d3"),
                                   ((str(tmp / "absent.xyz"), "x", str(tmp / "x")), {}, "not a file")):
                try:
                    server.cp2k_bench_input("hokusai", *args, **kw)
                except ValueError as e:
                    assert text in str(e), e
                else:
                    raise AssertionError(f"cp2k_bench_input accepted {args[:2]} {kw}")
            assert not (tmp / "z").exists() and not (tmp / "x").exists()

    def yagura_cif_tools_read_convert_and_warn():
        """cif_inspect and structure_to_extxyz on a small disordered CIF and a
        molecule with no cell (needs ASE, the [yagura] extra CI installs)."""
        from yagura import server as ysrv
        fx = pathlib.Path(__file__).resolve().parent / "fixtures" / "structures"
        r = ysrv.cif_inspect(str(fx / "disordered.cif"))
        assert r["cell"] == {"lengths": [6.0, 6.0, 6.0], "angles": [90.0, 90.0, 90.0]}, r
        assert r["partial"] == ["H1A", "H1B"] and r["partial_groups"] == {"1": ["H1A"], "2": ["H1B"]}, r
        assert r["keys"]["_space_group_it_number"] == "1" and len(r["sites"]) == 4, r
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "cell.xyz"
            c = ysrv.structure_to_extxyz(str(fx / "disordered.cif"), str(out))
            assert c["formula"] == "CH2O" and c["electrons"] == 16, c
            assert c["multiplicities"] == "1, 3, 5, ..." and "partial sites (H1A, H1B)" in c["warnings"][0], c
            assert ysrv.structure_check(str(out))["natoms"] == 4
            box = ysrv.structure_to_extxyz(str(fx / "h2_nocell.xyz"), str(pathlib.Path(d) / "h2.xyz"), vacuum=6)
            assert box["cell"]["lengths"][:2] == [12.0, 12.0] and box["warnings"] == [], box
            for args, kw, text in (((str(fx / "h2_nocell.xyz"), str(pathlib.Path(d) / "x.xyz")), {}, "no complete cell"),
                                   ((str(fx / "disordered.cif"), str(out)), {}, "exists"),
                                   ((str(fx / "disordered.cif"), str(fx / "disordered.cif")), {}, "must differ")):
                try:
                    ysrv.structure_to_extxyz(*args, **kw)
                except ValueError as e:
                    assert text in str(e), e
                else:
                    raise AssertionError(f"structure_to_extxyz accepted {args}")

    def hyogo_make_subsys_matches_the_script_and_stays_separate():
        """hyogo's cp2k_make_subsys writes what make_subsys.py (the by-hand
        fallback) writes, from cp2k_kinds-shaped kinds; it refuses missing
        kinds; its server starts with an empty HOME; and the three staged
        groups (rookery, yagura, hyogo) import nothing from each other."""
        import ast
        import re
        from hyogo import server as hsrv
        root = pathlib.Path(__file__).resolve().parent.parent
        fx = root / "tests" / "fixtures" / "structures" / "clean.xyz"
        data = root / "tests" / "fixtures" / "cp2k_data"
        kinds = {"build_id": "x", "kinds": {
            "O": {"q": 6, "basis": "DZVP-MOLOPT-SR-GTH", "potential": "GTH-PBE-q6"},
            "H": {"q": 1, "basis": "DZVP-MOLOPT-SR-GTH", "potential": "GTH-PBE-q1"}}, "errors": {}}
        with tempfile.TemporaryDirectory() as d:
            for periodic in ("XYZ", "NONE"):
                old, new = pathlib.Path(d) / f"old-{periodic}", pathlib.Path(d) / f"new-{periodic}"
                subprocess.run([sys.executable, "-I",
                                str(root / "codes/common/structure-making/cp2k/make_subsys.py"),
                                str(fx), "--data-dir", str(data), "--periodic", periodic,
                                "--out", str(old)], check=True, capture_output=True, timeout=60)
                r = hsrv.cp2k_make_subsys(str(fx), kinds, str(new), periodic=periodic)
                a, b = old.read_text().splitlines(), new.read_text().splitlines()
                assert a[:1] + a[2:] == b[:1] + b[2:], (a, b)
                assert r["electrons"] == 8 and r["formula"] == "O1H2", r
            assert any("below 10 A" in w for w in
                       hsrv.cp2k_make_subsys(str(fx), kinds["kinds"], str(pathlib.Path(d) / "k"))["warnings"])
            for args, text in (((str(fx), {"O": kinds["kinds"]["O"]}, str(pathlib.Path(d) / "m")), "no kind for ['H']"),
                               ((str(fx), kinds, str(pathlib.Path(d) / "new-XYZ")), "exists"),
                               ((str(fx), {"O": {"q": 6}, "H": {"q": 1}}, str(pathlib.Path(d) / "q")), "needs q")):
                try:
                    hsrv.cp2k_make_subsys(*args)
                except ValueError as e:
                    assert text in str(e), e
                else:
                    raise AssertionError(f"cp2k_make_subsys accepted {args[1:]}")

        probe = ("import asyncio; from hyogo.server import mcp; "
                 "print(sorted(t.name for t in asyncio.run(mcp.list_tools())))")
        with tempfile.TemporaryDirectory() as home:
            out = subprocess.run([sys.executable, "-c", probe], cwd=root,
                                 env={**os.environ, "HOME": home},
                                 capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        live = sorted(re.findall(r"^\| `(\w+)` \|(?:[^|]*\|){4} live \|",
                                 (root / "hyogo" / "INVENTORY.md").read_text(), re.M))
        assert out.stdout.strip() == str(live), (
            f"hyogo serves {out.stdout.strip()}, INVENTORY.md lists {live} as live")

        groups = {"rookery": ("hpc_agent_core", "hpc_mcp", "hpc_codes", "facilities"),
                  "yagura": ("yagura",), "hyogo": ("hyogo",)}
        for name, pkgs in groups.items():
            others = {p for g, ps in groups.items() if g != name for p in ps}
            for pkg in pkgs:
                for f in (root / pkg).rglob("*.py"):
                    for node in ast.walk(ast.parse(f.read_text())):
                        mods = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                                [node.module] if isinstance(node, ast.ImportFrom) and node.module
                                and not node.level else [])
                        bad = [m for m in mods if m.split(".")[0] in others]
                        assert not bad, f"{f.relative_to(root)} ({name}) imports {bad}"

    def hyogo_make_input_matches_the_templates_and_uio66():
        """cp2k_make_input writes, after dropping comments and expanding @SET,
        the four revPBE templates and the five hand-written UiO-66 functional
        inputs (whose cp2k --check passed); cp2k_make_subsys writes their
        AUX_FIT lines; each refusal fires before anything is written."""
        import re
        from hyogo import server as hsrv
        root = pathlib.Path(__file__).resolve().parent.parent
        fx = root / "tests" / "fixtures" / "cp2k_inputs" / "uio66"

        def norm(text, fixed=None):
            """Drop comments and blanks, run @SET/@IF/@ENDIF as CP2K's preprocessor
            does (`fixed` pins variables, e.g. {"RESTART": "1"}), expand ${...}."""
            sets, out, live = dict(fixed or {}), [], []
            for line in text.splitlines():
                s = line.strip()
                if not s or s.startswith(("#", "!")):
                    continue
                s = re.sub(r"\$\{(\w+)\}", lambda m: sets[m.group(1)], s)
                if s.upper() == "@ENDIF":
                    live.pop()
                    continue
                m = re.match(r"@IF\s+(\S+)\s*(==|/=)\s*(\S+)$", s)
                if m:
                    live.append((m.group(1) == m.group(3)) == (m.group(2) == "=="))
                    continue
                if not all(live):
                    continue
                m = re.match(r"@SET\s+(\S+)\s+(.*)", s)
                if m:
                    if m.group(1) not in (fixed or {}):
                        sets[m.group(1)] = m.group(2).strip()
                    continue
                out.append(" ".join(s.split()).upper())
            return out

        q = {"H": 1, "C": 4, "O": 6, "Zr": 12}

        def uzh(fam, stem, aux):
            k = {e: {"q": n, "basis": f"DZVP-MOLOPT-{fam}-GTH-q{n}", "potential": f"{stem}-q{n}",
                     **({"aux_basis": f"admm-dzp-q{n}"} if aux else {})} for e, n in q.items()}
            return {"files": {"basis": ["BASIS_MOLOPT_UZH_2026.2"] + (["BASIS_ADMM_UZH"] if aux else []),
                              "potential": "POTENTIAL_UZH_2026.2"}, "kinds": k, "errors": {}}

        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            xyz = d / "uio66.xyz"
            xyz.write_text((fx / "uio66_prim_114_ch108.xyz").read_text())
            for f, fam, stem, aux in (("pbe", "PBE", "GTH-PBE", False), ("r2scan", "SCAN", "GTH-SCAN", False),
                                      ("pbe0", "PBE0", "GTH-PBE0", True), ("hse06", "PBE0", "GTH-PBE0", True),
                                      ("r2scan0", "SCAN", "GTH-SCAN", True)):
                k = uzh(fam, stem, aux)
                sub = hsrv.cp2k_make_subsys(str(xyz), k, str(d / f"{f}.inc"))
                if aux:
                    a = (d / f"{f}.inc").read_text().splitlines()
                    b = (fx / f"subsys_{'pbe0' if fam == 'PBE0' else 'scan'}_admm.inc").read_text().splitlines()
                    assert a[:1] + a[2:] == b[:1] + b[2:], f
                r = hsrv.cp2k_make_input("sp", f, sub, k, project=f"sp-{f}", cutoff=600,
                                         print_gap=True, save_wfn=True,
                                         hfx={"memory_mb": 2000} if aux else None)
                assert r["out"] == str(d / f"sp-{f}.inp"), r
                # the hand-written inputs leave CHARGE/MULTIPLICITY/UKS at CP2K's
                # defaults, and the GGA ones list BASIS_ADMM_UZH though unused
                got = [l for l in norm(pathlib.Path(r["out"]).read_text())
                       if not l.startswith(("CHARGE ", "MULTIPLICITY ", "UKS "))]
                want = [l for l in norm((fx / f"sp_{f}.inp").read_text())
                        if aux or l != "BASIS_SET_FILE_NAME BASIS_ADMM_UZH"]
                assert got == want, (f, [l for l in got if l not in want], [l for l in want if l not in got])
                assert r["settings"].get("HFX_RADIUS") == (5.9 if aux else None), r
                if f == "hse06":  # erfc(0.11 r)/r = 1e-6 at 14.3 A, capped below half of 11.97 A
                    assert any("reaches 14.3 A" in w for w in r["warnings"]), r

            legacy = {e: {"q": n, "basis": "DZVP-MOLOPT-SR-GTH", "potential": f"GTH-PBE-q{n}"}
                      for e, n in q.items()}
            sub = hsrv.cp2k_make_subsys(str(xyz), legacy, str(d / "rev.inc"))
            for rt, tpl in (("sp", "single-point/templates/single_point.inp"),
                            ("geo_opt", "geo-opt/templates/geo_opt.inp"),
                            ("cell_opt", "geo-opt/templates/cell_opt.inp"),
                            ("md_nvt", "aimd/templates/aimd_nvt.inp")):
                r = hsrv.cp2k_make_input(rt, "revpbe", sub, out_path=str(d / f"{rt}.inp"))
                got = norm(pathlib.Path(r["out"]).read_text())
                want = norm((root / "codes" / "cp2k" / tpl).read_text())
                assert got == want, (rt, [l for l in got if l not in want], [l for l in want if l not in got])

            # odd electrons give UKS; PERIODIC NONE gives &POISSON; duration sets WALLTIME
            water = root / "tests" / "fixtures" / "structures" / "clean.xyz"
            sub = hsrv.cp2k_make_subsys(str(water), {e: legacy[e] for e in ("O", "H")},
                                        str(d / "w.inc"), periodic="NONE", charge=1)
            r = hsrv.cp2k_make_input("md_nvt", "revpbe", sub, out_path=str(d / "w.inp"),
                                     duration="02:00:00", md={"timestep": 1.0})
            text = pathlib.Path(r["out"]).read_text()
            assert r["settings"]["UKS"] == ".TRUE." and r["settings"]["MULTIPLICITY"] == 2, r
            assert r["settings"]["CHARGE"] == 1 and r["settings"]["WALLTIME"] == 7200 - 900, r
            assert "PERIODIC NONE" in text and "&POISSON" in text, text
            assert any("hydrogen" in w for w in r["warnings"]), r

            pbe0 = uzh("PBE0", "GTH-PBE0", True)
            sub0 = hsrv.cp2k_make_subsys(str(xyz), pbe0, str(d / "p0.inc"))
            # a cell wider than twice the erfc range keeps CP2K's own range, uncapped
            wide = {**sub0, "widths": [40.0, 40.0, 40.0]}
            r = hsrv.cp2k_make_input("sp", "hse06", wide, pbe0, out_path=str(d / "wide.inp"),
                                     stress=True, guess_wfn="../pbe/pbe-RESTART.wfn",
                                     precision={"eps_pgf_orb": 1e-20}, hfx={"eps_schwarz": 1e-7})
            text = pathlib.Path(r["out"]).read_text()
            assert r["settings"]["HFX_RADIUS"] == 15.9 and not any("capped" in w for w in r["warnings"]), r
            for want in ("STRESS_TENSOR ANALYTICAL", "STRESS_UNIT GPa", "SCF_GUESS RESTART",
                         "WFN_RESTART_FILE_NAME ../pbe/pbe-RESTART.wfn", "SCREEN_ON_INITIAL_P  TRUE",
                         "EPS_PGF_ORB 1.0E-20", "EXCH_CORRECTION_FUNC     DEFAULT",
                         "MIN_PAIR_LIST_RADIUS -1", "EPS_SCHWARZ_FORCES   ${HFX_EPS_SCHWARZ}",
                         "@SET HFX_EPS_SCHWARZ  1.0E-7"):
                assert want in text, (want, text)
            assert not any("atomic guess" in w for w in r["warnings"]), r
            # purpose="guess": PBE in the hybrid's basis and potentials, wavefunction kept
            g = hsrv.cp2k_make_input("sp", "pbe", sub0, pbe0, purpose="guess")
            text = pathlib.Path(g["out"]).read_text()
            assert g["out"] == str(d / "guess.inp") and g["wfn"] == "guess-RESTART.wfn", g
            for want in ("RUN_TYPE    ENERGY", "# GUESS ONLY", "&RESTART ON", "SCF_GUESS ATOMIC",
                         "MAX_SCF   50", "POTENTIAL_FILE_NAME POTENTIAL_UZH_2026.2"):
                assert want in text, (want, text)
            assert "&HF" not in text and "AUXILIARY_DENSITY" not in text, text
            assert any("NOT a result" in w for w in g["warnings"]), g
            h = hsrv.cp2k_make_input("sp", "hse06", sub0, pbe0, out_path=str(d / "h.inp"),
                                     guess_wfn=g["wfn"])
            assert "MAX_SCF   30" in pathlib.Path(h["out"]).read_text(), h
            plain0 = uzh("PBE0", "GTH-PBE0", False)
            subp = hsrv.cp2k_make_subsys(str(xyz), plain0, str(d / "p.inc"))
            scan = uzh("SCAN", "GTH-SCAN", False)
            subs = hsrv.cp2k_make_subsys(str(xyz), scan, str(d / "s.inc"))
            for args, kw, text in (
                    (("sp", "r2scan", sub, {"files": {"basis": ["BASIS_MOLOPT"],
                                                      "potential": "GTH_POTENTIALS"}}), {},
                     "needs GTH-SCAN potentials"),
                    (("sp", "pbe0", subp, plain0), {}, "uses ADMM"),
                    (("sp", "pbe0", sub0, pbe0), {"hfx": {"radius": 6.0}}, "below half"),
                    (("sp", "hse06", sub0, pbe0), {"hfx": {"radius": 6.0}}, "below half"),
                    (("sp", "hse06", sub0, pbe0), {"hfx": {"eps_schwarz": 0.1}}, "small threshold"),
                    (("geo_opt", "pbe0", sub0, pbe0), {"stress": True}, "stress is for sp"),
                    (("sp", "pbe0", sub0, pbe0), {"guess_wfn": "a b.wfn"}, "without spaces"),
                    (("sp", "pbe", sub0, pbe0), {}, "needs GTH-PBE potentials"),
                    (("sp", "hse06", sub0, pbe0), {"purpose": "guess"}, "the hybrid itself"),
                    (("geo_opt", "pbe", sub0, pbe0), {"purpose": "guess"}, "single point"),
                    (("sp", "pbe", sub0), {"purpose": "guess"}, "hybrid's data files"),
                    (("sp", "pbe", sub0, pbe0), {"purpose": "seed"}, "purpose is"),
                    (("sp", "revpbe", sub), {"precision": {"eps": 1}}, "precision takes"),
                    (("sp", "r2scan", subs, scan), {"d3": "zero"}, "only with BJ"),
                    (("sp", "r2scan", subs), {}, "pass cp2k_kinds' result"),
                    (("neb", "revpbe", sub), {}, "run_type is one of"),
                    (("sp", "b3lyp", sub), {}, "functional is one of"),
                    (("sp", "revpbe", sub), {"multiplicity": 1}, "impossible"),
                    (("geo_opt", "revpbe", sub), {"duration": "12h"}, "HH:MM:SS"),
                    (("sp", "revpbe", sub), {"md": {"steps": 5}}, "md is for md_nvt"),
                    (("sp", "revpbe", sub), {"out_path": str(d / "w.inp")}, "exists")):
                try:
                    hsrv.cp2k_make_input(*args, **kw)
                except ValueError as e:
                    assert text in str(e), (text, e)
                else:
                    raise AssertionError(f"cp2k_make_input accepted {args[:2]} {kw}")

            # G18: the user's PBE-D3(BJ) cell_opt (cubic kept cubic, tight forces,
            # restart after a walltime stop). Compared: &CELL_OPT, the restart block
            # and SYMMETRY in &CELL; the DFT block and includes are that run's own.
            src = root / "tests" / "fixtures" / "cp2k_inputs" / "uio66_pbe_cellopt"
            conv = d / "conv.xyz"
            cell_inc = (src / "cell.inc").read_text()
            a = float(re.search(r"^\s*A\s+(\S+)", cell_inc, re.M).group(1))
            xyz_lines = (fx / "uio66_prim_114_ch108.xyz").read_text().splitlines()
            conv.write_text("\n".join([xyz_lines[0], f'Lattice="{a} 0 0 0 {a} 0 0 0 {a}" Properties=species:S:1:pos:R:3']
                                      + xyz_lines[2:]) + "\n")
            pbe = uzh("PBE", "GTH-PBE", False)
            subc = hsrv.cp2k_make_subsys(str(conv), pbe, str(d / "c.inc"), symmetry="CUBIC")
            assert subc["symmetry"] == "CUBIC" and "  SYMMETRY CUBIC" in (d / "c.inc").read_text(), subc
            assert norm(cell_inc)[-2:] == ["SYMMETRY CUBIC", "&END CELL"], norm(cell_inc)
            opt = {"max_iter": 300, "max_force": 1e-4, "rms_force": 5e-5,
                   "keep_angles": True, "keep_symmetry": True}
            r = hsrv.cp2k_make_input("cell_opt", "pbe", subc, pbe, out_path=str(d / "co.inp"),
                                     project="pbe", cutoff=1000, opt=opt)
            assert r["restart"] == {"set": "RESTART", "file": "pbe-1.restart", "wfn": "pbe-RESTART.wfn"}, r
            # G21: the stress-cutoff warning fires for cell_opt below 800 Ry only
            stress_warn = "below 800 Ry"
            assert not any(stress_warn in w for w in r["warnings"]), r  # cell_opt at 1000 Ry
            for rt in ("sp", "geo_opt", "cell_opt"):  # each at its default cutoff
                w = hsrv.cp2k_make_input(rt, "pbe", subc, pbe, out_path=str(d / f"g21-{rt}.inp"))
                assert w["settings"]["CUTOFF"] == (600 if rt == "cell_opt" else 450), w
                assert any(stress_warn in x for x in w["warnings"]) == (rt == "cell_opt"), w
            for rt in ("sp", "geo_opt"):
                w = hsrv.cp2k_make_input(rt, "pbe", subc, pbe, out_path=str(d / f"g21-{rt}-lo.inp"),
                                         cutoff=300)
                assert not any(stress_warn in x for x in w["warnings"]), w
            assert r["opt"] == {**opt}, r

            def section(lines, name):
                i = lines.index(f"&{name}")
                return lines[i:lines.index(f"&END {name}", i) + 1]
            theirs = norm("@SET RESTART 1\n" + (src / "opt.inp").read_text().split("&FORCE_EVAL")[0]
                          .replace("@INCLUDE", "# @INCLUDE"))
            text = pathlib.Path(r["out"]).read_text()
            for restart in ("0", "1"):
                # the one named difference: we write CP2K's default unit, [bar], explicitly
                ours = [l.replace(" [BAR]", "") for l in norm(text, {"RESTART": restart})]
                assert section(ours, "CELL_OPT") == section(theirs, "CELL_OPT"), \
                    (section(ours, "CELL_OPT"), section(theirs, "CELL_OPT"))
                ext = ["&EXT_RESTART", "RESTART_FILE_NAME PBE-1.RESTART", "&END EXT_RESTART"]
                assert (section(ours, "EXT_RESTART") == ext) if restart == "1" else ("&EXT_RESTART" not in ours)
                assert ("SCF_GUESS RESTART" if restart == "1" else "SCF_GUESS ATOMIC") in ours, (restart, ours)
            assert section(theirs, "EXT_RESTART") == ext, section(theirs, "EXT_RESTART")
            # from a hybrid guess: RESTART 0 reads guess_wfn, RESTART 1 the run's own wavefunction
            r = hsrv.cp2k_make_input("cell_opt", "hse06", {**sub0, "symmetry": None}, pbe0,
                                     out_path=str(d / "hco.inp"), guess_wfn="../sp/sp-RESTART.wfn")
            text = pathlib.Path(r["out"]).read_text()
            assert "WFN_RESTART_FILE_NAME ../SP/SP-RESTART.WFN" in norm(text, {"RESTART": "0"})
            assert "WFN_RESTART_FILE_NAME CELLOPT-RESTART.WFN" in norm(text, {"RESTART": "1"})
            for rt in ("geo_opt", "md_nvt"):
                r = hsrv.cp2k_make_input(rt, "revpbe", sub, out_path=str(d / f"r-{rt}.inp"))
                assert r["restart"]["file"] == f"{'opt' if rt == 'geo_opt' else 'md'}-1.restart", r
            assert hsrv.cp2k_make_input("sp", "revpbe", sub, out_path=str(d / "r-sp.inp"))["restart"] is None
            for kw, text in (({"symmetry": "CUBIC"}, "not CUBIC"),            # 60 deg primitive cell
                             ({"symmetry": "FCC"}, "symmetry is one of"),
                             ({"symmetry": "RHOMBOHEDRAL", "periodic": "NONE"}, "periodic cell")):
                try:
                    hsrv.cp2k_make_subsys(str(xyz), pbe, str(d / "bad.inc"), **kw)
                except ValueError as e:
                    assert text in str(e), (text, e)
                else:
                    raise AssertionError(f"cp2k_make_subsys accepted {kw}")
            assert hsrv.cp2k_make_subsys(str(xyz), pbe, str(d / "rh.inc"), symmetry="RHOMBOHEDRAL")["symmetry"]
            for args, kw, text in (
                    (("cell_opt", "pbe", subc, pbe), {"opt": {"rms_force": 1e-3}}, "at most max_force"),
                    (("cell_opt", "pbe", subc, pbe), {"opt": {"max_force": 5}}, "Ha/bohr"),
                    (("geo_opt", "pbe", subc, pbe), {"opt": {"keep_angles": True}}, "for cell_opt"),
                    (("cell_opt", "revpbe", sub), {"opt": {"keep_symmetry": True}}, "symmetry=")):
                try:
                    hsrv.cp2k_make_input(*args, out_path=str(d / "bad.inp"), overwrite=True, **kw)
                except ValueError as e:
                    assert text in str(e), (text, e)
                else:
                    raise AssertionError(f"cp2k_make_input accepted {args[:2]} {kw}")

    def cp2k_record_from_run_records_a_parsed_run():
        """G24: a cp2k_parse_output result plus `like` (a record of the same
        system and method) becomes a benchmarks record with the id, timing
        and extra fields the hand-written 2026-11-05 records had; refusals
        append nothing."""
        import gzip
        from hyogo.cp2k import output
        from hpc_codes import records, server
        root = pathlib.Path(__file__).resolve().parent.parent
        fx = root / "tests" / "fixtures" / "cp2k_outputs"
        parse = lambda n: output.parse(gzip.decompress((fx / f"{n}.out.gz").read_bytes()).decode())
        sp, opt = parse("r2scan-sp"), parse("hse06-cellopt")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}):
            env = {k: v for k, v in os.environ.items() if k != "HPC_RECORDS_ROOT"}
            with patch.dict(os.environ, env, clear=True):
                server.records_append("hokusai", "software_registry", {
                    "build_id": "cp2k-2026.2-fake", "facility": "hokusai", "code": "cp2k",
                    "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"},
                    "toolchain": "fake", "install_path": tmp, "env_script": tmp + "/env.sh",
                    "test_status": {"level": "none", "result": "pass"}, "default": True,
                    "date": "2026-11-04"})
                like = {"id": "hokusai-cp2k-2026-11-04-uio66conv-singlepoint-pbe-1n-16x7",
                        "facility": "hokusai", "partition": "mpc", "node_type": "MPC test",
                        "code": "cp2k", "version": "2026.2", "build_id": "cp2k-2026.2-fake",
                        "system": {"label": "UiO-66-conv", "atoms": 456,
                                   "elements": {"Zr": 24, "O": 128, "C": 192, "H": 112},
                                   "cell_volume_A3": 9115, "periodic": "xyz", "kpoints": "gamma"},
                        "method": {"functional": "PBE", "dispersion": "D3(BJ)", "basis": "DZVP",
                                   "cutoff_ry": 1000, "hybrid": False, "scf": "OT DIIS"},
                        "run_type": "single_point", "nodes": 1, "ranks_per_node": 16,
                        "threads_per_rank": 7, "metric": {"name": "wall_s", "value": 100.0},
                        "date": "2026-11-04", "valid": True}
                server.records_append("hokusai", "benchmarks", like)
                count = lambda: len(records.query("hokusai", "benchmarks", {}))

                r = server.cp2k_record_from_run("hokusai", sp, 1, like=like["id"],
                                                system={"cell_volume_A3": 9115},
                                                method={"functional": "r2SCAN"}, job_id="9737100")
                rec = r["record"]
                assert r["appended"] and r["valid"] and not r["warnings"], r
                assert r["id"] == "hokusai-cp2k-2026-11-05-uio66conv-singlepoint-r2scan-1n-16x7", r["id"]
                assert rec["metric"] == {"name": "wall_s", "value": 279.886}, rec["metric"]
                assert (rec["ranks_per_node"], rec["threads_per_rank"]) == (16, 7)
                assert rec["method"]["functional"] == "r2SCAN" and rec["method"]["cutoff_ry"] == 1000
                assert rec["system"]["electrons"] == 1936 and rec["system"]["cell_volume_A3"] == 9115
                assert rec["extra"] == {"forces_s": 248.6, "scf_steps": 55,
                                        "peak_mib_per_rank": 3276, "core_hours": 8.7}, rec["extra"]
                assert rec["job_id"] is None and r["provenance"]["job_ids"] == ["9737100"]
                assert records.query("hokusai", "benchmarks_provenance", {"id": r["id"]})

                r = server.cp2k_record_from_run("hokusai", opt, 2, like=like["id"],
                                                method={"functional": "HSE06", "hybrid": True},
                                                dry_run=True)
                rec = r["record"]
                assert not r["appended"] and r["valid"], r
                assert r["id"] == "hokusai-cp2k-2026-11-05-uio66conv-cellopt-hse06-2n-16x7", r["id"]
                assert rec["metric"] == {"name": "s_per_step", "value": 423.159}, rec["metric"]
                assert rec["extra"]["opt_steps"] == 21 and rec["extra"]["final_pressure_bar"] == 7.34
                assert rec["extra"]["hfx_eri_mib"] == 18952 and rec["extra"]["core_hours"] == 552.9
                assert rec["extra"]["first_step_s"] == 439.4 and len(opt["opt"]["step_times_s"]) == 22
                assert "converged in 21 steps" in rec["notes"] and r["provenance"] is None
                # G32: an optimisation's volume is its own final cell, not like's
                assert rec["system"]["cell_volume_A3"] == round(opt["cell"]["volume"], 1) != 9115, rec
                # G32: a single point prints no cell, so like's volume (maybe another
                # geometry) is dropped with a warning instead of copied
                r = server.cp2k_record_from_run("hokusai", sp, 1, like=like["id"], tag="g32",
                                                dry_run=True)
                assert "cell_volume_A3" not in r["record"]["system"], r["record"]["system"]
                assert any("cell_volume_A3 not copied" in w for w in r["warnings"]), r["warnings"]

                # an optimisation cut by the walltime: still a valid per-step time, warned
                text = gzip.decompress((fx / "hse06-cellopt.out.gz").read_bytes()).decode()
                cut = output.parse(text[: text.index(" OPTIMIZATION STEP:     12\n")])
                r = server.cp2k_record_from_run("hokusai", cut, 2, like=like["id"], tag="cut",
                                                dry_run=True)
                rec = r["record"]
                assert r["valid"] and rec["extra"]["opt_steps"] == 11, rec
                times = cut["opt"]["step_times_s"]
                assert len(times) == 12 and rec["extra"]["first_step_s"] == 439.4, times
                assert abs(rec["metric"]["value"] - sum(times) / 11) < 1e-3, rec["metric"]
                assert any("did not converge" in w for w in r["warnings"]), r["warnings"]
                # unconverged SCF: recorded, but not as a usable timing
                bad = {**sp, "scf": {**sp["scf"], "not_converged": 1}}
                r = server.cp2k_record_from_run("hokusai", bad, 1, like=like["id"], tag="bad",
                                                dry_run=True)
                assert not r["valid"] and "SCF not converged" in r["record"]["reason"], r

                before = count()
                for args, kw, msg in (
                        ((sp, 1), {"like": like["id"]}, "already exists"),   # the r2SCAN record above
                        ((sp, 3), {"like": like["id"]}, "do not divide"),
                        ((sp, 1), {"like": "no-such-record"}, "no benchmarks record"),
                        ((sp, 1), {}, "pass like="),
                        (({**sp, "natoms": 455}, 1), {"like": like["id"]}, "atoms"),
                        (({k: v for k, v in sp.items() if k != "ranks"}, 1), {"like": like["id"]},
                         "older cp2k_parse_output"),
                        (({"energy": 1}, 1), {"like": like["id"]}, "cp2k_parse_output result")):
                    try:
                        server.cp2k_record_from_run("hokusai", *args,
                                                    **({"method": {"functional": "r2SCAN"}}
                                                       if msg == "already exists" else {}), **kw)
                    except ValueError as e:
                        assert msg in str(e), (msg, str(e))
                    else:
                        raise AssertionError(f"expected a refusal naming {msg!r}")
                assert count() == before, "a refused call appended a record"
                assert not server.records_check("hokusai")["errors"]

    def hyogo_parse_output_reads_the_hokusai_runs():
        """hyogo's cp2k_parse_output gives the values read by hand from four
        HOKUSAI UiO-66 outputs (tests/fixtures/cp2k_outputs/README.md),
        agrees with rookery's parse_bench on energy and forces, flags a cut
        cell_opt as unfinished, and refuses a missing or non-CP2K file."""
        import gzip
        from hyogo import server as hsrv
        from hpc_codes.cp2k import parse_bench
        root = pathlib.Path(__file__).resolve().parent.parent
        fx = root / "tests" / "fixtures" / "cp2k_outputs"
        # hand-read values, 2026-11-06 (notes kept outside the repo)
        want = {
            "r2scan-sp": dict(run_type="ENERGY_FORCE", energy_ha=-4331.274668682077, gap_ev=3.363827,
                              fmax=8.10922350e-3, trace=-1.21111259818, steps=[55], outer=[2],
                              cp2k_s=279.886, hfx=None),
            "pbe0-sp": dict(run_type="ENERGY_FORCE", energy_ha=-4334.544226923467, gap_ev=4.664273,
                            fmax=1.01534426e-2, trace=-0.955742000377, steps=[6], outer=[1],
                            cp2k_s=581.987, hfx=32019),
            "hse06-sp": dict(run_type="ENERGY_FORCE", energy_ha=-4335.04533898211, gap_ev=3.986995,
                             fmax=None, trace=-0.836146728794, steps=[8], outer=[1],
                             cp2k_s=463.925, hfx=18501),
            "hse06-cellopt": dict(run_type="CELL_OPT", energy_ha=-4335.056972776092, gap_ev=4.034722,
                                  fmax=None, trace=None, steps=None, outer=None,
                                  cp2k_s=8886.337, hfx=18952),
        }
        close = lambda a, b, tol=1e-9: abs(a - b) <= tol * max(1.0, abs(b))
        with tempfile.TemporaryDirectory() as d:
            for name, w in want.items():
                out = pathlib.Path(d) / f"{name}.out"
                out.write_bytes(gzip.decompress((fx / f"{name}.out.gz").read_bytes()))
                r = hsrv.cp2k_parse_output(str(out))
                assert r["finished"] and r["run_type"] == w["run_type"], (name, r["run_type"])
                assert r["natoms"] == 456 and r["electrons"] == 1936, name
                assert r["started"] == "2026-11-05" and r["threads"] == 7, (name, r["started"])
                assert r["ranks"] == (16 if name == "r2scan-sp" else 32), (name, r["ranks"])
                assert close(r["energy_ha"], w["energy_ha"]) and close(r["gap_ev"], w["gap_ev"]), name
                assert r["scf"]["not_converged"] == 0, name
                if w["steps"] is not None:
                    assert r["scf"]["steps"] == w["steps"] and r["scf"]["outer"] == w["outer"], r["scf"]
                if w["fmax"] is not None:
                    assert close(r["forces"]["max"], w["fmax"], 1e-6), (name, r["forces"])
                if w["trace"] is not None:
                    assert close(r["stress_gpa"]["third_trace"], w["trace"]), name
                assert close(r["cost"]["cp2k_s"], w["cp2k_s"]), name
                assert (r["cost"]["hfx"] or {}).get("eri_ram_mib") == w["hfx"], (name, r["cost"]["hfx"])
                # rookery's benchmark parser reads the same energies and forces
                b = parse_bench.parse_out(out)
                assert b["done"] and close(r["energy_ha"], b["energies"][-1]), name
                if r["forces"]:
                    comps = b["forces"]
                    fmax = max((comps[i] ** 2 + comps[i + 1] ** 2 + comps[i + 2] ** 2) ** 0.5
                               for i in range(0, len(comps), 3))
                    assert close(r["forces"]["max"], fmax), (name, r["forces"]["max"], fmax)
                if name == "hse06-cellopt":
                    o, c = r["opt"], r["cell"]
                    assert o["converged"] and o["steps"] == 21 and not o["max_iter_hit"], o
                    assert close(o["internal_pressure_bar"], 7.3393678606), o
                    assert close(o["criteria"]["max_gradient"]["value"], 8.45438e-5), o["criteria"]
                    assert o["criteria"]["max_gradient"]["limit"] == 1e-4, o["criteria"]
                    assert all(v["converged"] for v in o["criteria"].values()), o["criteria"]
                    assert close(c["a"], 20.831819) and c["a"] == c["b"] == c["c"], c
                    assert c["alpha"] == c["beta"] == c["gamma"] == 90.0, c
                    assert len(r["scf"]["steps"]) == 23, r["scf"]
                    assert any("hfx_types.F:762" in x and "14.279 A" in x for x in r["warnings"]), \
                        r["warnings"]
                    assert not any("RESTART" in x for x in r["warnings"]), r["warnings"]
                    # cut mid-optimisation: no PROGRAM ENDED, not converged
                    text = out.read_text().splitlines()
                    cut = next(i for i, l in enumerate(text) if "OPTIMIZATION STEP:     10" in l)
                    part = pathlib.Path(d) / "cut.out"
                    part.write_text("\n".join(text[:cut]) + "\n")
                    p = hsrv.cp2k_parse_output(str(part))
                    assert not p["finished"] and not p["opt"]["converged"], p["opt"]
                    assert p["opt"]["steps"] == 9, p["opt"]
                    assert any("no PROGRAM ENDED" in x for x in p["warnings"]), p["warnings"]
                    # finished but not converged (as after WALLTIME): the restart switch
                    stop = pathlib.Path(d) / "stop.out"
                    stop.write_text("\n".join(text[:cut] + [" **** **** ******  **  PROGRAM ENDED AT"]))
                    p = hsrv.cp2k_parse_output(str(stop))
                    assert any("@SET RESTART 1" in x for x in p["warnings"]), p["warnings"]
            for args, text in (((str(pathlib.Path(d) / "missing.out"),), "fs_download"),
                               ((str(root / "hyogo" / "INVENTORY.md"),), "not a CP2K output")):
                try:
                    hsrv.cp2k_parse_output(*args)
                except ValueError as e:
                    assert text in str(e), e
                else:
                    raise AssertionError(f"cp2k_parse_output accepted {args}")

    def hyogo_chains_one_run_into_the_next():
        """G27: cp2k_parse_output names the files a follow-up run needs (the
        final xyz/cif CP2K printed, the wfn and restart), and cp2k_make_input
        turns a previous run's parse into guess_wfn (guess_from, guess_dir),
        refusing a wavefunction from other atoms, electrons, basis or
        potential files, and warning on an unfinished or unconverged run."""
        import copy
        import gzip
        from hyogo import server as hsrv
        root = pathlib.Path(__file__).resolve().parent.parent
        fx = root / "tests" / "fixtures" / "cp2k_outputs"
        q = {"H": 1, "C": 4, "O": 6, "Zr": 12}
        kinds = {"files": {"basis": ["BASIS_MOLOPT_UZH_2026.2", "BASIS_ADMM_UZH"],
                           "potential": "POTENTIAL_UZH_2026.2"},
                 "kinds": {e: {"q": n, "basis": f"DZVP-MOLOPT-PBE0-GTH-q{n}",
                               "potential": f"GTH-PBE0-q{n}", "aux_basis": f"admm-dzp-q{n}"}
                           for e, n in q.items()},
                 "errors": {}}
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            parsed = {}
            for name in ("hse06-cellopt", "hse06-sp"):
                out = d / f"{name}.out"
                out.write_bytes(gzip.decompress((fx / f"{name}.out.gz").read_bytes()))
                parsed[name] = hsrv.cp2k_parse_output(str(out))
            f = parsed["hse06-cellopt"]["files"]
            assert f == {"final_xyz": "hse06-cellopt-FINAL-1_21.xyz",
                         "final_cif": "hse06-cellopt-FINAL-1_21.cif",
                         "basis": "BASIS_MOLOPT_UZH_2026.2", "potential": "POTENTIAL_UZH_2026.2",
                         "wfn": "hse06-cellopt-RESTART.wfn", "restart": "hse06-cellopt-1.restart"}, f
            f = parsed["hse06-sp"]["files"]
            assert f["final_xyz"] is None and f["final_cif"] is None and f["restart"] is None, f
            assert f["wfn"] == "hse06-sp-RESTART.wfn", f

            # a subsys standing in for the 456-atom cell: the primitive fixture,
            # with the conventional cell's atom and electron counts
            xyz = d / "uio66.xyz"
            xyz.write_text((root / "tests" / "fixtures" / "cp2k_inputs" / "uio66"
                            / "uio66_prim_114_ch108.xyz").read_text())
            sub = hsrv.cp2k_make_subsys(str(xyz), kinds, str(d / "subsys.inc"))
            sub = {**sub, "natoms": 456, "electrons": 1936}
            prev = parsed["hse06-cellopt"]
            r = hsrv.cp2k_make_input("sp", "pbe0", sub, kinds, out_path=str(d / "pbe0.inp"),
                                     stress=True, guess_from=prev, guess_dir="../hse06-cellopt/")
            wfn = "../hse06-cellopt/hse06-cellopt-RESTART.wfn"
            text = (d / "pbe0.inp").read_text()
            assert f"WFN_RESTART_FILE_NAME {wfn}" in text and "SCF_GUESS RESTART" in text, text
            assert r["guess"] == {"wfn": wfn, "from_project": "hse06-cellopt",
                                  "checked": ["natoms", "electrons", "basis", "potential"],
                                  "final_xyz": "hse06-cellopt-FINAL-1_21.xyz"}, r["guess"]
            assert not any("guess_from" in w for w in r["warnings"]), r["warnings"]
            assert not any("atomic guess" in w for w in r["warnings"]), r["warnings"]
            # same folder by default; an opt keeps the wfn as its @SET WFN
            r = hsrv.cp2k_make_input("cell_opt", "hse06", sub, kinds, out_path=str(d / "co.inp"),
                                     guess_from=parsed["hse06-sp"])
            assert r["guess"]["wfn"] == "hse06-sp-RESTART.wfn", r["guess"]
            assert r["settings"]["WFN"] == "hse06-sp-RESTART.wfn", r["settings"]
            # warnings: unfinished, unconverged SCF, unconverged optimisation
            bad = copy.deepcopy(prev)
            bad["finished"] = False
            bad["scf"]["not_converged"] = 2
            bad["opt"]["converged"] = False
            r = hsrv.cp2k_make_input("sp", "pbe0", sub, kinds, out_path=str(d / "w.inp"),
                                     guess_from=bad, guess_dir="../x")
            ws = [w for w in r["warnings"] if w.startswith("guess_from")]
            assert len(ws) == 3, r["warnings"]

            def other(**kv):
                p = copy.deepcopy(prev)
                for k, v in kv.items():
                    if k in ("basis", "potential"):
                        p["files"][k] = v
                    else:
                        p[k] = v
                return p
            other_kinds = copy.deepcopy(kinds)
            refusals = (
                (dict(guess_from=other(natoms=114)), "114 atoms"),
                (dict(guess_from=other(electrons=1932)), "1932 electrons"),
                (dict(guess_from=other(basis="BASIS_MOLOPT")), "can't move between bases"),
                (dict(guess_from=other(potential="GTH_POTENTIALS")), "other potentials"),
                (dict(guess_from=prev, guess_wfn="x.wfn"), "not both"),
                (dict(guess_dir="../x"), "goes with guess_from"),
                (dict(guess_from={"natoms": 456}), "cp2k_parse_output"),
                (dict(guess_from=prev, guess_dir="../a b"), "without spaces"),
            )
            for i, (kw, msg) in enumerate(refusals):
                dst = d / f"refused{i}.inp"
                try:
                    hsrv.cp2k_make_input("sp", "pbe0", sub, other_kinds, out_path=str(dst), **kw)
                except ValueError as e:
                    assert msg in str(e), (msg, e)
                else:
                    raise AssertionError(f"cp2k_make_input accepted {kw}")
                assert not dst.exists(), dst

    def launch_plan_picks_from_the_shipped_records():
        """launch_plan against HOKUSAI's shipped benchmarks: the match ladder,
        cost vs time, a fixed node count, the job spec, and that it answers
        an unconfigured facility (it never reaches the cluster)."""
        from hpc_codes import server
        from hpc_codes.cp2k import tools

        def must_not_run(*_):
            raise AssertionError("launch_plan reached run_command")
        env = {k: v for k, v in os.environ.items() if k != "HOKUSAI_HOST"}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, env, clear=True), no_user_config("hokusai"), \
                patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}), \
                patch.object(tools, "run_command", must_not_run):
            build = {"build_id": "cp2k-2026.2-psmp-cpu-gcc", "facility": "hokusai", "code": "cp2k",
                     "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"},
                     "toolchain": "gcc", "install_path": "software/cp2k",
                     "env_script": "~/software/cp2k/cp2k_env.sh",
                     "test_status": {"level": "none", "result": "pass"},
                     "default": True, "date": "2026-11-04"}
            server.records_append("hokusai", "software_registry", build)
            uio = {"label": "UiO-66-conv", "atoms": 456}

            cost = server.launch_plan("hokusai", "cp2k", "aimd_step", uio, steps=1000)
            assert cost["basis"]["match"] == "exact", cost
            assert cost["layout"] == {"nodes": 1, "ranks_per_node": 16, "threads_per_rank": 7}, cost
            assert cost["estimate"]["value"] == 9.34 and cost["estimate"]["suggested_duration"] == "04:30:00", cost
            assert cost["warnings"] == [], cost
            spec = cost["job_spec"]
            assert spec["pre_launch"] == "source ~/software/cp2k/cp2k_env.sh", spec
            assert spec["launcher"].startswith("srun --mpi=pmix --cpus-per-task=7 "), spec
            assert spec["environment"]["OMP_NUM_THREADS"] == "7", spec
            assert spec["resources"] == {"node_count": 1, "processes_per_node": 16,
                                         "cpu_cores_per_process": 7}, spec
            assert spec["attributes"] == {"queue_name": "mpc"}, spec

            # time: 2 nodes (82 % efficient) beats 4 (57 %, below the 70 % floor)
            fast = server.launch_plan("hokusai", "cp2k", "aimd_step", uio, optimize="time")
            assert fast["layout"]["nodes"] == 2 and fast["estimate"]["value"] == 5.72, fast

            # an unmeasured node count borrows the nearest layout, with no estimate
            three = server.launch_plan("hokusai", "cp2k", "aimd_step", uio, nodes=3)
            assert three["layout"]["nodes"] == 3 and three["estimate"] is None, three
            assert any("no timing on 3 node(s)" in w for w in three["warnings"]), three

            near = server.launch_plan("hokusai", "cp2k", "aimd_step", {"atoms": 420})
            assert near["basis"]["match"] == "similar" and near["basis"]["system"] == "UiO-66-conv", near
            assert near["estimate"]["rough"], near

            none = server.launch_plan("hokusai", "cp2k", "geo_opt", {"label": "X", "atoms": 50})
            assert none["basis"]["match"] == "none" and none["estimate"] is None, none
            assert none["layout"]["ranks_per_node"] * none["layout"]["threads_per_rank"] == 112, none

            # a removed build is refused; a facility without the pack is refused
            server.records_append("hokusai", "software_registry",
                                  {**build, "build_id": "cp2k-gone", "default": False,
                                   "removed": "2026-11-04"})
            for call, text in ((lambda: server.launch_plan("hokusai", "cp2k", "aimd_step", uio,
                                                           build_id="cp2k-gone"), "No build"),
                               (lambda: server.launch_plan("rikyu", "cp2k", "aimd_step", uio),
                                "has not opted into"),
                               (lambda: server.launch_plan("hokusai", "cp2k", "md", uio), "run_type")):
                try:
                    call()
                except ValueError as e:
                    assert text in str(e), e
                else:
                    raise AssertionError(f"launch_plan did not refuse ({text})")

    def launch_plan_falls_back_and_names_the_method():
        """G17: an optimisation with no geo_opt/cell_opt record borrows the
        same system's single_point time per step (rough, with a warning), and
        a real cell_opt record wins once there is one. G20: a recorded label
        that the method filter empties says so and lists the recorded
        methods, with no estimate."""
        from hpc_codes import server
        from hpc_codes.cp2k import tools

        def must_not_run(*_):
            raise AssertionError("launch_plan reached run_command")
        env = {k: v for k, v in os.environ.items() if k != "HOKUSAI_HOST"}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, env, clear=True), no_user_config("hokusai"), \
                patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}), \
                patch.object(tools, "run_command", must_not_run):
            build = {"build_id": "cp2k-2026.2-psmp-cpu-gcc", "facility": "hokusai", "code": "cp2k",
                     "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"},
                     "toolchain": "gcc", "install_path": "software/cp2k",
                     "env_script": "~/software/cp2k/cp2k_env.sh",
                     "test_status": {"level": "none", "result": "pass"},
                     "default": True, "date": "2026-11-04"}
            server.records_append("hokusai", "software_registry", build)
            uio = {"label": "UiO-66-conv", "atoms": 456}
            sp = server.launch_plan("hokusai", "cp2k", "single_point", uio)
            assert sp["basis"]["match"] == "exact" and sp["basis"]["run_type"] == "single_point", sp
            assert not sp["estimate"]["rough"] and sp["estimate"]["metric"] == "wall_s", sp

            # (1) only a single point: its time stands in for one step
            opt = server.launch_plan("hokusai", "cp2k", "cell_opt", uio, steps=20)
            assert opt["basis"]["match"] == "exact" and opt["basis"]["run_type"] == "single_point", opt
            assert opt["basis"]["record_ids"] == sp["basis"]["record_ids"], opt
            assert opt["layout"] == sp["layout"], opt
            est = opt["estimate"]
            assert est["metric"] == "s_per_step" and est["rough"], est
            assert est["value"] == sp["estimate"]["value"], est
            assert est["total_wall_s"] == round(est["value"] * 20, 1), est
            assert any("no cell_opt timing for UiO-66-conv" in w and "single_point" in w
                       for w in opt["warnings"]), opt
            geo = server.launch_plan("hokusai", "cp2k", "geo_opt", uio)
            assert geo["basis"]["run_type"] == "single_point" and geo["estimate"]["total_wall_s"] is None, geo
            # an unrecorded label still gets no estimate
            none = server.launch_plan("hokusai", "cp2k", "cell_opt", {"label": "X", "atoms": 50})
            assert none["basis"]["match"] == "none" and none["estimate"] is None, none
            assert "no recorded system like this" in none["basis"]["why"], none

            # (2) a cell_opt record of the same system is preferred
            base = next(r for r in server.records_query("hokusai", "benchmarks",
                                                        {"run_type": "single_point"})
                        if r["id"] == sp["basis"]["record_ids"][0])
            cell = dict({k: v for k, v in base.items() if k != "_scope"},
                        id="launch-plan-cellopt-probe", run_type="cell_opt", job_id="1",
                        metric={"name": "s_per_step", "value": 41.0}, date="2026-11-05")
            server.records_append("hokusai", "benchmarks", cell)
            opt = server.launch_plan("hokusai", "cp2k", "cell_opt", uio, steps=20)
            assert opt["basis"]["run_type"] == "cell_opt", opt
            assert opt["basis"]["record_ids"] == ["launch-plan-cellopt-probe"], opt
            assert opt["estimate"] == {**opt["estimate"], "metric": "s_per_step", "value": 41.0,
                                       "rough": False, "total_wall_s": 820.0}, opt
            assert not any("single_point" in w for w in opt["warnings"]), opt

            # (3) the label is recorded, but not with this functional
            for rt in ("single_point", "aimd_step", "geo_opt"):
                other = server.launch_plan("hokusai", "cp2k", rt, uio, method={"functional": "r2SCAN"})
                assert other["basis"]["match"] == "none" and other["estimate"] is None, other
                why = other["basis"]["why"]
                assert "UiO-66-conv is recorded, but not with functional=r2SCAN" in why, why
                assert "functional: revPBE" in why and "no recorded system" not in why, why

    def launch_plan_checks_hybrid_memory():
        """G7: with a hybrid record that carries its HFX memory, launch_plan
        reports the integrals per rank and the estimated peak against the
        rank's share of node memory (HOKUSAI mpc: 112 GiB), skips a node
        count that wouldn't fit, warns on a fixed one and on an HFX
        MAX_MEMORY below the integrals per rank, and says when a hybrid
        record has no memory to check."""
        from hpc_codes import server
        from hpc_codes.cp2k import tools

        def must_not_run(*_):
            raise AssertionError("launch_plan reached run_command")
        env = {k: v for k, v in os.environ.items() if k != "HOKUSAI_HOST"}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, env, clear=True), no_user_config("hokusai"), \
                patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}), \
                patch.object(tools, "run_command", must_not_run):
            build = {"build_id": "cp2k-2026.2-psmp-cpu-gcc", "facility": "hokusai", "code": "cp2k",
                     "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"},
                     "toolchain": "gcc", "install_path": "software/cp2k",
                     "env_script": "~/software/cp2k/cp2k_env.sh",
                     "test_status": {"level": "none", "result": "pass"},
                     "default": True, "date": "2026-11-04"}
            server.records_append("hokusai", "software_registry", build)
            base = next(r for r in server.records_query("hokusai", "benchmarks",
                                                        {"run_type": "single_point"})
                        if r["system"]["label"] == "UiO-66-conv")
            base = {k: v for k, v in base.items() if k != "_scope"}
            m = {**base["method"], "functional": "PBE0", "hybrid": True}

            def rec(i, nodes, wall, extra=None, functional="PBE0"):
                server.records_append("hokusai", "benchmarks", dict(
                    base, id=f"g7-probe-{i}", job_id=str(i), date="2026-11-05", nodes=nodes,
                    ranks_per_node=16, threads_per_rank=7, method={**m, "functional": functional},
                    metric={"name": "wall_s", "value": wall}, **({"extra": extra} if extra else {})))
            uio = {"label": "UiO-66-conv", "atoms": 456}
            # no memory recorded: the check says it was skipped
            rec(1, 2, 582.0, functional="HSE06")
            p = server.launch_plan("hokusai", "cp2k", "single_point", uio, method={"functional": "HSE06"})
            assert p["memory"] is None, p
            assert any("no recorded HFX memory" in w for w in p["warnings"]), p["warnings"]
            # 64000 MiB of ERIs: 1 node (4000 MiB/rank + 2573 base > 0.9 x 7168)
            # is cheaper but can't hold them; 2 nodes can
            rec(2, 1, 1000.0)
            rec(3, 2, 582.0, {"hfx_eri_mib": 64000, "peak_mib_per_rank": 4573})
            p = server.launch_plan("hokusai", "cp2k", "single_point", uio, method={"functional": "PBE0"})
            assert p["layout"]["nodes"] == 2, p
            mem = p["memory"]
            assert mem["eri_per_rank_mib"] == 2000 and mem["est_peak_mib_per_rank"] == 4573, mem
            assert mem["limit_mib_per_rank"] == 6451 and mem["fits"] is True, mem
            assert mem["suggested_hfx_memory_mb"] == 2400 and mem["record_id"] == "g7-probe-3", mem
            one = next(o for o in p["basis"]["options"] if o["nodes"] == 1)
            assert one["memory"]["fits"] is False and one["memory"]["est_peak_mib_per_rank"] == 6573, one
            assert any("[1] node(s) would not hold" in w for w in p["warnings"]), p["warnings"]
            # a fixed node count that doesn't fit, and a MAX_MEMORY below the ERIs per rank
            p = server.launch_plan("hokusai", "cp2k", "single_point", uio,
                                   method={"functional": "PBE0"}, nodes=1, hfx_memory_mb=3000)
            assert p["memory"]["fits"] is False, p["memory"]
            assert any("out-of-memory" in w for w in p["warnings"]), p["warnings"]
            assert any("on the fly" in w for w in p["warnings"]), p["warnings"]
            p = server.launch_plan("hokusai", "cp2k", "single_point", uio,
                                   method={"functional": "PBE0"}, hfx_memory_mb=3000)
            assert not any("on the fly" in w or "out-of-memory" in w for w in p["warnings"]), p
            try:
                server.launch_plan("hokusai", "cp2k", "single_point", uio, hfx_memory_mb=0)
            except ValueError as e:
                assert "MAX_MEMORY" in str(e), e
            else:
                raise AssertionError("hfx_memory_mb=0 accepted")
            # G33: a correction (supersedes) replaces the record it names
            # (newer than g7-probe-3, so without the fix the superseded one is chosen)
            for rid, more in (("g7-probe-4", {}), ("g7-probe-4-v2", {"supersedes": "g7-probe-4"})):
                server.records_append("hokusai", "benchmarks", dict(
                    base, id=rid, job_id="4", date="2026-11-06", nodes=2, ranks_per_node=16,
                    threads_per_rank=7, method=m, metric={"name": "wall_s", "value": 600.0},
                    extra={"hfx_eri_mib": 32000, "peak_mib_per_rank": 4000}, **more))
            p = server.launch_plan("hokusai", "cp2k", "single_point", uio, method={"functional": "PBE0"})
            assert p["memory"]["record_id"] == "g7-probe-4-v2", p["memory"]
            assert "g7-probe-4" not in p["basis"]["record_ids"], p["basis"]
            # a GGA plan carries no memory block and no memory warning
            g = server.launch_plan("hokusai", "cp2k", "aimd_step", uio)
            assert g["memory"] is None and not any("memory" in w for w in g["warnings"]), g

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
        root = pathlib.Path(__file__).resolve().parent.parent
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

    def yagura_structure_check_flags_and_stays_separate():
        """yagura's structure_check on the fixtures, its server starting with
        an empty HOME, and the boundary that keeps it splittable: yagura
        imports nothing from the rookery packages, and they import nothing
        from yagura."""
        import ast
        from yagura import server as ysrv
        root = pathlib.Path(__file__).resolve().parent.parent
        fx = root / "tests" / "fixtures" / "structures"

        r = ysrv.structure_check(str(fx / "flagged.xyz"))
        assert not r["ok"] and r["natoms"] == 5, r
        assert {(f["pair"], f["kind"]) for f in r["flags"]} == {("C-H", "short_xh"), ("O-O", "overlap")}, r
        # plain Python types only, so the result is the same after JSON
        assert all(type(k) is str for k in r["composition"]), r["composition"]
        assert json.loads(json.dumps(r)) == r
        # minimum image: N at x=9.6 is 1.4 A from C at x=1.0 across the boundary
        assert {"pair": "C-N", "d": 1.4, "count": 1} in r["min_pair_distances"], r
        r = ysrv.structure_check(str(fx / "clean.xyz"))
        assert r["ok"] and r["composition"] == {"O": 1, "H": 2} and r["cell"]["volume"] == 512.0, r
        for bad, text in (("plain.xyz", "need extended xyz"), ("missing.xyz", "not a file")):
            try:
                ysrv.structure_check(str(fx / bad))
            except ValueError as e:
                assert text in str(e), e
            else:
                raise AssertionError(f"structure_check accepted {bad}")

        # stretch: the short C-H becomes 1.08 A, matching make_subsys.py's
        # stretch_ch (the by-hand fallback), and the input is never overwritten
        import importlib.util
        import numpy as np
        spec = importlib.util.spec_from_file_location(
            "make_subsys", root / "codes/common/structure-making/cp2k/make_subsys.py")
        ms = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ms)
        cell, el, xyz = ms.read_extxyz(fx / "flagged.xyz")
        expected, _ = ms.stretch_ch(cell, el, xyz.copy(), 1.08)
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "fixed.xyz"
            r = ysrv.structure_stretch_xh(str(fx / "flagged.xyz"), str(out))
            assert r["moved"] == 1 and r["shortest_before"] == 0.9, r
            lines = out.read_text().splitlines()
            got = np.array([[float(v) for v in l.split()[1:4]] for l in lines[2:]])
            assert np.allclose(got, expected, atol=1e-7), got
            assert lines[1] == (fx / "flagged.xyz").read_text().splitlines()[1], lines[1]
            assert [f["kind"] for f in ysrv.structure_check(str(out))["flags"]] == ["overlap"]
            for args, text in (((str(fx / "flagged.xyz"), str(fx / "flagged.xyz")), "must differ"),
                               ((str(fx / "flagged.xyz"), str(out)), "exists"),
                               ((str(fx / "flagged.xyz"), str(out), 1.08, ["C-O"], True), "X-H")):
                try:
                    ysrv.structure_stretch_xh(*args)
                except ValueError as e:
                    assert text in str(e), e
                else:
                    raise AssertionError(f"structure_stretch_xh accepted {args}")

        probe = ("import asyncio; from yagura.server import mcp; "
                 "print(sorted(t.name for t in asyncio.run(mcp.list_tools())))")
        with tempfile.TemporaryDirectory() as home:
            out = subprocess.run([sys.executable, "-c", probe], cwd=root,
                                 env={**os.environ, "HOME": home},
                                 capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        assert out.stdout.strip() == ("['cif_inspect', 'structure_check', 'structure_stretch_xh', "
                                      "'structure_to_extxyz', 'trajectory_msd', 'trajectory_rdf', "
                                      "'trajectory_vdos']"), out.stdout

        # without ASE: the server still starts with every tool, the numpy
        # tools (checks and trajectory analysis) work, and the CIF tools say
        # how to install it
        import shutil
        walk = pathlib.Path(tempfile.mkdtemp()) / "walk.xyz"
        rng = np.random.default_rng(1)
        X = np.cumsum(rng.normal(0, 0.05, (64, 4, 3)), axis=0) % 8.0
        walk.write_text("".join(f"4\nLattice=\"8 0 0 0 8 0 0 0 8\"\n"
                                + "".join(f"O {x:.5f} {y:.5f} {z:.5f}\n" for x, y, z in f) for f in X))
        no_ase = ("import sys, importlib.abc\n"
                  "class Block(importlib.abc.MetaPathFinder):\n"
                  "    def find_spec(self, name, path=None, target=None):\n"
                  "        if name == 'ase' or name.startswith('ase.'):\n"
                  "            raise ModuleNotFoundError(f'No module named {name!r}', name=name)\n"
                  "sys.meta_path.insert(0, Block())\n"
                  "import asyncio; from yagura import server\n"
                  "print(len(asyncio.run(server.mcp.list_tools())))\n"
                  f"print(server.structure_check({str(fx / 'clean.xyz')!r})['ok'])\n"
                  f"print(len(server.trajectory_rdf({str(walk)!r}, ['O-O'], 3.0, 10)['r']),\n"
                  f"      server.trajectory_msd({str(walk)!r}, ['O'], 1.0)['elements'][0]['natoms'],\n"
                  f"      server.trajectory_vdos({str(walk)!r}, 1.0, points=8)['natoms'])\n"
                  "try:\n"
                  f"    server.cif_inspect({str(fx / 'disordered.cif')!r})\n"
                  "except RuntimeError as e:\n"
                  "    print('needs ASE' in str(e))\n")
        out = subprocess.run([sys.executable, "-c", no_ase], cwd=root, capture_output=True,
                             text=True, timeout=120)
        shutil.rmtree(walk.parent)
        assert out.stdout.split() == ["7", "True", "10", "4", "4", "True"], (out.stdout, out.stderr[-2000:])

        def imports(folder):
            for f in (root / folder).rglob("*.py"):
                for node in ast.walk(ast.parse(f.read_text())):
                    if isinstance(node, ast.Import):
                        yield f, [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                        yield f, [node.module]
        rookery = ("hpc_agent_core", "hpc_mcp", "hpc_codes", "facilities")
        for f, names in imports("yagura"):
            bad = [n for n in names if n.split(".")[0] in rookery]
            assert not bad, f"{f.relative_to(root)} imports {bad}: yagura must stay separate"
        for pkg in rookery:
            for f, names in imports(pkg):
                bad = [n for n in names if n.split(".")[0] == "yagura"]
                assert not bad, f"{f.relative_to(root)} imports {bad}: rookery must not depend on yagura"

    def file_transfers_judge_by_exit_status():
        """A login-node banner on stderr must not fail an upload or download
        (HOKUSAI prints one on every SSH connection); a non-zero exit must."""
        from types import SimpleNamespace
        from hpc_agent_core.middleware import _transfer

        class Transport:
            def __init__(self, code):
                self.code, self.calls = code, []

            def transfer(self, raise_errors=True):
                self.calls.append(raise_errors)
                return [SimpleNamespace(returncode=self.code, stdout="",
                                        stderr="Welcome to the login node\n")]

        ok = Transport(0)
        _transfer(ok)
        assert ok.calls == [False], ok.calls
        try:
            _transfer(Transport(23))
        except RuntimeError as e:
            assert "exit 23" in str(e) and "Welcome" in str(e), e
        else:
            raise AssertionError("a failed transfer was reported as success")

    def every_server_refuses_unknown_tool_arguments():
        """G19: the SDK's argument models ignore extra fields, so a misspelt
        argument (or one sent to a server running code from before it
        existed) used to vanish and the tool ran with the default. Each
        group's shim refuses it, naming the argument; valid calls still run;
        every server starts with an empty HOME; the check falls back to the
        SDK's behaviour (and says so on stderr) if it ever breaks; and the
        three deliberate copies of the shim stay identical."""
        root = pathlib.Path(__file__).resolve().parent.parent
        fx = root / "tests" / "fixtures" / "structures" / "clean.xyz"
        probe = f"""
import asyncio, json, os, sys, tempfile
from unittest.mock import patch
import hpc_agent_core.mcp_server, yagura.mcp_server, hyogo.mcp_server
from hpc_mcp.hpc_server import mcp as hpc
from hpc_mcp.docs_server import mcp as docs
from hpc_codes.server import mcp as codes
from yagura.server import mcp as yag
from hyogo.server import mcp as hyo

def call(m, name, args):
    try:
        r = asyncio.run(m.call_tool(name, args))
    except Exception as e:
        return "ERR " + str(e)
    return "".join(c.text for c in r.content if hasattr(c, "text"))

for m in (hpc, docs, codes):
    assert isinstance(m, hpc_agent_core.mcp_server.MCPServer), m
assert isinstance(yag, yagura.mcp_server.MCPServer)
assert isinstance(hyo, hyogo.mcp_server.MCPServer)

out = tempfile.mkdtemp()
kinds = {{"O": {{"q": 6, "basis": "DZVP-MOLOPT-SR-GTH", "potential": "GTH-PBE-q6"}}}}
res = {{
    "rookery_bad": call(hpc, "get_facility", {{"facility": "hokusai", "faclity": "x"}}),
    "rookery_ok": call(hpc, "get_facility", {{"facility": "hokusai"}}),
    "codes_bad": call(codes, "launch_plan", {{"facilty": "hokusai"}}),
    "yagura_bad": call(yag, "structure_check", {{"path": {str(fx)!r}, "pth": "x"}}),
    "yagura_ok": call(yag, "structure_check", {{"path": {str(fx)!r}}}),
    "hyogo_bad": call(hyo, "cp2k_make_subsys", {{"xyz_path": {str(fx)!r}, "kinds": kinds,
                      "out_path": os.path.join(out, "a"), "symmetri": "CUBIC"}}),
    "hyogo_own": call(hyo, "cp2k_make_subsys", {{"xyz_path": {str(fx)!r}, "kinds": kinds,
                      "out_path": os.path.join(out, "b")}}),
    "hyogo_files": sorted(os.listdir(out)),
}}
with patch.object(type(yag), "list_tools", side_effect=RuntimeError("future SDK")):
    res["fallback_ok"] = call(yag, "structure_check", {{"path": {str(fx)!r}, "pth": "x"}})
print(json.dumps(res))
"""
        with tempfile.TemporaryDirectory() as home:
            out = subprocess.run([sys.executable, "-c", probe], cwd=root,
                                 env={**os.environ, "HOME": home},
                                 capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        res = json.loads(out.stdout.strip().splitlines()[-1])
        tail = "If the tool should have it, the server may be running old code: restart the session."
        assert res["rookery_bad"] == ("ERR get_facility got unknown argument(s) ['faclity']; "
                                      f"it takes: facility. {tail}"), res["rookery_bad"]
        assert '"scheduler": "slurm"' in res["rookery_ok"], res["rookery_ok"]
        assert res["codes_bad"].startswith("ERR launch_plan got unknown argument(s) ['facilty']"), res
        assert res["yagura_bad"] == f"ERR structure_check got unknown argument(s) ['pth']; it takes: path. {tail}"
        assert json.loads(res["yagura_ok"])["ok"] is True, res["yagura_ok"]
        assert res["hyogo_bad"].startswith(
            "ERR cp2k_make_subsys got unknown argument(s) ['symmetri']; it takes: xyz_path, kinds, "
            "out_path,") and "symmetry" in res["hyogo_bad"], res["hyogo_bad"]
        # the valid call reaches the tool, whose own refusal comes back
        assert "no kind for ['H']" in res["hyogo_own"] and "unknown argument" not in res["hyogo_own"], res
        assert res["hyogo_files"] == [], res["hyogo_files"]
        # a broken check must not break calls: the SDK default (extras ignored) applies
        assert json.loads(res["fallback_ok"])["ok"] is True, res["fallback_ok"]
        assert "unknown-argument check disabled" in out.stderr, out.stderr[-2000:]

        def body(pkg):
            text = (root / pkg / "mcp_server.py").read_text()
            return text[text.index("\nimport sys"):]
        assert body("hpc_agent_core") == body("yagura") == body("hyogo"), \
            "the three mcp_server.py shims are deliberate copies: change all three together"

    check("every server refuses unknown tool arguments", every_server_refuses_unknown_tool_arguments)
    check("cp2k tools parse and check on the login node",
          cp2k_tools_parse_and_check_on_the_login_node)
    check("file transfers judge by exit status, not stderr", file_transfers_judge_by_exit_status)
    check("cp2k_bench_dryrun runs the stage logic with a stub srun",
          cp2k_bench_dryrun_runs_the_stage_logic_with_a_stub_srun)
    check("cp2k_bench_records appends benchmarks and provenance",
          cp2k_bench_records_appends_benchmarks_and_provenance)
    check("cp2k tools check configuration first", cp2k_tools_check_configuration_first)
    check("cp2k_kinds reads the build data folder", cp2k_kinds_reads_the_build_data_folder)
    check("cp2k_bench_input matches the script", cp2k_bench_input_matches_the_script)
    check("yagura cif tools read, convert and warn", yagura_cif_tools_read_convert_and_warn)
    check("hyogo make_subsys matches the script and stays separate",
          hyogo_make_subsys_matches_the_script_and_stays_separate)
    check("hyogo make_input matches the templates and UiO-66",
          hyogo_make_input_matches_the_templates_and_uio66)
    check("hyogo parse_output reads the HOKUSAI runs", hyogo_parse_output_reads_the_hokusai_runs)
    check("hyogo chains one run into the next (G27)", hyogo_chains_one_run_into_the_next)
    check("launch_plan checks hybrid memory (G7)", launch_plan_checks_hybrid_memory)
    check("cp2k_record_from_run records a parsed production run (G24)",
          cp2k_record_from_run_records_a_parsed_run)
    check("launch_plan picks from the shipped records", launch_plan_picks_from_the_shipped_records)
    check("launch_plan falls back to single points and names the method",
          launch_plan_falls_back_and_names_the_method)
    check("yagura structure_check flags and stays separate", yagura_structure_check_flags_and_stays_separate)
    check("yagura trajectory analysis matches analytic cases",
          yagura_trajectory_analysis_matches_analytic_cases)
    check("records tools follow the adapter contract",
          records_tools_follow_the_adapter_contract)
    check("HPC_RECORDS_ROOT moves the user scope", records_root_moves_the_user_scope)
    check("shipped records are valid", shipped_records_are_valid)
    check("hpc-codes server starts with an empty HOME", codes_server_starts_with_empty_home)


def main() -> int:
    check_repo()
    for fac in config.list_facilities():
        check_facility(fac.slug)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} conformance check(s) FAILED:\n")
        for f in FAILURES:
            print(f)
        return 1
    print("All conformance checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
