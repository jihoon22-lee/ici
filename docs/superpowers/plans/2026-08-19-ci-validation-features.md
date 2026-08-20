# C++ and Python CI Validation Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 폐쇄망의 개별 CI 실행 환경에서 CMake/qmake C++, Python, C++/Python 혼합 프로젝트의 툴체인·빌드 정의·런타임·ABI·통합 경계를 검증한다.

**Architecture:** 각 OS에서는 하나의 독립적인 `ici verify`를 실행한다. Toolchain 엔진이 실제 실행 환경을 기록하고, Build Adapter가 프로젝트 정의대로 shadow build를 만든 뒤 artifact manifest를 남기며, 후속 엔진이 compile commands, Python 호환성, ELF/ABI, 혼합 통합 시나리오를 검증한다.

**Tech Stack:** Python 3.10, dataclasses, tomli, packaging, stdlib subprocess/json/shlex/pathlib, CMake/CTest, qmake/Make, GCC/binutils

**Spec:** `docs/design/ci-validation-roadmap.md`

> **상태: v0.4.0에서 전체 보류 (Deferred).** 이 문서의 Toolchain, CMake/qmake build
> adapter, compile DB, Python compatibility, ELF/ABI, 혼합 통합 검증 및 신규 CLI/리포터 연동은
> 이번 릴리스에 구현하지 않았다. 각 작업과 체크박스는 미래 릴리스의 별도 승인·별도 PR 범위로
> 유지한다. v0.4.0에는 기존 검증 기능 보강(Axis A)만 포함한다.

## Global Constraints

- `2026-08-19-existing-validation-hardening.md` 계획이 모두 완료된 상태에서 시작한다.
- Python 3.10에서 동작하며 네이티브 Python 확장 의존성을 추가하지 않는다.
- 신규 `packaging` 의존성은 `py3-none-any` wheel 검사를 통과해야 한다.
- 모든 명령은 shell 없이 argv 배열로 실행한다.
- OS별 결과를 집계하지 않으며 각 실행의 JSON/HTML에서 현재 환경과 결과를 확인한다.
- 외부 보안 DB, GitHub Security, SARIF, 인터넷 연결에 의존하지 않는다.
- 빌드와 테스트는 프로젝트 루트가 아닌 `build/ici/<adapter>` shadow directory를 사용한다.
- 필수 도구나 산출물이 없으면 PASS가 아니라 ERROR이며, 선택 기능만 SKIP을 허용한다.
- 각 작업은 테스트, 문서, `CHANGELOG.md` 변경을 포함한 독립 Conventional Commit으로 끝낸다.

## File Structure

- `src/ici/core/toolchain.py`: 실행 파일 탐색, 버전·target triple·OS capability 수집
- `src/ici/core/artifacts.py`: build artifact manifest 저장·로딩과 경로 검증
- `src/ici/build_adapters/base.py`: CMake/qmake가 구현할 공통 build 계약
- `src/ici/build_adapters/cmake.py`: CMake configure/build/CTest
- `src/ici/build_adapters/qmake.py`: qmake shadow build/Make
- `src/ici/build_adapters/registry.py`: 명시적 또는 탐지 기반 adapter 선택
- `src/ici/engines/toolchain.py`: 현재 CI 환경의 요구 도구와 버전 검증
- `src/ici/engines/build_definition.py`: adapter 실행 및 manifest 생성
- `src/ici/engines/compile_db.py`: compile_commands.json 정합성 검증
- `src/ici/engines/python_compat.py`: Target Python별 compile/import/package 검증
- `src/ici/engines/binary_compat.py`: ELF, 동적 의존성, 심볼 버전, RPATH 검증
- `src/ici/engines/integration.py`: argv 기반 혼합 언어 smoke contract 실행

---

### Task 1: Toolchain capability 수집과 정책 검사

