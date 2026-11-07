"""Development-only frozen `_qt_codegen` helper oracle; never called by the Rust product.

Calls the frozen `_cpp_code`, `_q_object_line`, `_include_names`, `_is_qt_include` and
`_qt_major` / `_qt_relevant` metadata rules on synthetic inputs (CR-only, CRLF, form feed and
U+2028 line ends, escaped newlines, raw-string delimiters, nested disabled branches) and records
each result, or the exception a frozen call raises.
"""

import json
import sys
from pathlib import Path

ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
sys.path.insert(0, str(ORACLE / "src"))

from ici.analysis import _qt_codegen as qt  # noqa: E402
from ici.core.context import (  # noqa: E402
    CompilationDefine,
    CompilationSearchPath,
    CompilationUnit,
)

ROOT = Path(__file__).resolve().parents[3]
assert Path(qt.__file__).is_relative_to(ORACLE), qt.__file__
TEXTS = [
    "",
    "Q_OBJECT",
    "class A {\n    Q_OBJECT\n};\n",
    "// Q_OBJECT\n/* Q_OBJECT */\n",
    "\"Q_OBJECT\"\n'Q'\n",
    'R"x(Q_OBJECT)x"\nQ_OBJECT\n',
    'R"(Q_OBJECT\n)"\nQ_OBJECT\n',
    'R"has space(Q_OBJECT)has space"\n',
    'R"12345678901234567(Q_OBJECT)12345678901234567"\n',
    '"escaped \\" Q_OBJECT"\nQ_OBJECT\n',
    '"line \\\n Q_OBJECT"\n',
    "#if 0\nQ_OBJECT\n#endif\n",
    "#if 0\n#elif 1\nQ_OBJECT\n#endif\n",
    "#if 1\n#elif 1\nQ_OBJECT\n#else\nQ_OBJECT\n#endif\n",
    "#if false\nQ_OBJECT\n#else\nQ_OBJECT\n#endif\n",
    "#ifdef X\n#if 0\n#else\nQ_OBJECT\n#endif\n#endif\n",
    "#endif\n#else\nQ_OBJECT\n",
    "  #  if   0  \nQ_OBJECT\n  #endif\nQ_OBJECT\n",
    "a\rb\r Q_OBJECT\r",
    "a\r\nQ_OBJECT\r\n",
    "a\x0cQ_OBJECT\n",
    "a\u2028Q_OBJECT\n",
    "MY_Q_OBJECT\nQ_OBJECTS\n",
    '#include "a.h"\n#include <QObject>\n// #include "b.h"\n',
    '  #  include "spaced.h"\n#include"tight.h"\n#  include <a/b.h>\n',
    '/* #include "c.h" */ #include "d.h"\n#include "e.h" // trailing\n',
    '#include "unterminated\n#include <>\n#include MACRO\n',
    '/* a\rb */\n#include "c.h"\n',
    '"\x0c"\n#include "f.h"\n',
    '#if 0\n#include "disabled.h"\n#endif\n',
    "#include <" + "x" * 1030 + ">\n",
]
NAMES = [
    "QObject",
    "QtCore/QObject",
    "QtCore",
    "Qt5Compat",
    "Qt6Core",
    "Qt",
    "Qtx",
    "Qt/x",
    "Q",
    "Qobject",
    "QA",
    "vector",
    "q",
    "QÉ",
]
UNITS = [
    {"argv": ["g++", "-I/usr/include/qt5", "-c", "a.cpp"]},
    {"argv": ["g++", "-I/usr/include/x86_64-linux-gnu/qt6/QtCore", "-c", "a.cpp"]},
    {"argv": ["g++", "-DQT_VERSION_MAJOR=5", "-c", "a.cpp"]},
    {"argv": ["g++", "-c", "a.cpp"], "defines": [["QT_VERSION_MAJOR", "6"]]},
    {"argv": ["g++", "-c", "a.cpp"], "defines": [["qt5core", None]]},
    {"argv": ["g++", "-Dqt6core", "-c", "a.cpp"]},
    {"argv": ["g++", "-DUSE_QT6CORE", "-c", "a.cpp"]},
    {"argv": ["g++", "-lQt5Widgets", "-c", "a.cpp"]},
    {"argv": ["g++", "-I/opt/Qt5.15/include", "-c", "a.cpp"]},
    {"argv": ["g++", "-I/opt/myqt5/include", "-c", "a.cpp"]},
    {"argv": ["g++", "-c", "a.cpp"], "include_paths": [["third_party/qt6", "include"]]},
    {"argv": ["g++", "-I/usr/include/qt5", "-I/usr/include/qt6", "-c", "a.cpp"]},
    {"argv": ["g++", "-DQT_CORE_LIB", "-c", "a.cpp"], "defines": [["QT_CORE_LIB", None]]},
    {"argv": ["g++", "-DQT_core_LIB", "-c", "a.cpp"]},
    {"argv": ["g++", "-DMY_QT_GUI_LIB_X", "-c", "a.cpp"]},
]


def outcome(function, *arguments):
    try:
        return {"value": function(*arguments)}
    except Exception as error:
        return {"error": f"{type(error).__name__}: {error}"}


def unit(case: dict) -> CompilationUnit:
    return CompilationUnit(
        source="a.cpp",
        directory=".",
        argv=tuple(case["argv"]),
        defines=tuple(CompilationDefine(name, value) for name, value in case.get("defines", [])),
        include_paths=tuple(
            CompilationSearchPath(path=path, kind=kind, scope="project", exists=True)
            for path, kind in case.get("include_paths", [])
        ),
    )


def metadata_relevant(item: CompilationUnit) -> bool:
    text = "\n".join(
        [*item.argv, *(path.path for path in item.include_paths), *(d.name for d in item.defines)]
    )
    return bool(qt._qt_major(item) or qt.re.search(r"\bQT_[A-Z0-9_]+_LIB\b", text))


oracle = {
    "texts": [
        {
            "text": text,
            "code": qt._cpp_code(text),
            "q_object_line": qt._q_object_line(text),
            "include_names": outcome(lambda value: list(qt._include_names(value)), text),
        }
        for text in TEXTS
    ],
    "names": {name: qt._is_qt_include(name) for name in NAMES},
    "units": [
        {
            **case,
            "majors": sorted(qt._qt_major(unit(case))),
            "metadata_relevant": metadata_relevant(unit(case)),
        }
        for case in UNITS
    ],
}
(ROOT / "tests/rust-parity/cpp/qt-lexical-oracle.json").write_text(
    json.dumps(oracle, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
)
print(f"{len(TEXTS)} texts, {len(NAMES)} names, {len(UNITS)} units")
