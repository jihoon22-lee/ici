"""Deterministic, line-preserving C++ lexical normalization for clone analysis.

This is deliberately a lexer, not a preprocessor or parser.  It removes comments
and directives, preserves token boundaries and language-significant spellings,
and normalizes ordinary identifiers and literal values for Type-2 matching.
"""

from __future__ import annotations

import bisect
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass

_TOKEN_SEPARATOR = "\x1f"
MAX_CPP_DUPLICATE_TOKENS = 500_000

_CPP_KEYWORDS = frozenset(
    """
    alignas alignof and and_eq asm atomic_cancel atomic_commit atomic_noexcept auto
    bitand bitor bool break case catch char char8_t char16_t char32_t class compl
    concept const consteval constexpr constinit const_cast continue co_await co_return
    co_yield decltype default delete do double dynamic_cast else enum explicit export
    extern false final float for friend goto if import inline int long module mutable
    namespace new noexcept not not_eq nullptr operator or or_eq override private
    protected public reflexpr register reinterpret_cast requires return short signed
    sizeof static static_assert static_cast struct switch synchronized template this
    thread_local throw true try typedef typeid typename union unsigned using virtual
    void volatile wchar_t while xor xor_eq
    """.split()  # noqa: SIM905 - the language keyword table is clearer grouped by spelling
)

_CPP_ATTRIBUTES = frozenset(
    {
        "assume",
        "carries_dependency",
        "deprecated",
        "fallthrough",
        "likely",
        "maybe_unused",
        "no_unique_address",
        "nodiscard",
        "noreturn",
        "unlikely",
    }
)

_QT_EXACT_IDENTIFIERS = frozenset(
    {
        "CONSTANT",
        "DESIGNABLE",
        "FINAL",
        "NOTIFY",
        "READ",
        "REQUIRED",
        "RESET",
        "REVISION",
        "SCRIPTABLE",
        "STORED",
        "USER",
        "WRITE",
        "QByteArrayLiteral",
        "QLatin1Char",
        "QLatin1String",
        "QStringLiteral",
        "Qt",
        "connect",
        "disconnect",
        "emit",
        "foreach",
        "forever",
        "invokeMethod",
        "qOverload",
        "qRegisterMetaType",
        "qobject_cast",
        "qvariant_cast",
        "signals",
        "slots",
    }
)

_DECLARATION_PREFIXES = frozenset(
    {
        "ID",
        "auto",
        "bool",
        "char",
        "char8_t",
        "char16_t",
        "char32_t",
        "double",
        "float",
        "int",
        "long",
        "short",
        "signed",
        "unsigned",
        "void",
        "wchar_t",
        "*",
        "&",
        "&&",
        ">",
        "]",
    }
)

_PUNCTUATORS = tuple(
    sorted(
        {
            "%:%:",
            "<=>",
            ">>=",
            "<<=",
            "->*",
            "...",
            "::",
            ".*",
            "->",
            "++",
            "--",
            "<<",
            ">>",
            "<=",
            ">=",
            "==",
            "!=",
            "&&",
            "||",
            "*=",
            "/=",
            "%=",
            "+=",
            "-=",
            "&=",
            "^=",
            "|=",
            "##",
            "<%",
            "%>",
            "<:",
            ":>",
            "%:",
        },
        key=len,
        reverse=True,
    )
)

_DIGRAPHS = {
    "<%": "{",
    "%>": "}",
    "<:": "[",
    ":>": "]",
    "%:": "#",
    "%:%:": "##",
}

_NUMBER_RE = re.compile(
    r"""
    (?P<core>
        0[xX][0-9A-Fa-f](?:'?[0-9A-Fa-f])*
            (?:\.(?:[0-9A-Fa-f](?:'?[0-9A-Fa-f])*)?)?
            (?:[pP][+-]?[0-9](?:'?[0-9])*)?
      | 0[bB][01](?:'?[01])*
      | (?:
            [0-9](?:'?[0-9])*\.(?:[0-9](?:'?[0-9])*)?
          | \.[0-9](?:'?[0-9])*
        )(?:[eE][+-]?[0-9](?:'?[0-9])*)?
      | [0-9](?:'?[0-9])*[eE][+-]?[0-9](?:'?[0-9])*
      | [0-9](?:'?[0-9])*
    )
    (?P<suffix>[A-Za-z_][A-Za-z0-9_]*)?
    """,
    re.VERBOSE,
)

_RAW_PREFIXES = ('u8R"', 'uR"', 'UR"', 'LR"', 'R"')
_STRING_PREFIXES = (
    'u8"',
    "u8'",
    'u"',
    "u'",
    'U"',
    "U'",
    'L"',
    "L'",
    '"',
    "'",
)


@dataclass(frozen=True)
class _SplicedCppSource:
    text: str
    physical_line_starts: tuple[int, ...]
    last_physical_source_line: int

    def line_at(self, offset: int) -> int:
        if offset < 0 or offset >= len(self.text):
            raise IndexError("logical C++ source offset is out of range")
        return bisect.bisect_right(self.physical_line_starts, offset)


