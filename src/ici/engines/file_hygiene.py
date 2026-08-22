"""File system hygiene lint — exec bits, CRLF/BOM, pycache, and shell syntax."""

import os
import time
from pathlib import Path

from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
    ToolEvidence,
)
from ici.core.runner import ProcessResult, run_process
from ici.engines.base import BaseEngine

_TEXT_SUFFIXES = frozenset(
    {".py", ".cpp", ".c", ".h", ".hpp", ".cc", ".cxx", ".toml", ".md", ".sh"}
)
_SKIP_PARTS = frozenset(
    {".venv", "venv", "build", ".git", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
)
_SHELL_SUFFIXES = frozenset({".sh"})


def _iter_candidate_files(project_root: Path) -> list[Path]:
    """List regular in-project files worth hygiene checks, pruning ignored dirs."""
    files: list[Path] = []
    for current, dir_names, file_names in os.walk(project_root):
        current_path = Path(current)
        dir_names[:] = [d for d in dir_names if d not in _SKIP_PARTS]
        for name in sorted(file_names):
            path = current_path / name
            if path.is_symlink():
                continue
            rel = path.relative_to(project_root)
            if any(part in _SKIP_PARTS for part in rel.parts[:-1]):
                continue
            files.append(path)
    return files


class _Finding:
    """Mutable collector shared by all hygiene checks."""

    def __init__(self) -> None:
        self.targets: list[InspectionTarget] = []

    def warn(self, rel_path: str, name: str, message: str, line_no: int = 1) -> None:
        self.targets.append(
            InspectionTarget(
                file_path=rel_path,
                start_line=line_no,
                target_name=f"Hygiene:{name}",
                status=EngineStatus.WARN,
                message=message,
            )
        )


def _has_exec_bit(path: Path) -> bool:
    try:
        return bool(os.stat(path).st_mode & 0o111)
    except OSError:
        return False


def check_exec_bit(finding: _Finding, rel: str, path: Path) -> None:
    if path.suffix in _TEXT_SUFFIXES - _SHELL_SUFFIXES and _has_exec_bit(path):
        finding.warn(rel, "ExecBit", "Source file should not be executable")


def read_prefix(path: Path, size: int = 65536) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(size)
    except OSError:
        return b""


def check_line_endings_and_bom(finding: _Finding, rel: str, path: Path) -> None:
    prefix = read_prefix(path)
    if not prefix:
        return
    if prefix.startswith(b"\xef\xbb\xbf"):
        finding.warn(rel, "Bom", "UTF-8 byte order mark found at file start")
    if b"\r\n" in prefix:
        finding.warn(rel, "Crlf", "CRLF line endings found — normalize to LF")


def check_pycache(finding: _Finding, rel: str, path: Path) -> None:
    if "__pycache__" in Path(rel).parts:
        finding.warn(rel, "PycacheDir", "__pycache__ directory should not be tracked")
    elif path.suffix == ".pyc":
        finding.warn(rel, "PycFile", "Compiled .pyc artifact should not be tracked")


class FileHygieneEngine(BaseEngine):
    """Detects common repository hygiene violations without external dependencies."""

    def run(self) -> EngineResult:
        t0 = time.time()
        cfg = self.get_config("file_hygiene")
        mode = cfg.get("mode", "pass_warn")
        required = bool(cfg.get("required", False))
        tool_evidence: list[ToolEvidence] = []
        finding = _Finding()

        for path in _iter_candidate_files(self.project_root):
            rel = str(path.relative_to(self.project_root))
            self._check_one(cfg, finding, rel, path)

        if cfg.get("shell_syntax", True):
            tool_evidence = self._check_shell_syntax(finding)

        issue_count = sum(1 for target in finding.targets if target.status != EngineStatus.PASS)
        status = self.evaluate_status(False, issue_count > 0, mode)
        summary = (
            f"File hygiene: {issue_count} issue(s)"
            if issue_count
            else f"File hygiene passed across {len(finding.targets)} checked item(s)"
        )
        return self.create_result(
            name="file_hygiene",
            status=status,
            summary=summary,
            duration=time.time() - t0,
            targets=finding.targets,
            required=required,
            evidence=EvidenceState.MEASURED,
            tool_evidence=tool_evidence,
        )

    def _check_one(self, cfg: dict, finding: _Finding, rel: str, path: Path) -> None:
        """Run every enabled textual check against one file."""
        if cfg.get("check_pycache", True):
            check_pycache(finding, rel, path)
        if path.suffix not in _TEXT_SUFFIXES:
            return
        if cfg.get("check_exec_bits", True):
            check_exec_bit(finding, rel, path)
        if cfg.get("check_crlf", True) or cfg.get("check_bom", True):
            check_line_endings_and_bom(finding, rel, path)

    def _check_shell_syntax(self, finding: _Finding) -> list[ToolEvidence]:
        """Run `bash -n` on every tracked shell script when bash is available."""
        bash = _find_bash()
        if bash is None:
            return []
        evidence: list[ToolEvidence] = []
        for script in _iter_shell_scripts(self.project_root):
            result = run_process([bash, "-n", str(script)], cwd=self.project_root)
            rel = str(script.relative_to(self.project_root))
            evidence.append(_to_evidence(bash, result))
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip().splitlines()
                message = detail[-1] if detail else f"bash -n exited {result.returncode}"
                finding.warn(rel, "ShellSyntax", message)
        return evidence


def _find_bash() -> str | None:
    from shutil import which

    return which("bash")


def _iter_shell_scripts(project_root: Path) -> list[Path]:
    return [p for p in _iter_candidate_files(project_root) if p.suffix in _SHELL_SUFFIXES]


def _to_evidence(bash: str, result: ProcessResult) -> ToolEvidence:
    return ToolEvidence(
        name="bash -n",
        path=bash,
        argv=["bash", "-n"],
        returncode=result.returncode,
        timed_out=result.timed_out,
        truncated=result.truncated,
        error=result.stderr.strip()[:500],
    )
