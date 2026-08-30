# qmake Shadow Build Freshness Guard

## Overview

qmake의 재사용 shadow build가 이전 정적 라이브러리를 링크한 test executable과
현재 소스에서 생성된 coverage metadata를 섞을 수 있는 문제를 보강했다. 이 문서는
그 원인과 adapter의 freshness 계약을 기록한다. 이번 문서 작업은 이미 구현된
rebase 후 `1098a62`/`f692a3c` 변경을 설명하는 것이며, 이 브랜치에서는 코드와
테스트를 수정하지 않았다.

## Context

실물 DiskMap 검증에서 qmake static core가 새 timestamp로 다시 빌드됐지만, 변경되지
않은 `test_scanner`와 `test_scanner_real_safety` 실행 파일은 재사용됐다. 그 결과
실행 파일은 stale archive를 계속 사용했고, 실행으로 생성된 `.gcda` stamp
`1417858375`가 새 `.gcno` stamp `1418147347`과 맞지 않았다. gcov는 이 상태를
coverage 0%로 읽었고, 의미 있는 테스트가 있었음에도 branch coverage `60.2%`와
TEM `3.82`로 ici 검증이 실패했다. qmake의 generated Makefile이 정적 archive를
소비하는 실행 파일의 dependency를 충분히 표현하지 않을 수 있어, 증분 build의
성공 여부만으로 freshness를 보장할 수 없다.

## Changes Made

### 1. Deterministic qmake freshness step

- `src/ici/core/cmake.py`에 `qmake_clean_argv()`를 추가했다.
- qmake `configure`가 성공한 뒤 같은 shadow에서 `[make, "clean"]`을 먼저 실행한다.
- clean 실행의 argv, 작업 디렉터리, 결과를 기존 `ToolEvidence` 경계에 기록한다.
- clean이 실패하면 parallel build로 넘어가지 않고 명시적인 adapter error를 만든다.
- clean이 성공한 경우에만 기존 `make --jobs=N` build를 실행한다.
- CMake adapter의 configure/build 순서와 argv는 변경하지 않았다.

### 2. Contract already covered by implementation commits

기존 구현 커밋의 계약 테스트는 다음 경우를 고정한다.

- 기존 shadow를 재사용해도 `configure → make clean → make --jobs=N` 순서를 지킨다.
- clean 실패는 조용히 stale build로 진행하지 않는다.
- 처음 생성된 shadow도 동일한 안전한 순서를 사용한다.
- CMake build에는 qmake clean 단계가 삽입되지 않는다.

핵심 실행 순서는 다음과 같다.

```text
qmake configure
        ↓
make clean  (same shadow, recorded evidence)
        ↓ only on success
make --jobs=N
```

## Verification Results

### Local ici gates

rebase된 test/fix 커밋 `1098a62`/`f692a3c` 기준으로 다음을 완료했다.

```text
uv run --python 3.10 pytest
811 passed in 42.79s

uvx ruff check .
uvx ruff format --check .
103 files passed

./scripts/build-pyz.sh  # two consecutive builds
sha256: 8fdb816ae394e5327ffa6f6ca6ddc0efca0a45addb48975e3b8eef6412a39018
10 pure-Python distributions; no certifi

./scripts/smoke.sh
PASS: Python 3.10, artifact integrity, and Zero-CDN checks
```

### ici self verification with the candidate pyz

The candidate pyz self-verified with exit code `0` and a policy `WARN`: 12 engines,
8 pass, 4 warn, 0 fail/error/skip; embedded test `811/811`; line/function/branch
coverage `88.1%/96.7%/78.8%`; TEM `4.84`; complexity `23/732`; duplicate rate
`16.03%`; duration `127.05s`. The only cycle finding was the pre-existing
`test/test_interpreter` cycle. Its capability snapshot contained 30 tools, 21 ready,
0 incomplete, and 9 unavailable; required `ruff`, `pytest`, and `python3` were ready
and health was `READY`.

The self-verification HTML at `/tmp/ici-qmake-freshness-self.html` was checked for
Zero-CDN compliance: `3,622,941` bytes, zero external `src`/`href` references, and
the tool capability snapshot rendered.

### Real DiskMap validation with the candidate pyz

The candidate pyz was run against the rebased DiskMap branch. The result was
`Suite PASS`: 10 pass, 0 warn, 0 fail/error, 2 skip; 9/9 tests; line/function/branch
coverage `96.6%/98.0%/85.0%`; TEM `4.90`; complexity `14/101/0 issues`; duplicate
rate `2.0%`; sanitizer clean; elapsed time `85.96s`.

The capability snapshot contained 30 tools, 21 ready, 0 incomplete, and 9 unavailable.
Required `g++` was ready and health was `READY`. Both qmake test and sanitize JSON
evidence recorded successful `/usr/bin/make clean` executions. The generated HTML was
`281,264` bytes, had zero external `src`/`href` references, and rendered the capability
snapshot.

Remote CI/PR/Pages verification is still pending. In particular, this branch has not
yet established the GitHub PR's green Merge Gate, sticky comment HTML links, or Pages
HTTP/Zero-CDN evidence. The real DiskMap result is therefore a completed local
cross-repository validation, not a main-branch merge or release claim.

## Next Steps

1. 이 문서 변경을 포함한 ici PR을 열고 전체 CI와 Merge Gate가 green인지 확인한다.
2. PR 댓글과 Pages에 게시된 HTML을 다시 읽어 링크와 Zero-CDN 조건을 확인한다.
3. ici 변경을 main에 병합한 뒤 toy PR의 ici pin을 갱신하고, 동일한 DiskMap native
   test/sanitize 및 qmake coverage를 재실행한다.
4. 결과와 양쪽 PR 링크를 toy 계획/`ICI-GAPS.md`에 기록하고, toy PR의 sticky HTML
   댓글까지 검증한 후에만 병합·릴리스한다.
