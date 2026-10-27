"""Cargo Clippy, ESLint and TypeScript providers with explicit execution effects."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.domain.enums import TaskKind
from ici.domain.finding import Finding, SourceSpan
from ici.domain.tasks import TaskSpec
from ici.execution.process import ExitContract, TaskOutcome


class EcosystemProvider:
    def __init__(self, name: str, root: Path):
        self.name = name
        self.root = root.resolve()

    def plan(
        self,
        executable: str,
        cwd: Path,
        targets: tuple[str, ...],
        task_id: str,
        unit_id: str,
        config_files: tuple[str, ...],
    ) -> ProviderPlan:
        extra = {}
        if self.name == "clippy":
            target = cwd / ".ici" / "cargo-target"
            argv = (
                executable,
                "clippy",
                "--offline",
                "--locked",
                "--message-format=json",
                "--jobs",
                "2",
                "--target-dir",
                str(target),
            )
            # Cargo may execute build.rs and proc macros. This is preparation,
            # even though its product is a static diagnostic.
            kind = TaskKind.PREPARE
            extra = {
                "resource_keys": (str(target),),
                "work_dirs": (str(target),),
                "env_overlay": (("RUSTUP_AUTO_INSTALL", "0"), ("CARGO_NET_OFFLINE", "true")),
            }
            contract = ExitContract(success=(0,), findings=(101,))
        elif self.name == "eslint":
            argv = (executable, "--format", "json", "--no-fix", "--", *targets)
            kind = TaskKind.ANALYZE
            contract = ExitContract(success=(0,), findings=(1,))
        else:
            config = "tsconfig.json" if (cwd / "tsconfig.json").is_file() else "jsconfig.json"
            project = ("--project", config) if (cwd / config).is_file() else targets
            argv = (executable, "--noEmit", "--pretty", "false", "--incremental", "false", *project)
            kind = TaskKind.ANALYZE
            contract = ExitContract(success=(0,), findings=(1, 2))
        return ProviderPlan(
            task=TaskSpec(
                id=task_id,
                kind=kind,
                provider=self.name,
                argv=argv,
                cwd=str(cwd),
                analysis_unit_ids=(unit_id,),
                input_refs=tuple(dict.fromkeys((*targets, *config_files))),
                cacheable=False,
                timeout_seconds=300,
                **extra,
            ),
            contract=contract,
        )

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        try:
            if self.name == "eslint":
                findings = self._eslint(outcome)
            elif self.name == "clippy":
                findings = self._clippy(outcome)
            else:
                findings = self._tsc(outcome)
            if outcome.exit_code and not findings:
                raise ValueError("nonzero exit without attributable diagnostics")
            limitations = {
                "clippy": "Cargo can execute build scripts and proc macros; offline dependencies must already exist; native target closure is not a baseline resolution proof",
                "eslint": "ESLint loads project configuration and plugins, which may execute JavaScript",
                "tsc": "TypeScript project references are checked without building missing referenced outputs; native project closure is not a baseline resolution proof",
            }
            return ParsedOutput(findings=tuple(findings), limitations=(limitations[self.name],))
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            return ParsedOutput(failed_to_parse=f"{self.name}: {error}")

    def _finding(self, outcome, path, line, column, rule, message, severity="warning", related=()):
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = Path(outcome.spec.cwd) / candidate
        relative = candidate.resolve().relative_to(self.root).as_posix()
        span = SourceSpan(relative, max(1, int(line)), start_column=max(1, int(column or 1)))
        fingerprint = hashlib.sha256(
            f"{self.name}|{rule}|{relative}|{line}|{message}".encode()
        ).hexdigest()
        return Finding(
            fingerprint=fingerprint,
            rule_id=f"{self.name}.{rule}",
            native_rule_id=rule,
            message=message,
            severity=severity,
            confidence="high",
            primary_location=span,
            related_locations=tuple(related),
            provider=self.name,
            task_id=outcome.spec.name,
        )

    def _eslint(self, outcome):
        documents = json.loads(outcome.parseable)
        if not isinstance(documents, list):
            raise ValueError("expected JSON file result array")
        found = []
        for document in documents:
            for message in document["messages"]:
                if not message.get("line") and not message.get("fatal"):
                    raise ValueError(message["message"])
                found.append(
                    self._finding(
                        outcome,
                        document["filePath"],
                        message.get("line", 1),
                        message.get("column", 1),
                        message.get("ruleId") or "parse",
                        message["message"],
                        "error" if message.get("severity") == 2 else "warning",
                    )
                )
        return found

    def _clippy(self, outcome):
        found = []
        finished = False
        for line in outcome.parseable.splitlines():
            record = json.loads(line)
            if record.get("reason") == "build-finished":
                finished = True
            if record.get("reason") != "compiler-message":
                continue
            message = record["message"]
            spans = message["spans"]
            primary = next((span for span in spans if span.get("is_primary")), None)
            if primary is None:
                if message["level"] == "error" and not found:
                    raise ValueError(message["message"])
                continue
            code = (message.get("code") or {}).get("code", "diagnostic")
            found.append(
                self._finding(
                    outcome,
                    primary["file_name"],
                    primary["line_start"],
                    primary["column_start"],
                    code,
                    message["message"],
                    "error" if message["level"] == "error" else "warning",
                )
            )
        if not finished:
            raise ValueError("missing Cargo build-finished record")
        return found

    def _tsc(self, outcome):
        found = []
        pattern = re.compile(r"^(.+)\((\d+),(\d+)\): error TS(\d+): (.*)$")
        for line in outcome.parseable.splitlines():
            match = pattern.match(line)
            if match:
                path, row, col, code, message = match.groups()
                found.append(self._finding(outcome, path, row, col, code, message, "error"))
            elif line.startswith("error TS"):
                raise ValueError(line)  # config/tool failure is not a source finding
            elif line.strip() and not line.startswith(" "):
                raise ValueError(f"unrecognized diagnostic: {line}")
        return found
