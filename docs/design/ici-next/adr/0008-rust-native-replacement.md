# ADR-0008 — Rust 단일 제품과 검증된 단일 cutover

- 상태: **accepted direction; R0 evidence in progress** (2026-10-28 사용자 실행 승인).
- 대체: ADR-0001 Python core, ADR-0002 CPython bundle 및 AGENTS §3/4/6/8/9의 새 제품 적용.
- 실행/인수: [R0–R9 계획](../../rust-replacement-execution-plan.md).

## 결정과 적용 경계

CLI/config/discovery/execution/자체 분석/results/report/publish/install은 Rust로 구현한다.
제품 안에 ici Python backend, worker, embedded CPython, fallback을 두지 않는다.
외부 프로젝트 검사 도구는 독립 process로 실행하며 ici core의 실행 환경과 구분한다.
C++/Qt viewer는 별도 기존 native 소비자로 유지한다.

기존 `src/ici`, Python packaging 및 gate는 공개 v0.13.0 기준선으로 동결한다.
R8 전체 기능 원장의 실제 증거가 닫히기 전에는 삭제하지 않는다. Rust 개발용 후보는
내부 인수 대상이며 stable 제품이 아니다. R9 한 cutover에서 제품 source/dispatch/배포와
게이트를 함께 교체한다. 과거 공개 artifact/tag와 Git history는 바꾸지 않는다.

§3 Python 3.10/순수 wheel 및 §4 pyz/launcher 동기화는 기존 Python 제품에만 적용한다.
Rust는 Cargo.lock/toolchain pin과 Rust fmt/Clippy/test, 기능별 실제 corpus,
재현 archive/설치 검증을 적용한다. 기존 gate를 먼저 없애 Rust 성공을 주장하지 않는다.
시스템 CA, 사용자 사설 CA, proxy, TLS 검증, no-root, Zero-CDN, 원본 보존,
증거/범위/실행/판정/게시 축 분리, 브랜치/PR 규약은 계속 적용한다.

## 공유 구현 계약

하나의 Cargo workspace에서 다음 경계를 사용한다. 의존 방향은 아래로만 흐른다.

1. `domain`: wire-compatible finding/span/measurement/check/scope/execution/gate/event,
   snapshot, canonical identity. 파일/프로세스 I/O와 policy를 포함하지 않는다.
2. `config`/`discovery`: 원본을 읽어 출처가 있는 effective config와 workspace를 만든다.
3. `execution`: argv/cwd/env/timeout/output limit/process group/DAG/cancellation/atomic storage.
   provider는 직접 subprocess를 만들지 않고 이 경계를 사용한다.
4. `analysis`/`providers`: 자체 Rust 분석 또는 명시 외부 도구의 raw observation.
   threshold/aggregation/baseline은 application이 적용한다.
5. `reporting`/`publication`: 같은 저장 result를 읽는다. 게시 실패가 분석 gate를 바꾸지 않는다.
6. `application`/`cli`: 위 경계를 연결한다. legacy CLI/config/result는 공통 의미의 projection이다.

첫 공유 wire 계약은 기존 `ici.next.run` v2, `ici.next.event` v1,
`ici.auxiliary` v1이다. result v1/legacy v3는 읽기/projection 호환을 유지한다.
Rust 내부 타입 이름은 wire를 변경하는 근거가 아니다. JSON golden과 기존 schema를 먼저 검증한다.
각 source 위치는 프로젝트 상대 path, 1-based line, 선택적 column이다.
parser offset은 byte로 보존하며 display 위치 변환은 source-map 경계 한 곳에서 수행한다.
parser/외부 도구마다 column 의미를 숨겨 섞지 않고 adapter에서 변환한다.

## 설정·identity·이전 상태

Sparse config는 `schema_version=2`를 쓴다. v1 workspace 및 legacy 형식은 명시 reader로
같은 effective model에 투영한다. `ici.toml` → `pyproject.toml [tool.ici]` 순이고,
`--config`는 지정 source만 읽는다. components 생략과 명시 빈 목록은 다른 의미다.
scalar 교체/map key 합성/exclude 교체/extend_exclude 추가를 구분한다.
init은 no-config와 같은 resolver의 최소 선언을 쓰며 기존 파일을 자동 덮어쓰지 않는다.

