"""Qt moc/uic/rcc linkage and major compile-evidence contracts."""

from __future__ import annotations

from pathlib import Path

from ici.core.capabilities import CapabilityInventory
from ici.core.context import (
    AnalysisContext,
    AnalysisIdentity,
    CompilationContext,
    CompilationDefine,
    CompilationSearchPath,
    CompilationUnit,
    ProjectModel,
    canonical_digest,
)
from ici.core.models import EngineStatus, FindingCategory
from ici.engines._qt_codegen import verify_qt_codegen


def _unit(
    root: Path,
    source: str,
    *,
    directory: str = "build",
    include_paths: tuple[CompilationSearchPath, ...] = (),
    qt_major: int | None = None,
) -> CompilationUnit:
    source_path = root / source
    argv = ["/usr/bin/clang++", "-std=c++20"]
    if qt_major is not None:
        argv.extend((f"-I/usr/include/qt{qt_major}", f"-DQT_VERSION_MAJOR={qt_major}"))
    argv.extend(("-c", str(source_path), "-o", f"{source_path.stem}.o"))
    return CompilationUnit(
        source=source,
        directory=directory,
        argv=tuple(argv),
        output=f"build/{source_path.stem}.o",
        compiler="clang++",
        language="c++",
        standard="c++20",
        defines=(
            CompilationDefine("QT_WIDGETS_LIB"),
            *(
                (CompilationDefine("QT_VERSION_MAJOR", str(qt_major)),)
                if qt_major is not None
                else ()
            ),
        ),
        include_paths=include_paths,
        configuration=canonical_digest({"source": source, "qt": qt_major}),
    )


def _context(root: Path, units: tuple[CompilationUnit, ...]) -> AnalysisContext:
    return AnalysisContext(
        project=ProjectModel(
            root=root,
            name="qt-codegen",
            version="1.0.0",
            project_type="cpp",
            source_dirs=("src",),
            cpp_sources=("src/widget.cpp",),
            cpp_headers=("src/widget.h",),
            compilable_cpp_sources=("src/widget.cpp",),
        ),
        capabilities=CapabilityInventory(capabilities={}),
        identity=AnalysisIdentity(
            source_commit="unavailable",
            config_digest=canonical_digest({"config": 1}),
            toolchain_digest=canonical_digest({"tools": 1}),
        ),
        compilation=CompilationContext(
            units=units,
            database_path="build/compile_commands.json",
            database_digest=canonical_digest({"units": [unit.source for unit in units]}),
            origin="cmake",
            generator="Ninja",
            unity_build=False,
        ),
    )


def _full_project(root: Path) -> tuple[list[Path], list[Path], list[Path], AnalysisContext]:
    source_dir = root / "src"
    generated = root / "build" / "autogen"
    (generated / "include").mkdir(parents=True)
    (generated / "ABC").mkdir()
    source_dir.mkdir()
    widget = source_dir / "widget.cpp"
    header = source_dir / "widget.h"
    widget.write_text(
        '#include <QWidget>\n#include "ui_dashboard.h"\nint widget() { return 0; }\n',
        encoding="utf-8",
    )
    header.write_text(
        "class Widget {\n    Q_OBJECT\n};\n",
        encoding="utf-8",
    )
    (source_dir / "dashboard.ui").write_text('<ui version="4.0"></ui>\n', encoding="utf-8")
    (source_dir / "theme.qrc").write_text("<RCC></RCC>\n", encoding="utf-8")
    (generated / "include" / "ui_dashboard.h").write_text(
        "class Ui_Dashboard { void setupUi(); };\n",
        encoding="utf-8",
    )
    (generated / "qrc_theme.cpp").write_text(
        "int qInitResources_theme() { return 1; }\n", encoding="utf-8"
    )
    (generated / "mocs_compilation.cpp").write_text(
        '#include "ABC/moc_widget.cpp"\n',
        encoding="utf-8",
    )
    (generated / "ABC" / "moc_widget.cpp").write_text(
        "// Meta object code from reading C++ file 'widget.h'\n",
        encoding="utf-8",
    )
    include = CompilationSearchPath("build/autogen/include", "include", "project", True)
    autogen = CompilationSearchPath("build/autogen", "include", "project", True)
    units = (
        _unit(root, "src/widget.cpp", include_paths=(include,), qt_major=6),
        _unit(root, "build/autogen/qrc_theme.cpp"),
        _unit(
            root,
            "build/autogen/mocs_compilation.cpp",
            directory="build/autogen",
            include_paths=(autogen,),
        ),
    )
    return [source_dir], [widget], [header], _context(root, units)


