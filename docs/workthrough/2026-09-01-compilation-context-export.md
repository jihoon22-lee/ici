# Standalone compilation-context export v1

## Overview

`ici export-compilation-context`는 검증 엔진 전체를 실행하지 않고, 측정된
`compile_commands.json`을 downstream 도구가 소비할 수 있는 결정론적 JSON으로 내보낸다.
이 문서는 현재 구현의 의도와 보안 경계를 기록한다. 공개 계약은
`ici.compilation-export/v1`이며, 출력에는 compiler 명령 전체나 호스트 비밀이 포함되지 않는다.

## Context

기존 `verify`의 `CompilationContext`는 C++ coverage와 후속 compiler-backed 분석이 공유하는
내부 snapshot이다. 별도의 export 소비자는 이 snapshot을 읽을 수 있지만, raw `argv`와 외부
checkout 경로를 그대로 받아서는 안 된다. 따라서 export는 다음 경계를 분리한다.

- 기본 호출은 프로젝트 메타데이터와 선택된 compile database를 읽기만 한다.
- database parser는 shell/compiler를 호출하지 않고, `arguments`를 `command`보다 우선한다.
- 빌드 시스템을 통해 DB를 만들 필요가 있을 때만 `--prepare`를 명시적으로 선택한다.
- 정규화·redaction·출력 writing은 서로 분리된 단계로 유지한다.

## Changes Made

### 1. CLI와 preparation 경계

구현 파일: `src/ici/__main__.py`, `src/ici/compilation_export_cli.py`,
`src/ici/core/compilation_export.py`, `src/ici/core/_compilation_export_project.py`

- `ici export-compilation-context`의 기본 출력은 `-`(stdout)이며, 성공 stdout은 JSON 한 개와
  마지막 개행만 포함한다. `--output` 파일 모드의 성공 stdout은 비어 있고 오류는 stderr로
  보낸다.
- 기본 project discovery는 루트 build descriptor와 metadata만 확인한다. subprocess, shell,
  compiler, recursive source scan을 사용하지 않고, export 호출 때문에 전역 기본 설정 파일을
  생성하지 않는다.
- `--database`는 project-relative POSIX 경로만 받는다. 절대 경로, Windows path syntax,
  root 탈출과 symlink escape는 거부한다.
- `--prepare`는 명시적으로 선택·설정한 DB와 auto-discovered DB가 모두 없을 때만 root
  CMake/qmake adapter를 호출한다. 명시 DB가 누락·손상됐으면 다른 DB로 대체하지 않고 오류를
  반환한다. CMake는 `build/ici-cmake-build`, qmake는 `build/ici-qmake-build` owned shadow를
  사용할 수 있으며, configure/build와 generated-source capture는 외부 도구 및 프로젝트 build
  상태를 바꿀 수 있다. 이 side effect는 default 경로에 포함되지 않는다.

### 2. Bounded compilation-database ingestion

구현 파일: `src/ici/core/compile_db.py`, `src/ici/core/_compile_db_commands.py`,
`src/ici/core/_compile_db_paths.py`

- DB는 최대 32 MiB와 200,000 entries로 제한한다.
- row별 `arguments`는 최대 32,768개와 총 1 MiB, DB 전체 expanded arguments는
  1,000,000개와 32 MiB, `command` 문자열은 4 MiB로 제한한다.
- response file은 project-contained regular file만 읽고, 깊이 4·파일별 4 MiB·aggregate 4 MiB와
  같은 argument bound를 적용한다.
- POSIX `shlex` 또는 Windows CRT 규칙으로만 command를 tokenize한다. duplicate JSON key,
  `NaN`/`Infinity`, 비정상 파일, path escape/symlink, malformed row와 읽는 중 변경은
  실행하거나 추측하지 않고 diagnostic 또는 거부로 처리한다.

### 3. Normalized public projection

구현 파일: `src/ici/core/compilation_export.py`,
`src/ici/core/_compilation_export_argv.py`

출력 unit에는 compiler family/name/path, language/standard, define, include/search path,
sysroot, target, output, configuration digest와 diagnostic을 남긴다. 내부 path는
project-relative POSIX로 투영하고 외부 path/sysroot는 `[external]`, credential과 안전하게
공개할 수 없는 scalar는 `***REDACTED***`로 치환한다. 외부 include의 existence는 `null`이며
raw `argv`/`command`는 공개하지 않는다.

