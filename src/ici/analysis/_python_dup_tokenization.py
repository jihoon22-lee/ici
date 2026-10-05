"""Line-preserving Python lexical normalization for Type-2 clone analysis."""

from __future__ import annotations

import ast
import bisect
import hashlib
import io
import token
import tokenize
from collections import defaultdict

_TOKEN_SEPARATOR = "\x1f"
MAX_PYTHON_DUPLICATE_TOKENS = 500_000
_PYTHON_KEYWORDS = frozenset(
    {
        "False",
        "None",
        "True",
        "and",
        "as",
        "assert",
        "async",
        "await",
        "break",
        "class",
        "continue",
        "def",
        "del",
        "elif",
        "else",
        "except",
        "finally",
        "for",
        "from",
        "global",
        "if",
        "import",
        "in",
        "is",
        "lambda",
        "nonlocal",
        "not",
        "or",
        "pass",
        "raise",
        "return",
        "try",
        "while",
        "with",
        "yield",
    }
)
_PYTHON_BUILTIN_CALLS = frozenset(
    {
        "abs",
        "aiter",
        "all",
        "anext",
        "any",
        "ascii",
        "bin",
        "bool",
        "breakpoint",
        "bytearray",
        "bytes",
        "callable",
        "chr",
        "classmethod",
        "compile",
        "complex",
        "delattr",
        "dict",
        "dir",
        "divmod",
        "enumerate",
        "eval",
        "exec",
        "filter",
        "float",
        "format",
        "frozenset",
        "getattr",
        "globals",
        "hasattr",
        "hash",
        "hex",
        "id",
        "input",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "locals",
        "map",
        "max",
        "memoryview",
        "min",
        "next",
        "object",
        "oct",
        "open",
        "ord",
        "pow",
        "print",
        "property",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "setattr",
        "slice",
        "sorted",
        "staticmethod",
        "str",
        "sum",
        "super",
        "tuple",
        "type",
        "vars",
        "zip",
        "__import__",
    }
)
_SKIPPED_TYPES = frozenset(
    {
        token.ENDMARKER,
        token.NEWLINE,
        tokenize.NL,
        token.ENCODING,
        token.COMMENT,
    }
)


def _semantic_identifier(value: str) -> str:
    if len(value) <= 128:
        return f"NAME({value})"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"NAME_SHA256({digest})"


def _name_token(
    value: str,
    position: tuple[int, int],
    soft_keywords: set[tuple[int, int]],
    following: str,
    semantic_anchor: bool,
) -> str:
    if value in _PYTHON_KEYWORDS or position in soft_keywords:
        return value
    if semantic_anchor or (following == "(" and value in _PYTHON_BUILTIN_CALLS):
        return _semantic_identifier(value)
    return "ID"


def _neighbor_syntax(tokens: list[tokenize.TokenInfo], index: int, step: int) -> str:
    cursor = index + step
    while 0 <= cursor < len(tokens):
        item = tokens[cursor]
        if item.type in {token.NEWLINE, token.ENDMARKER}:
            return ""
        if item.type not in {
            token.INDENT,
            token.DEDENT,
            tokenize.NL,
            tokenize.COMMENT,
        }:
            return item.string
        cursor += step
    return ""


def _qualified_root(tokens: list[tokenize.TokenInfo], index: int) -> str:
    def previous_significant(cursor: int) -> int:
        cursor -= 1
        while cursor >= 0 and tokens[cursor].type in {
            token.INDENT,
            token.DEDENT,
            tokenize.NL,
            token.COMMENT,
        }:
            cursor -= 1
        return cursor

    cursor = index
    while cursor >= 2:
        dot = previous_significant(cursor)
        root = previous_significant(dot)
        if dot < 0 or root < 0 or tokens[dot].string != "." or tokens[root].type != token.NAME:
            break
        cursor = root
    return tokens[cursor].string


def _case_keyword_position(
    tokens: list[tokenize.TokenInfo],
    token_starts: list[tuple[int, int]],
    pattern_position: tuple[int, int],
) -> tuple[int, int] | None:
    """Find the ``case`` token in the logical header containing a pattern."""

    cursor = bisect.bisect_left(token_starts, pattern_position) - 1
    while cursor >= 0:
        item = tokens[cursor]
        if item.type == token.NEWLINE:
            break
        if item.type == token.NAME and item.string == "case":
            return item.start
        cursor -= 1
    return None