**Files:**
- Create: `src/ici/core/toolchain.py`
- Create: `src/ici/engines/toolchain.py`
- Modify: `src/ici/doctor.py`
- Modify: `src/ici/config.py`
- Modify: `src/ici/engines/verify.py`
- Create: `tests/test_toolchain.py`
- Modify: `docs/engine-reference.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `ToolCapability(name, path, version, details)`
- Produces: `collect_tool_capability(name: str, argv: list[str]) -> ToolCapability`
- Produces: `ToolchainEngine.run() -> EngineResult`
- Consumes: hardening 계획의 `ProcessResult`, `ToolEvidence`, `EvidenceState`

- [ ] **Step 1: 실제 경로·버전·필수 도구 누락 테스트 작성**

```python
def test_toolchain_engine_errors_when_required_tool_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("ici.core.toolchain.shutil.which", lambda _name: None)
    config = {
        "engines": {
            "toolchain": {
                "enabled": True,
                "required": True,
                "required_tools": ["g++", "cmake"],
            }
        }
    }
    result = ToolchainEngine(tmp_path, config).run()
    assert result.status == EngineStatus.ERROR
    assert {t.target_name for t in result.targets} == {"g++", "cmake"}


def test_parse_numeric_version_ignores_vendor_suffix():
    assert parse_numeric_version("gcc (GCC) 8.5.0-22.el8") == (8, 5, 0)
```

- [ ] **Step 2: 새 테스트 실패 확인**

Run: `uv run --python 3.10 pytest tests/test_toolchain.py -v`

Expected: 모듈 import 실패.

- [ ] **Step 3: capability 타입과 버전 비교 구현**

```python
@dataclass
class ToolCapability:
    name: str
    path: str
    version: str
    available: bool
    details: dict[str, str] = field(default_factory=dict)


def parse_numeric_version(text: str) -> tuple[int, ...]:
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)+)", text)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))
```

gcc/g++에는 `-dumpfullversion -dumpversion`과 `-dumpmachine`, qmake에는 `-query
QT_VERSION`, Python에는 `-VV`, CMake/CTest/Make/binutils에는 `--version`을 사용한다. 설정에
절대 경로가 있으면 PATH보다 우선한다.

- [ ] **Step 4: ToolchainEngine 정책 구현**

기본 설정에 다음 구조를 추가한다.

```toml
[engines.toolchain]
enabled = true
required = true
required_tools = []

[engines.toolchain.minimum_versions]
```

`required_tools` 누락은 ERROR, 최소 버전 미달은 FAIL, 선택 도구 누락은 SKIP target으로
기록한다. OS/glibc/arch와 모든 capability는 `extra["environment"]`와
`extra["capabilities"]`에 저장한다.

- [ ] **Step 5: doctor가 같은 capability 수집 함수를 쓰도록 변경**

`doctor._check_tool()`의 중복 실행을 제거하고 `collect_tool_capability()` 결과를 기존 JSON/table
형식으로 변환한다. `qmake`, `ctest`, `gcov`, `readelf`, `objdump`, `nm`도 doctor 목록에 추가한다.

- [ ] **Step 6: 테스트, 문서, 커밋**

Run: `uv run --python 3.10 pytest tests/test_toolchain.py tests/test_env.py tests/test_cli.py -v`

Expected: PASS.

```bash
git add src/ici/core/toolchain.py src/ici/engines/toolchain.py src/ici/doctor.py \
  src/ici/config.py src/ici/engines/verify.py tests/test_toolchain.py \
  docs/engine-reference.md CHANGELOG.md
git commit -m "feat(toolchain): validate per-run CI capabilities"
```

### Task 2: Build Adapter 계약과 artifact manifest

**Files:**
- Create: `src/ici/build_adapters/__init__.py`
- Create: `src/ici/build_adapters/base.py`
- Create: `src/ici/build_adapters/registry.py`
- Create: `src/ici/core/artifacts.py`
- Create: `src/ici/engines/build_definition.py`
- Modify: `src/ici/config.py`
- Create: `tests/test_build_adapter.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `BuildRequest`, `BuildStep`, `BuildOutcome`, `BuildAdapter`
- Produces: `ArtifactManifest.save(path)` and `ArtifactManifest.load(path, project_root)`
- Produces: `select_build_adapter(project_root, config) -> BuildAdapter`
- Consumes: Task 1 tool paths and hardening 계획의 `ProcessResult`

- [ ] **Step 1: adapter 선택과 manifest 경계 테스트 작성**

```python
def test_adapter_selection_requires_choice_when_both_build_files_exist(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.16)\n")
    (tmp_path / "app.pro").write_text("TEMPLATE = app\n")
    with pytest.raises(BuildAdapterError, match="multiple build systems"):
        select_build_adapter(tmp_path, {})


def test_manifest_rejects_artifact_outside_project(tmp_path):
    manifest = ArtifactManifest(
        adapter="cmake",
        build_dir=str(tmp_path / "build"),
        artifacts=["../outside"],
        compile_commands=None,
    )
    with pytest.raises(ValueError, match="artifact path"):
        manifest.validate(tmp_path)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run --python 3.10 pytest tests/test_build_adapter.py -v`

