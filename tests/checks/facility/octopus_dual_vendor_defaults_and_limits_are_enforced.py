from hpc_agent_core.models import JobSpec
from checks._common import bare_spec

LABEL = 'dual-vendor defaults and machine limits are enforced'
FACILITIES = ('octopus',)


def octopus_dual_vendor_defaults_and_limits_are_enforced(slug, fac, backend):
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
