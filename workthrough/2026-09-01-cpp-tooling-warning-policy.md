# C++ diagnostic tooling 경고 정책 보정

## Overview

toy-projects BuildScope B5가 공개 ici v0.10.0을 실제 Release compilation database에 적용하면서
Qt 5와 Qt 6 모두에서 clazy 12개 translation unit 전부가 exit code 1로 실패했다. 빌드와 96개
테스트, exact compilation-context coverage는 정상이고 clang-tidy도 실행됐지만, production
`-Werror`가 clazy finding을 compiler error로 승격해 adapter가 diagnostic을 읽기 전에 process
failure로 닫힌 것이 원인이었다.

v0.10.1은 exact semantic context를 유지하면서 diagnostic-only 도구에서 warning escalation만
낮춘다. clang-tidy와 두 clazy provider가 동일한 projection을 사용하며 실제 syntax/context,
process, parser 오류에 대한 fail-closed 계약은 바꾸지 않는다.

## Changes Made

### Shared diagnostic projection

- `src/ici/engines/_cpp_tooling.py`의 `tooling_arguments`가 ici의 controlled syntax suffix를
  확인한 뒤 production warning policy를 결정적으로 변환한다.
- `-Werror`는 warning 선택을 추가하지 않으므로 제거한다.
- `-Werror=<rule>`은 해당 warning을 계속 선택하도록 `-W<rule>`로 바꾼다.
- `-pedantic-errors`와 `--pedantic-errors`는 같은 진단 집합을 warning으로 유지하도록
  `-pedantic`으로 바꾼다.
- GCC legacy `-Werror-implicit-function-declaration`은 대응하는 warning flag로 낮춘다.
- `-Wno-error`와 `-Wno-error=<rule>`, standard, define, include, ABI 및 나머지 warning flag는
  그대로 보존한다.

### Clazy provider parity

- standalone provider는 기존처럼 `--` 뒤에 shared projected arguments를 받는다.
- compiler-wrapper provider가 raw replay argv를 우회해 사용하던 경로를 제거했다. projected
  arguments 뒤에 ici가 소유한 `-Wall -Wextra -fsyntax-only <source>` suffix를 복원하므로 두
  provider의 warning policy와 read-only syntax operation이 동일하다.

### Regression coverage

- 새 shared-helper 테스트는 세 escalation 형태의 변환, `-Wno-error*`와 일반 warning 보존,
  controlled suffix shape의 fail-closed 거부를 검증한다.
- adapter 테스트는 clang-tidy, clazy standalone, clazy compiler-wrapper의 최종 argv를 각각
  검사한다.
- demotion 결과를 replay drop/reject/positive-safe 정책으로 다시 검증한다. 조작된
  `-Werror=p,-MD,...`, `-Werror=a,...`, `-Werror=l,...` 및 중첩 error form은 어떤 analyzer도
  실행하기 전에 `unsafe-tooling-warning-policy`로 거부한다.
- Linux actual-process clang-tidy와 Qt/clazy fixtures의 compilation command에 `-Werror`를
  추가했다. CI와 release workflow는 실제 도구를 필수로 설치하고 skip을 실패로 바꾸므로 이
  회귀를 mock만으로 통과시킬 수 없다.

## Verification Results

```text
uv run --python 3.10 pytest tests/test_cpp_tooling.py tests/test_clazy.py \
  tests/test_clang_tidy.py
40 passed

uv run --python 3.10 pytest
1526 passed, 4 skipped in 56.26s

uvx ruff check .
All checks passed!

uvx ruff format --check .
159 files already formatted

uv run --python 3.10 mypy src
Success: no issues found in 93 source files

./scripts/build-pyz.sh
10 distributions: py3-none-any; certifi absent; public schemas packaged

./scripts/smoke.sh
all smoke tests passed; ici 0.10.1; Python 3.10 direct execution;
packaged self-verification and HTML Zero-CDN passed
```

네 skip은 로컬에 없는 clang-tidy, clazy, clang++ actual-process 조건이다. CI/release의
`ICI_REQUIRE_STATIC_ANALYSIS_TOOLS=1` 환경에서는 skip이 허용되지 않으며, 원격 evidence는 PR과
exact-main gate가 끝난 뒤 문서에 추가한다. 로컬 release-candidate `dist/ici.pyz`는 2,199,690
bytes, SHA-256 `9ab1fa89c5d6f765c8ce349ac2614b0ca87706a0cf86b01729b01d284960a624`다. 이
파일은 tag 기반 release workflow의 trusted artifact와 구분한다.

## Design Notes

- compiler lint 자체는 production warning policy를 그대로 replay한다. 이번 projection은 finding
  수집이 목적인 clang-tidy/clazy 경계에만 적용된다.
- `/WX`는 현재 replay가 승인하는 GCC/Clang direct-driver 문법이 아니므로 이 변경의 입력 범위가
  아니다. 향후 clang-cl/MSVC driver 지원은 별도 compiler policy와 실제 Windows E2E를 갖춘 뒤
  확장해야 한다.
- nonzero, timeout, truncation, malformed output은 계속 atomic error다. warning 승격을 낮춘다고
  도구 자체의 실패를 진단 성공으로 오인하지 않는다.
- 첫 PR head `e76a9b3`은 모든 CI와 실제 도구 E2E를 통과했지만 독립 병합 전 리뷰가 projection
  뒤 생길 수 있는 compiler-subtool forwarding을 발견했다. green CI만으로 병합하지 않고
  adversarial regression을 추가해 새 head에서 전체 gate를 다시 실행한다.

## Next Steps

- [ ] PR의 actual clang-tidy/clazy E2E, Qt 5/6, self/viewer dogfood, sticky HTML/Pages를 감사한다.
- [ ] exact-main Merge Gate가 성공한 commit에 v0.10.1 tag를 만들고 9개 release asset과 checksum,
  provenance를 독립 확인한다.
- [ ] toy-projects BuildScope B5가 공개 v0.10.1을 checksum pin해 Qt 5/6 deep run을 다시 수행하고
  tool evidence, sticky comment, Zero-CDN Pages를 확인한다.
