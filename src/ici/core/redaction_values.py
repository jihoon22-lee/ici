"""Model-independent credential redaction primitives.

Keep these helpers below the domain-model layer so low-level tool discovery can
sanitize probe output without creating an import cycle through ``models``.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "***REDACTED***"

_QUOTED_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<key>password|passwd|secret|client[_-]?secret|api[_-]?key|access[_-]?key|auth[_-]?token|token)"
    r"(?P<op>\s*[=:]\s*)(?P<quote>[\"'])(?P<value>[^\r\n]*?)(?P=quote)"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<key>password|passwd|secret|client[_-]?secret|api[_-]?key|access[_-]?key|auth[_-]?token|token)"
    r"(?P<op>\s*[=:]\s*)(?P<value>[^\s,;\"']+)"
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?P<kind>[A-Z ]*PRIVATE KEY)-----.*?"
    r"(?:-----END (?P=kind)-----|\Z)",
    re.DOTALL,
)
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")
_FLAG_VALUE_RE = re.compile(
    r"(?i)(--?(?:password|passwd|secret|token|api[_-]?key)(?:=|\s+))([^\s]+)"
)
_KNOWN_TOKEN_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16})\b"
)


def _mask_assignment(match: re.Match[str]) -> str:
    quote = match.groupdict().get("quote", "")
    return f"{match.group('key')}{match.group('op')}{quote}{REDACTED}{quote}"


def redact_text(value: str) -> str:
    """Mask common credential forms while leaving diagnostic structure intact."""

    if not isinstance(value, str):
        raise ValueError(f"redaction input must be a string: {value!r}")
    masked = _QUOTED_SECRET_ASSIGNMENT_RE.sub(_mask_assignment, value)
    masked = _SECRET_ASSIGNMENT_RE.sub(_mask_assignment, masked)
    masked = _PRIVATE_KEY_RE.sub("-----BEGIN [REDACTED] PRIVATE KEY-----", masked)
    masked = _BEARER_RE.sub(rf"\1{REDACTED}", masked)
    masked = _FLAG_VALUE_RE.sub(rf"\1{REDACTED}", masked)
    return _KNOWN_TOKEN_RE.sub(REDACTED, masked)


def redact_data(value: Any) -> Any:
    """Recursively redact strings in reporter metadata without changing its shape."""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            base_key = redact_text(str(key))
            safe_key = base_key
            suffix = 2
            while safe_key in redacted:
                safe_key = f"{base_key}#{suffix}"
                suffix += 1
            redacted[safe_key] = redact_data(item)
        return redacted
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_data(item) for item in value)
    return value