Expected: 모듈 import 실패.

- [ ] **Step 3: 공통 타입과 adapter 추상 계약 구현**

```python
@dataclass
class BuildRequest:
    project_root: Path
    build_dir: Path
    jobs: int
    env: dict[str, str]


@dataclass
class BuildStep:
    name: str
    argv: list[str]
    cwd: str
    returncode: int
    duration: float
    stdout: str = ""
    stderr: str = ""


@dataclass
class BuildOutcome:
    adapter: str
    status: EngineStatus
    build_dir: Path
    steps: list[BuildStep]
    artifacts: list[Path]
    compile_commands: Path | None = None


class BuildAdapter(ABC):
    @abstractmethod
    def run(self, request: BuildRequest) -> BuildOutcome:
        raise NotImplementedError


@dataclass
class ArtifactManifest:
    adapter: str
    build_dir: str
    artifacts: list[str]
    compile_commands: str | None
    steps: list[dict[str, object]] = field(default_factory=list)

    def validate(self, project_root: Path) -> None:
        allowed_root = project_root.resolve()
        for value in [self.build_dir, *self.artifacts]:
            resolved = (allowed_root / value).resolve()
            try:
                resolved.relative_to(allowed_root)
            except ValueError as exc:
                raise ValueError(f"artifact path is outside project: {value}") from exc
```

- [ ] **Step 4: JSON artifact manifest와 선택 규칙 구현**

manifest 경로는 `build/ici/artifacts.json`으로 고정하고 adapter, build_dir, artifact 상대 경로,
compile_commands 상대 경로, 명령 단계와 생성 시각이 아닌 source commit SHA를 기록한다.
adapter 선택 우선순위는 `[engines.build_definition] adapter`, 단일 CMakeLists, 단일 `.pro` 순이다.
둘 다 있거나 `.pro`가 여러 개면 명시 설정을 요구한다.

- [ ] **Step 5: BuildDefinitionEngine 뼈대와 설정 추가**

```toml
[engines.build_definition]
enabled = true
required = false
adapter = "auto"
jobs = 4
artifact_globs = []
```

adapter를 찾지 못한 선택 엔진은 SKIP, required 엔진은 ERROR다. 실제 CMake/qmake 구현 전에는
registry에 test fake adapter만 주입할 수 있도록 constructor parameter를 제공한다.

- [ ] **Step 6: 테스트와 커밋**

Run: `uv run --python 3.10 pytest tests/test_build_adapter.py -v`

Expected: PASS.

```bash
git add src/ici/build_adapters src/ici/core/artifacts.py \
  src/ici/engines/build_definition.py src/ici/config.py tests/test_build_adapter.py CHANGELOG.md
git commit -m "feat(build): define adapters and artifact manifest"
```

### Task 3: CMake/CTest adapter

**Files:**
- Create: `src/ici/build_adapters/cmake.py`
- Modify: `src/ici/build_adapters/registry.py`
- Create: `tests/test_cmake_adapter.py`
- Modify: `docs/engine-reference.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `CMakeAdapter(BuildAdapter)`
- Consumes: `BuildRequest`, `BuildOutcome`, `artifact_globs`

- [ ] **Step 1: configure/build/test argv 테스트 작성**

```python
def test_cmake_adapter_uses_shadow_build_and_compile_database(tmp_path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.16)\n")
    calls = []
    monkeypatch.setattr(
        "ici.build_adapters.cmake.run_process",
        lambda argv, cwd=None, **kwargs: (
            calls.append((argv, cwd)) or ProcessResult(0, "", "", 0.01)
        ),
    )
    request = BuildRequest(tmp_path, tmp_path / "build/ici/cmake", 4, {})
    CMakeAdapter({"cmake": "/usr/bin/cmake", "ctest": "/usr/bin/ctest"}, []).run(request)
    assert calls[0][0] == [
        "/usr/bin/cmake",
        "-S",
        str(tmp_path),
        "-B",
        str(request.build_dir),
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
    ]
    assert calls[1][0] == [
        "/usr/bin/cmake",
        "--build",
        str(request.build_dir),
        "--parallel",
        "4",
    ]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run --python 3.10 pytest tests/test_cmake_adapter.py -v`

Expected: 모듈 import 실패.

- [ ] **Step 3: CMake configure/build/CTest 구현**

configure와 build가 성공한 경우 `CTestTestfile.cmake`가 존재하거나 `run_ctest=true`이면 다음을
실행한다.

```python
ctest_argv = [
    tools["ctest"],
    "--test-dir",
    str(request.build_dir),
    "--output-on-failure",
]
```

각 단계 실패는 즉시 BuildOutcome FAIL/ERROR로 반환하되 이전 단계의 증거는 유지한다.
`artifact_globs`는 build_dir 기준으로만 해석하고 결과 경로를 manifest validation에 통과시킨다.

- [ ] **Step 4: preset 지원 추가**

`configure_preset`이 있으면 `cmake --preset <name>`을 프로젝트 루트에서 실행한다.
`build_preset`, `test_preset`도 각각 `cmake --build --preset`, `ctest --preset` argv를 사용하며,
preset과 직접 `-S/-B` 모드는 동시에 설정할 수 없도록 config validation을 추가한다.

- [ ] **Step 5: CMake adapter 테스트와 커밋**

Run: `uv run --python 3.10 pytest tests/test_cmake_adapter.py tests/test_build_adapter.py -v`

Expected: PASS.

```bash
git add src/ici/build_adapters/cmake.py src/ici/build_adapters/registry.py \
  tests/test_cmake_adapter.py docs/engine-reference.md CHANGELOG.md
