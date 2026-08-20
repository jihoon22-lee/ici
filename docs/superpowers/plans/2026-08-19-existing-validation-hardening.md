# Existing Validation Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use Markdown checkboxes for tracking.

**Goal:** 현재 9개 검증 엔진, build, 설정, 리포터가 실행 여부와 실패 원인을 정확히 표현하고 허위 PASS를 만들지 않도록 보강한다.

**Architecture:** 기존 `EngineResult` 계약에 실행 증거와 필수 여부를 추가하고, 설정과 프로세스 실행을 공통 계층에서 검증한다. 각 엔진은 도구 미실행과 실측 실패를 구분하며, 오케스트레이터와 모든 리포터는 같은 게이트 판정 함수를 사용한다.

**Tech Stack:** Python 3.10, Typer, dataclasses, tomli/tomli-w, pytest, Ruff, mypy, stdlib subprocess/pathlib/json/html

**Spec:** `docs/design/ci-validation-roadmap.md`

## v0.4.0 완료 상태 (2026-08-20)

Task 1부터 Task 10까지의 구현, 회귀 테스트, 문서 정합성, CI 권한 분리 및 최종 품질 게이트를
v0.4.0 범위에서 모두 완료했다. 기존 9개 검증 엔진과 build/config/process/report/CLI 경로의
허위 PASS 방지 및 실행 증거 보강이 이번 릴리스의 완료 범위다.

- PR #10~#20에서 Task 1~10을 순차 병합했다.
- 검증 job은 `contents: read`만 사용하고 checkout은 `persist-credentials: false`다.
- `main` push 전용 `publish-main` job만 `contents: write`를 사용하며 PR 댓글/이슈/Checks
  쓰기 권한은 부여하지 않는다.
- v0.4.0 후보는 로컬에서 전체 pytest/mypy/Ruff/pyz/smoke 게이트와 workflow YAML/shell 정적
  검증을 통과했다. GitHub tag workflow 실행과 GitHub Release publication은 아직 수행하지
  않았으며, 배포 전 최종 릴리스 게이트로 남아 있다.
- 신규 Toolchain/build adapter/compile DB/Python compatibility/ELF-ABI/integration 엔진은
  이번 릴리스에 구현하지 않으며 별도 미래 계획으로 남긴다.

## Global Constraints

- Python 3.10에서 동작해야 하며 `tomllib`, 3.11+ 전용 문법을 사용하지 않는다.
- 런타임 의존성은 `py3-none-any` 순수 Python 휠만 허용한다.
- HTTP가 필요할 때 `requests`, `httpx`, `certifi`를 추가하지 않는다.
- 모든 외부 명령은 shell 없이 argv 배열로 실행한다.
- 모든 엔진은 검사한 대상의 `InspectionTarget` 위치를 반환한다.
- HTML은 Zero-CDN을 유지하고 동적 문자열을 HTML/JavaScript 문맥에 맞게 이스케이프한다.
- 각 작업은 테스트, 문서, `CHANGELOG.md` 변경을 포함한 독립 커밋으로 끝낸다.
- 구현은 `fix/<issue-name>` 브랜치와 GitHub PR 단위로 수행한다.

## File Structure

- `src/ici/core/models.py`: 결과 상태, 증거 상태, 도구 실행 증거, 공통 게이트 판정
- `src/ici/config.py`: 설정 계층 병합과 기본값
- `src/ici/config_schema.py`: 설정 타입·범위·알 수 없는 키 검증
- `src/ici/core/project.py`: 프로젝트 경계 내 소스 탐색과 TOML 메타데이터
- `src/ici/core/runner.py`: timeout·출력 제한·프로세스 그룹을 포함한 공통 실행 결과
- `src/ici/engines/verify.py`: 엔진 예외 격리와 suite 상태 집계
- `src/ici/engines/*.py`: 엔진별 실측 증거와 미실행 처리
- `src/ici/reporters/*.py`: ERROR/SKIP/evidence 직렬화 및 안전한 출력
- `.github/workflows/ci.yml`: 읽기 전용 검증과 쓰기 권한 게시 작업 분리

---

### Task 1: 결과 상태와 게이트 계약

**Files:**
- Modify: `src/ici/core/models.py`
- Modify: `src/ici/engines/base.py`
- Modify: `src/ici/engines/verify.py`
- Create: `tests/test_result_contract.py`
- Modify: `docs/engine-reference.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `EvidenceState`, `ToolEvidence`, `EngineResult.required`, `EngineResult.evidence`, `EngineResult.tool_evidence`
- Produces: `aggregate_suite_status(results: list[EngineResult]) -> EngineStatus`
- Consumes: 기존 `EngineStatus`, `EngineResult`, `VerificationSuiteResult`

- [x] **Step 1: 허위 PASS와 `pass_fail` 경고 처리를 고정하는 실패 테스트 작성**

```python
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    aggregate_suite_status,
)
from ici.engines.base import BaseEngine


