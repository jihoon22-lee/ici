# Rust 교체 기능·계약 원장

이 원장은 고정 공개판 소스의 정적 수집 결과다. 항목 수나 추출 성공은 Rust 동등성 인수가 아니다.
구현·검증·차이 검토·완료 상태 및 양성/음성/실패 fixture는 inventory.json과 closure/*.json에서
개별 관리한다. 아래 표는 inventory.json 자체의 상태다. fragment를 결합한 상태는 스크립트 실행
결과의 `closure` 값이며, 여러 출처가 같은 행을 다루면 가장 덜 닫힌 상태가 적용된다.
현재 status가 not_started인 행은 미구현·미검증이다. 전환 승인은 전체 원장과 source audit를 닫아야 한다.

- 고정 oracle: `v0.13.0`, source `6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188`.
- 공개 pyz SHA-256: `c179986f3d0eac110cf436a961458e56c0f4a218517e9883ff47cd67addc1cde`.
- Python/tool/interpreter/environment pin은 각 실행 oracle fixture에 추가로 필요하다.
  이 정적 원장은 도구 실행이나 환경 인수를 수행하지 않는다.

## 수집과 갱신

```bash
python3 scripts/rust_migration_inventory.py
python3 scripts/rust_migration_inventory.py --check
python3 scripts/rust_migration_inventory.py --self-test
python3 scripts/rust_migration_inventory.py --check --require-closed
```

Git archive의 고정 commit에서만 읽고 제품 모듈을 import/eval하지 않는다. Python 제품 소스를
cutover에서 제거한 뒤에도 Git history가 있으면 재생성할 수 있다. --check는 쓰지 않는다.
기존 migration/audit 증거는 재생성 시 보존하며 행 삭제로 증거가 유실되면 실패한다.
--require-closed는 미완료 요구·미검토 소스가 있으면 실패한다. 단순 complete 문자열만으로
닫히지 않으며 Rust 경로·비교 판정·실행 증거와 양성/음성/실패 fixture가 모두 필요하다.

## 기계 수집 범위

| 종류 | 항목 수 |
|---|---:|
| capability | 25 |
| check | 36 |
| command | 36 |
| command_group | 1 |
| config | 131 |
| config_key | 322 |
| descriptor | 19 |
| finding_site | 271 |
| wire_schema | 6 |
| 원본 파일 audit | 291 |
| Python module | 283 |

CLI는 decorator와 app.command(...)(function) 등록을 모두 수집하고 imported option 정의를
원래 파일까지 따라간다. alias는 개별 사용자 진입점으로 보존한다. config는 key allowlist와
각 reader 호출의 literal/expression을 그대로 기록한다. 동적인 key·rule·metric expression은
확정된 규칙 이름으로 가장하지 않으며 해당 source audit에서 분해·검토해야 한다.
finding_site는 rule/native rule/target/metrics 인자의 생성 지점이고 한 지점이 여러 규칙을
생성할 수 있다. 모든 파일의 SHA와 private/nested 함수를 포함한 symbol 목록을 별도로
수집하므로 extraction 밖의 동적 로직·template·asset을 조용히 제거할 수 없다.

원본 파일 audit는 mapped/development_only/unsupported/retired 처분 이유가 필요하다.
mapped는 대응 requirement ID를 연결한다. 구조가 달라졌다는 이유만으로 retired 처리하거나
finding 누락·위치·severity·scope·분모·완결성·exit 차이를 정규화해 숨기지 않는다.

## Descriptor / check

| 종류 | ID | 원본 | 상태 |
|---|---|---|---|
| check | `cpp.artifact` | `src/ici/languages/cpp/checks.py:148` | not_started |
| check | `cpp.binary-compat` | `src/ici/languages/cpp/checks.py:192` | not_started |
| check | `cpp.cognitive` | `src/ici/languages/cpp/checks.py:95` | not_started |
| check | `cpp.compile` | `src/ici/languages/cpp/checks.py:34` | not_started |
| check | `cpp.complexity` | `src/ici/languages/cpp/checks.py:88` | not_started |
| check | `cpp.coverage` | `src/ici/languages/cpp/checks.py:132` | not_started |
| check | `cpp.cycle` | `src/ici/languages/cpp/checks.py:104` | not_started |
| check | `cpp.diagnostics` | `src/ici/languages/cpp/checks.py:47` | not_started |
| check | `cpp.dup` | `src/ici/languages/cpp/checks.py:113` | not_started |
| check | `cpp.exception` | `src/ici/languages/cpp/checks.py:125` | not_started |
| check | `cpp.line` | `src/ici/languages/cpp/checks.py:21` | not_started |
| check | `cpp.sanitize` | `src/ici/languages/cpp/checks.py:166` | not_started |
| check | `cpp.test` | `src/ici/languages/cpp/checks.py:75` | not_started |
| check | `cpp.tidy` | `src/ici/languages/cpp/checks.py:59` | not_started |
| check | `cpp.tsan` | `src/ici/languages/cpp/checks.py:175` | not_started |
| check | `integration` | `src/ici/languages/integration.py:24` | not_started |
| check | `javascript.lint` | `src/ici/languages/registry.py:185` | not_started |
| check | `python.cognitive` | `src/ici/languages/python/checks.py:97` | not_started |
| check | `python.compat` | `src/ici/languages/python/checks.py:163` | not_started |
| check | `python.compat-runtime` | `src/ici/languages/python/checks.py:179` | not_started |
| check | `python.complexity` | `src/ici/languages/python/checks.py:90` | not_started |
| check | `python.coverage` | `src/ici/languages/python/checks.py:78` | not_started |
| check | `python.cycle` | `src/ici/languages/python/checks.py:106` | not_started |
| check | `python.dead` | `src/ici/languages/python/checks.py:152` | not_started |
| check | `python.dup` | `src/ici/languages/python/checks.py:115` | not_started |
| check | `python.exception` | `src/ici/languages/python/checks.py:142` | not_started |
| check | `python.format` | `src/ici/languages/python/checks.py:46` | not_started |
| check | `python.line` | `src/ici/languages/python/checks.py:32` | not_started |
| check | `python.lint` | `src/ici/languages/python/checks.py:39` | not_started |
| check | `python.resource` | `src/ici/languages/python/checks.py:132` | not_started |
| check | `python.security` | `src/ici/languages/python/checks.py:125` | not_started |
| check | `python.test` | `src/ici/languages/python/checks.py:66` | not_started |
| check | `python.type` | `src/ici/languages/python/checks.py:56` | not_started |
| check | `rust.clippy` | `src/ici/languages/registry.py:179` | not_started |
| check | `typescript.lint` | `src/ici/languages/registry.py:192` | not_started |
| check | `typescript.type` | `src/ici/languages/registry.py:193` | not_started |
| descriptor | `binary_compat` | `src/ici/core/pipeline.py:159` | not_started |
| descriptor | `build` | `src/ici/core/pipeline.py:150` | not_started |
| descriptor | `cognitive` | `src/ici/core/pipeline.py:115` | not_started |
| descriptor | `compile_db` | `src/ici/core/pipeline.py:95` | not_started |
| descriptor | `complexity` | `src/ici/core/pipeline.py:124` | not_started |
| descriptor | `cycle` | `src/ici/core/pipeline.py:123` | not_started |
| descriptor | `dead` | `src/ici/core/pipeline.py:147` | not_started |
| descriptor | `dup` | `src/ici/core/pipeline.py:148` | not_started |
| descriptor | `exception` | `src/ici/core/pipeline.py:149` | not_started |
| descriptor | `integration` | `src/ici/core/pipeline.py:167` | not_started |
| descriptor | `line` | `src/ici/core/pipeline.py:93` | not_started |
| descriptor | `lint` | `src/ici/core/pipeline.py:94` | not_started |
| descriptor | `python_compat` | `src/ici/core/pipeline.py:110` | not_started |
| descriptor | `resource` | `src/ici/core/pipeline.py:121` | not_started |
| descriptor | `sanitize` | `src/ici/core/pipeline.py:125` | not_started |
| descriptor | `security` | `src/ici/core/pipeline.py:122` | not_started |
| descriptor | `test` | `src/ici/core/pipeline.py:100` | not_started |
| descriptor | `thread_sanitize` | `src/ici/core/pipeline.py:134` | not_started |
| descriptor | `type` | `src/ici/core/pipeline.py:109` | not_started |

## CLI 진입점

| 명령 | 옵션/인자 수 | 상태 |
|---|---:|---|
| `ici <global>` | 1 | not_started |
| `ici analyze` | 14 | not_started |
| `ici build` | 0 | not_started |
| `ici cache` | 1 | complete |
| `ici cognitive` | 1 | not_started |
| `ici complexity` | 1 | not_started |
| `ici cycle` | 1 | not_started |
| `ici dead` | 1 | not_started |
| `ici diff` | 2 | not_started |
| `ici doctor` | 2 | not_started |
| `ici dup` | 1 | not_started |
| `ici env` | 2 | complete |
| `ici exception` | 1 | not_started |
| `ici export-compilation-context` | 4 | not_started |
| `ici import-report` | 5 | complete |
| `ici init` | 4 | not_started |
| `ici line` | 1 | verified |
| `ici lint` | 1 | not_started |
| `ici next diff` | 2 | not_started |
| `ici next doctor` | 7 | not_started |
| `ici next init` | 4 | not_started |
| `ici next migrate` | 3 | complete |
| `ici next plan` | 8 | not_started |
| `ici next publish` | 4 | difference_review |
| `ici next report` | 4 | not_started |
| `ici next verify` | 12 | not_started |
| `ici publish` | 3 | difference_review |
| `ici python-compat` | 1 | not_started |
| `ici report` | 4 | not_started |
| `ici resource` | 1 | not_started |
| `ici sanitize` | 1 | not_started |
| `ici security` | 1 | not_started |
| `ici test` | 1 | not_started |
| `ici thread-sanitize` | 1 | not_started |
| `ici type` | 1 | not_started |
| `ici verify` | 14 | not_started |

## Registry 밖·공유 계약

| 요구 | 보존할 의미 | 상태 |
|---|---|---|
| `capability:auxiliary-import` | SARIF uriBaseId mapping/refusal, JUnit native line, LCOV subset; NOT_EVALUATED | complete |
| `capability:build-artifact-abi` | Declared artifact ownership, ELF/linker/ABI, Make/CMake/qmake build effects | not_started |
| `capability:compile-context-export` | compile argv/cwd/response files, build variants, export schema and source closure | not_started |
| `capability:config-migration-preservation` | Preview, refusal of unknown schemas and preservation of user originals/comments | complete |
| `capability:cpp-parser-heuristic-confidence` | Bounded tokens, function boundaries, cognitive/CC limits and confidence labels | not_started |
| `capability:cpp-qt-generated-input` | moc/uic/rcc, clazy/tidy/compiler inputs and generated include attribution | not_started |
| `capability:discovery-config-origin` | Root/child boundaries, sparse config/init equivalence, origin and local overlays | not_started |
| `capability:doctor-env-cache` | Read-only diagnosis, tools and install collisions, project env, cache list/prune | not_started |
| `capability:execution-cancel-save-redact` | Process groups/descendants, timeouts, exit130, partial results, atomic saves, redaction | not_started |
| `capability:execution-dag-resource-lock` | Dependency planning, lock order, effects, snapshots and bounded process execution | not_started |
| `capability:identity-cache-baseline` | Fingerprint/policy/provider/scope/build identity, old evidence and conservative resolved | not_started |
| `capability:legacy-command-config-result-projection` | One common application with verify/build/doctor/env/cache and legacy result v3 projection | not_started |
| `capability:legacy-cpp-linker-dead` | Compiler/linker unused and dead-symbol evidence, generated/vendor scope | not_started |
| `capability:legacy-python-sanitize` | Python sanitizer/resource execution must not disappear behind cpp.sanitize | not_started |
| `capability:native-windows-disposition` | Record unsupported/limited Windows runner disposition; do not silently drop behavior | not_started |
| `capability:publish-ca-proxy-ghes` | Private/system CA, proxy, GHES, publication failure independent of analysis | complete |
| `capability:python-packaging-wheel` | PEP 440/508, metadata, RECORD, tags, import distribution, entrypoints and bounds | not_started |
| `capability:python-scope-binding` | Import alias, shadowing, scope and resource lifetime semantics | not_started |
| `capability:python-semantic-duplicates` | Tokenizer, semantic normalization, clustering, matches and region attribution | not_started |
| `capability:qmake-recorder` | Replace owned Python recorder with Rust internal binary entrypoint | not_started |
| `capability:report-console-markdown-json-html-sarif` | Issues-first/grouping/limits, all location/evidence axes, no CDN and large reports | not_started |
| `capability:runtime-integration` | Project interpreter selection, import-only versus -m probes, declared contracts | not_started |
| `capability:suppression` | Reasoned selectors, source/config suppression and preserved raw findings | not_started |
| `capability:test-quality-tem` | Tests/coverage denominators, zero tests, repeats/mutation and TEM formula boundaries | not_started |
| `capability:wire-result-events` | Run v2/read v1, event v1, stdout/stderr split, event order and same run_id | not_started |

## 수집기 검증 (2026-10-28)

- Python 3.10/3.14에서 같은 고정 inventory 재생성/check 통과. 제품 모듈 import 없는
  alias/source 위치·미완료 closure 거부·거짓 complete 거부·증거 보존/삭제 거부 self-test 통과.
- 현재 소스가 oracle과 같은 시점에서 Typer get_command로 실제 CLI를 읽기 검증했다:
  root/group 포함 37개 node, 115개 parameter 및 option alias가 추출 목록과 일치했다.
- 이 검사는 수집기의 신뢰성 근거이며 Rust 기능·known-bug 교정·실제 도구 parity 검증은 아니다.

## 인수에서 추가로 고정할 차이

- Python sanitize와 C++ linker dead는 36개 check 이름만으로 덮이지 않는다.
- legacy verify/build/doctor/env/cache와 next alias의 옵션 의미·exit·v3 projection을 각각 검증한다.
- plan/doctor/publish처럼 JSON Schema가 없는 출력은 실행 golden으로 별도 고정한다.
- ESLint rule off·plugin/lock/같은 경로 도구 교체는 scope/identity 변화다. 기존 결과를 읽는
  기능과 baseline resolved 판단 가능성을 분리하고 알려진 오판은 교정 기대값으로 검증한다.
- SARIF uriBaseId, JUnit line, LCOV subset 및 imported NOT_EVALUATED는 위치·증거 계약이다.
- 환경·배포·viewer·설치 shell/CI는 Python module 개수에 포함되지 않는 제품 표면이다.
  root 실행 계획 R0/R8/R9의 별도 설치·tool-pack·viewer·rollback 인수와 함께 닫아야 한다.
- runner_win은 Linux/WSL 전환의 지원 범위 밖이어도 처분 근거를 남긴다.
