from checks._common import bare_spec

LABEL = 'apply_defaults never overwrites caller values'


def explicit_values_survive_defaults(slug, fac, backend):
    """apply_defaults must never overwrite something the caller set."""
    spec = bare_spec()
    spec.attributes.queue_name = "caller-chosen-queue"
    spec.attributes.account = "caller-chosen-account"
    backend.apply_defaults(spec)
    assert spec.attributes.queue_name == "caller-chosen-queue", \
        "apply_defaults overwrote a caller-supplied queue_name"
    assert spec.attributes.account == "caller-chosen-account", \
        "apply_defaults overwrote a caller-supplied account"
