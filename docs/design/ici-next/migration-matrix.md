# Stable → ici-next 마이그레이션 매트릭스 (WP27 / #225)

| | |
|---|---|
|상태|**구현 완료**. `ici next migrate`가 이 표의 *설정* 축을 실제로 변환하며, 각 행의 판정은 `src/ici/config/convert.py`의 노트와 맞물려 있다.|
|근거 이슈|[WP27 #225](https://github.com/jihoon22-lee/ici/issues/225)|
|관련 규범|[spec-01](spec-01-workspace-config-cli.md) §3, [spec-04](spec-04-results-integration.md) §5–6, [spec-05](spec-05-verification-transition.md)|
|결과 스키마 매트릭스|[compatibility-v3-next.md](compatibility-v3-next.md) — `ici.result/v3` ↔ `ici.next.run`은 여기서 다룬다|

판정 열의 의미:

- **변환** — 의미가 보존되는 대응 키/명령이 있어 자동 변환된다.
- **확인** — 대응이 존재하지만 의미가 달라 사용자 검토가 필요하다.
- **미지원** — next 경로에 대응이 없다. 조용히 유지되지 않으며 사유를 명시한다.
- **제거** — 개념 자체가 사라진다(폴백·자동탐색 등 next가 금지한 동작).

## 1. 설정 파일과 발견 규칙

| stable | next | 판정 |
|---|---|---|
| 프로젝트 `ici.toml`(`[ici]`/`[project]`/`[engines]`) | 루트 `ici.toml`(`schema_version`/`[workspace]`/`[[components]]`) | **변환** — `ici next migrate` |
| `dev.toml` | 없음 | **제거** — 컴포넌트 파일 오버레이가 그 역할을 대신한다. migrate가 contested key를 보고한다 |
| `$XDG_CONFIG_HOME/ici/config.toml` 전역 | 없음 | **제거** — 사용자 전역 정책이 게이트를 바꾸는 것이 SPEC-01 §3이 이름 붙인 결함이다 |
| `ICI_CONFIG` 환경 변수 | `--config PATH` | **변환(의도 변경)** — 환경이 조용히 파일을 끼우는 대신 명령줄이 명시한다 |
| `.pro` 첫 파일 자동 선택 | `[[components]]` + `root`/`sources` 명시 선언 | **제거** — 자동 선택은 next에 없다. migrate는 단일 component를 만들고 source_dirs를 `sources` glob으로 옮긴다 |
| core Python 폴백(ici 인터프리터로 테스트) | `python.executable` 선언 필수 | **제거** — 인터프리터 미선언 시 `python.test`/`python.coverage`는 blocked이지 ici 런타임으로 돌지 않는다 |

## 2. 설정 키

### `[ici]`

| stable 키 | next 대응 | 판정 |
|---|---|---|
| `ici.profile` | `workspace.profile` | **변환** |
| `ici.version` | `schema_version = 1` | **미지원** — 스키마가 버전을 대신한다 |
| `ici.policy_name` | 없음(`policy_digest`는 실행 시 계산) | **미지원** |

### `[project]`

| stable 키 | next 대응 | 판정 |
|---|---|---|
| `project.name` | `[[components]] id` (+ workspace name) | **변환** |
| `project.source_dirs` | component `sources` glob | **변환** — 디렉터리→`dir/**`로 옮기며 노트에 검토 요청 |
| `project.type` | component `languages`(트리 스캔으로 검출) | **미지원→변환** — 선언이 아니라 파일이 결정한다 |
| `project.version` | 없음 | **미지원** |
| `project.cpp_pkg_config` | `[[builds]]`/컴파일 DB 명시 | **확인** — pkg-config 끼워넣기는 next에 없다 |
| `project.cpp_external_build_dirs` | `[[builds]]` 선언 | **확인** — 빌드 디렉터리 자동탐색은 없다 |
| `project.compile_database` | `[[builds]]` 선언 | **확인** — 동일한 의도, 다른 표현 |

### `[build]` / `[doctor]`

| stable 키 | next 대응 | 판정 |
|---|---|---|
| `build.python.entrypoint` | `python.executable` 개념 | **확인** — entrypoint 실행이 아니라 인터프리터 선언이다 |
| `build.make` / `build.artifacts` | `[[builds]]` + integration case의 `{artifact:...}` | **확인** |
| `doctor.required_tools` | 없음 — `next doctor`가 선택된 check에서 필요 도구를 도출 | **미지원** |

### `[engines.<name>]`

엔진 하나가 언어별 check 여럿으로 갈라진다 — `lint`를 켜면 ruff와 clang-tidy가
둘 다 켜졌던 것처럼, 변환도 둘 다 켠다.

| stable 엔진 | next check | 판정 |
|---|---|---|
| `line` | `python.line`, `cpp.line` | **변환** |
| `lint` | `python.lint`, `cpp.tidy`, `cpp.diagnostics` | **변환** |
| `compile_db` | `cpp.compile` | **변환** |
| `test` | `python.test`, `python.coverage`, `cpp.test`, `cpp.coverage` | **변환** |
| `type` | `python.type` | **변환** |
| `python_compat` | `python.compat`, `python.compat-runtime` | **변환** |
| `complexity` | `python.complexity`, `cpp.complexity` | **변환** |
| `cognitive` | `python.cognitive`, `cpp.cognitive` | **변환** |
| `sanitize` | `cpp.sanitize` | **변환** — deep 프로파일 전용이 됨 |
| `thread_sanitize` | `cpp.tsan` | **변환** — deep 프로파일 전용 |
| `dead` | `python.dead` | **변환(부분)** — `cpp.dead` 링커 경로는 미이관([WP20 disposition](inventory/wp20-dispositions.md)) |
| `dup` | `python.dup`, `cpp.dup` | **변환** |
| `exception` | `python.exception`, `cpp.exception` | **변환** |
| `cycle` | `python.cycle`, `cpp.cycle` | **변환** |
| `security` | `python.security` | **변환** |
| `resource` | `python.resource` | **변환** |
| `build` | `cpp.artifact` | **확인** — 산출물 계약은 변환되나 빌드 자체는 `[[builds]]`/`prepare` 선언에 달린다 |
| `binary_compat` | `cpp.binary-compat` | **변환** |
| `integration` | `integration` | **확인** — enabled 플래그가 아니라 `[[components.integrations]]` case 선언이 필요하다 |

공통 키의 처리:

| stable 키 | next 대응 | 판정 |
|---|---|---|
| `enabled` | `[checks."<id>"] enabled` | **변환** |
| `required` | `[checks."<id>"] required` | **변환** |
| `mode` | `workspace.profile`(fast/standard/deep) | **확인** — 엔진별 모드는 없다 |
| 임계값 계열(`warn_limit`, `fail_limit`, `min_tem_score`, `min_*_cov`, `warn_cc`, `fail_cc`, `warn_pct`, `fail_pct`, `warn`, `fail`, `warn_nesting` …) | 없음 | **확인** — 게이트는 required check의 MEASURED finding 유무다. 회귀 임계는 `next diff`/baseline 비교가 담당한다 |
| `gate_dirs`/`include_dirs`/`exclude_dirs` | component `sources`/`include`/`exclude` | **확인** — 범위 선언이 게이트 디렉터리를 대신한다 |
| `test.python`, `test.quality` 등 중첩 테이블 | component `python.*` / `integrations` | **확인** |
| `binary_compat.*` 정책 키(`max_glibc`, `forbidden_needed` …) | component `[[builds]]`/`[cpp]` 인접 선언으로 이동 | **확인** |
| `integration.cases`/`python_targets` | `[[components.integrations]]` 문법 | **변환(수기)** — migrate가 case 테이블로 옮기지 않는다. 선언 문법이 달라 사람이 옮긴다 |

## 3. CLI

| stable | next | 판정 |
|---|---|---|
| `ici verify` | `ici next verify` | **변환** — 종료 코드 0/1/2/3/130은 §4 |
| `ici verify --report` → `verify_report.json` | `--result PATH`(기본 `.ici/next/result.json`) | **변환** — 항상 저장되며 `--report` 플래그는 없다 |
| `ici verify --html` / `--open` | `ici next report` | **변환** — 렌더링은 실행과 분리됐다. 브라우저 열기는 없다 |
| `ici verify --sarif` | `ici next report --sarif` | **변환** |
| `ici verify --baseline` / `--fail-on-new` | `next verify --baseline` / `next diff` | **변환** — fingerprint_version·정책 digest 불일치는 incompatible로 거부된다 |
| `ici verify --write-baseline` | 결과 파일 자체가 baseline 입력 | **제거** — 별도 쓰기 경로 없이 저장된 result를 그대로 쓴다 |
| `ici verify --publish` | `ici next publish` | **변환** — 게시가 분석 job과 분리됐다(WP25) |
| `ici verify --github-summary` | 없음 | **미지원** — GHES 어노테이션은 WP25 게시 경로의 책임이다 |
| `ici <engine>` 개별 명령(`ici lint`, `ici test` …) | `ici next verify --checks`/`--python`/`--cpp` 선택 | **변환** — check 단위 선택이 엔진 명령을 대신한다 |
| `ici build` | `[[builds]]` 선언 + `next verify` | **확인** — 빌드는 ici가 직접 하는 게 아니라 선언된 prepare이다 |
| `ici doctor` | `ici next doctor` | **변환** |
| `ici env --sh/--csh` | 없음 | **제거** — 셸 초기화 파일 생성은 R01이 금지한 경로다 |
| `ici cache` | `.ici/cache/`는 verify 내부 상태 | **확인** — 수동 캐시 조작 명령은 없다 |
| `ici publish` | `ici next publish` | **변환** |
| `ici export-compilation-context` | `next plan`의 compile DB 표시 | **확인** — 별도 export 명령은 없다 |
| `ici --version` | `ici next --help`/`ici --version` 유지 | **유지** |

## 4. 종료 코드

| 상황 | stable | next |
|---|---|---|
| PASS | 0 | 0 |
| FAIL(위반 있음) | 1 | 1 |
| 설정/입력 오류 | 2 | 2 |
| 필수 증거 부족 | (없음 — FAIL로 합쳐짐) | **3** — INCOMPLETE가 별도 코드가 됐다 |
| 사용자 취소 | (예외 경로) | **130** — 부분 result가 기록된다 |

## 5. 결과·baseline·viewer

| 항목 | 대응 | 판정 |
|---|---|---|
| `ici.result/v3` result | `legacy_reader`가 `LegacyReport`로 읽는다 — 스냅샷·scope·task 신원은 limitation으로 기록 | **변환** — [compatibility 표](compatibility-v3-next.md) |
| v3 baseline | `next verify --baseline`은 `ici.next.run`만 받는다. v3 baseline은 fingerprint 의미가 달라 비교 불가로 거부하는 게 정직하다 | **확인** — 변환 도구 없음, 재측정을 권한다 |
| `verify_report.html` viewer | `next report`가 `.ici/next/result.html` 생성 — 동일 Zero-CDN 규약 | **변환** |
| v3 SARIF | `next report --sarif` | **변환** |

## 6. 전환 조건과 되돌림

- `ici next migrate`는 기본 dry-run이다. `--output`은 새 파일에만 쓰고,
  `--write`는 원본을 `ici.toml.stable`로 남긴 뒤 교체한다 — 되돌림은 파일
  복사다.
- 안정 배포물(`dist/ici.pyz`)과 v3 reader·migration fixture는 전환 후에도
  보존한다 — 두 경로를 영구 유지하지 않되, 돌아갈 다리는 끊지 않는다
  (#225 항목 6).
- 기본 경로 전환(cutover)은 #227이 최종 인수 근거를 확인한 뒤 별도 PR로
  진행한다 — 이 WP는 후보 목록과 문서만 만든다(§7).
- project `.venv`나 공용 Python을 바꾸는 설치 흐름은 없다 — next는 번들
  런타임을 쓰고 프로젝트 인터프리터는 선언된 것을 그대로 쓴다(인수 기준 5).

## 7. Cutover 후보 목록(PR C 초안)

기본 전환 시 제거/이관 대상. 각 항목은 제거 근거(usage search·테스트)가 붙는다.

| 후보 | 근거 |
|---|---|
| `src/ici/engines/` 전체(19개 engine) | next provider로 이관 완료 — disposition 표가 각각의 이전 경로를 기록 |
| `_ENGINE_COMMANDS` 개별 CLI | `--checks`/언어 선택으로 대체 |
| `config` stable loader(`ici.config.__init__`의 XDG/`dev.toml`/`ICI_CONFIG` 병합) | next discovery는 `[workspace]` 선언 파일만 읽는다 |
| `reporters/`(console/html/json_rep의 v3 경로) | `reporting/` view model + offline HTML로 대체 |
| `core/baseline.py` v3 baseline | `application/baseline.py`의 next 비교로 대체 — v3는 reader로만 남긴다 |
| `engines/publish.py` | `application/publish.py` + `adapters/ghes.py`로 대체 |
| `tests/`의 stable 전용 fixture·회귀 | corpus(#201)가 인수 기준을 넘기 전까지 보존 |
| `build-pyz.sh`·launcher·`tests/test_launcher.py` | 번들 배포로 대체 — stable artifact가 지원되는 동안 유지(AGENTS §4) |

제거는 이 표의 *결정*이 아니라 *후보*다 — 실제 cutover merge는 #227의
최종 인수 이후 별도 검토로, 이 PR 범위 밖이다.