git commit -m "feat(build): validate CMake and CTest projects"
```

### Task 4: qmake/Make adapter

**Files:**
- Create: `src/ici/build_adapters/qmake.py`
- Modify: `src/ici/build_adapters/registry.py`
- Create: `tests/test_qmake_adapter.py`
- Modify: `docs/engine-reference.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `QMakeAdapter(BuildAdapter)`
- Consumes: `BuildRequest`, 명시 `.pro` 경로, qmake/make 경로

- [ ] **Step 1: qmake와 make argv 테스트 작성**

```python
def test_qmake_adapter_uses_selected_project_file(tmp_path, monkeypatch):
    pro = tmp_path / "app.pro"
    pro.write_text("TEMPLATE = app\nTARGET = demo\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        "ici.build_adapters.qmake.run_process",
        lambda argv, cwd=None, **kwargs: (
            calls.append((argv, cwd)) or ProcessResult(0, "", "", 0.01)
        ),
    )
    build_dir = tmp_path / "build/ici/qmake"
    QMakeAdapter("/usr/bin/qmake", "/usr/bin/make", pro, []).run(
        BuildRequest(tmp_path, build_dir, 4, {})
    )
    assert calls[0][0] == ["/usr/bin/qmake", "-o", "Makefile", str(pro)]
    assert calls[0][1] == build_dir
    assert calls[1][0] == ["/usr/bin/make", "-C", str(build_dir), "-j4"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run --python 3.10 pytest tests/test_qmake_adapter.py -v`

Expected: 모듈 import 실패.

- [ ] **Step 3: shadow qmake build와 Qt 증거 구현**

build 전에 `qmake -query QT_VERSION`, `qmake -query QMAKE_SPEC`을 수집한다. `.pro`는 프로젝트
경계 안의 명시 경로 하나만 허용한다. qmake 성공 후 Makefile 존재를 확인하고 make를 실행한다.
테스트 명령은 `[engines.build_definition.qmake] test_argv`가 설정된 경우에만 argv 그대로 실행한다.

- [ ] **Step 4: 산출물과 실패 단계 검증 테스트 추가**

qmake returncode가 0이어도 Makefile이 없으면 ERROR, make 실패는 FAIL, artifact glob 결과가
없으면 required 정책에 따라 FAIL임을 테스트한다.

- [ ] **Step 5: qmake adapter 테스트와 커밋**

Run: `uv run --python 3.10 pytest tests/test_qmake_adapter.py tests/test_build_adapter.py -v`

Expected: PASS.

```bash
git add src/ici/build_adapters/qmake.py src/ici/build_adapters/registry.py \
  tests/test_qmake_adapter.py docs/engine-reference.md CHANGELOG.md
git commit -m "feat(build): validate qmake and Make projects"
```

### Task 5: compile_commands.json 검증 엔진

