"""Bundle normalisation: the same input has to produce the same bytes (#202).

The WP01 spike built a bundle that ran; it did not build the same one twice.
Two builds from identical input differed in 1,040 files, and each cause below
was measured on real bundles before being fixed here.

These tests use small synthetic trees rather than a real bundle, because a real
one needs a python-build-standalone runtime that is not present everywhere. What
they check is the logic; the end-to-end claim is that after this module ran, two
freshly built bundles compared byte for byte with zero differences.
"""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from assemble_bundle import (
    STABLE_SOURCE_ROOT,
    AssemblyError,
    collect_licenses,
    drop_vendor_scripts,
    normalize_bytecode,
    prune_record_entries,
    tree_digest,
)

MODULE = "def add(left, right):\n    return left + right\n"


def _tree(root: Path, *, name: str = "pkg") -> Path:
    package = root / "app" / name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "math_helpers.py").write_text(MODULE, encoding="utf-8")
    return root


def _pyc(root: Path) -> Path:
    matches = sorted(root.rglob("math_helpers.*.pyc"))
    assert matches, "no bytecode was produced"
    return matches[0]


class TestBytecodeIsContentAddressed:
    def test_two_trees_with_the_same_source_produce_the_same_bytecode(self, tmp_path):
        """The headline. Different directories, different mtimes, same bytes."""

        first, second = _tree(tmp_path / "a"), _tree(tmp_path / "b")
        for root in (first, second):
            normalize_bytecode(root / "app", Path(sys.executable))

        assert _pyc(first).read_bytes() == _pyc(second).read_bytes()

    def test_the_header_says_hash_based_not_timestamp_based(self, tmp_path):
        """A timestamp header is the original defect, so name it directly."""

        normalize_bytecode(_tree(tmp_path) / "app", Path(sys.executable))

        header = _pyc(tmp_path).read_bytes()[:16]
        _magic, flags, _first, _second = struct.unpack("<4sIII", header)

        assert flags & 0b1, "bit 0 clear means mtime-based invalidation"

    def test_the_recorded_source_path_is_not_the_build_directory(self, tmp_path):
        """co_filename is why matching headers still had differing bodies.

        Both measured builds sat at paths of equal length, so the sizes matched
        too and only a byte comparison showed it.
        """

        normalize_bytecode(_tree(tmp_path) / "app", Path(sys.executable))
        body = _pyc(tmp_path).read_bytes()[16:]

        assert str(tmp_path).encode() not in body
        assert STABLE_SOURCE_ROOT.encode() in body

    def test_recompilation_is_forced_over_existing_bytecode(self, tmp_path):
        """Without forcing, the pass is self-defeating: the interpreter writes
        fresh mtime-based .pyc for the stdlib it imports on the way in, and
        compileall then treats those as current. 41 modules survived that way."""

        root = _tree(tmp_path)
        subprocess.run(
            [sys.executable, "-m", "compileall", "-q", str(root / "app")],
            check=True,
            capture_output=True,
        )
        _magic, stale_flags, _a, _b = struct.unpack("<4sIII", _pyc(root).read_bytes()[:16])
        assert stale_flags == 0, "the fixture needs a timestamp-based .pyc to overwrite"

        normalize_bytecode(root / "app", Path(sys.executable))

        _magic, flags, _a, _b = struct.unpack("<4sIII", _pyc(root).read_bytes()[:16])
        assert flags & 0b1

    def test_a_missing_tree_is_refused_rather_than_silently_skipped(self, tmp_path):
        with pytest.raises(AssemblyError, match="missing tree"):
            normalize_bytecode(tmp_path / "absent", Path(sys.executable))


class TestVendorScriptsAreDropped:
    def test_they_are_removed_and_named(self, tmp_path):
        scripts = tmp_path / "app" / "vendor" / "bin"
        scripts.mkdir(parents=True)
        for name in ("typer", "pygmentize"):
            (scripts / name).write_text(f"#!{tmp_path}/runtime/python/bin/python3\n")

        removed = drop_vendor_scripts(tmp_path)

        assert removed == ("pygmentize", "typer")
        assert not scripts.exists()

    def test_a_bundle_without_them_is_not_an_error(self, tmp_path):
        assert drop_vendor_scripts(tmp_path) == ()


