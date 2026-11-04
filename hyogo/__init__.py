"""hyogo: running simulation methods (MD, enhanced sampling, MLIP training,
active learning) and writing their inputs.

Staged inside hpc-rookery until it splits into its own repository. It
never reaches a cluster: facts about a machine or a build come from
rookery's tools (launch_plan, cp2k_kinds), structures from yagura's,
passed in by the agent. It imports nothing from hpc_agent_core, hpc_mcp,
hpc_codes, facilities or yagura; tests/conformance.py enforces that.
"""