**Files:**
- Create: `src/ici/engines/compile_db.py`
- Modify: `src/ici/config.py`
- Modify: `src/ici/engines/verify.py`
- Create: `tests/test_compile_db.py`
- Modify: `docs/engine-reference.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `CompileCommand(directory, file, arguments)`
- Produces: `load_compile_database(path, project_root, build_root) -> list[CompileCommand]`
- Produces: `CompileDatabaseEngine.run_from_path(path: Path) -> EngineResult`
- Consumes: `build/ici/artifacts.json`

- [ ] **Step 1: 소스 누락, 경로 이탈, 표준 플래그 테스트 작성**

```python
def test_compile_db_reports_source_missing_from_database(tmp_path):
    source = tmp_path / "src" / "main.cpp"
    source.parent.mkdir()
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    db = tmp_path / "build" / "compile_commands.json"
    db.parent.mkdir()
    db.write_text("[]", encoding="utf-8")
    result = CompileDatabaseEngine(
        tmp_path,
        {"engines": {"compile_db": {"required": True, "required_flags": ["-std=c++17"]}}},
    ).run_from_path(db)
    assert result.status == EngineStatus.FAIL
    assert any(t.file_path == "src/main.cpp" for t in result.targets)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run --python 3.10 pytest tests/test_compile_db.py -v`

Expected: 모듈 import 실패.

- [ ] **Step 3: 안전한 compile database parser 구현**

```python
@dataclass
class CompileCommand:
    directory: Path
    file: Path
    arguments: list[str]
```

`arguments` 배열이 있으면 그대로 사용하고, `command` 문자열만 있으면 `shlex.split(...,
posix=True)`로 토큰화한다. 어떤 경우에도 실행하지 않는다. directory는 build root, file은 project
root 또는 허용된 generated source root 안에 있어야 한다.

- [ ] **Step 4: coverage와 플래그 검증 구현**

프로젝트의 모든 C/C++ source가 DB에 한 번 이상 포함되는지 확인한다. 컴파일러 executable,
`required_flags`, `forbidden_flags`, C++ 표준, include directory 존재 여부를 target으로 기록한다.
응답 파일 `@file`은 프로젝트/build 경계 안에 있을 때만 읽고 같은 방식으로 토큰화한다.

- [ ] **Step 5: 엔진 등록, 테스트, 커밋**

Run: `uv run --python 3.10 pytest tests/test_compile_db.py -v`

Expected: PASS.

```bash
git add src/ici/engines/compile_db.py src/ici/config.py src/ici/engines/verify.py \
  tests/test_compile_db.py docs/engine-reference.md CHANGELOG.md
git commit -m "feat(cpp): validate compilation database coverage"
```

### Task 6: Target Python runtime와 package 호환성 엔진

**Files:**
- Modify: `pyproject.toml`
- Create: `src/ici/engines/python_compat.py`
- Modify: `src/ici/config.py`
- Modify: `src/ici/config_schema.py`
- Modify: `src/ici/engines/verify.py`
- Create: `tests/test_python_compat.py`
- Modify: `docs/engine-reference.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `PythonTarget(target_id, executable, version)`
- Produces: `PythonCompatibilityEngine.run() -> EngineResult`
- Consumes: `packaging.specifiers.SpecifierSet`, project source dirs and metadata

- [ ] **Step 1: interpreter별 compile/import와 Requires-Python 테스트 작성**

```python
def test_python_compat_runs_compile_and_import_with_target(tmp_path, monkeypatch):
    src = tmp_path / "src" / "demo"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        "ici.engines.python_compat.run_process",
        lambda argv, cwd=None, env=None, **kwargs: (
            calls.append((argv, env)) or ProcessResult(0, "", "", 0.01)
        ),
    )
    config = {
        "engines": {
            "python_compat": {
                "required": True,
                "targets": [{"id": "py310", "executable": "/opt/python310/bin/python"}],
                "imports": ["demo"],
            }
        }
    }
    result = PythonCompatibilityEngine(tmp_path, config).run()
    assert result.status == EngineStatus.PASS
    assert calls[0][0][0] == "/opt/python310/bin/python"
    assert "-m" in calls[1][0] and "compileall" in calls[1][0]
```

- [ ] **Step 2: `packaging` 의존성과 실패 테스트 추가**

`pyproject.toml` dependencies에 `packaging>=24`를 추가한다. `Requires-Python >=3.12` 프로젝트를
Python 3.10 target으로 검사하면 FAIL하는 테스트를 추가한다.

Run: `uv lock`

Expected: `uv.lock`에 Python 3.10 호환 `packaging` pure-Python wheel resolution이 기록됨.