class DummyEngine(BaseEngine):
    def run(self):
        raise NotImplementedError


def test_required_not_run_result_blocks_suite():
    result = EngineResult(
        engine_name="test",
        status=EngineStatus.SKIP,
        summary="pytest was not executed",
        required=True,
        evidence=EvidenceState.NOT_RUN,
    )
    assert aggregate_suite_status([result]) == EngineStatus.ERROR


def test_empty_suite_is_error():
    assert aggregate_suite_status([]) == EngineStatus.ERROR


def test_pass_fail_promotes_warning_to_failure():
    engine = DummyEngine()
    assert engine.evaluate_status(False, True, "pass_fail") == EngineStatus.FAIL
```

- [x] **Step 2: 테스트가 현재 계약에서 실패하는지 확인**

Run: `uv run --python 3.10 pytest tests/test_result_contract.py -v`

Expected: `EvidenceState` 또는 `aggregate_suite_status` import 실패와 `pass_fail` assertion 실패.

- [x] **Step 3: 모델과 집계 함수를 최소 구현**

```python
class EngineStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIP = "SKIP"


class EvidenceState(str, Enum):
    MEASURED = "MEASURED"
    ESTIMATED = "ESTIMATED"
    NOT_RUN = "NOT_RUN"


@dataclass
class ToolEvidence:
    name: str
    path: str
    version: str = ""
    argv: list[str] = field(default_factory=list)
    returncode: int | None = None


def aggregate_suite_status(results: list[EngineResult]) -> EngineStatus:
    if not results:
        return EngineStatus.ERROR
    if any(
        r.required
        and (
            r.status in (EngineStatus.ERROR, EngineStatus.SKIP)
            or r.evidence == EvidenceState.NOT_RUN
        )
        for r in results
    ):
        return EngineStatus.ERROR
    if any(r.required and r.status == EngineStatus.FAIL for r in results):
        return EngineStatus.FAIL
    if any(r.status == EngineStatus.WARN for r in results):
        return EngineStatus.WARN
    return EngineStatus.PASS
```

`EngineResult`에는 하위 호환 기본값으로 `required=True`, `evidence=EvidenceState.MEASURED`,
`tool_evidence=[]`를 추가한다. `BaseEngine.evaluate_status()`의 `pass_fail`은 `has_fail or
has_warn`일 때 FAIL을 반환하게 한다. `VerifyOrchestrator`의 수동 boolean 집계는
`aggregate_suite_status()` 호출로 교체한다.

- [x] **Step 4: 결과 계약 테스트와 기존 모델 소비 테스트 실행**

Run: `uv run --python 3.10 pytest tests/test_result_contract.py tests/test_reporters.py -v`

Expected: PASS.

- [x] **Step 5: 엔진 레퍼런스와 변경 이력에 새 상태 계약 기록**

`docs/engine-reference.md`의 평가 모드 설명에 `ERROR`, `SKIP`, 증거 상태와 필수 엔진 규칙을
추가한다. `CHANGELOG.md` 최상단에 `## [Unreleased]`와 `### Changed`를 만들고 결과 계약을
기록한다.

- [x] **Step 6: 커밋**

```bash
git add src/ici/core/models.py src/ici/engines/base.py src/ici/engines/verify.py \
  tests/test_result_contract.py docs/engine-reference.md CHANGELOG.md
git commit -m "fix(core): make verification outcomes evidence-aware"
```

### Task 2: 설정 계층 병합과 스키마 검증

