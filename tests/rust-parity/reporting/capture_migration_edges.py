"""Development-only capture: frozen 6fe3e405 migrate_report_payload on malformed v2 edges.

Run with the immutable oracle on PYTHONPATH: python capture_migration_edges.py OUT.json
"""

import json
import sys

from ici.reporters.json_rep import migrate_report_payload

cases = []


def case(name, payload, root=None):
    try:
        expected = migrate_report_payload(payload, root)
        cases.append(dict(name=name, input=payload, project_root=root, expected=expected))
    except Exception as error:  # refusal is part of the contract
        cases.append(dict(name=name, input=payload, project_root=root, error=type(error).__name__))


case(
    "null-and-scalar-text",
    dict(
        schema_version="ici.result/v2",
        engine_name="lint",
        status="FAIL",
        summary=None,
        raw_output=12,
        tool_evidence=[
            dict(
                name=None,
                path=True,
                version=1.5,
                argv=[None, 1, True, "x"],
                returncode="3",
                timed_out="yes",
                truncated=0,
                error=None,
            )
        ],
        targets=[
            dict(
                file_path="a.py",
                start_line=2,
                target_name=None,
                message=None,
                snippet=False,
                status="FAIL",
            ),
            dict(file_path="a.py", start_line=3, target_name=7, message=2.5, status="WARN"),
        ],
    ),
)
case(
    "required-truthiness",
    dict(
        schema_version="ici.result/v2",
        results=[
            dict(engine_name="a", status="PASS", required=None),
            dict(engine_name="b", status="PASS", required=0),
            dict(engine_name="c", status="PASS", required="false"),
            dict(engine_name="d", status="PASS", required=[]),
            dict(engine_name="e", status="PASS"),
        ],
        suite_status="PASS",
    ),
)
case(
    "integer-numbers-become-floats",
    dict(
        schema_version="ici.result/v2",
        suite_status="WARN",
        duration=3,
        tem_score=4,
        max_tem_score=5,
        results=[
            dict(
                engine_name="complexity",
                status="WARN",
                score=3,
                max_score=10,
                duration=2,
                targets=[
                    dict(
                        file_path="m.py",
                        start_line=1,
                        status="WARN",
                        metrics={"complexity": 12, "ratio": 0.5, "flag": True},
                    )
                ],
            )
        ],
    ),
)
case(
    "windows-drive-root-casefold",
    dict(
        schema_version="ici.result/v2",
        engine_name="lint",
        status="WARN",
        targets=[
            dict(file_path="C:\\Repo\\src\\x.py", start_line=1, target_name="E1", status="WARN")
        ],
    ),
    "c:/repo",
)
case(
    "windows-drive-outside-root",
    dict(
        schema_version="ici.result/v2",
        engine_name="lint",
        status="WARN",
        targets=[dict(file_path="D:\\Repo\\x.py", start_line=1, status="WARN")],
    ),
    "C:/Repo",
)
case(
    "drive-relative-refused",
    dict(
        schema_version="ici.result/v2",
        engine_name="lint",
        status="WARN",
        targets=[dict(file_path="C:x.py", start_line=1, status="WARN")],
    ),
)
case(
    "digit-colon-is-relative",
    dict(
        schema_version="ici.result/v2",
        engine_name="lint",
        status="WARN",
        targets=[dict(file_path="1:odd.py", start_line=1, status="WARN")],
    ),
)
case(
    "root-slash",
    dict(
        schema_version="ici.result/v2",
        engine_name="lint",
        status="WARN",
        targets=[dict(file_path="/srv/app/x.py", start_line=1, status="WARN")],
    ),
    "/",
)
case(
    "string-integers",
    dict(
        schema_version="ici.result/v2",
        engine_name="lint",
        status="WARN",
        targets=[
            dict(
                file_path="x.py", start_line=" 7 ", end_line="+9", start_column="4_0", status="WARN"
            ),
            dict(file_path="x.py", start_line=2.9, end_line=1e3, status="WARN"),
        ],
    ),
)
case(
    "escape-and-traversal",
    dict(
        schema_version="ici.result/v2",
        engine_name="lint",
        status="WARN",
        targets=[dict(file_path="../x.py", start_line=1, status="WARN")],
    ),
)
case(
    "v3-engine-without-findings",
    dict(
        schema_version="ici.result/v3",
        engine_name="dup",
        status="WARN",
        extra={"keep": 1},
        targets=[dict(file_path="a.py", start_line=1, target_name="clone", status="WARN")],
    ),
)
case(
    "v3-suite-keeps-counts",
    dict(
        schema_version="ici.result/v3",
        suite_status="PASS",
        passed_count=9,
        results=[
            dict(engine_name="lint", status="PASS", findings=[]),
            dict(
                engine_name="line",
                status="PASS",
                targets=[dict(file_path="z.py", start_line=1, status="PASS")],
            ),
        ],
    ),
)
case(
    "secret-in-target-name",
    dict(
        schema_version="ici.result/v2",
        engine_name="security",
        status="WARN",
        targets=[
            dict(
                file_path="s.py",
                start_line=1,
                target_name="password=hunter2",
                status="WARN",
                message="token: abcdef",
            )
        ],
    ),
)
with open(sys.argv[1], "w", encoding="utf-8") as output:
    json.dump({"oracle": "6fe3e405", "cases": cases}, output, ensure_ascii=False, indent=2)
print(len(cases), [c["name"] for c in cases if "error" in c])