- [ ] **Step 3: 테스트 실패 확인**

Run: `uv run --python 3.10 pytest tests/test_python_compat.py -v`

Expected: 모듈 import 실패.

- [ ] **Step 4: Target Python 검증 구현**

각 target마다 `python -VV`, `python -m compileall -q <source_dirs>`를 실행한다. import 목록은
다음 고정 스크립트에 JSON 환경변수로 전달하여 코드 문자열 삽입을 피한다.

```python
IMPORT_SCRIPT = (
    "import importlib, json, os; "
    "[importlib.import_module(name) for name in json.loads(os.environ['ICI_IMPORTS'])]"
)
```

`pyproject.toml [project].requires-python`은 `SpecifierSet`으로 target version과 비교한다. console
script 값은 `module:function` 형식인지 검증하고 module을 import한다.

- [ ] **Step 5: wheel 정책 검사 구현**

`wheel_globs`가 설정되면 filename tag를 파싱한다. `require_pure_wheel=true`일 때 `*-none-any.whl`
이 아닌 파일은 FAIL한다. 혼합 프로젝트는 기본 false로 두어 native wheel을 허용한다.

- [ ] **Step 6: 테스트, 순수 wheel 게이트, 커밋**

Run: `uv run --python 3.10 pytest tests/test_python_compat.py tests/test_purity.py -v`

Expected: PASS와 `packaging`의 pure wheel 확인.

```bash
git add pyproject.toml src/ici/engines/python_compat.py src/ici/config.py \
  src/ici/config_schema.py src/ici/engines/verify.py tests/test_python_compat.py \
  docs/engine-reference.md CHANGELOG.md uv.lock
git commit -m "feat(python): validate target runtime compatibility"
```

### Task 7: ELF와 C++ ABI 호환성 엔진

**Files:**
- Create: `src/ici/engines/binary_compat.py`
- Modify: `src/ici/config.py`
- Modify: `src/ici/config_schema.py`
- Modify: `src/ici/engines/verify.py`
- Create: `tests/test_binary_compat.py`
- Modify: `docs/engine-reference.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `ElfFacts(elf_class, machine, needed, rpaths, required_versions)`
- Produces: `parse_readelf(header, dynamic, versions) -> ElfFacts`
- Consumes: artifact manifest and Task 1의 readelf capability

- [ ] **Step 1: readelf fixture parsing과 상한 위반 테스트 작성**

```python
def test_binary_compat_rejects_glibc_above_configured_floor(tmp_path, monkeypatch):
    manifest_path = write_manifest(tmp_path, artifacts=["build/ici/cmake/demo"])
    outputs = {
        "-h": "Class: ELF64\nMachine: Advanced Micro Devices X86-64\n",
        "-d": "Shared library: [libstdc++.so.6]\nRUNPATH Library runpath: [/tmp/build/lib]\n",
        "-V": "Name: GLIBC_2.28\nName: GLIBCXX_3.4.26\n",
    }
    monkeypatch.setattr(
        "ici.engines.binary_compat.run_readelf",
        lambda _tool, mode, _path: outputs[mode],
    )
    config = {
        "engines": {
            "binary_compat": {
                "required": True,
                "manifest": str(manifest_path),
                "max_glibc": "2.17",
                "forbid_absolute_rpath": True,
            }
        }
    }
    result = BinaryCompatibilityEngine(tmp_path, config).run()
    assert result.status == EngineStatus.FAIL
    assert any(t.target_name == "GLIBC_2.28" for t in result.targets)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run --python 3.10 pytest tests/test_binary_compat.py -v`

Expected: 모듈 import 실패.

- [ ] **Step 3: 실행 없는 readelf 수집과 parser 구현**

각 artifact에 `readelf -h`, `readelf -d`, `readelf -V`를 실행한다. `ldd`로 대상 바이너리를
로드하지 않는다. 정규식으로 ELF class/machine, NEEDED, RPATH/RUNPATH, GLIBC/GLIBCXX/CXXABI
요구 버전을 추출한다.

- [ ] **Step 4: ABI 정책과 위치 target 구현**

```toml
[engines.binary_compat]
enabled = false
required = false
expected_machine = "Advanced Micro Devices X86-64"
max_glibc = "2.17"
max_glibcxx = "3.4.19"
forbid_absolute_rpath = true
allowed_needed = []
forbidden_needed = []
```

버전 비교는 Task 6의 `packaging.version.Version`을 사용한다. 바이너리 파일은 라인 번호가 없으므로
`start_line=1`, `target_name`에 심볼 또는 dynamic tag를 기록한다. 절대 프로젝트/build 경로가
strings 또는 debug section에 남았는지도 선택 설정으로 검사한다.

- [ ] **Step 5: 테스트, 문서, 커밋**

Run: `uv run --python 3.10 pytest tests/test_binary_compat.py -v`

Expected: PASS.

```bash
git add src/ici/engines/binary_compat.py src/ici/config.py src/ici/config_schema.py \
  src/ici/engines/verify.py tests/test_binary_compat.py docs/engine-reference.md CHANGELOG.md
