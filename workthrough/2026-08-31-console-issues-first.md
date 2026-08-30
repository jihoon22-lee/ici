# I1-4 issues-first 콘솔 구현 및 로컬 검증

## Overview

I1-4 issues-first console과 공통 grouping을 구현했다. `ici verify`는 engine별 표시 상한과
group-by를 적용하면서도 v3 finding inventory와 baseline occurrence를 변경하지 않으며,
duplicate는 실행 중 clone group 관계와 같은 파일의 겹치는 region만 표시상 합친다. 이 기록은
로컬 구현·품질 검증 결과와 PR·CI 경계를 함께 남긴다. 최종 안정 self verify는 완료됐고,
PR/CI Merge Gate의 URL과 실행 근거만 `TODO_AFTER_PR_AND_CI`로 추적한다.

## Context

기존 console은 엔진별 target과 duplicate clone occurrence를 모두 펼쳐 self verify 결과의
원인보다 출력이 커졌다. 같은 clone group에서 겹치는 source region도 반복 표시됐고, 좁은
터미널에서는 summary table이 읽기 어려웠다.

I1-1의 v3 finding과 I1-3의 baseline은 전체 inventory와 occurrence 단위 identity를 보존해야
한다. 따라서 grouping과 cap은 결과 객체를 변경하지 않는 console-only projection으로 분리했고,
HTML `Issues` 탭은 native v3 finding inventory를 소비하도록 보강했다.

## Decisions

### CLI 계약

- `--verbose`는 `ici verify` 전용 상세 표시 모드이며 console cap을 해제한다.
- `--max-findings N`은 engine별 console display group 상한이다. 기본값은 engine별 5개이며
  `0`은 summary-only다.
- `--group-by`는 `engine`, `severity`, `category`, `file`, `rule`만 허용한다.

### 표시와 데이터 보존

- cap/grouping은 terminal display에만 적용한다.
- JSON·HTML·Markdown과 baseline의 원본 inventory, targets, findings, delta occurrences는
  모두 보존한다.
- duplicate는 같은 실행의 같은 clone group 안에서 같은 파일의 inclusive line interval이
  겹칠 때만 union한다. 인접 interval과 다른 clone group은 합치지 않는다.
- merged display group에는 원본 occurrence와 fingerprint를 유지하고, clone group 숫자 ID는
  baseline identity가 아닌 실행 중 표시 정보로만 사용한다.
- 정렬과 80-column 줄바꿈은 deterministic하게 처리한다.

## Changes Made

### 1. Verify CLI와 옵션 전달

Files: `src/ici/__main__.py`, `src/ici/engines/verify.py`

- `verify`에 `--verbose`, `--max-findings`, `--group-by`를 추가했다.
- `--max-findings`는 0 이상으로 제한한다.
- console 옵션을 orchestrator에서 reporter로 전달하되 JSON/HTML/Markdown 실행 경로에는
  console cap을 전달하지 않는다.

### 2. 공통 console projection

File: `src/ici/reporters/issue_view.py`

- immutable `ConsoleOptions`, `IssueGroup`, `IssueLocation`, `IssueSelection` 모델을 추가했다.
- native v3 finding과 legacy target adapter를 함께 읽어 actionable finding을 선택한다.
- 동일 fingerprint와 같은 파일의 겹치는 region만 transitively union하고, related location도
  표시 위치에 포함한다.
- duplicate는 `extra.clone_groups`의 실행 내 관계를 사용해 cross-file clone을 하나의 표시
  group으로 구성하되, file별 interval을 별도로 유지한다.
- malformed display record는 구조화 결과를 손상시키지 않고 표시에서 제외한다.
- engine별 cap, verbose 전체 표시, deterministic sort, hidden count를 계산한다.

### 3. Rich console 개선

File: `src/ici/reporters/console.py`

- summary table에 issue 수를 추가하고 좁은 터미널에서 duration column을 조정한다.
- issues-first drill-down에 display group, severity, rule, location, 대표 snippet을 표시한다.
- 전체 actionable finding, display group, represented/hidden 수와 재실행 명령을 출력한다.
- `engine`, `severity`, `category`, `file`, `rule` bucket별 panel을 지원한다.
- baseline panel도 주입된 console 객체를 사용해 80-column/golden 테스트와 함께 동작하게 했다.