def _identifier_start(character: str) -> bool:
    return character == "_" or character.isalpha()


def _identifier_continue(character: str) -> bool:
    return character == "_" or character.isalnum()


def _consume_identifier(text: str, start: int) -> int:
    end = start + 1
    while end < len(text) and _identifier_continue(text[end]):
        end += 1
    return end


def _semantic_identifier(identifier: str) -> str:
    if len(identifier) <= 128:
        return f"NAME({identifier})"
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
    return f"NAME_SHA256({digest})"


def _identifier_token(identifier: str, previous: str, following: str) -> str:
    if (
        identifier in _CPP_KEYWORDS
        or identifier in _CPP_ATTRIBUTES
        or identifier in _QT_EXACT_IDENTIFIERS
        or re.fullmatch(r"(?:Q|QT|QML)_[A-Z0-9_]+", identifier)
    ):
        return identifier
    declaration_name = following == "(" and (
        previous in _DECLARATION_PREFIXES
        or previous.startswith("NAME(")
        or previous.startswith("NAME_SHA256(")
    )
    if (
        previous in {".", ".*", "->", "->*", "::", "case", "goto"}
        or following == "::"
        or (following == "(" and not declaration_name)
        or (len(identifier) > 1 and identifier[0].isupper() and identifier == identifier.upper())
    ):
        return _semantic_identifier(identifier)
    return "ID"


def _following_syntax(text: str, start: int) -> str:
    index = start
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            if newline < 0:
                return ""
            index = newline + 1
            continue
        if text.startswith("/*", index):
            close_at = text.find("*/", index + 2)
            if close_at < 0:
                return ""
            index = close_at + 2
            continue
        break
    if text.startswith("::", index):
        return "::"
    return text[index] if index < len(text) else ""


def _literal_suffix(text: str, start: int) -> tuple[int, str]:
    if start < len(text) and _identifier_start(text[start]):
        end = _consume_identifier(text, start)
        return end, text[start:end]
    return start, ""


def _with_suffix(kind: str, suffix: str) -> str:
    return f"{kind}:UDL({suffix})" if suffix else kind


def _consume_raw_literal(text: str, start: int) -> tuple[int, str] | None:
    prefix = next((item for item in _RAW_PREFIXES if text.startswith(item, start)), None)
    if prefix is None:
        return None
    delimiter_start = start + len(prefix)
    opening = text.find("(", delimiter_start, delimiter_start + 17)
    if opening < 0:
        return None
    delimiter = text[delimiter_start:opening]
    if any(character.isspace() or character in "()\\" for character in delimiter):
        return None
    closing = ")" + delimiter + '"'
    close_at = text.find(closing, opening + 1)
    if close_at < 0:
        return len(text), "UNTERMINATED_RAW_STRING_LIT"
    end = close_at + len(closing)
    end, suffix = _literal_suffix(text, end)
    return end, _with_suffix("RAW_STRING_LIT", suffix)


def _consume_ordinary_literal(text: str, start: int) -> tuple[int, str] | None:
    prefix = next((item for item in _STRING_PREFIXES if text.startswith(item, start)), None)
    if prefix is None:
        return None
    quote = prefix[-1]
    kind = "STRING_LIT" if quote == '"' else "CHAR_LIT"
    index = start + len(prefix)
    while index < len(text):
        character = text[index]
        if character == "\\":
            if index + 1 >= len(text):
                return len(text), f"UNTERMINATED_{kind}"
            index += 2
            continue
        if character == quote:
            end, suffix = _literal_suffix(text, index + 1)
            return end, _with_suffix(kind, suffix)
        if character == "\n":
            return index, f"UNTERMINATED_{kind}"
        index += 1
    return len(text), f"UNTERMINATED_{kind}"


def _consume_literal(text: str, start: int) -> tuple[int, str] | None:
    return _consume_raw_literal(text, start) or _consume_ordinary_literal(text, start)


def _consume_directive(text: str, start: int) -> int:
    """Consume one logical directive after translation-phase line splicing."""

    newline = text.find("\n", start)
    return len(text) if newline < 0 else newline + 1


def _splice_cpp_source(text: str) -> _SplicedCppSource:
    """Apply C++ phase-2 line splicing and retain physical line provenance.

    Compact logical offsets retain each physical line start without allocating
    one Python object per source character.  Removing the pair before lexing is
    important: identifiers, numbers, literals, comments, and directives may
    all cross a splice, and only an immediately adjacent backslash/newline pair
    is removed.
    """

    physical_line_starts = [0]
    removed = 0
    for match in re.finditer(r"\\\n|\n", text):
        if match.group() == "\\\n":
            logical_offset = match.start() - removed
            removed += 2
        else:
            logical_offset = match.end() - removed
        physical_line_starts.append(logical_offset)

    last_source_line = 0
    if text:
        last_source_line = len(physical_line_starts) - 1 + int(not text.endswith("\n"))
    return _SplicedCppSource(
        text.replace("\\\n", ""),
        tuple(physical_line_starts),
        last_source_line,
    )


