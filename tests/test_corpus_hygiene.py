"""The corpus must not carry internal source, environments or logs (#201).

This is one of #201's acceptance criteria and nothing was checking it. A corpus
register is a standing invitation to add fixtures, and a fixture is usually
built by copying something that reproduced a bug — which is exactly how an
absolute home directory, an internal host name or a shared-storage path ends up
committed.

The patterns are deliberately narrow. A check that fired on ordinary content
would be switched off within a week, so each one below is measured against the
whole current corpus and reports nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fixture_manifest import Fixture, load_manifest

# A path rooted in a real machine's filesystem. Fixture content addresses files
# relative to the fixture, so any of these came from somewhere else.
ABSOLUTE_PATH = re.compile(r"(?<![\w/])/(?:home|Users|root|mnt|media|srv|opt|var)/")

# Names that only exist inside the organisation this tool was built for. They
# are hardcoded in core/env.py, which the inventory already records as an R01
# violation; what matters here is that they never reach the corpus.
INTERNAL_MARKER = re.compile(r"nas_shared|ips-core-lib|DEVOPS_INFRA_ROOT|ICI_INFRA_ROOT", re.I)

# An address is a fact about somebody's network.
HOST_OR_ADDRESS = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b[\w-]+\.(?:corp|internal|local|lan)\b", re.I
)

# Assignment shapes, not the words alone: "password" in prose is fine, a line
# that sets one is not.
SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token)\s*[=:]"
)

PATTERNS = (
    ("an absolute path from someone's machine", ABSOLUTE_PATH),
    ("an internal name", INTERNAL_MARKER),
    ("a host name or address", HOST_OR_ADDRESS),
    ("something shaped like a credential", SECRET_ASSIGNMENT),
)


def _files(entry: Fixture) -> list[Path]:
    if entry.path.is_file():
        return [entry.path]
    return sorted(path for path in entry.path.rglob("*") if path.is_file())


def _corpus_text() -> list[tuple[str, Path, str]]:
    """Every registered fixture's readable content, with where it came from."""

    collected: list[tuple[str, Path, str]] = []
    for fixture_id, entry in load_manifest().items():
        for path in _files(entry):
            try:
                collected.append((fixture_id, path, path.read_text(encoding="utf-8")))
            except (UnicodeDecodeError, OSError):
                continue
    return collected


@pytest.mark.parametrize("description,pattern", PATTERNS, ids=lambda value: str(value)[:40])
def test_no_registered_fixture_carries(description, pattern):
    offences = [
        f"{fixture_id} {path.name}:{text[: match.start()].count(chr(10)) + 1} -> {match.group(0)!r}"
        for fixture_id, path, text in _corpus_text()
        for match in pattern.finditer(text)
    ]

    assert offences == [], f"{description}:\n" + "\n".join(offences)


class TestThePatternsActuallyMatch:
    """A hygiene check nobody can see fail is decorative.

    Each case is the thing the corresponding pattern exists to catch, so a
    rewrite that accidentally neutered one fails here rather than going quiet.
    """

    def test_an_absolute_home_path_is_caught(self):
        assert ABSOLUTE_PATH.search("/home/someone/project/src/main.py")

    def test_a_relative_path_is_not(self):
        assert not ABSOLUTE_PATH.search("src/app/main.py")

    def test_a_url_path_is_not_mistaken_for_a_filesystem_path(self):
        assert not ABSOLUTE_PATH.search("https://example.com/home/docs")

    def test_the_shared_storage_name_is_caught(self):
        assert INTERNAL_MARKER.search("nas_shared/libs/cpp")

    def test_an_internal_hostname_is_caught(self):
        assert HOST_OR_ADDRESS.search("build01.corp")

    def test_an_ip_address_is_caught(self):
        assert HOST_OR_ADDRESS.search("10.0.0.7")

    def test_a_version_number_is_not_mistaken_for_an_address(self):
        assert not HOST_OR_ADDRESS.search("ruff 0.6.9")

    def test_an_assignment_is_caught_but_the_word_alone_is_not(self):
        assert SECRET_ASSIGNMENT.search("api_key = abc123")
        assert not SECRET_ASSIGNMENT.search("refuses a negative password length")


class TestTheCheckCoversTheWholeRegister:
    def test_every_fixture_contributes_at_least_one_readable_file(self):
        """Otherwise a fixture could pass by being unreadable."""

        seen = {fixture_id for fixture_id, _path, _text in _corpus_text()}

        assert seen == set(load_manifest())
