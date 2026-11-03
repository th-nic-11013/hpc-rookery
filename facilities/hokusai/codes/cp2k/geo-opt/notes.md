On {{DISPLAY_NAME}}, dependents of a failed `afterok` job are **never**
cancelled automatically: they sit pending with `DependencyNeverSatisfied`
until cancelled by ID.