`source_bytes_digest`는 선택된 DB 원본 bytes의 SHA-256이다. `semantic_digest`와 unit별
`configuration_digest`는 redaction 이후의 정규화된 값과 origin/generator/unity 상태를
canonical JSON으로 해시한다. unit과 JSON key는 안정적으로 정렬하며 `--pretty`는 whitespace만
추가한다.

실제 DB를 읽었다는 `evidence`는 `MEASURED`로 유지한다. 외부 또는 redacted 값, unknown
compiler, unmodeled option, 비치명 unit/context diagnostic, unity build가 있으면 해당 unit과
export의 `comparison_state`를 `inconclusive`로 표시한다. 치명적인 error-level diagnostic은
payload를 만들지 않고 exit 1로 닫는다. 이는 측정 결과를 `ESTIMATED`로 가장하지 않으면서
의미 비교의 한계를 소비자에게 알리는 방식이다.

### 4. Atomic output and packaged schema

구현 파일: `src/ici/core/_compilation_export_io.py`,
`src/ici/schemas/ici-compilation-export-v1.schema.json`

파일 출력은 대상 디렉터리의 임시 regular file에 기록하고 flush·`fsync`·atomic replace와
directory sync를 수행한다. 기존 regular file은 원자적으로 교체하며 허용된 symlink는
referent가 아니라 link 자체를 교체한다. compilation database, `ici.toml`, `dev.toml`,
`pyproject.toml`, 그 alias와 special file은 output target에서 보호한다. 출력은 32 MiB를
넘을 수 없다.

기계 계약은 draft 2020-12 JSON Schema
[`ici-compilation-export-v1.schema.json`](../../src/ici/schemas/ici-compilation-export-v1.schema.json)으로
관리한다. schema는 package data로 wheel/ZipApp에 포함되며 `scripts/build-pyz.sh`가 ZipApp
구성 전에 기존 v3 schema와 함께 존재하는지 확인한다. standalone v1은 기존
`ici.result/v3` report contract와 별개의 payload이다.

## Code Examples

```bash
# 기존 DB만 읽고 JSON을 stdout으로 출력
ici export-compilation-context

# project-relative DB를 선택해 checkout 밖에 저장
ici export-compilation-context \
  --database build/compile_commands.json \
  --output /tmp/ici-compilation-context.json --pretty

# DB가 없을 때만 CMake/qmake 준비를 허용
ici export-compilation-context --prepare \
  --output /tmp/ici-compilation-context.json
```

```text
success: 0
invalid option/path, missing measured DB or usable unit: 2
fatal diagnostic, serialization or write error: 1
```

## Verification Results

최종 로컬 구현 gate는 다음과 같다.

- Python 3.10 full pytest: 1,333 passed in 51.99s. Focused export/compile-context 묶음은
  stdout JSON-only, exit 1/2, static discovery, `--prepare` dispatch, database/path bounds,
  duplicate key/non-finite JSON, database-wide repeated-response budget, redaction, deterministic
  ordering/digest, protected hardlink/symlink/special output과 referent 보존을 포함한다.
  quoted relative define path regression도 unit directory 기준으로 해석하고, 외부 탈출은
  redaction하는 계약으로 고정했다.
- Ruff check 전체 PASS, Ruff format 148 files PASS, mypy 88 source files PASS.
- export 관련 5개 module의 focused branch coverage는 85%~100%였다. 최종 self report에서
  export/compile-DB 변경 범위의 line, module-coverage, type, high-complexity, exception finding은
  각각 0건이다. DB-wide budget을 helper로 분리하면서 `_parse_row` complexity도 18에서 15로
  낮췄다.
- Python 3.10 대상 pyz를 두 번 빌드한 SHA-256은
  `d9d83b20832ca8d0133653e00b1f7a20861c2ee855b06d0de1f0328137a382ca`으로 일치했다.
  10개 distribution은 모두 `py3-none-any`, certifi/native extension은 없었고 기존 v3와 새
  compilation-export v1 schema가 모두 package data에 포함됐다. smoke와 Python 3.10 직접 실행,
  artifact equality, Zero-CDN도 PASS였다. 최종 BuildScope export는 별도의
  `check-jsonschema` Draft 2020-12 검증도 통과했다.
