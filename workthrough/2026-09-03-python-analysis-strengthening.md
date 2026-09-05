# Python 분석 강화 문서화 workthrough

## Overview

이 문서는 Python 정밀 분석 slice의 사용자-facing 계약을 한 곳에 정리한다. 내장
`python_compat` runtime 검증, 보수적인 Python finding 표시 projection, 최신 엔진 profile 수를
README·사용자 가이드·엔진 레퍼런스·마스터 계획·CHANGELOG에 동기화했다. 이 문서화 작업과
구현 slice는 버전 `0.10.2`를 변경하거나 release를 승인하지 않는다.

## Context

이번 branch에는 다음 구현이 추가·강화되어 있었다.

- `python_compat`가 Python source 프로젝트의 runtime, metadata, syntax/API floor를 검사한다.
- `interpreters = []`는 `sys.executable`을 required runtime으로 선택하고, 명시 interpreter는
  `required_interpreters`에 있을 때만 required가 된다.
- `-VV`와 `-B -m compileall -q -f`는 직접 실행되고, import smoke는 top-level side effect를
  고려해 `imports`에 명시한 모듈만 opt-in으로 실행된다.
- console·HTML·Markdown은 precise overlapping source region과 trusted semantic context가 있는
  경우에만 canonical Python rule group을 표시한다. JSON과 baseline은 producer별 원본 finding을
  유지한다.

## Changes Made

### 1. Profile와 engine inventory

README, `docs/engine-reference.md`, `docs/user-guide.md`, 마스터 계획에 현재 descriptor 수를
동기화했다.

| Profile | 내장 engine 수 |
|---|---:|
| `fast` | 12 |
| `standard` | 14 |
| `deep` | 16 |

`python_compat`가 세 profile에 포함된다는 점과 total 16을 현재 사용자-facing 설명에 추가했다.
역사적 release/self-verify 수치는 당시 evidence로서 보존했다.

### 2. Python runtime compatibility contract

`README.md`, `docs/engine-reference.md`, `docs/user-guide.md`, `CHANGELOG.md`에 다음을
문서화했다.

```toml
[engines.python_compat]
interpreters = []           # current ici interpreter; required
required_interpreters = []  # configured required subset
imports = []                # explicit import-smoke opt-in
target_version = ""         # infer from requires-python when empty
```

- 실제 executable마다 `-VV`를 호출해 version을 확인한다.
- `-B -m compileall -q -f`와 임시 `PYTHONPYCACHEPREFIX`로 source를 compile한다.
- `pyproject.toml`의 PEP 440 `project.requires-python`을 runtime version과 비교한다.
- `target_version` 또는 metadata에서 추론한 floor에 syntax `feature_version`과 bounded
  standard-library API inventory를 적용한다.
- floor violation은 precise 1-indexed line/column target으로 남긴다.
- 명시 import만 `-I -B` contained subprocess에서 실행한다. 자동 발견 import는 metadata로만
  남기며 top-level code 실행 경계를 문서에 명시했다.
- 성공 결과는 `MEASURED`이고 각 command의 path/version/argv/return code/timeout/truncation을
  `ToolEvidence`에 보존한다. optional unavailable는 WARN, required unavailable는 ERROR/NOT_RUN,
  incompatibility는 required policy에서 FAIL이다.
- 외부 interpreter가 독립적으로 교체될 수 있으므로 결과 cache key/entry 생성과 재사용을
  비활성화한다.

### 3. Display-only canonical Python grouping

세 display reporter가 공유하는 projection의 보수적 경계를 문서화했다. merge에는 같은
canonical project-relative Python path, 양쪽의 `end_line`·`start_column`·`end_column`을 포함한
1-indexed precise region, 실제 overlap이 모두 필요하다. line-only/ambiguous/adjacent/different
path/unknown rule은 merge하지 않으며, broad Ruff alias는 trusted semantic context가 있어야
native AST rule과 합쳐진다. 표시 그룹은 original count, producer count, engine/rule/tool version
provenance를 보여 준다.

projection은 `EngineResult`를 수정하지 않는다. JSON `targets`/`findings`와 baseline inventory
및 delta는 원래 producer finding, fingerprint, precise location, tool identity를 모두 보존한다.
console은 cap, Markdown은 bounded table, HTML은 전체 display projection을 사용한다.

### 4. I5 plan/checklist synchronization

마스터 계획에서 I5-2의 cross-producer dedup 항목과 I5-3 runtime compatibility 항목을
완료(`[x]`)로 표시했다. envlens는 feature candidate를 Python 3.10.21과 최신 설치 interpreter인
Python 3.14.7에서 각각 실행해 모두 통과했다. I5 checkpoint 자체는 완료로 표시하지 않았다.
초기 문제 정의의 보안 표현은 현재 bounded AST 구현과 모순되지 않도록 갱신했으며, historical
CHANGELOG 문구도 AST 규칙으로 정리했다.

## Verification Results

### Focused regression tests

```text
$ uv run --python 3.10 pytest tests/test_python_compat.py tests/test_python_rule_identity.py tests/test_console_issues_first.py
49 passed, 1 skipped in 0.72s
```

### Documentation checks

```text
$ git diff --check -- README.md docs/engine-reference.md docs/user-guide.md CHANGELOG.md \
    docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md
PASS (no whitespace errors)
```

### Full repository and cross-runtime checks

```text
$ uv run --python 3.10 pytest -q
PASS
$ uvx ruff check . && uvx ruff format --check .
PASS (216 files formatted)
$ ./scripts/build-pyz.sh
PASS (ici.pyz built from pure-Python wheels)
$ python3.10 dist/ici.pyz python-compat  # cwd: toy-projects/envlens
PASS
$ python3.14 dist/ici.pyz python-compat  # cwd: toy-projects/envlens
PASS
```

PR/CI and release verification remain outside this local implementation record.

## Next Steps

- Complete I5-4 packaging/environment integrity work and only then evaluate the I5 checkpoint.
- Keep this branch's docs changes uncommitted for the parent workflow as requested.