git commit -m "feat(cpp): validate ELF and ABI compatibility"
```

### Task 8: C++/Python 혼합 통합 스모크 엔진

**Files:**
- Create: `src/ici/engines/integration.py`
- Modify: `src/ici/config.py`
- Modify: `src/ici/config_schema.py`
- Modify: `src/ici/engines/verify.py`
- Create: `tests/test_integration_engine.py`
- Modify: `docs/engine-reference.md`
- Modify: `docs/user-guide.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `IntegrationCase(name, argv, expected_exit, stdout_contains, stderr_contains, timeout)`
- Produces: safe placeholder resolution for `{python:<id>}` and `{artifact:<name>}`
- Consumes: Target Python map and artifact manifest

- [ ] **Step 1: argv 실행과 placeholder 경계 테스트 작성**

```python
def test_integration_case_resolves_python_and_artifact_without_shell(tmp_path, monkeypatch):
    manifest = write_manifest(tmp_path, artifacts=["build/ici/cmake/demo"])
    calls = []
    monkeypatch.setattr(
        "ici.engines.integration.run_process",
        lambda argv, **kwargs: calls.append(argv) or ProcessResult(0, "ready\n", "", 0.01),
    )
    config = {
        "engines": {
            "integration": {
                "required": True,
                "manifest": str(manifest),
                "python_targets": {"py310": sys.executable},
                "cases": [
                    {
                        "name": "python-to-cpp",
                        "argv": ["{python:py310}", "tests/smoke.py", "{artifact:demo}"],
                        "expected_exit": 0,
                        "stdout_contains": ["ready"],
                    }
                ],
            }
        }
    }
    result = IntegrationEngine(tmp_path, config).run()
    assert result.status == EngineStatus.PASS
    assert calls[0][0] == sys.executable
    assert calls[0][-1].endswith("build/ici/cmake/demo")
```

- [ ] **Step 2: 알 수 없는 placeholder와 빈 cases 실패 테스트 추가**

`{shell:...}` 또는 존재하지 않는 artifact/python id는 ConfigError다. required 엔진의 cases 빈 배열은
ERROR, 선택 엔진의 빈 배열은 SKIP임을 테스트한다.

- [ ] **Step 3: 테스트 실패 확인**

Run: `uv run --python 3.10 pytest tests/test_integration_engine.py -v`

Expected: 모듈 import 실패.

- [ ] **Step 4: contract 실행 구현**

각 case는 argv list만 허용하고 known placeholder를 각 토큰 전체와 치환한다. 환경변수는
`env_allowlist`에 선언한 이름만 현재 환경에서 복사하고, case별 `env` 값은 문자열만 허용한다.
종료 코드, 필수 stdout substring, 필수 stderr substring, 금지 substring을 검사한다.

- [ ] **Step 5: 혼합 시나리오 예제 문서화**

```toml
[engines.integration]
enabled = true
required = true

[[engines.integration.cases]]
name = "python-client-cpp-server"
argv = ["{python:py310}", "tests/smoke_client.py", "{artifact:demo-server}"]
expected_exit = 0
stdout_contains = ["integration-ok"]
timeout = 30
```

- [ ] **Step 6: 테스트, 문서, 커밋**

Run: `uv run --python 3.10 pytest tests/test_integration_engine.py -v`

Expected: PASS.

```bash
git add src/ici/engines/integration.py src/ici/config.py src/ici/config_schema.py \
  src/ici/engines/verify.py tests/test_integration_engine.py \
  docs/engine-reference.md docs/user-guide.md CHANGELOG.md
git commit -m "feat(integration): verify mixed-language smoke contracts"
```

### Task 9: 신규 엔진 통합, 독립 OS 실행 가이드, 전체 검증

