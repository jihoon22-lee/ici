"""Contract tests for the ici-next design documents (WP00, #198).

These tests keep the adopted design documents honest about the code they
describe. They deliberately verify three things and nothing more:

1. The 19-descriptor inventory table matches the live registry. #198's
   acceptance criteria require an exact name match, so a descriptor added or
   removed without touching the inventory must fail here rather than leave the
   document quietly stale.
2. Every relative link between the ici-next documents resolves. The documents
   are the normative source after this PR, so a broken cross-reference is a
   real defect in them.
3. The TOML examples in SPEC-01 parse. The field *semantics* are WP05's job
   (#203) — this only catches examples that are not valid TOML at all.

No behaviour of ici itself is exercised. These are documentation contracts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomli

from ici.core.pipeline import ENGINE_DESCRIPTORS
from ici.core.support import ENGINE_NAMES

REPO_ROOT = Path(__file__).resolve().parents[1]
DESIGN_DIR = REPO_ROOT / "docs" / "design" / "ici-next"
INVENTORY_DIR = DESIGN_DIR / "inventory"

# Documents WP00 adopts. Listed explicitly so that deleting one fails a test
# instead of silently shrinking the checked set.
EXPECTED_DOCUMENTS = (
    "README.md",
    "roadmap.md",
    "architecture.md",
    "spec-01-workspace-config-cli.md",
    "spec-02-distribution-execution.md",
    "spec-03-analysis-engines.md",
    "spec-04-results-integration.md",
    "spec-05-verification-transition.md",
    "requirements-traceability.md",
    "compatibility-v3-next.md",
    "inventory/current-engines.md",
    "inventory/execution-flow.md",
    "inventory/baseline-measurements.md",
    "adr/README.md",
    "adr/0001-keep-python-core.md",
    "adr/0002-standalone-runtime-bundle.md",
    "adr/0003-agents-invariant-scoping.md",
    "adr/0004-check-provider-separation.md",
    "adr/0005-tem-formula-freeze.md",
    "adr/0006-runtime-selection.md",
    "adr/0007-project-test-provider.md",
    "spikes/wp01-runtime-environment.md",
)

# Inline links only: [text](target). Reference-style links are not used in
# these documents, and skipping them keeps the pattern readable.
_LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

# Rows of the inventory tables look like "|line|`LineCountEngine`|...".
_TABLE_ROW_PATTERN = re.compile(r"^\|([a-z_]+)\|")


def _design_documents() -> list[Path]:
    return [DESIGN_DIR / name for name in EXPECTED_DOCUMENTS]


def _inventory_engine_names(heading: str) -> list[str]:
    """Collect the first column of the *first* table under the given heading.

    The inventory keeps one row per descriptor, so the first column of each
    table row is an engine name. Only the first contiguous table is read: some
    sections carry a second table keyed by engine as well (for example the
    per-language tool requirements under the active-mode table), and merging
    the two would double-count engines.
    """

    text = (INVENTORY_DIR / "current-engines.md").read_text(encoding="utf-8")
    for section in text.split("\n## "):
        if not section.startswith(heading):
            continue
        names: list[str] = []
        for line in section.splitlines():
            match = _TABLE_ROW_PATTERN.match(line)
            if match:
                names.append(match.group(1))
            elif names and not line.startswith("|"):
                break
        return names
    raise AssertionError(f"heading not found in current-engines.md: {heading}")


class TestDocumentsExist:
    def test_every_adopted_document_is_present(self):
        missing = [path for path in _design_documents() if not path.is_file()]
        assert not missing, f"missing ici-next documents: {missing}"

    def test_no_unlisted_markdown_slips_into_the_design_dir(self):
        """A new document must be added to EXPECTED_DOCUMENTS to be adopted.

        Otherwise a draft can sit next to the normative set without ever being
        reviewed as part of it.
        """

        found = {
            str(path.relative_to(DESIGN_DIR)).replace("\\", "/")
            for path in DESIGN_DIR.rglob("*.md")
        }
        assert found == set(EXPECTED_DOCUMENTS)


class TestRegistryCoverage:
    """#198 acceptance: the registry's 19 names and the inventory match exactly."""

    def test_registry_and_support_matrix_agree(self):
        assert len(ENGINE_DESCRIPTORS) == len(ENGINE_NAMES)
        assert tuple(descriptor.name for descriptor in ENGINE_DESCRIPTORS) == ENGINE_NAMES

    def test_scheduling_table_lists_every_descriptor_in_registry_order(self):
        documented = _inventory_engine_names("1. 스케줄링")
        assert documented == [descriptor.name for descriptor in ENGINE_DESCRIPTORS]

    def test_language_mode_table_lists_every_descriptor(self):
        documented = _inventory_engine_names("2. 언어별 active mode")
        assert documented == [descriptor.name for descriptor in ENGINE_DESCRIPTORS]

    def test_config_table_lists_every_descriptor(self):
        documented = _inventory_engine_names("3. 사용자 설정 키")
        assert documented == [descriptor.name for descriptor in ENGINE_DESCRIPTORS]

    def test_disposition_table_lists_every_descriptor(self):
        """R09: no descriptor may be left without a disposition row."""

        documented = _inventory_engine_names("7. 잠정 disposition")
        assert documented == [descriptor.name for descriptor in ENGINE_DESCRIPTORS]

    def test_factory_names_in_inventory_match_the_registry(self):
        text = (INVENTORY_DIR / "current-engines.md").read_text(encoding="utf-8")
        for descriptor in ENGINE_DESCRIPTORS:
            assert f"`{descriptor.factory_name}`" in text, (
                f"inventory does not name the factory for {descriptor.name}"
            )


