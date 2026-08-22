"""Tests for compile_db engine and parser."""

import json
from pathlib import Path

import pytest

from ici.build_adapters.base import BuildAdapterError
from ici.core.compile_db import extract_standard, load_compile_database
from ici.core.models import EngineStatus
from ici.engines.compile_db import CompileDbEngine

_CFG = {"engines": {"compile_db": {"mode": "pass_warn", "required": False}}}


def _write_db(root: Path, entries: list[dict], where: str = "build/ici/cmake") -> Path:
    db = root / where / "compile_commands.json"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_text(json.dumps(entries), encoding="utf-8")
    return db


def test_no_cpp_sources_passes(tmp_path: Path):
    result = CompileDbEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.PASS
    assert "not applicable" in result.summary


def test_missing_database_warns(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main(){}\n", encoding="utf-8")
    result = CompileDbEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN


def test_uncovered_source_warns(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main(){}\n", encoding="utf-8")
    (src / "extra.cpp").write_text("int extra(){return 0;}\n", encoding="utf-8")
    _write_db(
        tmp_path,
        [
            {
                "directory": str(tmp_path),
                "file": str(src / "main.cpp"),
                "arguments": ["g++", "-std=c++17", "-c", str(src / "main.cpp")],
            }
        ],
    )
    result = CompileDbEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    assert any("NotInDb" in t.target_name for t in result.targets)


def test_full_coverage_passes(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main(){}\n", encoding="utf-8")
    _write_db(
        tmp_path,
        [
            {
                "directory": str(tmp_path),
                "file": str(src / "main.cpp"),
                "command": "g++ -std=c++17 -I" + str(tmp_path / "include") + " -c main.cpp",
            }
        ],
    )
    (tmp_path / "include").mkdir()
    result = CompileDbEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.PASS


def test_required_flag_policy_warns(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main(){}\n", encoding="utf-8")
    _write_db(
        tmp_path,
        [
            {
                "directory": str(tmp_path),
                "file": str(src / "main.cpp"),
                "arguments": ["g++", "-std=c++11", "-c", str(src / "main.cpp")],
            }
        ],
    )
    cfg = {"engines": {"compile_db": {"required_flags": ["-std=c++17"]}}}
    result = CompileDbEngine(tmp_path, cfg).run()
    assert result.status == EngineStatus.WARN
    assert any("MissingFlag" in t.target_name for t in result.targets)


def test_entry_outside_project_rejected(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main(){}\n", encoding="utf-8")
    outside = tmp_path.parent / "elsewhere.cpp"
    outside.write_text("int x;\n", encoding="utf-8")
    db = _write_db(
        tmp_path,
        [{"directory": str(tmp_path), "file": str(outside), "arguments": ["g++", "-c", "x"]}],
    )
    with pytest.raises(BuildAdapterError, match="escapes project"):
        load_compile_database(db, tmp_path, tmp_path)


def test_extract_standard_variants():
    assert extract_standard(["-std=c++17"]) == "c++17"
    assert extract_standard(["-std", "c++20"]) == "c++20"
    assert extract_standard(["-Wall"]) is None


def test_explicit_db_path_is_used(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main(){}\n", encoding="utf-8")
    custom = _write_db(
        tmp_path,
        [
            {
                "directory": str(tmp_path),
                "file": str(src / "main.cpp"),
                "arguments": ["g++", "-c", str(src / "main.cpp")],
            }
        ],
        where="custom",
    )
    engine = CompileDbEngine(tmp_path, _CFG, db_path=custom)
    result = engine.run()
    assert result.status == EngineStatus.PASS
