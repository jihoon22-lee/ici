# qmake Shadow Build Freshness Guard

## Overview

qmake의 재사용 shadow build가 이전 정적 라이브러리를 링크한 test executable과
현재 소스에서 생성된 coverage metadata를 섞을 수 있는 문제를 보강했다. 이 문서는
그 원인과 adapter의 freshness 계약을 기록한다. 이번 문서 작업은 이미 구현된
`f325e62`/`740a88e` 변경을 설명하는 것이며, 이 브랜치에서는 코드와 테스트를
수정하지 않았다.

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

구현 브랜치에서 이미 수행된 focused contract suite는 `58 passed`였고 Ruff 검사도
통과했다. 다만 이 문서 커밋 시점에는 다음 검증을 완료된 것으로 간주하지 않는다.

- Python 3.10 전체 품질 게이트, pyz 재현성 build, smoke
- GitHub PR의 전체 CI와 Merge Gate
- 새 ici adapter를 사용한 실제 DiskMap qmake test 및 coverage 검증
- toy PR의 ici HTML 생성·게시, 실제 sticky comment의 HTML 링크와 GitHub Pages
  응답/Zero-CDN 확인

실물 DiskMap에서 발견된 stale shadow 재현을 새 ici가 해소하는지는 ici 변경이 main에
반영된 뒤 toy 쪽 cross-repository 검증에서 다시 측정해야 한다. 실패하는 상태는
main이나 toy PR에 병합하지 않는다.

## Next Steps

1. 이 문서 변경을 포함한 ici PR을 열고 전체 CI와 Merge Gate가 green인지 확인한다.
2. PR 댓글과 Pages에 게시된 HTML을 다시 읽어 링크와 zero-CDN 조건을 확인한다.
3. ici 변경을 main에 병합한 뒤 candidate `ici.pyz`로 DiskMap native test와 실제
   qmake coverage를 재실행한다.
4. 결과와 양쪽 PR 링크를 toy 계획/`ICI-GAPS.md`에 기록하고, toy PR의 sticky HTML
   댓글까지 검증한 후에만 병합한다.
