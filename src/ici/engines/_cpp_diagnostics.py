"""Strict, bounded GCC/Clang diagnostic normalization for C++ analysis."""

from __future__ import annotations

import json
import os
import re
import stat
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ici.core.models import EngineStatus, InspectionTarget

MAX_DIAGNOSTICS = 10_000
MAX_DIAGNOSTIC_DEPTH = 32
MAX_DIAGNOSTIC_LINE = 2_147_483_647
MAX_MESSAGE_CHARS = 8_192
MAX_REPLACEMENT_CHARS = 8_192
MAX_DIAGNOSTIC_OUTPUT_CHARS = 1_000_000
MAX_CLAZY_SOURCE_ROOTS = 512
MAX_CLAZY_SOURCE_LINE_BYTES = MAX_MESSAGE_CHARS * 4 + 2

_TEXT_DIAGNOSTIC_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>[1-9]\d*)(?::(?P<column>[1-9]\d*))?:\s*"
    r"(?P<kind>fatal error|error|warning|note|remark):\s*"
    r"(?P<message>\S.*?)(?:\s+\[(?P<rule>[-A-Za-z0-9_.]+)\])?$"
)
_TEXT_CONTEXT_RE = re.compile(r"^\s*(?:\d+\s*\|.*|\|.*|[\^~].*)$")
_TEXT_LEGACY_PREVIEW_RE = re.compile(r"^[ \t]+[\x21-\x7e][\x20-\x7e]{0,8191}$")
_TEXT_CONTEXT_HEADER_RE = re.compile(
    r"^.+:\s+(?:At global scope|In (?:function|member function|constructor|destructor|"
    r"lambda function|instantiation of)(?: .*)?):$"
)
_TEXT_REQUIRED_FROM_RE = re.compile(r"^.+:[1-9]\d*(?::[1-9]\d*)?:\s+required (?:from|by)\s+\S.*$")
_TEXT_TRAILER_RE = re.compile(
    r"^(?:[1-9]\d* (?:warning|warnings|error|errors) generated\.|compilation terminated\.)$"
)
_CLANG_TIDY_GENERATED_RE = re.compile(r"^(?P<count>[1-9]\d*) warnings? generated\.$")
_CLANG_TIDY_SUPPRESSED_RE = re.compile(
    r"^Suppressed (?P<count>[1-9]\d*) warnings? \([^\r\n]{1,512}\)\.$"
)
_CLANG_TIDY_HEADER_HINT_RE = re.compile(
    r"^Use -header-filter=.{1,512}(?: or leave it as default)? to display errors "
    r"from all non-system headers\."
    r"(?: Use -system-headers to display errors from system headers as well\.)?$"
)
_CLANG_TIDY_EMPTY_NOTE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>[1-9]\d{0,9})"
    r"(?::(?P<column>[1-9]\d{0,9}))?:\s*note:\s*$"
)
_CLANG_TIDY_CONVERSION_NOTE_RE = re.compile(
    r"^'.{1,256}' and '.{1,256}' may be implicitly converted: \S.{1,4096}$"
)
_CLANG_TIDY_PARAMETER_RANGE_NOTE_RE = re.compile(
    r"^the (?:first|last) parameter in the range is '.{1,256}'$"
)
_CLAZY_RULE_RE = re.compile(r"^-Wclazy-(?P<name>[a-z0-9](?:[a-z0-9_.-]{0,254}[a-z0-9])?)$")
_CLAZY_RULE_MARKER_RE = re.compile(r"\[-W[^\]\r\n]*")
_TEXT_FIXIT_RE = re.compile(
    r'^fix-it:"(?P<file>(?:[^"\\]|\\.)*)":\{'
    r"(?P<start_line>[1-9]\d*):(?P<start_column>[1-9]\d*)-"
    r"(?P<end_line>[1-9]\d*):(?P<end_column>[1-9]\d*)"
    r'\}:"(?P<replacement>(?:[^"\\]|\\.)*)"$'
)


@dataclass(frozen=True)
class CppFixIt:
    """One read-only replacement suggestion emitted by a compiler."""

    file_path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    replacement: str


@dataclass(frozen=True)
class CppDiagnostic:
    """A normalized compiler or clang-tool diagnostic."""

    target: InspectionTarget
    tool_rule_id: str = ""
    family: str = "compiler"
    fixits: tuple[CppFixIt, ...] = ()
    # Explanatory clang-tool notes belong to one actionable primary
    # diagnostic.  Keeping them nested preserves their locations, messages,
    # and fix-its without inflating warning or finding counts.
    related_diagnostics: tuple[CppDiagnostic, ...] = ()
    # JSON diagnostics may omit their location entirely. The shared parser
    # retains a safe external sentinel for reporting, while exact adapters use
    # this bit to distinguish "unlocated" from a genuinely external path.
    has_location: bool = True


