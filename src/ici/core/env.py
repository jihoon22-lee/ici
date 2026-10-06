"""The interpreter contract the shipped launcher keeps.

Only the candidate list survives here: ``scripts/launcher.sh`` searches these
names, in this order, when the pyz runs on a bare system interpreter
(AGENTS §4). Everything else this module used to find — infra roots, NAS
paths, uv, project interpreters — belonged to machinery that no longer runs.
"""

PYTHON_CANDIDATES = [
    "python3.14",
    "python3.13",
    "python3.12",
    "python3.11",
    "python3.10",
    "python3",
]
