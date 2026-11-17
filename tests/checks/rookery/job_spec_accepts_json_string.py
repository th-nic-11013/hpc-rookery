import json
from checks._common import no_user_config

LABEL = 'job spec accepts a JSON string'


def job_spec_accepts_json_string():
    """Some clients send `spec` as a JSON string. render_job_script and
    submit_job accept it, validate it exactly as an object (a string and
    an object render the same script), and bad JSON gives a clear error.
    The advertised schema stays the JobSpec object."""
    from pydantic import TypeAdapter, ValidationError
    from hpc_mcp import hpc_server

    spec = {"name": "t", "executable": "bash run.sh", "directory": "~/agent/work/t",
            "resources": {"node_count": 1, "processes_per_node": 2, "cpu_cores_per_process": 4},
            "attributes": {"duration": "03:00:00", "queue_name": "mpc", "account": "rb000000"}}
    # the registered tool validates its argument with this annotation before the body runs
    adapter = TypeAdapter(hpc_server.SpecArg)
    a, b = adapter.validate_python(spec), adapter.validate_python(json.dumps(spec))
    assert a == b, (a, b)
    with no_user_config("hokusai"):
        as_obj = hpc_server.render_job_script("hokusai", a)
        as_str = hpc_server.render_job_script("hokusai", b)
    assert as_obj == as_str, "a JSON-string spec rendered a different script"

    try:
        adapter.validate_python('{"name": "t", ')
    except ValidationError as e:
        assert "not valid JSON" in str(e) and "not a string" in str(e), e
    else:
        raise AssertionError("invalid JSON was accepted")
    try:   # a string is still validated as a JobSpec: no relaxed rules
        adapter.validate_python(json.dumps({**spec, "name": "bad name"}))
    except ValidationError as e:
        assert "not allowed" in str(e), e
    else:
        raise AssertionError("an invalid name passed through the string path")
    assert TypeAdapter(hpc_server.SpecArg).json_schema().get("$ref") or \
        TypeAdapter(hpc_server.SpecArg).json_schema().get("type") == "object"