def _syntax_context(
    text: str, tokens: list[tokenize.TokenInfo]
) -> tuple[set[tuple[int, int]], set[str]]:
    """Locate structural soft keywords and import-bound semantic API names."""

    try:
        tree = ast.parse(text)
    except (IndentationError, RecursionError, SyntaxError, ValueError):
        return set(), set()
    structural: set[tuple[int, int]] = set()
    imported: set[str] = set()
    token_starts = [item.start for item in tokens]
    for node in ast.walk(tree):
        if isinstance(node, ast.Match):
            structural.add((node.lineno, node.col_offset))
            for branch in node.cases:
                pattern_line = getattr(branch.pattern, "lineno", None)
                pattern_column = getattr(branch.pattern, "col_offset", None)
                if type(pattern_line) is int and type(pattern_column) is int:
                    case_position = _case_keyword_position(
                        tokens,
                        token_starts,
                        (pattern_line, pattern_column),
                    )
                    if case_position is not None:
                        structural.add(case_position)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name.partition(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    imported.add(alias.asname or alias.name)
    return structural, imported


def _number_token(value: str) -> str:
    lowered = value.casefold().replace("_", "")
    if lowered.endswith("j"):
        return "COMPLEX_LIT"
    if "." in lowered or "e" in lowered:
        return "FLOAT_LIT"
    return "INT_LIT"


def _string_token(value: str) -> str:
    index = 0
    while index < len(value) and value[index].casefold() in {"b", "f", "r", "u"}:
        index += 1
    prefix = value[:index].casefold()
    raw = "r" in prefix
    if "f" in prefix:
        kind = "FSTRING_LIT"
    elif "b" in prefix:
        kind = "BYTES_LIT"
    else:
        kind = "STRING_LIT"
    return f"RAW_{kind}" if raw else kind


def _error_token(value: str) -> str | None:
    if not value or value.isspace():
        return None
    if value in {'"', "'"}:
        return "UNTERMINATED_STRING_LIT"
    return f"ERROR_TOKEN(U+{ord(value[0]):04X})"


def _token_error_marker(error: BaseException) -> tuple[int, str]:
    line = 1
    if len(error.args) > 1 and isinstance(error.args[1], tuple) and error.args[1]:
        candidate = error.args[1][0]
        if type(candidate) is int and candidate > 0:
            line = candidate
    message = str(error.args[0]).casefold() if error.args else ""
    if "multi-line string" in message or "string" in message:
        kind = "UNTERMINATED_STRING_LIT"
    elif "multi-line statement" in message or "eof" in message:
        kind = "UNTERMINATED_STATEMENT"
    elif isinstance(error, IndentationError):
        kind = "INVALID_INDENTATION"
    else:
        kind = "TOKENIZATION_ERROR"
    return line, kind


def _collect_tokens(
    text: str, max_tokens: int
) -> tuple[list[tokenize.TokenInfo], tuple[int, str] | None]:
    collected: list[tokenize.TokenInfo] = []
    try:
        for item in tokenize.generate_tokens(io.StringIO(text).readline):
            if len(collected) >= max_tokens:
                raise ValueError(f"max_tokens={max_tokens} exceeded while tokenizing Python source")
            collected.append(item)
    except (IndentationError, tokenize.TokenError) as error:
        return collected, _token_error_marker(error)
    return collected, None


def _import_token_indexes(tokens: list[tokenize.TokenInfo]) -> set[int]:
    """Return only tokens belonging to import-first simple statements."""

    excluded: set[int] = set()
    statement: list[int] = []
    bracket_depth = 0

    def finish_statement() -> None:
        significant = [
            tokens[index]
            for index in statement
            if tokens[index].type
            not in {
                token.INDENT,
                token.DEDENT,
                token.NEWLINE,
                tokenize.NL,
                token.COMMENT,
            }
        ]
        if (
            significant
            and significant[0].type == token.NAME
            and significant[0].string in {"from", "import"}
        ):
            excluded.update(statement)
        statement.clear()

    for index, item in enumerate(tokens):
        if item.type == token.ENDMARKER:
            finish_statement()
            break
        if item.type == token.OP and item.string == ";" and bracket_depth == 0:
            finish_statement()
            continue
        statement.append(index)
        if item.type == token.OP:
            if item.string in {"(", "[", "{"}:
                bracket_depth += 1
            elif item.string in {")", "]", "}"}:
                bracket_depth = max(0, bracket_depth - 1)
        if item.type == token.NEWLINE and bracket_depth == 0:
            finish_statement()
    finish_statement()
    return excluded


def _validated_source_and_limit(text: str, max_tokens: int | None) -> tuple[str, int]:
    if not isinstance(text, str):
        raise TypeError("Python source must be text")
    if "\x00" in text:
        raise ValueError("Python source must not contain a null byte")
    token_limit = MAX_PYTHON_DUPLICATE_TOKENS if max_tokens is None else max_tokens
    if type(token_limit) is not int or token_limit <= 0:
        raise ValueError("max_tokens must be a positive integer")
    return text.replace("\r\n", "\n").replace("\r", "\n"), token_limit


def _canonical_token(
    tokens: list[tokenize.TokenInfo],
    index: int,
    soft_keywords: set[tuple[int, int]],
    imported_names: set[str],
) -> str | None:
    item = tokens[index]
    if item.type == token.NAME:
        previous = _neighbor_syntax(tokens, index, -1)
        following = _neighbor_syntax(tokens, index, 1)
        imported_root = _qualified_root(tokens, index) in imported_names
        return _name_token(
            item.string,
            item.start,
            soft_keywords,
            following,
            imported_root and (previous == "." or following in {".", "("}),
        )
    if item.type == token.NUMBER:
        return _number_token(item.string)
    if item.type == token.STRING:
        return _string_token(item.string)
    if item.type == token.INDENT:
        return "INDENT"
    if item.type == token.DEDENT:
        return "DEDENT"
    if item.type == token.OP:
        return item.string
    if item.type == token.ERRORTOKEN:
        return _error_token(item.string)
    return f"TOKEN_TYPE({item.type})"


def _canonical_tokens_by_line(
    tokens: list[tokenize.TokenInfo],
    excluded_indexes: set[int],
    soft_keywords: set[tuple[int, int]],
    imported_names: set[str],
) -> dict[int, list[str]]:
    by_line: dict[int, list[str]] = defaultdict(list)
    fstring_depth = 0
    for index, item in enumerate(tokens):
        line = item.start[0]
        if index in excluded_indexes or item.type in _SKIPPED_TYPES:
            continue
        token_name = token.tok_name.get(item.type, "")
        if token_name == "FSTRING_START":
            if fstring_depth == 0:
                by_line[line].append(_string_token(item.string))
            fstring_depth += 1
            continue
        if fstring_depth:
            if token_name == "FSTRING_END":
                fstring_depth -= 1
            continue
        canonical = _canonical_token(tokens, index, soft_keywords, imported_names)
        if canonical:
            by_line[line].append(canonical)
    return by_line


def tokenize_python_lines(
    text: str, *, max_tokens: int | None = None
) -> tuple[tuple[int, str], ...]:
    """Return canonical Python tokens grouped by their physical start line.

    Comments and import-first logical statements are excluded to retain the
    engine's existing boilerplate policy.  Lexical errors become opaque bounded
    markers instead of making an in-progress source tree crash clone analysis.
    """

    text, token_limit = _validated_source_and_limit(text, max_tokens)
    tokens, error = _collect_tokens(text, token_limit)
    excluded_indexes = _import_token_indexes(tokens)
    soft_keywords, imported_names = _syntax_context(text, tokens)
    by_line = _canonical_tokens_by_line(tokens, excluded_indexes, soft_keywords, imported_names)

    if error is not None:
        error_line, marker = error
        by_line[error_line].append(marker)

    return tuple(
        (line, _TOKEN_SEPARATOR.join(canonical))
        for line, canonical in sorted(by_line.items())
        if canonical and any(item != "DEDENT" for item in canonical)
    )