- 최종 packaged self verify는 WARN(Pass 8, Warn 4, Fail 0, Error 0, Skip 1), tests 1,333/1,333,
  line/function/branch 89.2%/96.8%/80.6%, TEM 4.84, cache hit 0, engine duration 121.72s
  (wall 125.09s)였다. HTML은 5,696,688 bytes, SHA-256
  `adc9a49c78c2f5ea5666c58a96555cd73b281587f891e11175654a7ac973b3d5`, title
  `ici Verification Report — ici`, 외부 reference 0건이었다.
- 같은 candidate pyz를 실제 BuildScope canonical CMake context에 적용했다. verify는 WARN
  (Pass 11, Warn 2, Fail 0, Error 0, Skip 0), coverage line/function/branch 95.2%/100%/84.3%,
  compile DB 7/7 production units·16 configurations·0 issue, tests 45/45, TEM 5.00이었다.
  engine duration은 20.52s (wall 21.22s)였다. HTML은 490,420 bytes, SHA-256
  `faf4646b27b2e2c50501fb96280aa70741254dba8e7b383e5ede033ab519cb85`, 올바른 title과 외부
  reference 0건이었다.
- BuildScope v2 producer native snapshot SHA-256은
  `ee0e59f484a82cbdb09d8085a241929e15b0130e2c51f824c361f808f6c611f5`였고, 이를 같은 public
  projection으로 투영해 ici v1 export와 source, target, language, standard, directory, output,
  compiler family/name/wrapper, command style, target triple, define, undefine, include
  kind/order/scope의 16 unit·6 target·14 field group을 대조했다. mismatch는 0건이었고,
  checkout leak과 raw `argv`/`command`도 각각 0건이었다. ici export deterministic SHA-256은
  `6f0e99872ab0041f174f9b708cb2a0bd5e60569ce06fe825644541c0ae2162c9`, semantic digest는
  `sha256:a7db541ae2daa0c19365f80c1bdbe5090049c86b423000fdf9b6f8e85a857a48`였다.

### Same-basename active-header edge local revalidation

The existing `test_trace_uses_compiler_selected_same_basename_without_ambiguity` keeps a
mocked `run_process` regression for parser and compiler-selection behavior. The new
`test_real_compiler_trace_selects_the_first_same_basename_header` calls
`build_compiler_cpp_graph(..., runner=run_process)` and executes the actual probed compiler
process for each `g++`/`clang++` parameter. The local Python 3.10 focused run passed the mock and
actual `g++` cases (`2 passed`); the `clang++` case was skipped because `clang++` is unavailable.
The compiler trace selected the first `-I` `common.hpp` and excluded the alternate header with
the same basename. This closes the edge locally only; it adds no PR/CI/Pages evidence and does
not change the release/version record below.

The latest local full gate, including this actual-process test, reports Python 3.10 pytest
`1,334 passed, 1 skipped`; Ruff check/format PASS for 148 files; and mypy PASS for 88 source files.
`build-pyz` and smoke also passed. The current artifact is 2,166,828 bytes with SHA-256
`0f82aa95eb940072a735c591737f5b77d9dd16b32751aa03600ad3c5978bb158`. These are local facts only;
the new test has no PR/CI/Pages remote evidence yet.

### Remote follow-up