**Files:**
- Modify: `src/ici/config.py`
- Create: `src/ici/config_schema.py`
- Modify: `src/ici/__main__.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli.py`
- Modify: `docs/user-guide.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `validate_config(config: dict[str, Any]) -> None`
- Produces: `ConfigError(ValueError)`
- Produces: `load_config(base_dir: Path | None = None) -> dict[str, Any]` with deterministic merge order
- Consumes: `DEFAULT_CONFIG`, `_deep_merge`, `get_global_config_path()`

- [x] **Step 1: 전역·프로젝트·명시 설정 병합과 잘못된 설정 실패 테스트 작성**

```python
def test_load_config_merges_global_project_and_explicit(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg"
    global_file = xdg / "ici" / "ici.toml"
    global_file.parent.mkdir(parents=True)
    global_file.write_text("[engines.line]\nwarn_limit = 400\n", encoding="utf-8")
    (tmp_path / "ici.toml").write_text("[engines.line]\nfail_limit = 900\n", encoding="utf-8")
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("[engines.line]\nwarn_limit = 300\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("ICI_CONFIG", str(explicit))

    config = load_config(tmp_path)
    assert config["engines"]["line"]["warn_limit"] == 300
    assert config["engines"]["line"]["fail_limit"] == 900


def test_load_config_rejects_invalid_threshold_order(tmp_path):
    (tmp_path / "ici.toml").write_text(
        "[engines.line]\nwarn_limit = 1000\nfail_limit = 500\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="warn_limit"):
        load_config(tmp_path)
```

- [x] **Step 2: 새 설정 테스트 실패 확인**

Run: `uv run --python 3.10 pytest tests/test_config.py -v`

Expected: 첫 파일에서 로딩이 중단되어 병합 assertion 실패, `ConfigError` import 실패.

- [x] **Step 3: 결정적 병합 순서와 스키마 검증 구현**

```python
def _config_paths(base: Path) -> list[Path]:
    paths = [get_global_config_path(), base / "ici.toml", base / "dev.toml"]
    explicit = os.environ.get("ICI_CONFIG")
    if explicit:
        paths.append(Path(explicit).expanduser().resolve())
    return paths


def validate_config(config: dict[str, Any]) -> None:
    engines = config.get("engines")
    if not isinstance(engines, dict):
        raise ConfigError("engines must be a table")
    line = engines.get("line", {})
    warn_limit = line.get("warn_limit", 500)
    fail_limit = line.get("fail_limit", 1000)
    if not isinstance(warn_limit, int) or not isinstance(fail_limit, int):
        raise ConfigError("engines.line limits must be integers")
    if warn_limit < 1 or fail_limit < warn_limit:
        raise ConfigError("engines.line.warn_limit must be <= fail_limit")
```

`load_config()`은 존재하는 모든 파일을 순서대로 `_deep_merge()`하고 마지막에
`validate_config()`를 호출한다. TOML 구문 오류와 명시 설정 파일 부재는 조용히 무시하지 않고
`ConfigError`로 변환한다.

- [x] **Step 4: 모든 CLI 엔진에 effective config 전달**

```python
def _create_engine(engine_cls):
    root = Path.cwd().resolve()
    return engine_cls(root, load_config(root))
```

`cmd_line`부터 `cmd_exception`, `cmd_build`까지 직접 생성자를 호출하는 부분을
`_create_engine()`으로 교체한다. callback에서 설정을 읽고 버리는 동작은 제거한다.

- [x] **Step 5: CLI 설정 적용 회귀 테스트 작성 및 실행**

```python
def test_line_command_uses_project_config(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "large.py").write_text("x = 1\n" * 3, encoding="utf-8")
    (tmp_path / "ici.toml").write_text(
        "[engines.line]\nwarn_limit = 1\nfail_limit = 2\nmode = 'pass_warn_fail'\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["line"])
    assert result.exit_code == 1
```

Run: `uv run --python 3.10 pytest tests/test_config.py tests/test_cli.py -v`

Expected: PASS.

- [x] **Step 6: 문서 갱신 후 커밋**

```bash
git add src/ici/config.py src/ici/config_schema.py src/ici/__main__.py \
  tests/test_config.py tests/test_cli.py docs/user-guide.md CHANGELOG.md
git commit -m "fix(config): merge and validate effective policy"
```

### Task 3: 프로젝트 경계와 메타데이터 파싱

**Files:**
- Modify: `src/ici/core/project.py`
- Create: `tests/test_project_metadata.py`
- Modify: `tests/test_project_layout.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `resolve_project_path(base: Path, value: str) -> Path`
- Produces: `read_project_metadata(base: Path) -> tuple[str, str]`
- Consumes: `get_source_dirs()`, `get_project_name()`, `get_project_version()`

- [x] **Step 1: 경로 이탈과 잘못된 TOML 메타데이터 회귀 테스트 작성**

```python
def test_source_dirs_cannot_escape_project(tmp_path):
    config = {"project": {"source_dirs": ["../outside"]}}
    with pytest.raises(ValueError, match="outside project root"):
        get_source_dirs(tmp_path, config)


def test_project_metadata_reads_project_table(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo-app'\nversion = '2.4.1'\n", encoding="utf-8"
    )
    assert get_project_name(tmp_path) == "demo-app"
    assert get_project_version(tmp_path) == "v2.4.1"
```

- [x] **Step 2: 테스트 실패 확인**

Run: `uv run --python 3.10 pytest tests/test_project_layout.py tests/test_project_metadata.py -v`

Expected: `../outside`가 허용되거나 메타데이터가 잘못 파싱되어 FAIL.

- [x] **Step 3: canonical path containment와 tomli 기반 메타데이터 구현**

```python
def resolve_project_path(base: Path, value: str) -> Path:
    candidate = (base / value).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"path is outside project root: {value}") from exc
    return candidate


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        return tomli.load(stream)
```

`ici.toml`의 top-level `name/version`과 `pyproject.toml`의 `[project]`를 명시적으로 구분하고,
이름은 `[A-Za-z0-9._-]+`, 버전은 `v`를 제거한 뒤 같은 문자 집합만 허용한다.

- [x] **Step 4: 프로젝트 관련 전체 테스트 실행**

Run: `uv run --python 3.10 pytest tests/test_project_layout.py tests/test_project_metadata.py -v`

Expected: PASS.

- [x] **Step 5: 변경 이력 갱신 후 커밋**

```bash
git add src/ici/core/project.py tests/test_project_layout.py tests/test_project_metadata.py CHANGELOG.md
git commit -m "fix(project): contain paths and parse metadata safely"
```

### Task 4: 서브프로세스 제한과 엔진 예외 격리

**Files:**
- Modify: `src/ici/core/runner.py`
- Modify: `src/ici/engines/verify.py`
- Create: `tests/test_runner.py`
- Create: `tests/test_verify_orchestrator.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `ProcessResult(returncode, stdout, stderr, duration, timed_out, truncated)`
- Produces: `run_process(..., timeout=300.0, max_output_chars=1_000_000) -> ProcessResult`
- Consumes: Task 1의 `EvidenceState`, `ToolEvidence`, `aggregate_suite_status()`

- [x] **Step 1: timeout·출력 제한·엔진 예외 테스트 작성**

```python
def test_run_process_marks_timeout(tmp_path):
    result = run_process(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        cwd=tmp_path,
        timeout=0.05,
    )
    assert result.timed_out is True
    assert result.returncode == 124


def test_run_process_truncates_output(tmp_path):
    result = run_process(
        [sys.executable, "-c", "print('x' * 1000)"],
        cwd=tmp_path,
        max_output_chars=100,
    )
    assert result.truncated is True
    assert len(result.stdout) <= 100
```

오케스트레이터 테스트에는 첫 번째 fake engine이 `RuntimeError("boom")`을 발생시키고 두 번째
engine이 PASS를 반환하도록 구성하여 결과가 `ERROR, PASS` 두 개 모두 남는지 검증한다.

- [x] **Step 2: 테스트 실패 확인**

Run: `uv run --python 3.10 pytest tests/test_runner.py tests/test_verify_orchestrator.py -v`

Expected: tuple 반환에는 `timed_out`이 없고 오케스트레이터가 예외에서 중단되어 FAIL.

- [x] **Step 3: `ProcessResult`와 안전한 프로세스 종료 구현**

```python
@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False
    truncated: bool = False


def _limit(text: str, maximum: int) -> tuple[str, bool]:
    if len(text) <= maximum:
        return text, False
    return text[:maximum], True
```

POSIX에서는 `start_new_session=True`로 실행하고 timeout 시 `os.killpg(pid, signal.SIGTERM)` 후
짧은 유예 뒤 SIGKILL한다. Windows에서는 `proc.kill()`로 폴백한다. 모든 기존 tuple unpacking을
`result.returncode` 형식으로 한 번에 마이그레이션한다.

- [x] **Step 4: 엔진 실행을 try/except로 격리**

```python
try:
    result = engine_instance.run()
except Exception as exc:
    result = EngineResult(
        engine_name=name,
        status=EngineStatus.ERROR,
        summary=f"Engine crashed: {type(exc).__name__}: {exc}",
        required=bool(eng_cfg.get("required", True)),
        evidence=EvidenceState.NOT_RUN,
    )
results.append(result)
```

- [x] **Step 5: 전체 엔진 테스트 실행 후 커밋**

Run: `uv run --python 3.10 pytest -q`

Expected: 전체 PASS.

```bash
git add src/ici/core/runner.py src/ici/engines tests/test_runner.py \
  tests/test_verify_orchestrator.py CHANGELOG.md
git commit -m "fix(runner): bound subprocesses and isolate engine failures"
```

### Task 5: 테스트·커버리지 엔진의 실측 보장

**Files:**
- Modify: `src/ici/engines/test.py`
- Modify: `tests/test_test_engine.py`
- Modify: `docs/engine-reference.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `_resolve_python() -> list[str]`
- Produces: test result `EvidenceState.MEASURED|ESTIMATED|NOT_RUN`
- Consumes: Task 4의 `ProcessResult`

- [x] **Step 1: 테스트 0개·pytest 실행 실패·커버리지 미실측 테스트 작성**

```python
def test_zero_tests_is_failure(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    result = TestEngine(tmp_path).run()
    assert result.status == EngineStatus.FAIL
    assert result.extra["total_tests"] == 0


def test_required_coverage_cannot_pass_with_estimate(tmp_python_project, monkeypatch):
    engine = TestEngine(tmp_python_project, {"engines": {"test": {"coverage_required": True}}})
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda _cmd: None)
    result = engine.run()
    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
```

- [x] **Step 2: 현재 추정치 폴백으로 테스트가 실패함을 확인**

Run: `uv run --python 3.10 pytest tests/test_test_engine.py -v`

Expected: 빈 프로젝트가 PASS이거나 coverage estimate가 임계값을 통과하여 FAIL.

- [x] **Step 3: Python 도구를 같은 인터프리터의 `-m`으로 통일**

```python
def _resolve_python(self) -> list[str]:
    configured = self.get_config("test").get("python")
    if configured:
        return [str(configured)]
    venv_python = self.project_root / ".venv" / "bin" / "python"
    return [str(venv_python)] if venv_python.exists() else [sys.executable]
```

pytest는 `[*python_cmd, "-m", "pytest"]`, coverage는 `[*python_cmd, "-m", "coverage"]`,
unittest도 같은 python_cmd로 실행한다. PATH의 pytest/coverage 스크립트를 직접 선택하지 않는다.

- [x] **Step 4: 종료 코드와 수집 건수를 판정에 포함**

pytest 종료 코드 5 또는 `total_tests == 0`은 FAIL, 실행 파일 부재와 timeout은 ERROR로 처리한다.
`coverage_required=true`인데 coverage JSON 또는 gcov 결과가 없으면 ERROR이며, false이면
ESTIMATED/WARN으로 표시하되 TEM 임계값 PASS 근거로 사용하지 않는다.

- [x] **Step 5: TestEngine 전체 회귀 테스트 실행**

Run: `uv run --python 3.10 pytest tests/test_test_engine.py -v`

Expected: PASS.

- [x] **Step 6: 문서와 변경 이력 갱신 후 커밋**

```bash
git add src/ici/engines/test.py tests/test_test_engine.py docs/engine-reference.md CHANGELOG.md
git commit -m "fix(test): require executed tests and measured coverage"
```

### Task 6: lint와 type 엔진 실행 증거 보강

**Files:**
- Modify: `src/ici/engines/lint.py`
- Modify: `src/ici/engines/type_check.py`
- Create: `tests/test_lint_engine.py`
- Create: `tests/test_type_engine.py`
- Modify: `docs/engine-reference.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: 도구별 `ToolEvidence`
- Consumes: `ProcessResult`, `EvidenceState`

- [x] **Step 1: 도구 자체 실패와 미설치 상태 테스트 작성**

```python
def test_ruff_non_json_failure_is_error(tmp_python_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which", lambda name: "/bin/ruff" if name == "ruff" else None
    )
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(2, "", "invalid option", 0.01),
    )
    result = LintEngine(tmp_python_project).run()
    assert result.status == EngineStatus.ERROR


def test_required_mypy_missing_is_not_pass(tmp_python_project, monkeypatch):
    monkeypatch.setattr("ici.engines.type_check.shutil.which", lambda _name: None)
    result = TypeCheckEngine(
        tmp_python_project,
        {"engines": {"type": {"required": True, "mypy_required": True}}},
    ).run()
    assert result.status == EngineStatus.ERROR
```

- [x] **Step 2: 새 테스트 실패 확인**

Run: `uv run --python 3.10 pytest tests/test_lint_engine.py tests/test_type_engine.py -v`

Expected: 비정상 도구 출력이 0 violations PASS로 처리되어 FAIL.

- [x] **Step 3: 도구 종료 코드 분기와 증거 기록 구현**

ruff JSON 파싱 실패와 returncode 2 이상은 ERROR, returncode 1은 발견 사항 FAIL로 구분한다.
mypy도 정상 분석 오류와 도구 크래시/잘못된 옵션을 stderr와 종료 코드로 구분한다. C++ type
검증을 수행하지 않았으면 summary에 C++ 지원을 주장하지 않고 해당 세부 검증을 SKIP으로 남긴다.

- [x] **Step 4: lint/type 및 CLI 테스트 실행**

Run: `uv run --python 3.10 pytest tests/test_lint_engine.py tests/test_type_engine.py tests/test_cli.py -v`

Expected: PASS.

- [x] **Step 5: 문서와 변경 이력 갱신 후 커밋**

```bash
git add src/ici/engines/lint.py src/ici/engines/type_check.py \
  tests/test_lint_engine.py tests/test_type_engine.py docs/engine-reference.md CHANGELOG.md
git commit -m "fix(engine): report lint and type tool failures"
```

### Task 7: sanitize, dead, exception 엔진의 미구현 경로 제거

**Files:**
- Modify: `src/ici/engines/sanitize.py`
- Modify: `src/ici/engines/dead.py`
- Modify: `src/ici/engines/exception.py`
- Create: `tests/test_sanitize_engine.py`
- Create: `tests/test_dead_engine.py`
- Create: `tests/test_exception_engine.py`
- Modify: `docs/engine-reference.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `_check_python_resource_warnings()`
- Produces: private function reference 분석 결과
- Produces: Python `raise caught_exception` 탐지 결과
- Consumes: Task 5의 Target Python 선택 방식

- [x] **Step 1: 현재 stub을 드러내는 테스트 작성**

```python
def test_dead_engine_reports_unreferenced_private_function(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("def _unused():\n    return 1\n", encoding="utf-8")
    result = DeadCodeEngine(tmp_path).run()
    assert any(t.target_name == "_unused()" for t in result.targets)


def test_exception_engine_reports_lost_traceback(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        "try:\n    work()\nexcept Exception as exc:\n    raise exc\n", encoding="utf-8"
    )
    result = ExceptionSafetyEngine(tmp_path).run()
    assert any(t.target_name == "LostTraceback" for t in result.targets)
```

sanitize 테스트는 `ResourceWarning`을 발생시키는 작은 pytest fixture를 만들고
`python -W error::ResourceWarning -m pytest` 경로가 실행되는지 검증한다.

- [x] **Step 2: 세 엔진의 테스트가 실패하는지 확인**

Run: `uv run --python 3.10 pytest tests/test_sanitize_engine.py tests/test_dead_engine.py tests/test_exception_engine.py -v`

Expected: stub `pass`와 항상 false인 Python sanitize 때문에 FAIL.

- [x] **Step 3: Python dead/exception 분석 최소 구현**

dead는 private module-level function 정의와 `ast.Call`/`ast.Name` 참조를 별도 집합으로 수집하고,
데코레이터로 등록된 함수, `__all__`, protocol callback 이름은 제외한다. exception은 각
`ExceptHandler.name`과 그 body 안의 `ast.Raise`를 비교하여 `raise exc`를 WARN으로 기록한다.

- [x] **Step 4: Python resource warning 검증 구현**

```python
argv = [
    *self._resolve_python(),
    "-W",
    "error::ResourceWarning",
    "-m",
    "pytest",
    "-o",
    "addopts=",
    "tests",
]
```

테스트가 없거나 pytest가 없으면 required 정책에 따라 ERROR 또는 SKIP을 반환한다. C++ sanitizer
컴파일 실패를 더 이상 무시하지 않고 컴파일 단계 ERROR target으로 남긴다.

- [x] **Step 5: 세 엔진 테스트와 전체 회귀 테스트 실행**

Run: `uv run --python 3.10 pytest tests/test_sanitize_engine.py tests/test_dead_engine.py tests/test_exception_engine.py -v`

Expected: PASS.

- [x] **Step 6: 문서와 변경 이력 갱신 후 커밋**

```bash
git add src/ici/engines/sanitize.py src/ici/engines/dead.py src/ici/engines/exception.py \
  tests/test_sanitize_engine.py tests/test_dead_engine.py tests/test_exception_engine.py \
  docs/engine-reference.md CHANGELOG.md
git commit -m "fix(engine): replace incomplete safety checks with evidence"
```

### Task 8: build 엔진 메타데이터와 산출물 안전성

**Files:**
- Modify: `src/ici/engines/build.py`
- Modify: `src/ici/config_schema.py`
- Create: `tests/test_build_engine.py`
- Modify: `tests/test_config.py`
- Modify: `docs/user-guide.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: Task 3의 안전한 metadata와 project path 함수
- Produces: build 단계별 `ToolEvidence`와 실제 산출물 target

- [x] **Step 1: 무산출물 PASS, 경로 이탈, 잘못된 entrypoint 테스트 작성**

```python
def test_build_fails_when_project_produces_no_artifact(tmp_path):
    (tmp_path / "src").mkdir()
    result = BuildEngine(tmp_path).run()
    assert result.status == EngineStatus.FAIL
    assert "no artifact" in result.summary.lower()


def test_build_rejects_unsafe_project_name(tmp_path):
    (tmp_path / "ici.toml").write_text("name = '../escape'\ntype = 'python'\n", encoding="utf-8")
    result = BuildEngine(tmp_path).run()
    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
```

- [x] **Step 2: 테스트 실패 확인**

Run: `uv run --python 3.10 pytest tests/test_build_engine.py -v`

Expected: 빈 build가 env script만 생성하고 PASS하여 FAIL. metadata/설정 오류는
`pytest.raises`가 아니라 `EngineResult.ERROR`/`EvidenceState.NOT_RUN`으로 정규화된다.

- [x] **Step 3: Python entrypoint와 산출물 검증 구현**

top-level `[build.python] entrypoint="pkg.cli:main"` 또는
`pyproject.toml [project.scripts]`만 launcher entrypoint로 사용한다. 첫 번째 `.py` 파일을
임의 entrypoint로 선택하지 않는다. 모든 configured source directory의 non-symlink `.py`를
library로 복사하고 source tree에는 `compileall`/`.pyc`를 만들지 않는다. 산출된 launcher,
library, C++ binary 중 하나도 없으면 FAIL하며 env script만으로는 PASS하지 않는다.

- [x] **Step 4: C++ generic build의 허용 범위 제한**

main translation unit이 정확히 하나인 단순 프로젝트만 direct `g++` build를 허용한다. CMakeLists,
`.pro`, 다중 main 또는 링크 대상이 여러 개인 프로젝트는 실제 build adapter가 필요하다는 ERROR를
반환한다. 신규 어댑터는 두 번째 계획에서 추가한다.

- [x] **Step 5: build 테스트 실행 후 커밋**

Run: `uv run --python 3.10 pytest tests/test_build_engine.py tests/test_config.py tests/test_project_metadata.py -v`

Expected: PASS.

```bash
git add src/ici/engines/build.py src/ici/config_schema.py tests/test_build_engine.py \
  tests/test_config.py docs/user-guide.md CHANGELOG.md
git commit -m "fix(build): validate metadata and produced artifacts"
```

### Task 9: 리포터 직렬화, HTML 안전성, CLI 종료 코드 통일

**Files:**
- Modify: `src/ici/reporters/json_rep.py`
- Modify: `src/ici/reporters/console.py`
- Modify: `src/ici/reporters/markdown.py`
- Modify: `src/ici/reporters/html.py`
- Modify: `src/ici/__main__.py`
- Modify: `tests/test_reporters.py`
- Modify: `tests/test_cli.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: JSON `schema_version = "ici.result/v2"`
- Produces: `exit_code_for_status(status: EngineStatus) -> int`
- Consumes: Task 1의 ERROR/SKIP/evidence/tool_evidence

- [x] **Step 1: JSON 계약, HTML 특수문자, CLI ERROR 종료 테스트 작성**

```python
def test_json_v2_contains_evidence(tmp_path):
    output = tmp_path / "report.json"
    save_json_report(sample_suite, output)
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["schema_version"] == "ici.result/v2"
    assert data["results"][0]["evidence"] == "MEASURED"


def test_html_escapes_path_used_by_javascript(tmp_path):
    target = InspectionTarget(
        file_path="src/a'b</script>.py",
        start_line=1,
        status=EngineStatus.FAIL,
    )
    html = render_suite_with_target(tmp_path, target)
    assert "</script>.py'" not in html
```

- [x] **Step 2: 현재 리포터 테스트 실패 확인**

Run: `uv run --python 3.10 pytest tests/test_reporters.py tests/test_cli.py -v`

Expected: schema/evidence 누락 또는 JavaScript 문자열 노출로 FAIL.

- [x] **Step 3: 공통 직렬화와 문맥별 escaping 구현**

JSON에는 required, evidence, tool_evidence, ERROR count를 추가한다. HTML text/attribute는
`html.escape()`, JavaScript 인수는 `json.dumps(value, ensure_ascii=False)` 결과를 사용한다.
문자열 연결로 `onclick="openLoc('...')"`를 만들지 않는다.

- [x] **Step 4: 모든 CLI 명령의 종료 코드를 공통 함수로 전환**

```python
def exit_code_for_status(status: EngineStatus) -> int:
    if status in (EngineStatus.FAIL, EngineStatus.ERROR):
        return 1
    if status == EngineStatus.SKIP:
        return 2
    return 0
```

각 command는 결과를 출력한 뒤 반환 코드가 0이 아니면 `typer.Exit(code=code)`를 발생시킨다.

- [x] **Step 5: 리포터·CLI 테스트 후 커밋**

Run: `uv run --python 3.10 pytest tests/test_reporters.py tests/test_cli.py -v`

Expected: PASS.

```bash
git add src/ici/reporters src/ici/__main__.py tests/test_reporters.py tests/test_cli.py CHANGELOG.md
git commit -m "fix(report): serialize outcomes safely and align exit codes"
```

### Task 10: CI 권한 분리, 문서 정합성, 최종 품질 게이트

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/architecture.md`
- Modify: `docs/ci-integration.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_purity.py`

**Interfaces:**
- Consumes: JSON v2 보고서와 기존 `ReportPublisher`
- Produces: PR 코드를 실행하지 않는 보고서 게시 workflow

- [x] **Step 1: CI workflow 정적 보안 테스트 작성**

`tests/test_purity.py`에 YAML을 텍스트로 읽는 다음 검사를 추가한다.

```python
def test_pr_verify_workflow_has_read_only_permissions():
    text = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    verify_job = text.split("  verify:", 1)[1].split("\n  publish-main:", 1)[0]
    assert "contents: read" in verify_job
    assert "pull-requests: write" not in verify_job
    assert "--publish" not in verify_job
```

- [x] **Step 2: 현재 workflow에서 테스트 실패 확인**

Run: `uv run --python 3.10 pytest tests/test_purity.py -v`

Expected: 현재 CI의 write 권한과 `--publish` 때문에 FAIL.

- [x] **Step 3: 검증과 게시 workflow 분리**

`ci.yml`의 기본 권한은 `contents: read`로 설정한다. `verify` job은 PR과 main push 모두에서
JSON/HTML artifact만 생성하고 `--publish`를 호출하지 않는다. 별도의 `publish-main` job은
`github.event_name == 'push' && github.ref == 'refs/heads/main'`인 경우만 실행하고 job 수준에서
`contents: write`만 받는다. PR 댓글, issue, Checks 쓰기 권한은 부여하지 않는다.

```yaml
permissions:
  contents: read

jobs:
  verify:
    permissions:
      contents: read
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.10"
      - uses: astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d # v10.0.1
      - run: uvx ruff check . && uvx ruff format --check .
      - run: uv run --python 3.10 pytest -v
      - run: ./scripts/build-pyz.sh && ./scripts/smoke.sh
      - run: dist/ici.pyz verify --report --html verify_report.html --github-summary
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        if: always()
        with:
          name: ici-verification-report
          path: |
            verify_report.html
            verify_report.json

  publish-main:
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    needs: verify
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.10"
      - uses: astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d # v10.0.1
      - run: ./scripts/build-pyz.sh
      - run: dist/ici.pyz verify --html verify_report.html --publish
        env:
          GITHUB_TOKEN: ${{ github.token }}
```

이 단계에서는 PR sticky comment 게시를 중단한다. PR 실행에 쓰기 권한을 다시 부여하는 방식으로
기능을 보존하지 않는다.

- [x] **Step 4: 현재 구현과 일치하도록 문서 수정**

`docs/architecture.md`의 존재하지 않는 `ThreadPoolExecutor` 설명을 순차 실행과 예외 격리로
수정한다. `docs/engine-reference.md`, `README.md`, `docs/ci-integration.md`에서 미구현 기능을
완료 기능으로 표현한 문구를 실제 증거 계약에 맞춘다.

- [x] **Step 5: 전체 품질 게이트 실행**

Run: `TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run --python 3.10 pytest`

Expected: 전체 PASS.

Run: `uvx ruff check .`

Expected: PASS.

Run: `uvx ruff format --check .`

Expected: PASS.

Run: `./scripts/build-pyz.sh && ./scripts/smoke.sh`

Expected: 재현 가능한 `dist/ici.pyz` 생성과 Python 3.10+ smoke PASS.

- [x] **Step 6: 최종 문서·CI 커밋**

```bash
git add .github/workflows docs README.md CHANGELOG.md tests/test_purity.py
git commit -m "fix(ci): separate verification from privileged publishing"
```

## Final Review Checklist

- [x] 필수 미실행 엔진, 테스트 0개, all-disabled가 성공하지 않는다.
- [x] 전역·프로젝트·명시 설정이 순서대로 병합되고 모든 CLI가 같은 설정을 쓴다.
- [x] timeout과 엔진 예외가 부분 결과를 남긴다.
- [x] 추정 커버리지가 실측 임계값을 통과시키지 않는다.
- [x] 도구 크래시와 실제 발견 사항이 ERROR/FAIL로 구분된다.
- [x] JSON v2, 콘솔, Markdown, HTML, CLI 종료 코드가 같은 상태를 표현한다.
- [x] PR 검증 작업에 GitHub 쓰기 토큰이 노출되지 않는다.
- [x] pytest, Ruff, pyz build, smoke 품질 게이트가 모두 통과한다.