새 cache/state는 `.ici/rust-v1` 및 사용자 cache의 `ici/rust-v1` namespace다.
현재 Python cache는 읽어서 새 cache인 것처럼 쓰지 않는다. binary build digest, tool bytes,
argv/env, policy, 입력 closure/config/plugin/lockfile을 identity에 넣는다.
미확정 closure는 cache 재사용과 resolved 증거를 제한한다.

finding normalization v3는 호환 fixture가 증명하는 rule/provider에 한해 유지한다.
읽을 수 있는 baseline이 비교 가능하다는 뜻은 아니다. check별 정책/toolchain/scope가
비교 가능한 경우 new/unchanged를 유지하되 미완료/누락 scope는 carried로 남긴다.
비교 불가 fingerprint를 대량 new로 바꾸지 않고 incompatible/비교 제한과 새 baseline
생성 방법을 보고한다. `--fail-on-new`가 미실행/불완전을 PASS로 바꾸지 않는다.

## 제한된 외부 Python probe 허용 목록

모든 probe는 명시 선택된 **프로젝트 Python**에만 수행한다. core fallback은 없다.
plan은 argv를 표시하지만 실행하지 않는다. 공통 실행기로 process group/timeout/output cap을
적용하며 출력 파싱 실패는 unknown/incomplete다. 입력은 argv/JSON으로 전달하고 shell eval은 없다.

|목적|argv/입출력|효과와 한도|
|---|---|---|
|runtime version/implementation/platform|`<python> -I -c <fixed metadata script>` → JSON version/implementation/platform|읽기, 5초, 64 KiB|
|sysconfig/ABI metadata|`<python> -I -c <fixed sysconfig script>` → JSON SOABI/EXT_SUFFIX/platform|읽기, 5초, 64 KiB|
|선언한 compatibility import-only|`<python> -I -c <fixed importlib script> <root> <JSON modules>`|사용자가 선언한 모듈 import 효과; main 실행 금지, 30초, 1 MiB, bytecode 쓰기 차단|
|명시 설치/runtime 검증|프로젝트의 `-m pytest`, `-m coverage`, `-m mypy`, build 도구|probe가 아닌 외부 test/build task; 계획과 작업 소유 산출물/timeout 명시|

AST/token/security/resource/complexity/cognitive/dup/TEM/PEP metadata 분석을 위 probe로
위임할 수 없다. qmake compiler recorder는 같은 Rust binary의 숨겨진 내부 mode다.
`python -m <project module>`은 import-only와 동등하지 않으므로 대신 쓰지 않는다.

## 배포·복구·미확정 evidence

기본 지원 목표는 Linux/WSL x86_64 native archive와 rootless installer다.
실제 libc/CPU 하한, parser pin/문법, native tool pack 버전/digest/license 및 TLS transport는
[parser 평가](../../rust-replacement/parser-evaluation.md)와
[tool/transport 평가](../../rust-replacement/tool-transport-evaluation.md)의 실측으로 고정한다.
평가 전 값은 supported로 주장하지 않는다. 구성 변경은 해당 evidence를 다시 확인한다.

`ICI_PYTHON`은 새 core 런타임 선택에 사용하지 않으며 설정돼 있으면 doctor에서 이전
Python 설치 변수임을 안내한다. 기존 `.venv/bin/ici` 등 PATH/절대경로 충돌을 진단한다.
rollback은 공개 v0.13.0을 명시 선택하는 절차이며 사용자 config/result를 옛 파일로
복구하지 않는다. tool pack은 설치/setup 단계에서만 네트워크를 사용할 수 있다.
분석 중 묵시 설치는 금지한다. RHEL/GHES 현장은 별도 실측 지원이며 일반 릴리스의
일괄 blocker가 아니다. 미시험 macOS/native Windows 동등 지원은 주장하지 않는다.

## 검증과 closure

전체 기능 inventory + check/rule별 양성·음성·실패 fixture와 실제 도구 E2E,
known-bug 교정 기대값, Python 없는 실행/subprocess trace, TLS 실패/사설 CA/proxy,
설치·업데이트·rollback·공개 byte 검증이 필요하다. 테스트 수는 parity 수치가 아니다.
R0 spike는 R1–R9 완료가 아니다. 하나라도 미판정/회귀/기능 누락이 있으면 R8/R9는 열린다.
