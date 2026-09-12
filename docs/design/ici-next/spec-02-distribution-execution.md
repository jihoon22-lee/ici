# SPEC-02 — 설치에서 실행까지의 공통 계약

| | |
|---|---|
|상태|**채택된 목표 계약 (adopted target contract)**. 신규 설계이며 실제 지원 버전은 시험으로 확정한다.|
|원문 이슈|[SPEC-02 #194](https://github.com/jihoon22-lee/ici/issues/194)|
|상위|[roadmap.md](roadmap.md) / [architecture.md](architecture.md) / 설정 [spec-01](spec-01-workspace-config-cli.md)|
|요구사항|R01~R04, R07~R08, R10~R11, R14|
|주 담당 WP|[#202](https://github.com/jihoon22-lee/ici/issues/202) (bundle), [#204](https://github.com/jihoon22-lee/ici/issues/204) (resolver), [#205](https://github.com/jihoon22-lee/ici/issues/205) (executor), [#208](https://github.com/jihoon22-lee/ici/issues/208) (DAG), [#209](https://github.com/jihoon22-lee/ici/issues/209) (cache)|

## 1. 배포 계약

```
ici-<version>-linux-x86_64/
  bin/ici
  runtime/python/
  app/
  tools/python-static/
  tools/cpp-static/
  templates/
  schemas/
  licenses/
  manifest.json
```

- 표준 bundle은 ici 본체와 기본 정적 분석 도구를 준비한다. 프로젝트 GCC/Qt/Python을 교체하거나
  공용 site-packages를 수정하지 않는다.
- `verify`/`init`/`doctor`/`plan`/`report` 실행 중 download/pip/uv 설치·업데이트를 호출하지
  않는다. 기존 project 테스트 의존성과 명시 external analyzer 설치는 정상 프로젝트 준비로
  허용한다.
- install directory는 읽기 전용이어도 된다. run output은 기본 workspace `.ici/runs/<run_id>`,
  공유 cache는 사용자 cache root, lock/tmp는 별도 private path를 사용한다. 모두 명시 경로로
  변경 가능하다.
- Python `.venv` 디렉터리를 그대로 복사하는 방식으로 이동 가능성을 주장하지 않는다. 다른 절대
  경로·공백 경로·symlink launcher·일반 사용자·clean HOME에서 실행 시험한다.
- manifest: bundle/core/schema 버전, 고정 source commit, runtime artifact id/digest, tool
  version/digest, OS/architecture/libc/CPU requirements, 라이선스·출처·포함파일 목록,
  build input lock digest.
- 다운로드와 재배포 라이선스 검토는 제작 단계에서 수행한다. 빌드 기록과 checksum을 보존한다.
  checksum만으로 공급자 신뢰를 증명했다고 하지 않는다.
- CPython 3.13/PBS GNU Linux는 시험 후보다. runtime과 LLVM/Clang/clazy 등 네이티브 패키지의 실제
  RHEL 8.10 호환을 함께 확인한다. 개발용 Ubuntu/WSL 통과를 RHEL 확인으로 대체하지 않는다.
- 기존 pyz는 전환 기간 안정 배포물로 보존한다. 새 bundle과 같은 기능의 두 런타임 경로를 영구
  유지한다는 약속은 하지 않는다.

→ [ADR-0002 전용 런타임 bundle](adr/0002-standalone-runtime-bundle.md)

> **현행 대응물 (측정됨)**: 지금의 배포물은 `dist/ici.pyz` 단일 파일 + `scripts/launcher.sh`
> polyglot 프리앰블이다. `scripts/build-pyz.sh:38`이 `EXPECTED_UV_VERSION="0.12.5"`를 정확히
> 고정하는데, 이것이 §1의 "build input lock digest"에 가장 가까운 현행 자산이다. 이번 측정
> 환경의 uv는 0.8.17이어서 pyz 빌드·재현성은 **검증하지 못했다**.
> → [inventory/baseline-measurements.md §3.2](inventory/baseline-measurements.md)

## 2. 런처와 환경 보존

런처 진입 환경을 먼저 보존한 후 ici core의 import/library 환경을 격리한다. 프로젝트 child에는
원래 환경과 명시된 task overlay만 적용한다.
`PYTHONPATH`/`PYTHONHOME`/`VIRTUAL_ENV`/`PATH`/`LD_LIBRARY_PATH`/Qt 관련 변수에 대한 전후 실측
표를 작성한다.

- core는 프로젝트 패키지를 import하지 않는다. 프로젝트 테스트는 실제 프로젝트 패키지를 사용한다.
- bundle bin/lib 경로를 모든 child의 검색 경로 앞에 넣지 않는다. 도구별 절대 실행 경로를 사용한다.
- core 환경을 깨끗하게 하면서 프로젝트 환경을 잃는 구현을 금지한다. snapshot 전달에 임시 저장이
  필요하면 private 권한·원자적 생성·실행 후 정리·보고서 제외를 검증한다.
- 전역 환경을 작업마다 수정하지 않는다. source/devenv 해석은 구현하지 않는다.
- Python `-I`는 import 격리에 사용 가능한 도구이지 동적 라이브러리와 모든 외부 코드의 sandbox가
  아니다.

> **현행 위반 지점 (측정됨)**: `engines/test_interpreter.py:35` `_build_python_test_env`가
> `os.environ.copy()` 후 `PYTHONPATH`를 앞쪽에 주입하고, `:39`가 WSL에서 `TMPDIR`/`TMP`/`TEMP`를
> `/tmp`로 강제한다. `core/env.py:11` `PYTHON_CANDIDATES`와 `scripts/launcher.sh:5`가 시스템
> 인터프리터 6개 후보를 순차 탐색한다(AGENTS가 순서 일치를 불변식으로 강제).
> → [inventory/execution-flow.md §4](inventory/execution-flow.md) 1·6·7·9번 행

## 3. ToolResolver

|역할|선택 우선순위|
|---|---|
|프로젝트 Python|명시 executable → 상속 환경의 `python`, 없으면 `python3`. 선택된 실행기가 버전 조건과 충돌하면 조용히 다음 실행기로 바꾸지 말고 진단|
|qmake|명시 경로/명령 → 상속 PATH의 qmake. 여러 빌드 정의는 설정으로 선택|
|실제 compiler|compile DB/빌드 정의의 실제 invocation 기준. PATH의 최신 GCC로 덮어쓰지 않음|
|정적 analyzer|설정된 bundle provider 기본; external 선택 시 명시 command/path. 실패 시 다른 provider로 자동 대체 금지|
|pytest/coverage|선택된 프로젝트 Python으로 모듈 probe·실행. core Python으로 fallback 금지|

`ResolvedTool`은 `launch_path`와 identity용 `real_path`를 구분한다. **venv Python symlink를
realpath로 치환하여 실행하면 환경이 달라질 수 있으므로 실행 경로를 보존한다.** version probe는
timeout/출력 제한을 갖고 cache한다. 설정값, PATH 선택, 충돌 후보, 선택 이유, tested/untested
capability를 doctor에 표시한다. 전체 모듈 목록과 환경 원문은 자동 공개하지 않는다.

> **현행 위반 지점 (측정됨)**: `engines/test_interpreter.py:13` `_resolve_python`이 `.venv` 후보
> 실패 시 `sys.executable`(= ici core 인터프리터)로 fallback한다. 표의 마지막 행이 금지하는
> 동작이다. Ruff·mypy는 `core/env.py:64` `find_project_executable`로 프로젝트 `.venv`에서
> 찾으므로 R02가 줄이려는 반복 설치 의존을 만든다.

## 4. TaskSpec

필수 필드: `id`, `kind(probe/prepare/analyze/test/collect)`, `analysis_unit_ids`, `provider`,
`argv[]`, `cwd`, `env_ref/overlay`, `input_refs[]`, `output_specs[]`, `depends_on[]`,
`timeout_seconds`, `output_limit`, `resource_keys[]`, `cache_policy`, `failure_policy`.

- argv는 배열이며 기본 `shell=False`. 사용자 명시 shell command는 별도 opt-in 실행으로 기록하고
  환경 초기화 엔진으로 확대하지 않는다.
- 모든 mutable step은 plan에서 이름·입력·출력·영향 build dir를 보여준다. prepare가 없거나
  승인되지 않은 경우 BLOCKED.
- build unit key에는 variant·build definition·실제 tool/environment identity를 포함한다. 같은
  build dir 쓰기는 exclusive lock이다.
- qmake SUBDIRS 공유 prepare는 한 번 실행하고 여러 component에 검증된 manifest를 제공한다.
- 같은 provider 실행을 공유할 때 도구 digest·argv·cwd·환경·source/rule config가 같아야 한다.
  type checker 두 개나 서로 다른 compilation variant는 합치지 않는다.
- user-configured build/test는 임의 코드 실행이다. 실행 허용과 도구 설치는 별도 책임이며
  `plan`은 실행하지 않는다.

> **현행 차이 (측정됨)**: 현재 DAG 노드는 엔진 단위(`EngineDescriptor`)이고 프로세스 단위가
> 아니다. `exec=read-only`로 선언된 엔진 중 8개가 실제로는 외부 프로세스를 실행한다
> (`cognitive`, `complexity`, `cycle`, `dead`, `lint`, `python_compat`, `type`, `binary_compat`).
> read-only는 "산출물을 변경하지 않는다"는 의미이며 "프로세스를 띄우지 않는다"가 아니다.
> → [inventory/current-engines.md §4](inventory/current-engines.md)

## 5. Scheduler·실패·취소

- READY→RUNNING→SUCCEEDED/FAILED/CANCELLED; 선행 실패는 BLOCKED, 미선택은 계획의 omitted
  scope다. 관찰 결과의 품질 위반은 task FAILED와 구분한다.
- 병렬도 상한·read-only 분석 병렬화·build resource lock을 적용한다. lock 획득 실패를 무시하고
  중복 빌드하지 않는다.
- 독립 task는 한 task 실패 후에도 계속 가능하다. 필수 실패는 최종 completeness에 반영한다.
- 취소는 process group에 전달하고 grace 후 강제 종료한다. 자식 프로세스·pipe·tmp/lock 정리를
  검증한다. SIGINT는 가능한 partial result를 남기고 exit 130.
- timeout·signal·truncated stdout·malformed result·missing output을 구별한다. parser에 필요한
  출력이 잘렸으면 빈 finding=성공으로 처리하지 않는다.
- source/input가 실행 도중 달라졌으면 mixed snapshot을 경고/미완료 처리한다. 관련 identity를
  다시 계산한다.
- 결과/manifest/cache는 임시 파일 작성 후 atomic rename. 실패한 task artifact는 성공 manifest에
  포함하지 않는다.

> 보존할 현행 자산: `core/runner.py`가 이미 bounded capture(`_BoundedCapture`),
> timeout(`_remaining`/`_wait_process`), process group 종료(`_terminate_process`),
> Windows job object(`_start_windows_job`)를 구현한다. `ProcessResult`에 `timed_out`·`truncated`
> 플래그가 있어 §5의 "timeout·truncated를 구별한다"를 이미 부분 만족한다.
> `PipelineExecutor`(`core/pipeline.py:312`)가 read-only는 ThreadPool 병렬, build는 순차로
> 실행하는 정책도 §5와 같은 방향이다.

## 6. 캐시와 재현성

key에 core/provider/parser 버전, tool digest, 관련 native config, component/analysis scope,
source·generated·external dependency identity, compile argv/cwd/response files, relevant
environment, variant를 포함한다. unknown dependency가 있으면 persistent reuse를 끄는 것이
기본이다.

- commit SHA만으로 cache key를 만들지 않는다. dirty/untracked·외부 헤더·생성 파일·tool 사용자
  설정이 결과를 바꾸는지 확인한다.
- 분석 raw observation cache와 policy evaluation을 분리한다. 임계값만 바뀌면 재판정은 가능하되
  stale verdict를 재사용하지 않는다.
- test/sanitizer/integration는 기본 cross-run cache 금지. 한 run의 검증된 테스트 evidence는
  coverage/TEM이 공유 가능하다.
- cache hit는 원래 input identity와 생성 run를 표시한다. 변조·부분 파일·허용 경로 밖 symlink는
  거절한다.
- 초기에는 correctness 우선으로 cache를 제한한다. 성능 수치를 먼저 꾸며 약속하지 않고 고정
  fixture의 cold/warm 실행을 측정해 예산을 확정한다.

> 현행: `core/cache.py`·`core/cache_identity.py`·`core/cache_codec.py`가 존재하고
> `verify.py:247`이 `project_source_digest(project)`를 캐시 키의 일부로 쓴다. 이 키가 §6이
> 요구하는 항목들(외부 헤더, 생성 파일, tool 사용자 설정 등)을 실제로 덮는지는
> **이번 조사에서 확인하지 못했다.** → [WP11 #209](https://github.com/jihoon22-lee/ici/issues/209)

## 7. 관측과 진단

각 task는 선택된 executable, 버전, 논리 input/output, redacted command, duration,
exit/signal/timeout, log 위치, 수행/누락 이유를 기록한다. doctor는
`문제 → 영향을 받는 component/check → 수정할 ici/tool 설정 → 재실행할 진단` 순서로 보여준다.
무조건 `pip install`을 답으로 제시하지 않는다.

## 8. 필수 인수 시험

- 다른 interpreter/Qt/GCC가 PATH에 여러 개 있고 프로젝트 정의가 다를 때 올바른 선택과 충돌 설명.
- 상속 site-packages 및 외부 venv, symlink interpreter, `PYTHONPATH`와 `LD_LIBRARY_PATH`가 있는
  환경에서 core/project 양방향 분리.
- read-only bundle, 다른 install path, clean HOME, offline, tool 누락/구버전/잘린 출력.
- 동일 build dir 병렬 실행·다른 variant·부분 실패·취소·process tree 정리·중간 artifact 재사용 금지.
- 같은 입력의 중복 provider 실행은 1회, 다른 설정은 별도 실행.
- source/외부 헤더/config/tool 변경으로 cache invalidation, unknown dependency persistent cache
  차단.

## 완료

- [ ] manifest/schema와 실제 bundle smoke test가 일치한다.
- [ ] 공통 runner/resolver가 기존 엔진별 환경 탐색을 대체한다.
      (현행 탐색 지점 10개 목록 → [inventory/execution-flow.md §4](inventory/execution-flow.md))
- [ ] 런타임/도구/프로젝트 지원표에 실제 측정 근거와 미확인 항목이 구분된다.
      (현재 측정 상태 → [inventory/baseline-measurements.md §4](inventory/baseline-measurements.md))
- [ ] 안전한 실패·복구·캐시 무효화 테스트가 연결된다.

공식 참고: [관리형 CPython/PBS](https://docs.astral.sh/uv/concepts/python-versions/),
[Python 실행 옵션](https://docs.python.org/3/using/cmdline.html),
[Compilation database](https://clang.llvm.org/docs/JSONCompilationDatabase.html).