class TestRecordsStayTrue:
    def _record(self, tmp_path: Path, body: str) -> Path:
        dist = tmp_path / "app" / "vendor" / "typer-0.27.2.dist-info"
        dist.mkdir(parents=True)
        record = dist / "RECORD"
        record.write_text(body, encoding="utf-8")
        return record

    def test_a_row_for_a_deleted_script_is_pruned(self, tmp_path):
        """It is a checksum for a file nobody can verify, and it was the last
        thing differing between two builds."""

        record = self._record(
            tmp_path,
            "../../bin/typer,sha256=AAAA,234\ntyper/__init__.py,sha256=BBBB,10\n",
        )

        pruned = prune_record_entries(tmp_path, ("typer",))

        assert pruned == ("typer-0.27.2.dist-info",)
        assert record.read_text(encoding="utf-8") == "typer/__init__.py,sha256=BBBB,10\n"

    def test_rows_for_files_still_present_are_kept(self, tmp_path):
        record = self._record(tmp_path, "typer/__init__.py,sha256=BBBB,10\n")

        prune_record_entries(tmp_path, ("typer",))

        assert "typer/__init__.py" in record.read_text(encoding="utf-8")

    def test_nothing_is_touched_when_no_script_was_removed(self, tmp_path):
        record = self._record(tmp_path, "../../bin/typer,sha256=AAAA,234\n")

        assert prune_record_entries(tmp_path, ()) == ()
        assert "bin/typer" in record.read_text(encoding="utf-8")


class TestLicencesAreCollected:
    def test_each_dependency_contributes_its_licence(self, tmp_path):
        """The spike shipped CPython's licence and nothing else, so every
        bundled dependency went out without one."""

        for package in ("typer-0.27.2", "rich-15.0.0"):
            dist = tmp_path / "app" / "vendor" / f"{package}.dist-info"
            dist.mkdir(parents=True)
            (dist / "LICENSE").write_text(f"{package} terms", encoding="utf-8")

        collected = collect_licenses(tmp_path)

        assert collected == ("rich-15.0.0-LICENSE", "typer-0.27.2-LICENSE")
        assert (tmp_path / "licenses" / "typer-0.27.2-LICENSE").read_text() == "typer-0.27.2 terms"

    def test_a_package_shipping_several_licence_files_contributes_all_of_them(self, tmp_path):
        dist = tmp_path / "app" / "vendor" / "packaging-26.3.dist-info"
        dist.mkdir(parents=True)
        (dist / "LICENSE.APACHE").write_text("apache", encoding="utf-8")
        (dist / "LICENSE.BSD").write_text("bsd", encoding="utf-8")

        collected = collect_licenses(tmp_path)

        assert collected == ("packaging-26.3-LICENSE.APACHE", "packaging-26.3-LICENSE.BSD")

    def test_unrelated_metadata_is_not_mistaken_for_a_licence(self, tmp_path):
        dist = tmp_path / "app" / "vendor" / "typer-0.27.2.dist-info"
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text("Name: typer", encoding="utf-8")
        (dist / "RECORD").write_text("", encoding="utf-8")

        assert collect_licenses(tmp_path) == ()


class TestTreeDigest:
    def test_identical_content_digests_the_same(self, tmp_path):
        first, second = _tree(tmp_path / "a"), _tree(tmp_path / "b")

        assert tree_digest(first / "app") == tree_digest(second / "app")

    def test_changed_content_changes_the_digest(self, tmp_path):
        root = _tree(tmp_path)
        before = tree_digest(root / "app")
        (root / "app" / "pkg" / "math_helpers.py").write_text("x = 1\n", encoding="utf-8")

        assert tree_digest(root / "app") != before

    def test_a_renamed_file_changes_the_digest(self, tmp_path):
        """Path is digested alongside content, so a move is a change."""

        root = _tree(tmp_path)
        before = tree_digest(root / "app")
        (root / "app" / "pkg" / "math_helpers.py").rename(root / "app" / "pkg" / "helpers.py")

        assert tree_digest(root / "app") != before

    def test_a_symlink_is_digested_by_its_target_not_followed(self, tmp_path):
        """A file replaced by a link to the same bytes is not the same bundle."""

        root = _tree(tmp_path)
        before = tree_digest(root / "app")
        target = root / "app" / "pkg" / "math_helpers.py"
        target.unlink()
        target.symlink_to("elsewhere.py")

        assert tree_digest(root / "app") != before