@dataclass(frozen=True)
class DiagnosticParseResult:
    """Atomic parse outcome; diagnostics are empty whenever ``error`` is set."""

    diagnostics: tuple[CppDiagnostic, ...] = ()
    format_name: str = "text"
    error: str = ""


@dataclass(frozen=True)
class _Region:
    file_path: str
    start_line: int
    start_column: int | None
    end_line: int | None
    end_column: int | None


@dataclass(frozen=True)
class _ClangTidyText:
    retained: tuple[str, ...]
    generated: int | None = None
    suppressed: int = 0
    header_hint: bool = False
    error: str = ""


def _bounded_text(value: Any, label: str, *, limit: int = MAX_MESSAGE_CHARS) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{label} must be a non-empty string")
    if len(value) > limit:
        raise ValueError(f"{label} exceeds the bounded size")
    return value.strip()


def _bounded_replacement(value: Any, label: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError(f"{label} must be a string")
    if len(value) > MAX_REPLACEMENT_CHARS:
        raise ValueError(f"{label} exceeds the bounded size")
    return value


def _number(value: Any, label: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if not 0 < value <= MAX_DIAGNOSTIC_LINE:
        raise ValueError(f"{label} is outside the supported range")
    return value


def _text_number(value: str | None, label: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if value is None or len(value) > 10:
        raise ValueError(f"{label} is invalid")
    try:
        parsed = int(value)
    except (OverflowError, ValueError) as err:
        raise ValueError(f"{label} is invalid") from err
    return _number(parsed, label, optional=optional)


def _diagnostic_path(project_root: Path, cwd: Path, value: Any) -> str:
    raw = _bounded_text(value, "diagnostic file", limit=4_096)
    try:
        lexical = Path(raw)
        path = (lexical if lexical.is_absolute() else cwd / lexical).resolve(strict=False)
        return path.relative_to(project_root).as_posix()
    except (OSError, RuntimeError, ValueError):
        # Tool diagnostics can legitimately originate in system headers. Keep
        # a location without publishing a machine-specific absolute path.
        return "[external]"


def _position(value: Any, label: str) -> tuple[str, int, int | None]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    file_value = value.get("file")
    line = _number(value.get("line"), f"{label}.line")
    column_value = value.get("column", value.get("display-column", value.get("byte-column")))
    column = _number(column_value, f"{label}.column", optional=True)
    assert line is not None
    return _bounded_text(file_value, f"{label}.file", limit=4_096), line, column


def _json_region(project_root: Path, cwd: Path, value: Any) -> _Region:
    if not isinstance(value, dict):
        raise ValueError("diagnostic location must be an object")
    if "caret" in value:
        caret = value["caret"]
        start = value.get("start", caret)
        finish = value.get("finish", caret)
    else:
        caret = value
        start = value
        finish = value
    caret_file, caret_line, caret_column = _position(caret, "diagnostic caret")
    start_file, start_line, start_column = _position(start, "diagnostic start")
    finish_file, finish_line, finish_column = _position(finish, "diagnostic finish")
    if start_file != caret_file or finish_file != caret_file:
        raise ValueError("diagnostic range crosses files")
    if (start_line, start_column or 0) > (caret_line, caret_column or 0):
        raise ValueError("diagnostic start follows the caret")
    if (finish_line, finish_column or 0) < (caret_line, caret_column or 0):
        raise ValueError("diagnostic finish precedes the caret")
    return _Region(
        file_path=_diagnostic_path(project_root, cwd, caret_file),
        start_line=caret_line,
        start_column=caret_column,
        end_line=finish_line,
        end_column=finish_column,
    )


def _kind(value: Any) -> tuple[str, EngineStatus]:
    kind = _bounded_text(value, "diagnostic kind", limit=32).casefold()
    if kind in {"fatal error", "error", "internal compiler error", "sorry, unimplemented"}:
        return kind, EngineStatus.FAIL
    if kind in {"warning", "note", "remark", "anachronism"}:
        return kind, EngineStatus.WARN
    raise ValueError(f"unsupported diagnostic kind: {kind}")


def _json_fixit(project_root: Path, cwd: Path, value: Any) -> CppFixIt:
    if not isinstance(value, dict):
        raise ValueError("diagnostic fix-it must be an object")
    replacement_value = value.get("string", value.get("replacement"))
    replacement = _bounded_replacement(replacement_value, "diagnostic fix-it replacement")
    if "start" in value and ("next" in value or "end" in value):
        start = value["start"]
        end = value.get("next", value.get("end"))
    elif isinstance(value.get("range"), dict):
        range_value = value["range"]
        start = range_value.get("start")
        end = range_value.get("end")
    else:
        raise ValueError("diagnostic fix-it range is missing")
    start_file, start_line, start_column = _position(start, "fix-it start")
    end_file, end_line, end_column = _position(end, "fix-it end")
    if start_file != end_file or start_column is None or end_column is None:
        raise ValueError("diagnostic fix-it range is invalid")
    if (end_line, end_column) < (start_line, start_column):
        raise ValueError("diagnostic fix-it range is reversed")
    return CppFixIt(
        file_path=_diagnostic_path(project_root, cwd, start_file),
        start_line=start_line,
        start_column=start_column,
        end_line=end_line,
        end_column=end_column,
        replacement=replacement,
    )


def _json_diagnostic(
    project_root: Path,
    cwd: Path,
    value: Any,
    output: list[CppDiagnostic],
    *,
    depth: int,
) -> None:
    if depth > MAX_DIAGNOSTIC_DEPTH:
        raise ValueError("diagnostic nesting exceeds the bounded depth")
    if len(output) >= MAX_DIAGNOSTICS:
        raise ValueError("diagnostic count exceeds the bounded limit")
    if not isinstance(value, dict):
        raise ValueError("diagnostic entry must be an object")
    kind, status = _kind(value.get("kind", value.get("level")))
    message = _bounded_text(value.get("message"), "diagnostic message")
    locations = value.get("locations")
    if locations is not None:
        if not isinstance(locations, list) or len(locations) > 128:
            raise ValueError("diagnostic locations must be a bounded list")
        location_value = locations[0] if locations else None
        for secondary in locations[1:]:
            _json_region(project_root, cwd, secondary)
    else:
        location_value = value.get("location")
    region = (
        _json_region(project_root, cwd, location_value)
        if location_value is not None
        else _Region("[external]", 1, None, None, None)
    )
    rule_value = value.get("option", value.get("rule", value.get("check_name", "")))
    if rule_value:
        rule = _bounded_text(rule_value, "diagnostic rule", limit=256)
        if re.fullmatch(r"[-A-Za-z0-9_.]+", rule) is None:
            raise ValueError("diagnostic rule contains unsupported characters")
    else:
        rule = ""
    fixits_value = value.get("fixits", [])
    if not isinstance(fixits_value, list) or len(fixits_value) > 128:
        raise ValueError("diagnostic fix-its must be a bounded list")
    fixits = tuple(_json_fixit(project_root, cwd, item) for item in fixits_value)
    target_name = f"Compiler:{rule}" if rule else "C++Syntax"
    output.append(
        CppDiagnostic(
            target=InspectionTarget(
                file_path=region.file_path,
                start_line=region.start_line,
                end_line=region.end_line,
                start_column=region.start_column,
                end_column=region.end_column,
                target_name=target_name,
                status=status,
                message=f"{kind}: {message}",
            ),
            tool_rule_id=rule,
            fixits=fixits,
            has_location=location_value is not None,
        )
    )
    children = value.get("children", [])
    if not isinstance(children, list):
        raise ValueError("diagnostic children must be a list")
    for child in children:
        _json_diagnostic(project_root, cwd, child, output, depth=depth + 1)


def _json_entries(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        raise ValueError("compiler JSON root must be an array or object")
    if "diagnostics" in value:
        diagnostics = value["diagnostics"]
        if not isinstance(diagnostics, list):
            raise ValueError("compiler JSON diagnostics must be an array")
        return diagnostics
    if "kind" in value or "level" in value:
        return [value]
    raise ValueError("compiler JSON object has no diagnostics")


def _parse_json(project_root: Path, cwd: Path, text: str) -> DiagnosticParseResult:
    try:
        value = json.loads(text)
        output: list[CppDiagnostic] = []
        for entry in _json_entries(value):
            _json_diagnostic(project_root, cwd, entry, output, depth=0)
        return DiagnosticParseResult(tuple(output), "json")
    except (json.JSONDecodeError, RecursionError, ValueError) as err:
        return DiagnosticParseResult(format_name="json", error=str(err))


def _decode_c_string(value: str, label: str, *, replacement: bool = False) -> str:
    try:
        decoded = json.loads(f'"{value}"')
    except json.JSONDecodeError as err:
        raise ValueError(f"{label} is not a supported quoted string") from err
    if replacement:
        return _bounded_replacement(decoded, label)
    return _bounded_text(decoded, label, limit=4_096)


def _text_fixit(project_root: Path, cwd: Path, match: re.Match[str]) -> CppFixIt:
    file_value = _decode_c_string(match.group("file"), "fix-it file")
    replacement = _decode_c_string(
        match.group("replacement"), "fix-it replacement", replacement=True
    )
    start_line = _text_number(match.group("start_line"), "fix-it start line")
    start_column = _text_number(match.group("start_column"), "fix-it start column")
    end_line = _text_number(match.group("end_line"), "fix-it end line")
    end_column = _text_number(match.group("end_column"), "fix-it end column")
    assert start_line is not None
    assert start_column is not None
    assert end_line is not None
    assert end_column is not None
    if (end_line, end_column) < (start_line, start_column):
        raise ValueError("fix-it range is reversed")
    return CppFixIt(
        file_path=_diagnostic_path(project_root, cwd, file_value),
        start_line=start_line,
        start_column=start_column,
        end_line=end_line,
        end_column=end_column,
        replacement=replacement,
    )


def _parse_text(project_root: Path, cwd: Path, text: str) -> DiagnosticParseResult:
    output: list[CppDiagnostic] = []
    ancillary_output = False
    try:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = _TEXT_DIAGNOSTIC_RE.match(line)
            if match:
                if len(output) >= MAX_DIAGNOSTICS:
                    raise ValueError("diagnostic count exceeds the bounded limit")
                kind, status = _kind(match.group("kind"))
                file_path = _diagnostic_path(project_root, cwd, match.group("file"))
                line_number = _text_number(match.group("line"), "diagnostic line")
                column = _text_number(match.group("column"), "diagnostic column", optional=True)
                assert line_number is not None
                message = _bounded_text(match.group("message"), "diagnostic message")
                rule = match.group("rule") or ""
                output.append(
                    CppDiagnostic(
                        target=InspectionTarget(
                            file_path=file_path,
                            start_line=line_number,
                            start_column=column,
                            target_name=f"Compiler:{rule}" if rule else "C++Syntax",
                            status=status,
                            message=f"{kind}: {message}",
                        ),
                        tool_rule_id=rule,
                    )
                )
                continue
            fixit_match = _TEXT_FIXIT_RE.match(line)
            if fixit_match:
                if not output:
                    raise ValueError("fix-it appeared before a diagnostic")
                fixit = _text_fixit(project_root, cwd, fixit_match)
                output[-1] = replace(output[-1], fixits=(*output[-1].fixits, fixit))
                continue
            if line.startswith("In file included from") or line.startswith("from "):
                ancillary_output = True
                continue
            if output and _TEXT_CONTEXT_RE.fullmatch(line) is not None:
                continue
            if (
                _TEXT_CONTEXT_HEADER_RE.fullmatch(line)
                or _TEXT_REQUIRED_FROM_RE.fullmatch(line)
                or _TEXT_TRAILER_RE.fullmatch(line)
            ):
                ancillary_output = True
                continue
            raise ValueError(f"unrecognized compiler output line: {line!r}")
        if ancillary_output and not output:
            raise ValueError("compiler context output has no located diagnostic")
        return DiagnosticParseResult(tuple(output), "text")
    except ValueError as err:
        return DiagnosticParseResult(format_name="text", error=str(err))


def parse_compiler_diagnostics(
    project_root: Path,
    cwd: Path,
    stdout: str,
    stderr: str,
) -> DiagnosticParseResult:
    """Parse compiler output atomically without accepting mixed/unknown formats."""

    root = project_root.resolve(strict=False)
    if len(stdout) + len(stderr) > MAX_DIAGNOSTIC_OUTPUT_CHARS:
        return DiagnosticParseResult(error="compiler diagnostic output exceeds the bounded size")
    if "\x00" in stdout or "\x00" in stderr:
        return DiagnosticParseResult(error="compiler diagnostic output contains a null byte")
    non_empty = [stream.strip() for stream in (stdout, stderr) if stream.strip()]
    if not non_empty:
        return DiagnosticParseResult()
    text = non_empty[0] if len(non_empty) == 1 else "\n".join(non_empty)
    if text.lstrip().startswith(("[", "{")):
        return _parse_json(root, cwd, text)
    return _parse_text(root, cwd, text)


def _normalize_clazy_rule(rule: str) -> str:
    match = _CLAZY_RULE_RE.fullmatch(rule)
    if match is None:
        raise ValueError("clazy diagnostic rule is unsupported or unsafe")
    return f"clazy-{match.group('name')}"


@dataclass
class _ClazyTextState:
    output: list[CppDiagnostic] = field(default_factory=list)
    ancillary_output: bool = False
    inherited_rule: str = ""
    previous_family: str = ""
    saw_diagnostic: bool = False
    diagnostic_count: int = 0
    legacy_source_line: str | None = None
    legacy_context_stage: int = 0
    source_roots: tuple[Path, ...] = ()
    source_context_bytes: int = 0
    source_line_cache: dict[tuple[Path, int, tuple[int, int, int, int]], str] = field(
        default_factory=dict
    )


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _clazy_source_roots(project_root: Path, values: tuple[Path, ...]) -> tuple[Path, ...]:
    if len(values) > MAX_CLAZY_SOURCE_ROOTS:
        raise ValueError("clazy source-root count exceeds the bounded limit")
    roots = [project_root]
    for value in values:
        if not isinstance(value, Path):
            raise ValueError("clazy source roots must be paths")
        try:
            root = value.resolve(strict=True)
            if not root.is_dir() or root == Path(root.anchor):
                raise ValueError("clazy source root is not a bounded directory")
        except (OSError, RuntimeError) as err:
            raise ValueError("clazy source root is unavailable") from err
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _clazy_source_line(
    cwd: Path,
    raw_path: str,
    line_number: int,
    state: _ClazyTextState,
) -> str | None:
    """Read one allowlisted source line under a per-output byte budget."""

    try:
        raw = _bounded_text(raw_path, "diagnostic file", limit=4_096)
        lexical = Path(raw)
        path = (lexical if lexical.is_absolute() else cwd / lexical).resolve(strict=True)
        if not any(_inside(path, root) for root in state.source_roots):
            return None
        details = path.stat()
        if not stat.S_ISREG(details.st_mode):
            return None
    except (OSError, RuntimeError, ValueError):
        return None

    identity = (details.st_dev, details.st_ino, details.st_size, details.st_mtime_ns)
    key = (path, line_number, identity)
    if key in state.source_line_cache:
        return state.source_line_cache[key]
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        opened_identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        if not stat.S_ISREG(opened.st_mode) or opened_identity != identity:
            return None
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            selected: str | None = None
            for current_number in range(1, line_number + 1):
                available = MAX_DIAGNOSTIC_OUTPUT_CHARS - state.source_context_bytes
                if available <= 0:
                    return None
                current = handle.readline(min(MAX_CLAZY_SOURCE_LINE_BYTES, available + 1))
                if not current:
                    return None
                if len(current) > available:
                    state.source_context_bytes = MAX_DIAGNOSTIC_OUTPUT_CHARS
                    return None
                state.source_context_bytes += len(current)
                line = current.rstrip(b"\r\n").decode("utf-8", errors="replace")
                if len(line) > MAX_MESSAGE_CHARS:
                    return None
                if current_number == line_number:
                    selected = line
                    break
            after = os.fstat(handle.fileno())
            after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            if selected is None or after_identity != opened_identity:
                return None
            state.source_line_cache[key] = selected
            return selected
    except (OSError, RuntimeError, ValueError):
        return None
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
    return None


def _select_clazy_rule(
    rule: str,
    kind: str,
    state: _ClazyTextState,
) -> tuple[str, str] | None:
    if rule and not rule.startswith("-Wclazy-"):
        if not rule.startswith("-W"):
            raise ValueError("compiler diagnostic rule is unsupported or unsafe")
        if kind not in {"warning", "remark", "note"}:
            raise ValueError("compiler error cannot be excluded from clazy output")
        state.inherited_rule = ""
        state.previous_family = "compiler"
        return None
    if rule:
        normalized_rule = _normalize_clazy_rule(rule)
        state.inherited_rule = normalized_rule
        state.previous_family = "clazy"
        return normalized_rule, "ClazyNote" if kind == "note" else "Clazy"
    if kind == "note" and state.previous_family == "compiler":
        return None
    if kind == "note" and state.inherited_rule:
        state.previous_family = "clazy"
        return state.inherited_rule, "ClazyNote"
    raise ValueError("clazy diagnostic has no check identifier")


def _append_clazy_diagnostic(
    project_root: Path,
    cwd: Path,
    match: re.Match[str],
    state: _ClazyTextState,
) -> None:
    state.diagnostic_count += 1
    if state.diagnostic_count > MAX_DIAGNOSTICS:
        raise ValueError("diagnostic count exceeds the bounded limit")
    kind, status = _kind(match.group("kind"))
    file_path = _diagnostic_path(project_root, cwd, match.group("file"))
    line_number = _text_number(match.group("line"), "diagnostic line")
    column = _text_number(match.group("column"), "diagnostic column", optional=True)
    assert line_number is not None
    state.legacy_source_line = _clazy_source_line(
        cwd,
        match.group("file"),
        line_number,
        state,
    )
    state.legacy_context_stage = 0
    message = _bounded_text(match.group("message"), "diagnostic message")
    if _CLAZY_RULE_MARKER_RE.search(message):
        raise ValueError("clazy diagnostic contains a conflicting rule marker")

    selected = _select_clazy_rule(match.group("rule") or "", kind, state)
    state.saw_diagnostic = True
    if selected is None:
        return
    normalized_rule, target_prefix = selected
    state.output.append(
        CppDiagnostic(
            target=InspectionTarget(
                file_path=file_path,
                start_line=line_number,
                start_column=column,
                target_name=f"{target_prefix}:{normalized_rule}",
                status=status,
                message=f"{kind}: {message}",
            ),
            tool_rule_id=normalized_rule,
            family="clazy",
        )
    )


def _end_legacy_context(state: _ClazyTextState) -> None:
    state.legacy_source_line = None
    state.legacy_context_stage = -1


def _consume_clazy_context(raw_line: str, line: str, state: _ClazyTextState) -> bool:
    if line.startswith("In file included from") or line.startswith("from "):
        state.ancillary_output = True
        _end_legacy_context(state)
        return True
    if (
        state.legacy_context_stage == 0
        and state.legacy_source_line is not None
        and raw_line == state.legacy_source_line
    ):
        state.legacy_context_stage = 1
        return True
    if state.saw_diagnostic and _TEXT_CONTEXT_RE.fullmatch(line):
        if state.legacy_context_stage == 1 and line.startswith(("^", "~")):
            state.legacy_context_stage = 2
        else:
            _end_legacy_context(state)
        return True
    if (
        state.legacy_context_stage == 2
        and len(raw_line) <= MAX_MESSAGE_CHARS
        and _TEXT_LEGACY_PREVIEW_RE.fullmatch(raw_line)
    ):
        state.legacy_source_line = None
        state.legacy_context_stage = 3
        return True
    if (
        _TEXT_CONTEXT_HEADER_RE.fullmatch(line)
        or _TEXT_REQUIRED_FROM_RE.fullmatch(line)
        or _TEXT_TRAILER_RE.fullmatch(line)
    ):
        state.ancillary_output = True
        _end_legacy_context(state)
        return True
    return False


def _parse_clazy_text(
    project_root: Path,
    cwd: Path,
    text: str,
    source_roots: tuple[Path, ...],
) -> DiagnosticParseResult:
    """Parse clazy output and validate compiler diagnostics before excluding them."""

    try:
        roots = _clazy_source_roots(project_root, source_roots)
    except ValueError as err:
        return DiagnosticParseResult(format_name="clazy-text", error=str(err))
    state = _ClazyTextState(source_roots=roots)
    try:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            match = _TEXT_DIAGNOSTIC_RE.fullmatch(line)
            if match:
                _append_clazy_diagnostic(project_root, cwd, match, state)
                continue

            if _TEXT_FIXIT_RE.fullmatch(line):
                raise ValueError("clazy fix-it output is not supported")
            if _consume_clazy_context(raw_line, line, state):
                continue
            raise ValueError(f"unrecognized clazy output line: {line!r}")

        if state.ancillary_output and not state.saw_diagnostic:
            raise ValueError("clazy context output has no located diagnostic")
        return DiagnosticParseResult(tuple(state.output), "clazy-text")
    except (OverflowError, ValueError) as err:
        return DiagnosticParseResult(format_name="clazy-text", error=str(err))


def parse_clazy_diagnostics(
    project_root: Path,
    cwd: Path,
    stdout: str,
    stderr: str,
    *,
    source_roots: tuple[Path, ...] = (),
) -> DiagnosticParseResult:
    """Parse clazy diagnostics atomically with a strict rule allowlist shape."""

    if not isinstance(stdout, str) or not isinstance(stderr, str):
        return DiagnosticParseResult(
            format_name="clazy-text", error="clazy diagnostic output must be text"
        )
    if len(stdout) + len(stderr) > MAX_DIAGNOSTIC_OUTPUT_CHARS:
        return DiagnosticParseResult(
            format_name="clazy-text",
            error="clazy diagnostic output exceeds the bounded size",
        )
    if "\x00" in stdout or "\x00" in stderr:
        return DiagnosticParseResult(
            format_name="clazy-text",
            error="clazy diagnostic output contains a null byte",
        )
    non_empty = [stream.strip() for stream in (stdout, stderr) if stream.strip()]
    if not non_empty:
        return DiagnosticParseResult(format_name="clazy-text")
    root = project_root.resolve(strict=False)
    return _parse_clazy_text(root, cwd, "\n".join(non_empty), source_roots)


def _bounded_empty_note(match: re.Match[str]) -> bool:
    column = match.group("column")
    return (
        len(match.group("file")) <= 4_096
        and int(match.group("line")) <= MAX_DIAGNOSTIC_LINE
        and (column is None or int(column) <= MAX_DIAGNOSTIC_LINE)
    )


def _empty_note_has_expected_parent(
    empty_note: re.Match[str], parent: re.Match[str] | None
) -> bool:
    return bool(
        parent
        and parent.group("kind") == "warning"
        and parent.group("rule") == "bugprone-easily-swappable-parameters"
        and parent.group("file") == empty_note.group("file")
        and parent.group("line") == empty_note.group("line")
        and parent.group("column") == empty_note.group("column")
    )


def _is_expected_conversion_note(
    pending: re.Match[str],
    diagnostic: re.Match[str],
    parent: re.Match[str] | None,
    last_parameter_line: str | None,
) -> bool:
    if (
        parent is None
        or last_parameter_line is None
        or diagnostic.group("kind") != "note"
        or diagnostic.group("rule") is not None
        or diagnostic.group("file") != pending.group("file")
        or diagnostic.group("file") != parent.group("file")
        or not _CLANG_TIDY_CONVERSION_NOTE_RE.fullmatch(diagnostic.group("message"))
    ):
        return False
    line_values = (
        parent.group("line"),
        diagnostic.group("line"),
        last_parameter_line,
    )
    if any(len(value) > 10 for value in line_values):
        return False
    first_line, diagnostic_line, last_line = (int(value) for value in line_values)
    return first_line <= diagnostic_line <= last_line <= MAX_DIAGNOSTIC_LINE


def _empty_note_error() -> _ClangTidyText:
    return _ClangTidyText(
        (), error="clang-tidy empty note is not a valid bugprone structural separator"
    )


def _split_clang_tidy_text(stdout: str, stderr: str) -> _ClangTidyText:
    retained: list[str] = []
    generated: int | None = None
    suppressed = 0
    header_hint = False
    structural_parent: re.Match[str] | None = None
    structural_last_parameter_line: str | None = None
    pending_empty_note: re.Match[str] | None = None
    for raw_line in (stdout + "\n" + stderr).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        empty_note = _CLANG_TIDY_EMPTY_NOTE_RE.fullmatch(line)
        if empty_note:
            if (
                pending_empty_note
                or not _bounded_empty_note(empty_note)
                or not _empty_note_has_expected_parent(empty_note, structural_parent)
            ):
                return _empty_note_error()
            pending_empty_note = empty_note
            continue
        diagnostic = _TEXT_DIAGNOSTIC_RE.fullmatch(line)
        if diagnostic:
            if pending_empty_note and not _is_expected_conversion_note(
                pending_empty_note,
                diagnostic,
                structural_parent,
                structural_last_parameter_line,
            ):
                return _empty_note_error()
            if pending_empty_note:
                pending_empty_note = None
                # LLVM 18 can emit one empty structural separator and
                # conversion note for every convertible pair within the same
                # easily-swappable parameter range. Keep the bounded primary
                # context until a different diagnostic shape ends the group.
            elif diagnostic.group("rule") is not None:
                structural_parent = diagnostic
                structural_last_parameter_line = None
            elif structural_parent and (
                diagnostic.group("kind") != "note"
                or diagnostic.group("file") != structural_parent.group("file")
                or not _CLANG_TIDY_PARAMETER_RANGE_NOTE_RE.fullmatch(diagnostic.group("message"))
            ):
                structural_parent = None
                structural_last_parameter_line = None
            elif structural_parent and diagnostic.group("message").startswith("the last parameter"):
                structural_last_parameter_line = diagnostic.group("line")
            retained.append(raw_line)
            continue
        if _TEXT_CONTEXT_RE.fullmatch(line):
            retained.append(raw_line)
            continue
        if pending_empty_note:
            return _empty_note_error()
        if match := _CLANG_TIDY_GENERATED_RE.fullmatch(line):
            if generated is not None:
                return _ClangTidyText(
                    (), error="clang-tidy emitted duplicate generated-warning summaries"
                )
            generated = int(match.group("count"))
            continue
        if match := _CLANG_TIDY_SUPPRESSED_RE.fullmatch(line):
            if suppressed:
                return _ClangTidyText(
                    (), error="clang-tidy emitted duplicate suppression summaries"
                )
            suppressed = int(match.group("count"))
            continue
        if _CLANG_TIDY_HEADER_HINT_RE.fullmatch(line):
            header_hint = True
            continue
        structural_parent = None
        structural_last_parameter_line = None
        retained.append(raw_line)
    if pending_empty_note:
        return _empty_note_error()
    return _ClangTidyText(
        retained=tuple(retained),
        generated=generated,
        suppressed=suppressed,
        header_hint=header_hint,
    )


def _normalize_clang_tidy(
    diagnostics: tuple[CppDiagnostic, ...],
) -> DiagnosticParseResult:
    normalized: list[CppDiagnostic] = []
    related_by_primary: list[list[CppDiagnostic]] = []
    for diagnostic in diagnostics:
        rule = diagnostic.tool_rule_id
        if diagnostic.target.message.startswith("note:"):
            if not normalized:
                return DiagnosticParseResult(
                    format_name="clang-tidy-text",
                    error="clang-tidy note has no preceding primary diagnostic",
                )
            parent = normalized[-1]
            if rule and rule != parent.tool_rule_id:
                return DiagnosticParseResult(
                    format_name="clang-tidy-text",
                    error="clang-tidy note check does not match its primary diagnostic",
                )
            note_prefix = (
                "ClangAnalyzerNote" if parent.family == "clang-analyzer" else "ClangTidyNote"
            )
            note = replace(
                diagnostic,
                target=replace(
                    diagnostic.target,
                    target_name=f"{note_prefix}:{parent.tool_rule_id}",
                ),
                tool_rule_id=parent.tool_rule_id,
                family=parent.family,
            )
            related_by_primary[-1].append(note)
            continue
        if rule:
            analyzer = rule.startswith("clang-analyzer-")
            family = "clang-analyzer" if analyzer else "clang-tidy"
            prefix = "ClangAnalyzer" if analyzer else "ClangTidy"
            normalized.append(
                replace(
                    diagnostic,
                    target=replace(diagnostic.target, target_name=f"{prefix}:{rule}"),
                    family=family,
                )
            )
            related_by_primary.append([])
            continue
        return DiagnosticParseResult(
            format_name="clang-tidy-text",
            error="clang-tidy diagnostic has no check identifier",
        )
    grouped = tuple(
        replace(primary, related_diagnostics=tuple(related))
        for primary, related in zip(normalized, related_by_primary, strict=True)
    )
    return DiagnosticParseResult(grouped, "clang-tidy-text")


def _clang_tidy_accounting_error(
    text: _ClangTidyText, diagnostics: tuple[CppDiagnostic, ...]
) -> str:
    # Clang's generated-warning counter and clang-tidy's rendered diagnostic
    # inventory have different granularity: overlapping checks may be
    # coalesced into one rendered warning.  The suppression trailer proves
    # ignored diagnostics were accounted for, but its count therefore cannot
    # be added to the rendered-message count and compared for equality.
    if text.generated is not None and not diagnostics and not text.suppressed:
        return "clang-tidy generated-warning summary has no diagnostic accounting"
    if text.header_hint and text.generated is None and not text.suppressed and not diagnostics:
        return "clang-tidy header-filter hint has no diagnostic summary"
    return ""


def parse_clang_tidy_diagnostics(
    project_root: Path,
    cwd: Path,
    stdout: str,
    stderr: str,
) -> DiagnosticParseResult:
    """Parse clang-tidy text atomically and separate analyzer check families."""

    root = project_root.resolve(strict=False)
    if len(stdout) + len(stderr) > MAX_DIAGNOSTIC_OUTPUT_CHARS:
        return DiagnosticParseResult(
            format_name="clang-tidy-text",
            error="clang-tidy diagnostic output exceeds the bounded size",
        )
    if "\x00" in stdout or "\x00" in stderr:
        return DiagnosticParseResult(
            format_name="clang-tidy-text",
            error="clang-tidy diagnostic output contains a null byte",
        )
    text = _split_clang_tidy_text(stdout, stderr)
    if text.error:
        return DiagnosticParseResult(format_name="clang-tidy-text", error=text.error)
    result = _parse_text(root, cwd, "\n".join(text.retained))
    if result.error:
        return replace(result, format_name="clang-tidy-text")
    normalized = _normalize_clang_tidy(result.diagnostics)
    if normalized.error:
        return normalized
    if error := _clang_tidy_accounting_error(text, normalized.diagnostics):
        return DiagnosticParseResult(format_name="clang-tidy-text", error=error)
    return normalized
