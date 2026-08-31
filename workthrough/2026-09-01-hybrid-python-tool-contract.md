# Hybrid Python Tool Contract and v0.7.1

## Overview

BuildScope B0를 공개 ici v0.7.0으로 검증하는 과정에서 hybrid 프로젝트의 Python 도구 계약과
릴리스 checksum 소비 경로에 실제 결함이 드러났다. 우회 설정으로 toy CI를 통과시키지 않고,
ici에서 원인을 수정해 v0.7.1 패치 경계로 묶는다.

## Reproduction

- 격리된 Python 3.10 환경에 Ruff, pytest, coverage, mypy를 설치하고 해당 interpreter와
  `bin` 경로를 모두 선택했다.
- BuildScope의 source roots는 `python`, `src`, `include`였고 Python 코드는 첫 root에만 있었다.
- capability snapshot은 23 ready, required tool health READY였지만 mypy argv가
  `mypy --ignore-missing-imports include python src`여서 exit 2, type `ERROR/NOT_RUN`이 됐다.
- 공개 v0.7.0 자산의 해시는 일치했지만 `ici.pyz.sha256`은 내부 경로 `dist/ici.pyz`를
  기록해 자산 디렉터리에서 표준 `sha256sum --check`가 실패했다.

## Changes Made

- Python module capability는 프로젝트 `.venv` 또는 현재 interpreter를 선택해
  `python -m <module> --version`으로 bounded probe한다.
- type engine은 shared mypy capability를 재사용하고 실제 Python 파일을 포함하는 source
  root만 mypy 대상으로 전달한다.
- Python module 이름은 정규화된 dotted identifier만 허용해 probe argv를 shell 없이 유지한다.
- 릴리스 checksum은 `dist` 안에서 생성해 manifest filename을 `ici.pyz`로 고정한다.
- package/config version을 0.7.1로 동기화하고 회귀 테스트와 문서를 추가했다.

## Verification Plan

- focused type/toolchain/inventory/workflow tests와 Python 3.10 full pytest를 실행한다.
- Ruff check/format, focused mypy, 두 번의 재현 가능한 pyz build와 smoke를 실행한다.
- 새 pyz로 격리된 BuildScope hybrid verify를 재실행해 mypy가 `python` root만 받고 type이
  tool error 없이 끝나는지 확인한다.
- PR CI, sticky HTML 댓글, ici/viewer Pages를 독립 확인하고 exact main gate 뒤 v0.7.1을
  릴리스한다. 공개 자산에서 `sha256sum --check ici.pyz.sha256`를 직접 재검증한다.

## Local Verification Results

- focused type/toolchain/inventory/workflow 회귀 테스트 84개와 Python 3.10 전체 1,280개
  테스트가 통과했다. 전체 실행 시간은 49.47초였다.
- Ruff check와 142개 파일 format check, 변경한 두 source의 focused mypy가 통과했다.
- 현재 source의 pyz 두 빌드는 모두 2,152,615 bytes와 SHA-256
  `084a1feb9c4af18b611e38b64391dc08f3418b1d91c763ef9aa9dd83b9fdba60`을 기록했다.
  10개 배포판은 전부 `py3-none-any`이며 certifi는 포함되지 않았다.
- 새 checksum 명령의 실제 산출물은 `ici.pyz`를 기록했고 자산 디렉터리에서
  `sha256sum --check ici.pyz.sha256`가 `ici.pyz: OK`로 통과했다.
- 새 pyz를 격리된 Python 3.10 환경의 BuildScope에 투입한 결과 12 PASS/1 WARN,
  0 FAIL/ERROR/SKIP, 9/9 tests, TEM 5.00, line/function/branch 96.3/100.0/86.8%,
  compile DB 4/4 production units·13 configurations, complexity 14 PASS, 총 22.78초였다.
  pytest/coverage/mypy capability와 실제 mypy 실행은 같은 interpreter를 사용했고 mypy는
  `python` root만 받아 rc0였다. 남은 WARN은 C++ type checking 미구현 하나뿐이다.

원격 PR, Pages, exact-main gate와 v0.7.1 release evidence는 아직 pending이다.