def test_cmake_autogen_links_all_inputs_and_qt6_compile_evidence(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source_dirs, cpp_files, headers, context = _full_project(root)

    outcome = verify_qt_codegen(
        root,
        source_dirs,
        cpp_files,
        headers,
        context,
        compiled_sources={"src/widget.cpp"},
    )

    assert outcome.mode == "exact"
    assert outcome.errors == []
    assert outcome.warnings == []
    assert outcome.inputs_checked == 3
    assert (outcome.ui_checked, outcome.qrc_checked, outcome.moc_checked) == (1, 1, 1)
    assert (outcome.qt5_units, outcome.qt6_units) == (0, 1)
    assert all(target.status is EngineStatus.PASS for target in outcome.targets)
    assert {target.target_name for target in outcome.targets} == {
        "QtUicLinkage",
        "QtRccLinkage",
        "QtMocLinkage",
        "QtCompatibility:Qt6",
    }
    assert outcome.findings == []


def test_missing_generated_outputs_fail_at_each_original_input(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source_dirs, cpp_files, headers, context = _full_project(root)
    for path in (
        root / "build/autogen/include/ui_dashboard.h",
        root / "build/autogen/qrc_theme.cpp",
        root / "build/autogen/ABC/moc_widget.cpp",
    ):
        path.unlink()

    outcome = verify_qt_codegen(
        root,
        source_dirs,
        cpp_files,
        headers,
        context,
        compiled_sources={"src/widget.cpp"},
    )

    failed = [target for target in outcome.targets if target.status is EngineStatus.FAIL]
    assert {target.file_path for target in failed} == {
        "src/dashboard.ui",
        "src/theme.qrc",
        "src/widget.h",
    }
    assert {finding.category for finding in outcome.findings} == {FindingCategory.BUILD}
    assert outcome.errors == []


def test_handwritten_lookalike_outputs_do_not_satisfy_codegen_evidence(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source_dirs, cpp_files, headers, context = _full_project(root)
    for path in (
        root / "build/autogen/include/ui_dashboard.h",
        root / "build/autogen/qrc_theme.cpp",
        root / "build/autogen/ABC/moc_widget.cpp",
    ):
        path.write_text("int handwritten_lookalike = 1;\n", encoding="utf-8")

    outcome = verify_qt_codegen(
        root,
        source_dirs,
        cpp_files,
        headers,
        context,
        compiled_sources={"src/widget.cpp"},
    )

    assert sum(target.status is EngineStatus.FAIL for target in outcome.targets) == 3
    assert {
        target.target_name for target in outcome.targets if target.status is EngineStatus.FAIL
    } == {
        "QtUicLinkage",
        "QtRccLinkage",
        "QtMocLinkage",
    }


def test_qmake_direct_moc_unit_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "project"
    src = root / "src"
    generated = root / "build" / "qmake"
    src.mkdir(parents=True)
    generated.mkdir(parents=True)
    source = src / "widget.cpp"
    header = src / "widget.h"
    source.write_text("#include <QWidget>\n", encoding="utf-8")
    header.write_text("class Widget { Q_OBJECT };\n", encoding="utf-8")
    (generated / "moc_widget.cpp").write_text(
        "// Meta object code from reading C++ file 'widget.h'\n",
        encoding="utf-8",
    )
    units = (
        _unit(root, "src/widget.cpp", qt_major=5),
        _unit(root, "build/qmake/moc_widget.cpp", directory="build/qmake"),
    )
    context = _context(root, units)

    outcome = verify_qt_codegen(
        root,
        [src],
        [source],
        [header],
        context,
        compiled_sources={"src/widget.cpp"},
    )

    moc = next(target for target in outcome.targets if target.target_name == "QtMocLinkage")
    assert moc.status is EngineStatus.PASS
    assert outcome.qt5_units == 1
    assert outcome.qt6_units == 0


def test_inputs_without_exact_context_are_explicit_warnings(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source_dirs, cpp_files, headers, _context_value = _full_project(root)

    outcome = verify_qt_codegen(root, source_dirs, cpp_files, headers, None)

    assert outcome.mode == "unavailable"
    assert outcome.inputs_checked == 3
    assert len(outcome.warnings) == 3
    assert all(target.status is EngineStatus.WARN for target in outcome.targets)
    assert {target.file_path for target in outcome.targets} == {
        "src/dashboard.ui",
        "src/theme.qrc",
        "src/widget.h",
    }


def test_q_object_in_comments_strings_and_raw_literals_is_ignored(tmp_path: Path) -> None:
    root = tmp_path / "project"
    src = root / "src"
    src.mkdir(parents=True)
    source = src / "main.cpp"
    source.write_text(
        '// Q_OBJECT\n/* Q_OBJECT */\nconst char *a = "Q_OBJECT";\n'
        'const char *b = R"tag(Q_OBJECT)tag";\nint main() { return 0; }\n',
        encoding="utf-8",
    )

    outcome = verify_qt_codegen(root, [src], [source], [], None)

    assert outcome.mode == "not_applicable"
    assert outcome.inputs_checked == 0
    assert outcome.targets == []


def test_conflicting_qt_major_evidence_is_a_compatibility_failure(tmp_path: Path) -> None:
    root = tmp_path / "project"
    src = root / "src"
    src.mkdir(parents=True)
    source = src / "widget.cpp"
    header = src / "widget.h"
    source.write_text("#include <QWidget>\n", encoding="utf-8")
    header.write_text("// no moc macro\n", encoding="utf-8")
    unit = _unit(root, "src/widget.cpp", qt_major=5)
    unit = CompilationUnit(
        **{
            **unit.__dict__,
            "argv": (*unit.argv, "-I/usr/include/qt6"),
        }
    )
    context = _context(root, (unit,))

    outcome = verify_qt_codegen(
        root,
        [src],
        [source],
        [header],
        context,
        compiled_sources={"src/widget.cpp"},
    )

    target = next(
        target
        for target in outcome.targets
        if target.target_name == "QtCompatibility:ConflictingMajor"
    )
    assert target.status is EngineStatus.FAIL
    assert outcome.findings[0].category is FindingCategory.COMPATIBILITY
