from checks._common import bare_spec

LABEL = 'a defaulted queue reaches the script'


def rendered_script_has_a_queue(slug, fac, backend):
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
