"""ici's own line count on the next path, counting the same thing as before.

#206 item 2 asks for the existing algorithm to be *connected*, not reproduced.
The stable engine that used to share it is gone; ``ici.analysis.line_count``
is now the one implementation both tests and the check delegate to — the
first test asserts that delegation, since a second implementation that means
almost the same thing is the thing being avoided.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.analysis.line_count import count_lines
from ici.domain.enums import TaskState
from ici.languages.python.lines import LineRequest, count


def _measurement(observation, name: str) -> float:
    (found,) = [item for item in observation.measurements if item.name == name]
    return found.value


def test_the_next_path_uses_the_shared_count_implementation(tmp_path: Path) -> None:
    # Not "they agree on this file": they are the same function — the check
    # delegates every file to ici.analysis.line_count.
    import inspect

    from ici.languages.python import lines

    assert "count_lines" in inspect.getsource(lines)
    sample = tmp_path / "a.py"
    sample.write_text("import os\n\n# note\ncode = 1\n")

    assert count_lines(sample) == (2, 1, 1)


def test_counts_code_comment_and_blank(tmp_path: Path) -> None:
    source = tmp_path / "a.py"
    source.write_text("import os\n\n# a note\nvalue = 1\n")

    observation = count(LineRequest(project_root=tmp_path, files=(source,)))

    assert observation.state is TaskState.SUCCEEDED
    assert _measurement(observation, "lines_code") == 2
    assert _measurement(observation, "lines_comment") == 1
    assert _measurement(observation, "lines_blank") == 1
    assert _measurement(observation, "lines_total") == 4


def test_raw_counts_are_kept_so_they_can_be_combined_later(tmp_path: Path) -> None:
    # SPEC-04 forbids averaging ratios across components; only raw counts can
    # be combined, and only if they survive to here.
    source = tmp_path / "a.py"
    source.write_text("value = 1\n# note\n")

    observation = count(LineRequest(project_root=tmp_path, files=(source,)))
    code = next(item for item in observation.measurements if item.name == "lines_code")

    assert code.numerator == 1
    assert code.denominator == 2


def test_counting_nothing_is_a_configuration_error_not_an_empty_result(tmp_path: Path) -> None:
    # A check that quietly counts nothing reports a project as measured when
    # nothing was looked at.
    with pytest.raises(ValueError, match="at least one file"):
        LineRequest(project_root=tmp_path, files=())


def test_an_empty_file_is_counted_and_not_called_unreadable(tmp_path: Path) -> None:
    empty = tmp_path / "empty.py"
    empty.write_text("")

    observation = count(LineRequest(project_root=tmp_path, files=(empty,)))

    assert observation.limitations == ()
    assert _measurement(observation, "files_counted") == 1


def test_a_file_that_could_not_be_read_is_named_rather_than_counted_as_zero(
    tmp_path: Path,
) -> None:
    # count_lines returns zeros both for an empty file and for one it could not
    # decode. Telling them apart matters: an unreadable file counted as zero
    # lines is a file nobody looked at, reported as a file with nothing in it.
    missing = tmp_path / "gone.py"

    observation = count(LineRequest(project_root=tmp_path, files=(missing,)))

    assert observation.limitations, "an unreadable file passed for an empty one"
    assert "gone.py" in observation.limitations[0]
    assert _measurement(observation, "files_counted") == 0


def test_several_files_are_summed(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a = 1\n")
    (tmp_path / "b.py").write_text("b = 2\nc = 3\n")

    observation = count(
        LineRequest(project_root=tmp_path, files=(tmp_path / "a.py", tmp_path / "b.py"))
    )

    assert _measurement(observation, "lines_code") == 3
    assert _measurement(observation, "files_counted") == 2