**Files:**
- Modify: `src/ici/__main__.py`
- Modify: `src/ici/engines/verify.py`
- Modify: `src/ici/reporters/html.py`
- Modify: `src/ici/reporters/markdown.py`
- Modify: `src/ici/reporters/json_rep.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_reporters.py`
- Create: `tests/test_ci_validation_flow.py`
- Modify: `docs/architecture.md`
- Modify: `docs/ci-integration.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `toolchain`, `build_definition`, `compile_db`, `python_compat`, `binary_compat`, `integration`
- Produces: 한 OS 실행에 대한 재현 가능한 JSON v2와 HTML 결과

- [ ] **Step 1: 엔진 실행 순서와 독립 결과 테스트 작성**

```python
def test_ci_engines_run_in_dependency_order():
    names = [name for name, _engine in VerifyOrchestrator.engine_definitions()]
    assert names.index("toolchain") < names.index("build_definition")
    assert names.index("build_definition") < names.index("compile_db")
    assert names.index("build_definition") < names.index("binary_compat")
    assert names.index("binary_compat") < names.index("integration")
```

`tests/test_ci_validation_flow.py`는 fake CMake toolchain과 manifest를 사용해 한 번의 verify에서
환경 증거, build artifact, compile DB, binary, integration 결과가 각각 별도 EngineResult로
JSON에 기록되는지 검증한다.

- [ ] **Step 2: engine registry와 개별 CLI command 통합**

`VerifyOrchestrator.engine_definitions()`를 classmethod로 추출하여 순서를 단일 위치에서 관리한다.
`ici toolchain`, `ici build-check`, `ici compile-db`, `ici python-compat`, `ici binary-compat`,
`ici integration` 명령을 추가하고 hardening 계획의 공통 config/exit code 함수를 사용한다.

- [ ] **Step 3: 신규 결과를 리포터에 표시**

HTML과 Markdown의 summary에는 환경 ID, 사용한 adapter, Target Python, artifact 수를 추가한다.
별도 대형 탭을 늘리지 않고 Issues-First 목록과 engine detail table을 재사용한다. JSON에는 현재
OS 결과만 저장하고 다른 실행 파일을 탐색하거나 병합하지 않는다.

- [ ] **Step 4: OS별 독립 실행 예제 문서화**

`docs/ci-integration.md`에는 다음 형태만 제시한다.

```bash
# RHEL 8.10 runner
ICI_PYTHON=/opt/python/3.10/bin/python3.10 dist/ici.pyz verify \
  --report --html verify-rhel8.html

# 신규 RHEL runner에서 별도로 실행
ICI_PYTHON=/opt/python/3.12/bin/python3.12 dist/ici.pyz verify \
  --report --html verify-next.html
```

두 결과를 합치는 명령이나 공통 PASS 판정은 문서에 추가하지 않는다.

- [ ] **Step 5: 전체 품질 게이트 실행**

Run: `TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run --python 3.10 pytest`

Expected: 전체 PASS.

Run: `uvx ruff check .`

Expected: PASS.

Run: `uvx ruff format --check .`

Expected: PASS.

Run: `./scripts/build-pyz.sh && ./scripts/smoke.sh`

Expected: pure-wheel 검사, 재현 가능한 pyz 생성, smoke PASS.

- [ ] **Step 6: 최종 통합 커밋**

```bash
git add src/ici tests docs README.md CHANGELOG.md pyproject.toml uv.lock
git commit -m "feat(ci): integrate C++ and Python validation flow"
```

## Final Review Checklist

- [ ] 각 OS 실행은 다른 결과를 읽거나 합치지 않고 자신의 환경 증거만 저장한다.
- [ ] 필수 compiler/build/Python/binutils 누락은 PASS가 아니다.
- [ ] CMake와 qmake는 실제 프로젝트 정의와 shadow build를 사용한다.
- [ ] compile DB는 전체 C++ source coverage와 요구 flags를 검증한다.
- [ ] Target Python별 compile, import, package metadata 결과가 분리된다.
- [ ] ELF 검증은 대상 바이너리를 실행하지 않고 readelf 결과만 사용한다.
- [ ] 통합 case는 shell 없는 argv와 허용 placeholder만 사용한다.
- [ ] JSON/HTML만으로 OS, 툴 경로·버전, 명령 단계, 실패 원인을 확인할 수 있다.
- [ ] pytest, Ruff, pyz build, smoke 품질 게이트가 모두 통과한다.
