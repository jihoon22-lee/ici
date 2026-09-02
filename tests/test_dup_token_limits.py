"""Fail-closed resource limits for duplicate-analysis tokenization.

The tokenizer budgets are deliberately injected with tiny values here.  The
aggregate engine budget is injected by monkeypatching its internal invariant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.core.models import EngineStatus, EvidenceState
from ici.engines import dup as dup_module
from ici.engines._cpp_dup_tokenization import tokenize_cpp_lines
from ici.engines._python_dup_tokenization import tokenize_python_lines
from ici.engines.dup import DuplicateEngine


@pytest.mark.parametrize(
    ("tokenizer", "source", "limit"),
    [
        (
            tokenize_cpp_lines,
            "int first = 1;\nint second = 2;\n",
            4,
        ),
        (
            tokenize_python_lines,
            "first = 1\nsecond = 2\n",
            3,
        ),
    ],
    ids=["cpp", "python"],
)
def test_tokenizers_raise_deterministic_value_error_when_max_tokens_is_exceeded(
    tokenizer, source: str, limit: int
):
    errors: list[str] = []

    for _ in range(2):
        with pytest.raises(ValueError, match="max_tokens") as raised:
            tokenizer(source, max_tokens=limit)
        errors.append(str(raised.value))

    assert errors[0] == errors[1]


@pytest.mark.parametrize(
    ("tokenizer", "source"),
    [
        (tokenize_cpp_lines, "int value = 1;\n"),
        (tokenize_python_lines, "value = 1\n"),
    ],
    ids=["cpp", "python"],
)
def test_tokenizers_preserve_output_when_an_injected_max_tokens_budget_is_sufficient(
    tokenizer, source: str
):
    assert tokenizer(source, max_tokens=10_000) == tokenizer(source)


@pytest.mark.parametrize("invalid_limit", [None, 0, -1, True, 1.5, "10"])
def test_tokenizers_reject_invalid_explicit_max_tokens(invalid_limit):
    for tokenizer, source in (
        (tokenize_cpp_lines, "int value = 1;\n"),
        (tokenize_python_lines, "value = 1\n"),
    ):
        if invalid_limit is None:
            # None deliberately selects the built-in default rather than an invalid override.
            assert tokenizer(source, max_tokens=None) == tokenizer(source)
            continue
        with pytest.raises(ValueError, match="positive integer"):
            tokenizer(source, max_tokens=invalid_limit)


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _duplicate_engine(root: Path) -> DuplicateEngine:
    return DuplicateEngine(
        root,
        {
            "project": {"source_dirs": ["src"]},
            "engines": {"dup": {}},
        },
    )


@pytest.mark.parametrize(
    ("extension", "tokenizer_name", "clean_source", "trigger_source"),
    [
        ("py", "tokenize_python_lines", "value = 1\n", "trigger = 2\n"),
        ("cpp", "tokenize_cpp_lines", "int value = 1;\n", "int trigger = 2;\n"),
    ],
    ids=["python", "cpp"],
)
def test_duplicate_engine_tokenizer_value_error_is_error_not_partial_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extension: str,
    tokenizer_name: str,
    clean_source: str,
    trigger_source: str,
):
    _write(tmp_path, f"src/a.{extension}", clean_source)
    _write(tmp_path, f"src/z.{extension}", trigger_source)
    real_tokenizer = getattr(dup_module, tokenizer_name)
    calls: list[dict[str, object]] = []

    def fail_on_trigger(text: str, *args, **kwargs):
        calls.append(dict(kwargs))
        if "trigger" in text:
            raise ValueError("max_tokens=1 exceeded")
        return real_tokenizer(text)

    monkeypatch.setattr(dup_module, tokenizer_name, fail_on_trigger)

    result = _duplicate_engine(tmp_path).run()

    assert calls == [{}, {}]
    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    tokenization_errors = [
        target for target in result.targets if target.target_name == "SourceTokenizationError"
    ]
    assert len(tokenization_errors) == 1
    assert tokenization_errors[0].file_path == f"src/z.{extension}"
    assert tokenization_errors[0].status == EngineStatus.ERROR
    assert "max_tokens" in tokenization_errors[0].message
    assert all(target.status == EngineStatus.ERROR for target in result.targets)


def test_duplicate_engine_aggregate_normalized_character_budget_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _write(tmp_path, "src/a.py", "left_value = 1\n")
    _write(tmp_path, "src/b.py", "right_value = 2\n")
    monkeypatch.setattr(dup_module, "MAX_DUPLICATE_NORMALIZED_CHARS", 15)

    first = _duplicate_engine(tmp_path).run()
    second = _duplicate_engine(tmp_path).run()

    assert first.status == EngineStatus.ERROR
    assert first.evidence == EvidenceState.NOT_RUN
    assert second.status == EngineStatus.ERROR
    assert second.evidence == EvidenceState.NOT_RUN
    first_errors = [
        target for target in first.targets if target.target_name == "SourceTokenizationError"
    ]
    second_errors = [
        target for target in second.targets if target.target_name == "SourceTokenizationError"
    ]
    assert len(first_errors) == 1
    assert len(second_errors) == 1
    assert first_errors[0].file_path == "src/b.py"
    assert second_errors[0].file_path == "src/b.py"
    assert first_errors[0].status == EngineStatus.ERROR
    assert "normalized" in first_errors[0].message.lower()
    assert first_errors[0].message == second_errors[0].message
    assert all(target.status == EngineStatus.ERROR for target in first.targets)
