# Self-quality baseline and mypy annotation cleanup

## Overview

I0-4 기준으로 `ici` 자체 품질 게이트를 현재 측정값에 맞게 보정했다. Mypy의
`annotation-unchecked` note 원인을 네 엔진 생성자의 untyped body로 한정해 동작 변경 없이
Python 3.10 호환 타입 시그니처를 적용했고, self verify를 동일 조건에서 세 번 실행해
임계값과 I1 성능 기준선을 고정했다.

## Context

`uv run --python 3.10 mypy --show-error-codes src/ici`는 변경 전 다음 네 위치에서
13개의 반복 note를 냈다.

| 위치 | 원인 | 반복 |
|---|---|---:|
| `src/ici/engines/sanitize.py:53` | `SanitizeEngine.__init__(*args, **kwargs)` | 2 |
| `src/ici/engines/exception.py:363` | `ExceptionSafetyEngine.__init__(*args, **kwargs)` | 1 |
| `src/ici/engines/dead.py:21` | `DeadCodeEngine.__init__(*args, **kwargs)` | 1 |
| `src/ici/engines/test.py:57-66` | `TestEngine.__init__(*args, **kwargs)`의 변수 annotation | 9 |

기존 저장소 정책은 측정값보다 크게 낮은 TEM `2.0`, branch `35%`, function `60%`를
사용하고 있었다. 정책을 바로 실측값으로 고정하면 실행기 변동에 취약하므로, 측정값과
충분한 여유를 둔 TEM `4.5`, branch `70%`, function `90%`를 floor로 선택했다.

## Changes Made

### 1. Typed engine constructors

Files: `src/ici/engines/sanitize.py`, `src/ici/engines/exception.py`,
`src/ici/engines/dead.py`, `src/ici/engines/test.py`

각 생성자를 `BaseEngine`과 같은 인자·반환형으로 바꿨다. `*args/**kwargs`의 호출 유연성을
새로운 동작으로 확장하지 않고, 기존의 두 인자 전달을 명시적으로 유지했다.

### 2. Calibrated self-quality policy

File: `ici.toml`

2026-08-31 최신 `origin/main@fa3ad28` 기준 세 번의 측정값을 주석에 남기고, 다음 ratchet을
세 번 연속 측정·설명 가능한 상태·실측 coverage 조건으로 제한했다.

```toml
min_tem_score = 4.5
min_branch_cov = 70.0
min_func_cov = 90.0
```

### 3. Baseline and documentation synchronization

- `docs/baselines/2026-08-31-self-quality.json`: 세 번의 self verify 결과, mypy note 진단,
  console 줄 수, duplicate group 수, floor와 ratchet margin을 기계적으로 읽을 수 있는
  `ici.self-quality-baseline/v1` 구조로 저장했다.
- `docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md`: 현재
  기준선과 I0-4 네 체크 항목을 갱신했다.
- `docs/engine-reference.md`: 저장소 dogfood floor 설명을 새 정책과 기준선 링크에 맞췄다.
- `tests/test_config.py`: 저장소 정책 floor 계약을 새 값으로 갱신했다.
- `CHANGELOG.md`: Unreleased에 note 제거와 기준선 보정을 기록했다.

## Verification Results

### Mypy and unit tests

```text
uv run --python 3.10 mypy --show-error-codes src/ici
Success: no issues found in 51 source files

uv run --python 3.10 pytest
634 passed in 25.70s
```

### Repeated self verify

Command: `./dist/ici.pyz verify --report` (the same result was also reproduced with the Python 3.10 development runner)

| Run | Exit | Tests | TEM | Line / Branch / Function | Console lines | Duplicate groups | Type |
|---:|---:|---:|---:|---|---:|---:|---|
| 1 | 0 | 634/634 | 4.78 | 85.9% / 77.9% / 95.691% | 2,276 | 237 | PASS |
| 2 | 0 | 634/634 | 4.78 | 85.9% / 77.9% / 95.691% | 2,276 | 237 | PASS |
| 3 | 0 | 634/634 | 4.78 | 85.9% / 77.9% / 95.691% | 2,276 | 237 | PASS |

The suite remains `WARN` because the pre-existing line/cycle/complexity/dup findings remain;
there were 8 PASS, 4 WARN, 0 FAIL, and 0 ERROR engines. The test gate itself passed, and all
three runs had zero `annotation-unchecked` notes. The duplicate group count is 237 after the
typed-constructor change (the pre-change measurement was 236), so the persisted baseline uses
the post-change value.

### Focused static checks

```text
uvx ruff check src/ici/engines/sanitize.py src/ici/engines/exception.py \
  src/ici/engines/dead.py src/ici/engines/test.py tests/test_config.py
All checks passed!

git diff --check
passed
```

## Next Steps

Raise one floor at a time only when a later implementation change has three consecutive runs
above the current floor plus the documented margin (TEM +0.10, branch/function +3 percentage
points), with measured coverage and no unexplained test/tool warnings. I1 may reduce console
noise and restructure duplicate findings; that work should record a new baseline rather than
silently changing this one.
