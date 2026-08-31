# `lint` 설정 소스 범위 준수

## Overview

`LintEngine`이 프로젝트의 선언된 Python 소스 범위만 검사하도록 보강했다. 기존에는
프로젝트 루트 어디에든 `.py` 파일이 있으면 Python lint가 활성화되거나 Ruff가 `.`을
검사했기 때문에, C++ 프로젝트의 보조 스크립트와 Python 프로젝트의 제외 디렉터리가
검증 결과에 섞일 수 있었다.

## Context

`project.source_dirs`는 공통 프로젝트 모델이 제공하는 분석 범위의 계약이다. 그러나
lint 엔진은 이 모델의 inventory 대신 `project_root.rglob("*.py")`와 Ruff의 `.` 경로를
사용했다. 그 결과 다음 두 가지 문제가 생겼다.

- C++ 프로젝트에 범위 밖의 잘못된 Python 파일이 있으면 Python AST 폴백이 실행됐다.
- Python 프로젝트에서도 설정된 `src/` 밖의 Python 파일까지 Ruff check/format 대상이 됐다.

이는 엔진 간 대상 집합이 달라지는 문제이며, 정상적으로 검사하지 않아야 할 파일의
오류가 PR 판정을 바꾸거나 보고서에 포함될 수 있다.

## Changes Made

### 1. 공통 source inventory로 Python lint 활성화 제어

- 파일: `src/ici/engines/lint.py`
- `BaseEngine.project_python_sources()`가 반환하는 정렬된 `Path` 목록을 한 번 수집한다.
- 목록이 비어 있으면 Python lint를 실행하지 않는다.
- Python과 C++ 모두 적용 대상이 없을 때는 `LintScope` target을 `SKIP`으로 기록하고,
  evidence는 `NOT_APPLICABLE`로 남겨 도구 미설치(`NOT_RUN`)와 구별한다.

### 2. Ruff와 AST 폴백을 동일한 범위에 고정

- Ruff check/format은 `.` 대신 source inventory의 project-relative Python 경로를
  명시적인 argv로 전달한다.
- Ruff가 없을 때의 AST syntax fallback도 같은 파일 목록만 파싱한다.
- `extra.python_files_parsed`는 실제 선택된 파일 수를 계속 기록하므로, 검사한 범위를
  보고서에서 확인할 수 있다.
- 파일: `tests/test_lint_engine.py`에 C++ 범위 밖 Python 무시, Ruff 경로 제한,
  AST 파싱 수 회귀 검증을 추가했다.

핵심 실행 형태는 다음과 같다.

```text
project.source_dirs = ["src"]
        ↓
project_python_sources()
        ↓
ruff check src/sample_pkg/__init__.py src/sample_pkg/core.py ...
ruff format --check src/sample_pkg/__init__.py src/sample_pkg/core.py ...
AST parse: 동일한 목록만
```

## Verification Results

소스 범위 회귀 테스트를 Python 3.10으로 실행했다.

```text
$ uv run --python 3.10 pytest tests/test_lint_engine.py
60 passed in 0.26s
```

검증한 계약은 다음과 같다.

- C++ `src/`만 선언된 프로젝트의 `benchmarks/out.py`는 Python lint를 활성화하지 않는다.
- Ruff check/format argv에는 `.`이나 절대 경로가 없고, 선언된 `src/` 파일만 포함된다.
- 범위 밖의 잘못된 Python syntax는 AST fallback 결과를 만들지 않는다.
- 선택된 Python 파일 수가 `extra.python_files_parsed`에 반영된다.

구현·회귀 테스트 커밋은 각각 `464a626`과 `e5786af`이며, 이 문서는 해당 변경을
설명하는 문서 전용 후속 커밋으로 작성했다. 전체 저장소 품질 게이트와 원격 PR 증거는
이 문서 커밋을 포함한 PR에서 다시 확인한다.

## Next Steps

- 전체 Python 3.10 테스트, Ruff, reproducible pyz build, smoke를 소스 범위 수정과 함께
  실행한다.
- PR의 ici self-dogfood 결과에서 C++/Python 범위가 의도한 대로 적용됐는지와 HTML
  리포트의 target/evidence를 확인한다.
