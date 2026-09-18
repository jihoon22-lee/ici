# WP28 검증 — 교차 검증·성능·보안·현장 인수 (#226)

| | |
|---|---|
|상태|**PR A+B 부분 완료** — 구·신 디퍼렌셜·성능 측정·보안 경계·bundle E2E가 자동화로 걸렸다(§4a). RHEL/GHES 현장 인수(항목 6·7)는 **미수행** — §5 표에 각각의 상태를 명시한다.|
|근거 이슈|[WP28 #226](https://github.com/jihoon22-lee/ici/issues/226)|
|규범|[spec-05](spec-05-verification-transition.md) §2(측정 계층), §7(현장 인수)|

## 1. 추적표 연결 (항목 1)

`R01~R15 → SPEC → WP → evidence` 추적은
[requirements-traceability.md](requirements-traceability.md)가 소유한다. 이 WP이
추가한 근거는 §2~§4에 있다. 표에서 아직 evidence가 없는 행(실행 근거가 필요한
성능 수치·현장 환경)은 이 문서 §5에 미완료로 남는다.

## 2. 구·신 디퍼렌셜 (항목 2)

자동화: [`tests/test_next_differential.py`](../../../tests/test_next_differential.py)
— 등록된 시드 결함 fixture를 stable 엔진과 `ici next verify` 양쪽으로 실행해
파일 단위로 비교한다.

| fixture | stable | next | 판정 |
|---|---|---|---|
| `cpp/cycle_pair` | CycleEngine이 a.hpp·b.hpp 각각 target | `cpp.cycle`이 루프 1건 — 나머지 파일은 `related_locations` | **구조 차이**(defect당 finding 1개 vs 파일당 target) — 파일 전원이 명명되므로 손실 없음 |
| `cpp/complexity_hot` | WARN, classify* > 15 | `cpp.complexity` 동일 파일·함수 | 동일 |
| `cpp/clone_pair` | WARN Type-2 클론 | `cpp.dup` | 동일 |
| `cpp/dtor_throw` | FAIL 소멸자 throw | `cpp.exception` | 동일 |
| `cpp/oversized_file` | WARN 500줄 초과 | ~~없음~~ → **회귀로 확정, 이 PR에서 복원** | `*.line`에 고정 임계 규칙 복원(500 medium / 1000 high, stable 기본값) |
| `cpp/clean_baseline` | 전부 PASS | text check finding 0 | 동일 |
| `python/single_project` | 전부 PASS | text check finding 0 | 동일 |
| `python/multi_component` | `components/`는 기본 source dir이 아니라 0파일 PASS | 선언된 component가 beta를 봄 → `python.complexity`가 classify() 검출 | **의도된 구조 차이** — 선언 scope가 디렉터리 추론을 이긴다(R05/R07) |

**디퍼렌셜이 잡은 실제 회귀 2건**(이 PR에서 수정):

1. `*.line`의 파일 크기 규칙이 이관되지 않았다 — WP20 disposition의
   "제거된 기능 없음"과 충돌. `languages/python/lines.py`에 stable 기본
   임계(500/1000 코드 라인)를 고정 상수로 복원했다. next에 임계값 설정
   키는 없다(migration matrix §2 "확인") — 탐지는 check의 규칙이고,
   판정은 게이트의 몫이다.
2. `python.dup`/`cpp.dup`이 `DuplicateComparisonLimit`을 catch하지 않아
   실행이 traceback으로 죽었다. stable 엔진은 같은 한계를 ERROR
   (NOT_RUN)로 번역한다 — 이제 `languages/duplicates.py`가 FAILED
   observation + limitation으로 변환해 게이트가 INCOMPLETE로 본다.

## 3. 성능 측정 (항목 3)

자동화: [`scripts/benchmark_next.py`](../../../scripts/benchmark_next.py) —
고정 크기 fixture(기본 200개 모듈, 모듈당 고유 이름·분기)에서 cold/warm
벽시계·peak RSS·태스크 수·산출물 크기·취소 지연을 측정하고 JSON 레코드를
낸다. 추세 기록물이며 게이트가 아니다.

측정값(이 개발 환경, Python 3.10.21, Linux, 200개 모듈):

| 축 | 값 |
|---|---|
| cold `next verify` | 2.03 s, peak RSS 45.6 MiB |
| warm `next verify` | 1.48 s — 재사용된 태스크 1개(외부 도구; 내부 check는 매번 실행) |
| result.json | 6.9 KB (finding 1건) |
| result.html | 6.3 KB |
| `.ici/cache` | 875 B |
| SIGINT → 종료 | 0.11 s, `run.completed`까지 스트림이 닫힘 |

해석: 캐시가 줄이는 것은 외부 도구 재실행뿐이다 — 내부 check은 의도적으로
매번 실행된다(관측 재사용이 아니라 재판정). 취소 지연은 첫 unit 경계까지의
스케줄링 오버헤드 수준이다. 이 수치는 예산 승인이 아니라 기준선이다 —
실제 runner 자원(RHEL 현장)에서의 측정이 §5에 남아 있다.

## 4. 보안 경계 (항목 4)

자동화: [`tests/test_next_security.py`](../../../tests/test_next_security.py) +
`tests/test_cache.py`의 integrity 케이스.

| 경계 | 시험 | 결과 |
|---|---|---|
| component `root` 이탈 | `root = "../outside"` → 설정 로드 거부 | 거부, exit 2 — 밖을 탐색하지 않는다 |
| 캐시 변조 | 저장된 observation의 finding 편집·integrity 필드 제거·손상 JSON | 모두 miss + evict — 다시 실행해 결함 재검출. **`integrity` sha256 필드를 이 PR에서 추가** — 저장 당시의 바이트와 다른 내용은 채택되지 않는다 |
| publish 토큰 | transport가 에러 본문에 토큰을 되돌려줌 | stdout/stderr/`publish.json` 어디에도 토큰 없음 — 토큰은 Authorization 헤더에만 실린다 |
| 결과의 경로 | 저장된 result의 모든 span | workspace 상대만 — 절대경로·`..` 없음(R15) |
| HTML 이스케이프 | finding 메시지에 `<script>` | WP24 테스트에서 이스케이프 검증됨 |

**명시적 한계 — 신뢰 경계:** `.ici/` 쓰기 권한을 가진 로컬 공격자는
integrity를 재계산해 캐시를 오염시킬 수 있다 — 다이제스트는 키가 없어
변조"탐지"가 아니라 변조"부재 증명"이다. 이 위협은 소스 편집과 동일한
권한을 요구하므로 별도 방어를 두지 않는다(`.git` 인덱스와 같은 경계).
원격 입력(서버 응답 본문)이 출력에 실리는 경로는 위 표에서 닫혔다.

## 4a. bundle E2E (항목 5) — PR B

방법: `scripts/spikes/wp01/build-bundle.sh`로 로컬 PBS CPython **3.13.15**
(uv store에 이미 있던 것 — 다운로드 없음)를 runtime으로 쓴 bundle을 조립하고,
릴리스 경로의 [`scripts/bundle/smoke.sh`](../../../scripts/bundle/smoke.sh)를
실행했다. 빌드 시점의 `pip --target`만 네트워크를 쓴다 — 실행 시점은 아래
netns 케이스가 증명한다.

| 케이스 | 결과 |
|---|---|
| in-place 실행(version/help/doctor/line 분석) | **PASS** |
| 다른 설치 경로로 이동 후 실행 | **PASS** |
| PATH symlink 경유 실행 | **PASS** |
| clean HOME(`XDG_*` unset) | **PASS** |
| read-only 설치(bind mount ro) | **PASS** — 사전 컴파일된 bundle이라 설치 디렉터리에 쓰지 않는다 |
| **네트워크 인터페이스 0개**(`unshare -n`) | **PASS** — proxy 제거가 아니라 인터페이스 자체를 없앤 실행. strace가 없어도 syscall 증명과 동등하다 — 호출할 인터페이스가 존재하지 않는다 |
| offline + clean HOME + read-only 동시 | **PASS** |
| 설치 디렉터리 무기록 | **PASS** — 분석 후 6,123개 파일 전부 무결 |
| 빈 HOME에서 패키지 설치 없음 | **PASS** — site-packages/dist-info/whl/pip·uv 캐시 0건 |
| 두 버전 병행 | **PASS** — 각 설치가 자기 root를 해석한다(공유 캐시·절대경로 고정 없음) |
| missing tool | **확인됨** — bundle은 ruff만 싣는다. mypy/프로젝트 인터프리터 부재는 traceback이 아니라 해당 check의 INCOMPLETE observation(증거 수준 축)으로 기록된다 |
| 부분 범위 | **확인됨** — `[checks.*] enabled=false`로 lint만 선택한 실행은 verify exit 1(위반 존재)로 완료된다 — 부분 실행을 전체 통과로 보고하지 않는다 |
| failed-result HTML | **확인됨** — exit 1 결과로 `next report`가 외부 참조 0건의 페이지를 렌더한다(FAIL·INCOMPLETE·finding 전부 표시) |

런타임 메모: bundle의 glibc 상한은 **2.17**로 측정됐다 — RHEL 8의
glibc 2.28 이내이므로 ABI 수준에서는 호환된다. RHEL 8.10에서의 실제 실행
인수는 별개로 남는다(§5).

smoke case 11(next path E2E)은 **BLOCKED**로 보고됐다 — bundle이 mypy를
싣지 않고 fixture가 `[python] executable`을 선언하지 않아 verify가 exit 3
(INCOMPLETE)을 반환하기 때문이다. 스크립트가 이를 PASS로 접지 않는 것이
의도된 동작이다 — analyzer 없는 bundle을 lint가 깨끗한 bundle처럼 보이게
하는 실패를 이 케이스가 존재해서 막는다.

## 5. 미수행·미확인 — 추정으로 채우지 않는다

| 항목 | 상태 |
|---|---|
| 항목 5 — bundle 전체 E2E | **수행 — §4a**(로컬 PBS 3.13.15). RHEL에서의 재실행은 항목 6에 남는다 |
| 항목 6 — RHEL 8.10 현장 인수 | **미수행 — → [#265](https://github.com/jihoon22-lee/ici/issues/265)**. 이 환경은 RHEL이 아니다. checklist와 비민감 결과 양식은 [field-acceptance.md](field-acceptance.md) |
| 항목 7 — 실제 GHES/runner/action 버전·권한·sticky 재시도 | **미수행 — → [#265](https://github.com/jihoon22-lee/ici/issues/265)**. mock transport와 Ubuntu CI만 확인됐다 — 실제 GHES 확인을 성공으로 표기하지 않는다 |
| 성능 예산 승인 | **미승인**. §3 수치는 이 개발 환경의 기준선이며 runner 자원 근거가 없다 |
| syscall 수준 오프라인 증명 | **§4a로 대체** — `unshare -n`이 인터페이스 자체를 제거하므로 strace 없이도 "네트워크 호출이 있었으면 실패했어야 한다"가 증명됐다. 실제로 모두 통과했다 |

## 복구

치명 회귀 발견 시 candidate 사용을 중단하고 이전 bundle/설정으로 복귀한다
— 임계값을 낮춰 release blocker를 지우지 않는다(#226의 복구 조항).
