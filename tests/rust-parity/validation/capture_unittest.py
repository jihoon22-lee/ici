"""Development-only real unittest output capture; runtime paths must be explicit arguments."""

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = '''import unittest
class Standard(unittest.TestCase):
    def test_pass(self):
        """A documented test exercises multiline verbose output."""
        self.assertTrue(True)
    @unittest.skip("optional")
    def test_skip(self):
        pass
    @unittest.expectedFailure
    def test_expected(self):
        self.assertTrue(False)
    @unittest.expectedFailure
    def test_unexpected(self):
        self.assertTrue(True)
    def test_subtests(self):
        for value in [1, 2]:
            with self.subTest(value=value):
                self.assertEqual(value, 0)
    def test_error(self):
        raise RuntimeError("observed runtime error")
    def test_fail(self):
        self.assertTrue(False)
'''
if len(sys.argv) < 2:
    raise SystemExit("pass at least one explicit project Python executable")
cases = []
for executable in sys.argv[1:]:
    environment = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryDirectory(prefix="ici-unittest-capture-") as temporary:
        root = Path(temporary)
        (root / "tests").mkdir()
        (root / "tests/test_standard.py").write_text(SOURCE)
        version = subprocess.check_output(
            [executable, "--version"], text=True, env=environment
        ).strip()
        output = subprocess.run(
            [executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert output.returncode == 1
        cases.append(
            {
                "version": version,
                "stdout": output.stdout.replace(str(root), "<ROOT>"),
                "stderr": output.stderr.replace(str(root), "<ROOT>"),
                "exit": output.returncode,
                "expected_cases": 7,
                "expected_passed": 2,
                "expected_failed_nodes": 4,
                "expected_subtests": 2,
            }
        )
destination = Path(__file__).with_name("unittest-native.json")
destination.write_text(
    json.dumps(
        {
            "schema_id": "ici.unittest-native-fixture",
            "source": SOURCE,
            "source_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
            "cases": cases,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
