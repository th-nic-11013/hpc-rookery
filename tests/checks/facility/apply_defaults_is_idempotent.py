from checks._common import bare_spec

LABEL = 'apply_defaults is idempotent'


def apply_defaults_is_idempotent(slug, fac, backend):
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