class TestRelativeLinks:
    @pytest.mark.parametrize("document", _design_documents(), ids=lambda p: p.name)
    def test_relative_links_resolve(self, document: Path):
        if not document.is_file():
            pytest.skip(f"{document} is checked by TestDocumentsExist")

        broken = []
        for target in _LINK_PATTERN.findall(document.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            resolved = (document.parent / path_part).resolve()
            if not resolved.exists():
                broken.append(target)

        assert not broken, f"{document.name} has broken relative links: {broken}"


class TestSpec01TomlExamples:
    """Only checks that the examples are valid TOML.

    Field semantics and the JSON Schema are WP05 (#203). A test that asserted
    meaning here would claim a contract that is not implemented yet.
    """

    def test_toml_examples_parse(self):
        text = (DESIGN_DIR / "spec-01-workspace-config-cli.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```toml\n(.*?)```", text, flags=re.DOTALL)
        assert len(blocks) >= 2, "SPEC-01 should keep the root and child config examples"
        for index, block in enumerate(blocks):
            try:
                parsed = tomli.loads(block)
            except ValueError as err:  # tomli raises TOMLDecodeError (a ValueError)
                raise AssertionError(f"SPEC-01 TOML example {index} does not parse: {err}") from err
            assert parsed.get("schema_version") == 1


class TestAgentsScoping:
    """ADR-0003: the revision must scope invariants without deleting them."""

    def test_agents_declares_both_paths(self):
        text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        assert "적용 범위" in text
        assert "stable 경로" in text
        assert "next 경로" in text

    def test_agents_keeps_the_stable_invariants(self):
        """These must survive the revision — #198 forbids deleting gates first."""

        text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for invariant in (
            "Python 3.10 하한",
            "py3-none-any",
            "dist/ici.pyz",
            "PYTHON_CANDIDATES",
            "시스템 CA",
            "Zero-CDN HTML",
        ):
            assert invariant in text, f"AGENTS.md no longer mentions {invariant}"

    def test_stable_gate_files_still_exist(self):
        """The revision must not remove the gates it scopes."""

        for relative in (
            "scripts/build-pyz.sh",
            "scripts/smoke.sh",
            "scripts/launcher.sh",
            "tests/test_launcher.py",
            ".github/workflows/ci.yml",
            ".github/workflows/release.yml",
        ):
            assert (REPO_ROOT / relative).exists(), f"{relative} was removed"

    def test_python_floor_is_unchanged(self):
        """ADR-0003 promises not to move these for next-path work."""

        pyproject = tomli.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert pyproject["project"]["requires-python"] == ">=3.10"
        assert pyproject["tool"]["ruff"]["target-version"] == "py310"
