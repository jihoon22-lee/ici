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

### Remote follow-up

- Feature [PR #110](https://github.com/jihoon22-lee/ici/pull/110)은 head `3ce564a`에서 exact main
  commit `6b44f32869944a0941cab63eb94489b92c543a58`로 병합됐다. [CI run 33448847117](https://github.com/jihoon22-lee/ici/actions/runs/33448847117)은
  required checks와 `Merge Gate`를 모두 성공시켰고, [sticky comment](https://github.com/jihoon22-lee/ici/pull/110#issuecomment-5485964934)는
  marker 1과 두 report link를 기록했다.
- 독립 PR Pages는 HTTP 200·`text/html`·expected title·external reference 0건이었다. ici는
  5,690,362 bytes, SHA-256 `fbda099830ee7f0505b76b410963e2531904ea199e36e0292953d2cf73f45014`,
  viewer는 345,176 bytes, SHA-256
  `cff8fdc355bf09a5fcceda0f4c1715988b693a2b61f6fac23641dd3d6a6ea115`였다.
- main [CI run 33449333028](https://github.com/jihoon22-lee/ici/actions/runs/33449333028)도 `Merge Gate`와
  `Publish Main`을 포함해 성공했다. main Pages는 HTTP 200·`text/html`·expected title·external
  reference 0건으로, ici 5,690,362 bytes/SHA-256
  `99445ff8da2458d6bd5d861d63ae9318db374dfbc60a66bc6cc60ff5cc05894d`, viewer 345,176 bytes/SHA-256
  `4626e354eba2638e07c3c6a254e4ae5cb95291a86c13f4bebe92bef1d892696d`였다.
- 원격 PR·main Pages 증거는 확보됐지만 release PR/tag/assets와 same-basename active-header
  비교는 아직 pending이다.

## Next Steps

- PR #110의 required CI, sticky comment, ici/viewer Pages와 main 반영 증거를 위에 기록했다.
  별도 release PR/tag/assets와 공개 checksum 검증이 남아 있다.
- 공개 release artifact를 BuildScope의 교차 구현 비교에 고정하고, I3의 남은 same-basename
  active header edge 대조를 toy fixture와 실제 compiler trace로 완료한다.