def cpp_directive_lines(text: str) -> frozenset[int]:
    """Return physical lines occupied by actual preprocessing directives."""

    if not isinstance(text, str):
        raise TypeError("C++ source must be text")
    if "\x00" in text:
        raise ValueError("C++ source must not contain a null byte")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    spliced = _splice_cpp_source(normalized)
    logical = spliced.text
    index = 0
    at_line_prefix = True
    barriers: set[int] = set()
    while index < len(logical):
        character = logical[index]
        if character == "\n":
            at_line_prefix = True
            index += 1
            continue
        if character.isspace():
            index += 1
            continue
        if at_line_prefix and (character == "#" or logical.startswith("%:", index)):
            start = index
            end = _consume_directive(logical, start)
            start_line = spliced.line_at(start)
            end_line = spliced.line_at(end - 1)
            if end == len(logical) and (not logical or logical[-1] != "\n"):
                end_line = max(end_line, spliced.last_physical_source_line)
            barriers.update(range(start_line, end_line + 1))
            index = end
            at_line_prefix = True
            continue
        if logical.startswith("//", index):
            newline = logical.find("\n", index + 2)
            index = len(logical) if newline < 0 else newline
            continue
        if logical.startswith("/*", index):
            close_at = logical.find("*/", index + 2)
            end = len(logical) if close_at < 0 else close_at + 2
            if "\n" in logical[index:end]:
                at_line_prefix = True
            index = end
            continue
        literal = _consume_literal(logical, index)
        if literal is not None:
            index = literal[0]
            at_line_prefix = False
            continue
        at_line_prefix = False
        index += 1
    return frozenset(barriers)


def _number_token(match: re.Match[str]) -> str:
    core = match.group("core")
    suffix = match.group("suffix") or ""
    lower = core.casefold()
    is_float = "." in core or (("e" in lower and not lower.startswith("0x")) or "p" in lower)
    return _with_suffix("FLOAT_LIT" if is_float else "INT_LIT", suffix)


def tokenize_cpp_lines(text: str, *, max_tokens: int | None = None) -> tuple[tuple[int, str], ...]:
    """Return canonical C++ tokens grouped by their physical start line.

    Malformed trailing comments or literals are retained as bounded opaque
    tokens.  This keeps the heuristic deterministic without pretending that it
    parsed a translation unit successfully.
    """

    if not isinstance(text, str):
        raise TypeError("C++ source must be text")
    if "\x00" in text:
        raise ValueError("C++ source must not contain a null byte")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    spliced = _splice_cpp_source(text)
    text = spliced.text
    token_limit = MAX_CPP_DUPLICATE_TOKENS if max_tokens is None else max_tokens
    if type(token_limit) is not int or token_limit <= 0:
        raise ValueError("max_tokens must be a positive integer")

    by_line: dict[int, list[str]] = defaultdict(list)
    index = 0
    at_line_prefix = True
    previous_token = ""
    token_count = 0

    def emit(token_line: int, token: str) -> None:
        nonlocal previous_token, token_count
        token_count += 1
        if token_count > token_limit:
            raise ValueError(f"max_tokens={token_limit} exceeded while tokenizing C++ source")
        by_line[token_line].append(token)
        previous_token = token

    while index < len(text):
        character = text[index]
        if character == "\n":
            at_line_prefix = True
            index += 1
            continue
        if character.isspace():
            index += 1
            continue

        if at_line_prefix and (character == "#" or text.startswith("%:", index)):
            index = _consume_directive(text, index)
            at_line_prefix = True
            previous_token = ""
            continue

        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline
            continue
        if text.startswith("/*", index):
            comment_line = spliced.line_at(index)
            close_at = text.find("*/", index + 2)
            if close_at < 0:
                emit(comment_line, "UNTERMINATED_BLOCK_COMMENT")
                break
            end = close_at + 2
            if "\n" in text[index:end]:
                at_line_prefix = True
            index = end
            continue

        literal = _consume_literal(text, index)
        if literal is not None:
            end, token = literal
            emit(spliced.line_at(index), token)
            index = end
            at_line_prefix = False
            continue

        number = _NUMBER_RE.match(text, index)
        if number is not None:
            emit(spliced.line_at(index), _number_token(number))
            index = number.end()
            at_line_prefix = False
            continue

        if _identifier_start(character):
            end = _consume_identifier(text, index)
            identifier = text[index:end]
            emit(
                spliced.line_at(index),
                _identifier_token(
                    identifier,
                    previous_token,
                    _following_syntax(text, end),
                ),
            )
            index = end
            at_line_prefix = False
            continue

        punctuator = next(
            (item for item in _PUNCTUATORS if text.startswith(item, index)),
            character,
        )
        emit(spliced.line_at(index), _DIGRAPHS.get(punctuator, punctuator))
        index += len(punctuator)
        at_line_prefix = False

    return tuple(
        (line_number, _TOKEN_SEPARATOR.join(tokens))
        for line_number, tokens in sorted(by_line.items())
        if tokens
    )
