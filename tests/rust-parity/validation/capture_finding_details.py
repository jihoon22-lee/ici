"""Development-only capture of frozen producer fields; never used by the Rust product."""

import dataclasses
import hashlib
import importlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
REVISION = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
NAMES = [
    "src/ici/engines/test_quality.py",
    "src/ici/engines/sanitize.py",
    "src/ici/engines/thread_sanitize.py",
    "src/ici/analysis/binary_abi.py",
]
# Execute only Git-archived reference sources, including after the Rust cutover removes src/ici.
source_sha256 = {}
archive = subprocess.check_output(["git", "archive", REVISION, "src/ici"], cwd=ROOT)
reference = tempfile.TemporaryDirectory(prefix="ici-reference-findings-")
reference_root = Path(reference.name)
with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
    for member in bundle.getmembers():
        name = PurePosixPath(member.name)
        if not member.isfile():
            continue
        if name.is_absolute() or ".." in name.parts or name.parts[:2] != ("src", "ici"):
            raise ValueError("reference archive contains an unsafe member")
        target = reference_root.joinpath(*name.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = bundle.extractfile(member)
        assert source is not None
        target.write_bytes(source.read())
for name in NAMES:
    source_sha256[name] = hashlib.sha256((reference_root / name).read_bytes()).hexdigest()
sys.path.insert(0, str(reference_root / "src"))
models = importlib.import_module("ici.core.models")
EngineStatus, InspectionTarget, SourceLocation = (
    models.EngineStatus,
    models.InspectionTarget,
    models.SourceLocation,
)
binary_finding = importlib.import_module("ici.analysis.binary_abi").binary_finding
SanitizeEngine = importlib.import_module("ici.engines.sanitize").SanitizeEngine
TestQualityMixin = importlib.import_module("ici.engines.test_quality").TestQualityMixin
ThreadSanitizeEngine = importlib.import_module("ici.engines.thread_sanitize").ThreadSanitizeEngine

cases = []
for kind, target in [
    (
        "slow",
        InspectionTarget(
            file_path="tests/test_sample.py",
            start_line=4,
            target_name="[test-quality] Slow test: tests/test_sample.py::test_sample (call)",
            status=EngineStatus.WARN,
            message="pytest call phase took 1.500s (threshold 1.000s)",
            metrics={"duration": 1.5, "threshold": 1.0, "phase": "call"},
        ),
    ),
    (
        "flaky",
        InspectionTarget(
            file_path="tests/test_sample.py",
            start_line=4,
            target_name="[test-quality] Flaky test: tests/test_sample.py::test_sample",
            status=EngineStatus.WARN,
            message="pytest verdict changed across bounded repeat runs: run 1=PASSED, run 2=FAILED",
            metrics={"runs": 2, "statuses": ["PASSED", "FAILED"], "passes": 1, "failures": 1},
        ),
    ),
]:
    cases.append(
        {"id": kind, "finding": dataclasses.asdict(TestQualityMixin._quality_findings([target])[0])}
    )
for kind, tool, defect in [
    ("asan", "AddressSanitizer", "heap-use-after-free"),
    ("ubsan", "UndefinedBehaviorSanitizer", "signed-integer-overflow"),
    ("lsan", "LeakSanitizer", "memory-leak"),
    ("tsan", "ThreadSanitizer", "data-race"),
]:
    engine = (ThreadSanitizeEngine if kind == "tsan" else SanitizeEngine).__new__(
        ThreadSanitizeEngine if kind == "tsan" else SanitizeEngine
    )
    target_name = (
        "TSan Error" if kind == "tsan" else "LSan Error" if kind == "lsan" else "ASan/UBSan Error"
    )
    location = SourceLocation(path="src/suite.cpp", start_line=3, start_column=2)
    diagnostic = SimpleNamespace(
        kind=kind,
        tool_name=tool,
        defect=defect,
        message=f"{tool} detected {defect.replace('-', ' ')}",
        primary_location=location,
        related_locations=[],
    )
    target = InspectionTarget(
        file_path=location.path,
        start_line=location.start_line,
        start_column=location.start_column,
        target_name=target_name,
        status=EngineStatus.FAIL,
        message=diagnostic.message,
    )
    record = SimpleNamespace(target=target, diagnostic=diagnostic)
    engine._sanitizer_findings = [record]
    finding = dataclasses.asdict(engine._normalized_findings()[0])
    cases.append({"id": kind, "finding": finding})
    engine._sanitizer_findings = [record, record]
    cases.append(
        {"id": kind + "-duplicate", "finding": dataclasses.asdict(engine._normalized_findings()[0])}
    )
cases.append(
    {
        "id": "binary",
        "finding": dataclasses.asdict(
            binary_finding(
                "ici.binary.non-elf",
                "suite",
                "Selected binary manifest artifact is not an ELF object",
                "elf.magic",
            )
        ),
    }
)
(ROOT / "tests/rust-parity/validation/finding-details-oracle.json").write_text(
    json.dumps(
        {"source_revision": REVISION, "source_sha256": source_sha256, "cases": cases},
        default=lambda value: value.value,
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)

reference.cleanup()
