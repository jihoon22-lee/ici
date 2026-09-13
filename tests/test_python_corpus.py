"""The Python seeds, and what today's ici actually does with them (#201 step 3).

The register's job is to record measured behaviour, so these tests assert what
the current implementation produces rather than what it ought to. Where the two
differ, the test says so and names the work package that will change it — that
way the same fixture becomes the "before" side of the comparison instead of
needing to be rewritten.

``multi_component`` is here for one measurement in particular: whether a
component layout is analysed at all depends on the name of the directory
holding it, and when it is not analysed the result is PASS.
"""

from __future__ import annotations

import shutil

import pytest

from fixture_manifest import require
from ici.core.models import EngineStatus
from ici.core.project import DEFAULT_SOURCE_DIRS, get_source_dirs
from ici.engines.complexity import ComplexityEngine
from ici.engines.cycle import CycleEngine
from ici.engines.line import LineCountEngine

TEXT_ENGINES = (ComplexityEngine, LineCountEngine, CycleEngine)


def _findings(result) -> list:
    return [target for target in result.targets if target.status != EngineStatus.PASS]


class TestTheControlStaysQuiet:
    """A detector that fires on single_project has gained a false positive."""

    @pytest.mark.parametrize("engine_cls", TEXT_ENGINES, ids=lambda cls: cls.__name__)
    def test_nothing_is_reported(self, engine_cls):
        result = engine_cls(require("python/single_project").path).run()

        assert result.status is EngineStatus.PASS
        assert _findings(result) == []

    def test_its_sources_are_actually_discovered(self):
        """Otherwise the silence above would prove nothing — see the class below."""

        root = require("python/single_project").path

        assert [path.name for path in get_source_dirs(root)] == ["src"]


class TestWhetherCodeIsAnalysedDependsOnTheDirectoryName:
    """The measurement multi_component exists for.

    ``components`` is not in DEFAULT_SOURCE_DIRS and discovery does not recurse,
    so identical code is analysed or not according to what its parent directory
    is called. The half that matters is the verdict: when nothing is discovered,
    every engine reports PASS, which reads exactly like clean code.

    This is recorded, not endorsed. The scope model that distinguishes "nothing
    in scope" from "nothing wrong" is #207's (R05); changing it here would be
    redesigning scope semantics inside a corpus change.
    """

    def test_components_is_not_a_directory_ici_looks_in(self):
        assert "components" not in DEFAULT_SOURCE_DIRS

    def test_nothing_is_discovered(self):
        root = require("python/multi_component").path

        assert get_source_dirs(root) == []

    @pytest.mark.parametrize("engine_cls", TEXT_ENGINES, ids=lambda cls: cls.__name__)
    def test_zero_files_analysed_is_reported_as_pass(self, engine_cls):
        """The gap, stated plainly: a run that looked at nothing passes."""

        result = engine_cls(require("python/multi_component").path).run()

        assert result.status is EngineStatus.PASS
        assert _findings(result) == []

    def test_the_same_code_under_a_known_name_is_analysed(self, tmp_path):
        """The contrast. Only the directory name differs."""

        root = tmp_path / "renamed"
        shutil.copytree(require("python/multi_component").path, root)
        (root / "components").rename(root / "packages")

        assert [path.name for path in get_source_dirs(root)] == ["packages"]

    def test_and_then_the_defect_is_found(self, tmp_path):
        root = tmp_path / "renamed"
        shutil.copytree(require("python/multi_component").path, root)
        (root / "components").rename(root / "packages")

        result = ComplexityEngine(root).run()
        findings = _findings(result)

        assert result.status is EngineStatus.WARN
        assert len(findings) == 1
        assert findings[0].target_name.startswith("classify")
        assert findings[0].metrics["complexity"] == 18

    def test_the_defect_is_attributed_to_beta_and_not_to_alpha(self, tmp_path):
        """alpha is the in-fixture control: a finding there is a misattribution,
        not a second detection."""

        root = tmp_path / "renamed"
        shutil.copytree(require("python/multi_component").path, root)
        (root / "components").rename(root / "packages")

        findings = _findings(ComplexityEngine(root).run())
        paths = {finding.file_path for finding in findings}

        assert all("beta" in path for path in paths)
        assert not any("alpha" in path for path in paths)

    def test_configuration_can_point_ici_at_the_components(self, tmp_path):
        """The gap is in the default, not in the mechanism: an explicit
        source_dirs finds them."""

        root = require("python/multi_component").path
        config = {"project": {"source_dirs": ["components"]}}

        assert [path.name for path in get_source_dirs(root, config)] == ["components"]