- Feature [PR #110](https://github.com/jihoon22-lee/ici/pull/110)은 head `3ce564a`에서
  `6b44f32869944a0941cab63eb94489b92c543a58`로 병합됐다. [CI run 33448847117](https://github.com/jihoon22-lee/ici/actions/runs/33448847117)은
  required checks와 `Merge Gate`를 모두 통과했고, sticky comment marker 1개·report link 2개 및
  독립 PR ici/viewer Pages의 HTTP 200·correct title·external resource reference 0건을 확인했다.
- Release [PR #111](https://github.com/jihoon22-lee/ici/pull/111)은 head
  [`13d870f`](https://github.com/jihoon22-lee/ici/commit/13d870f6bd8c6bd9ddc89b703e40b1d22b7567f4)에서
  exact main commit
  [`27574109e0f3fc24d6e96eca05bfded4e041d3fa`](https://github.com/jihoon22-lee/ici/commit/27574109e0f3fc24d6e96eca05bfded4e041d3fa)로
  병합됐다. [PR CI run 33450379770](https://github.com/jihoon22-lee/ici/actions/runs/33450379770)은
  all green이었고, [sticky comment](https://github.com/jihoon22-lee/ici/pull/111#issuecomment-5486185531)는
  marker 1과 두 report link를 기록했다.
- 독립 PR [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/111/)와
  [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/111/)는 HTTP 200·`text/html`·correct
  title·external reference 0건이었다. ici는 5,690,362 bytes/SHA-256
  `862c72443ca80040e0bc4524d31c5f5f7e8adb26292faf665f125ce09a9e53af`, viewer는 345,176
  bytes/SHA-256 `e6c86558ce00666e8151c1b4020abd26115f3dd6846dca06b275d5b7b75366ff`였다.
- Exact main [CI run 33450906375](https://github.com/jihoon22-lee/ici/actions/runs/33450906375)은 all green이었다.
  main [ici Pages](https://jihoon22-lee.github.io/ici/ici/main/)와 [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/main/)
  도 HTTP 200·`text/html`·correct title·external reference 0건이며 기존 기록 hash를 유지했다:
  ici 5,690,362 bytes/SHA-256 `99445ff8da2458d6bd5d861d63ae9318db374dfbc60a66bc6cc60ff5cc05894d`,
  viewer 345,176 bytes/SHA-256 `4626e354eba2638e07c3c6a254e4ae5cb95291a86c13f4bebe92bef1d892696d`.
- Annotated [`v0.8.0` tag](https://github.com/jihoon22-lee/ici/releases/tag/v0.8.0)는 exact main SHA에
  연결됐다. [Release run 33451310453](https://github.com/jihoon22-lee/ici/actions/runs/33451310453)은
  `Validate Release Provenance`와 `Build & Publish Release`를 모두 green으로 완료했고, published
  release는 non-draft/non-prerelease이며 `ici.pyz`, `ici.pyz.sha256`, self/viewer HTML·JSON,
  `icirv`, `icirv-gui`, `icirv-gui.README.txt`의 정확히 9개 asset을 포함한다.
- Downloaded `ici.pyz`는 version `0.8.0`을 보고했고 checksum 검증은 GitHub API digest
  `sha256:bb723a30b0ed07936fcf81c7e2b4425832fd86210286b0e6b1b619e1b434142e`와 일치했다. Release
  self/viewer HTML SHA-256은 각각
  `ccfbb3709864c7bf578a0635d66a63b82448304aefd616e1b57a3d9d59038539`와
  `6ee8d2e5b29453155af5e84323a8d829c1bcb3be80c345ab6d99d27b6560412a`였고, correct title·external
  reference 0건 및 두 JSON valid를 확인했다.
- Public v0.8.0 BuildScope verify는 WARN(Pass 11, Warn 2; tests 45/45; TEM 5.00), HTML SHA-256은
  `567957be0fcf978d756116262b4075f1655050902227b0b9d1428fe7a1080b6b`였다. Public export SHA-256은
  `f1d7e1297c773f55777d939a552c11f300a5f59652839f59495037ac227e83d`, semantic digest는
  `sha256:68f86ddf572ba781573f24d8a7319c6abd0f606b980ea1594e9f0616da71e95f`, native v2 snapshot은
  `085f70450cd89171d3fd4011d35ccc35e8658ab5308b64e398ea0b0793c45d8a`였다. Schema validation은
  passed했고, 16 unit·6 target·14 field group에서 mismatch·checkout leak·raw `argv`/`command` key는
  모두 0건이었다.
- Release와 public artifact evidence는 완료됐고, I3-5의 same-basename active-header edge는
  위 local actual-process trace 대조로 완료됐다. 이 후속 테스트에 대한 새 PR/CI/Pages
  evidence는 주장하지 않는다.

## Next Steps

- PR #111의 merge commit·required CI·sticky comment·PR/main Pages와 v0.8.0 release/tag/assets,
  checksum, version, report, BuildScope/export evidence를 위에 기록했다.
- I3-5의 same-basename active-header edge local actual-process 대조와 BuildScope
  target-by-target define·standard·include comparison은 완료됐다. public projection은 16
  unit·6 target·14 field group에서 mismatch 0이었다. 다음 남은 조건은 새 actual-process test의
  PR/CI/Pages remote evidence이며, 현재 release/version은 v0.8.0으로 유지한다.