### 4. HTML Issues inventory

Files: `src/ici/reporters/html/report.py`, `src/ici/reporters/html/sections/issues.py`,
`src/ici/reporters/html/utils.py`

- Issues 탭을 native v3 finding inventory 기반으로 보강했다.
- console cap과 무관하게 전체 issue inventory와 안전한 location control을 유지한다.
- 기존 zero-CDN 및 redaction/escaping 경계를 유지한다.

### 5. 테스트

Files: `tests/test_cli.py`, `tests/test_console_issues_first.py`,
`tests/test_verify_orchestrator.py`

- CLI 옵션·기본값·검증 경로와 orchestrator 전달을 검증했다.
- engine별 cap, `0` summary-only, verbose cap 해제, group-by 5종을 검증했다.
- native v3 finding, fingerprint overlap, clone group overlap/cross-file 보존,
  deterministic ordering, malformed display data를 검증했다.
- console selection 전후 suite/JSON/HTML 원본 inventory 불변성을 검증했다.
- 80-column Rich output이 character-by-character vertical wrap을 만들지 않는지 검증했다.

## Code Examples

```bash
# engine별 최대 5 display group, file bucket
ici verify --group-by file

# engine summary만 표시
ici verify --max-findings 0

# cap을 해제한 상세 console
ici verify --verbose --group-by severity
```

console projection의 데이터 경계는 다음과 같다.

```text
EngineResult / v3 findings / baseline inventory
                    │
                    └─ immutable console cap + grouping projection
                                      │
                                      └─ terminal display

JSON / HTML / Markdown / baseline ───── full original inventory
```

## Verification Results

### Local implementation evidence

```text
implementation/test commits: 814679c + d80a027
dist/ici.pyz: exit 0; suite WARN
self verify output: 144 lines / 15,288 bytes
HTML artifact: 3,381,263 bytes
Python 3.10 current full suite: 756/756 passed
focused console tests: 16 passed
ruff check / ruff format --check: passed
build-pyz: pure-Python 10 distributions, no certifi, 2.0 MiB artifact
smoke: all checks passed
coverage: line 87.7% / function 96.6% / branch 78.6%
TEM: 4.83
engines: Pass 8 / Warn 4 / Fail 0 / Error 0 / Skip 0
complexity: max 23, 64 issues
duplicate: 16.2%, 338 groups, 1,006 actionable occurrences
```

기본 cap은 engine별 5 display group, `--max-findings 0`은 summary-only, `--verbose`는 cap
해제로 동작했으며 `engine|severity|category|file|rule` 5종을 확인했다.
최종 안정 self verify 출력에 내장된 test engine 수치는 749/749였으며, 이는 이후 추가 테스트
커밋 전의 안정 실행 산출물이다. 현재 전체 품질 게이트 수치는 위 756/756으로 별도 기록한다.

### Final stable self measurement

최종 안정 self verify는 terminal output 144 lines, actionable 1,088, visible 21/420 display
groups, represented 34, hidden 1,054 findings/399 groups였다. HTML에는 clone group card 정확히
338개와 issue engine row 합계 1,088개가 보존됐고 external script/stylesheet reference는
0개였다.

초기 self 측정은 output 152 lines, actionable 1,086, visible 23/422 groups, represented 36,
hidden 1,050 findings/399 groups였고 HTML clone group card 338개도 확인했다. 해당 초기 suite의
lint FAIL은 에이전트 파일 작성 경합에 따른 참고 기록이며, 최종 품질 판정에는 위 안정 측정만
사용한다.

```text
final self verify: COMPLETE (local)
I1-4 implementation and local verification: COMPLETE
I1 overall checkpoint: COMPLETE (local)
PR/CI Merge Gate: TODO_AFTER_PR_AND_CI
```

## Next Steps

- PR/CI Merge Gate를 수행하고 실제 URL·실행 결과를 `TODO_AFTER_PR_AND_CI` 위치에 반영한다.
- I2 shared context와 toy T1 reliability 작업으로 이어가며, 이 문서의 로컬 측정값은 기준
  증거로 보존한다.
