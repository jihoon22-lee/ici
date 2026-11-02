# AGENTS.md R9 cutover 초안 (검토용, 미적용)

- 상태: **draft for root review.** 이 파일은 R9 cutover 이후의 `AGENTS.md` 제안 전문이다.
  현재 `AGENTS.md`는 바꾸지 않았다. 프로젝트 지침 파일 변경은 사용자 승인 아래 root가 적용한다.
- 적용 방법: R9 cutover 변경에서 아래 `---` 사이의 본문으로 `AGENTS.md`를 교체한다.
  `tests/next/test_ici_next_inventory.py`의 `TestAgentsScoping`이 요구하는 문구
  ("적용 범위", "stable 경로", "next 경로", "Python 3.10 하한", "py3-none-any", "dist/ici.pyz",
  "PYTHON_CANDIDATES", "시스템 CA", "Zero-CDN HTML")는 역사 절에 남겨 두었다. 그 시험은 같은
  cutover 변경에서 `tests/next/**`와 함께 제거되므로([제거 체크리스트 §2-D](r9-removal-checklist.md)),
  문구 유지는 적용 순서와 무관하게 중간 commit이 깨지지 않게 하기 위한 것이다.
- 바뀌는 점 요약: 제품 경로가 하나(Rust native)가 된다. 기존 §3/§4/§6의 Python 제품 규칙과
  §8–§10의 전환 규칙은 역사 기록이 되고, Rust toolchain·native 배포·Rust gate가 규범이 된다.
  §1 브랜치·PR, §2 커밋·문서, §5 설계 원칙, §7 릴리스 cadence, 시스템 CA는 그대로 유지한다.

---

# AGENTS.md — ici 개발 규약

이 문서는 `ici` 프로젝트에 기여하는 사람 및 LLM 에이전트가 **반드시 지켜야 하는 불변식(Invariants)**을 정의한다.

## 적용 범위

ici는 **Rust native 단일 제품**이다([ADR-0008](docs/design/ici-next/adr/0008-rust-native-replacement.md),
[실행 계획](docs/design/rust-replacement-execution-plan.md) R0–R9). 제품 코드·진입점·자체 분석·
report·publish·설치·업데이트는 Rust이며 `crates/`에 있다. 아래 §1–§7이 모든 변경에 적용된다.

과거의 두 경로 — v0.13.x까지 공개된 `dist/ici.pyz` Python 제품(**stable 경로**)과 전용 CPython
bundle을 목표로 한 **next 경로** — 는 R9 cutover로 종료됐다. 그 규약은 §9에 역사 기록으로 남는다.
과거 공개 artifact, tag, Git history는 바꾸지 않는다.

경로와 무관하게 항상 적용되는 제약: §1 브랜치·PR 워크플로, §2 커밋·문서 규약, §3의 시스템 CA 조항,
§5 코드 설계 원칙(위치 추적·리포터 분리·root 배제·Zero-CDN·노이즈 최소화), §7 릴리스 cadence.

---

## 1. 브랜치 및 GitHub PR 기반 개발 전략 (Branching & PR Workflow)

- **`main` 브랜치 직접 푸시/작업 금지**: 모든 변경 사항은 반드시 목적에 맞는 별도 브랜치에서 작업한 후, **GitHub Pull Request(PR)**를 통해 `main`에 병합(Merge)해야 한다.
- **브랜치 네이밍 규칙**:
  - `feat/<feature-name>`: 새로운 check·provider·리포터, CLI 옵션, 핵심 기능 개발
  - `fix/<issue-name>`: 버그 수정, lint/Clippy 오류 해결, 예외 처리 개선
  - `refactor/<target>`: 기능 변경 없는 구조 개선, 복잡도 분리, 모듈화
  - `docs/<doc-name>`: 문서 추가, `CHANGELOG.md` 및 가이드 작성
  - `test/<test-name>`: 테스트 케이스 추가 및 검증 로직 보강
  - `chore/<task-name>`: 빌드 스크립트, 의존성, CI/CD 설정 변경
- `I4-3`, `T0`, `B1`, `D2`, `R9` 같은 roadmap 코드를 PR 제목/요약의 primary 또는 sole 내용으로
  삼지 않는다. 제목은 사용자에게 보이는 결과나 기술적 결과를 설명해야 하며, roadmap key는
  필요할 때만 body의 mapping 항목으로 덧붙인다.
- **GitHub PR 병합 절차 (Standard GitHub PR Workflow)**:
  1. 작업 브랜치 생성: `git checkout -b <type>/<description>`
  2. 코드 구현 및 품질 게이트 검증 (§6)
  3. Conventional Commit 메시지로 작업 브랜치에 커밋: `git commit -m "<type>(<scope>): <summary>"`
  4. GitHub 원격 저장소로 브랜치 푸시: `git push -u origin <type>/<description>`
  5. GitHub CLI로 PR 생성: `gh pr create --title "<type>(<scope>): <summary>" --body "..."`
  6. PR 머지 수행: `gh pr merge --squash --delete-branch`
  7. 로컬 `main` 브랜치 동기화: `git checkout main && git pull origin main`

---

## 2. 커밋 규약 및 문서화 불변식 (Strict Rules)

- **작업 단위별 즉각 커밋 의무**: 의미 있는 기능 구현, 버그 수정, 리팩토링, UI 개선 단위 작업이 완료될 때마다 즉시 Git 커밋을 수행해야 한다.
- **Conventional Commits 준수**:
  - `feat:` 새로운 check·provider·리포터, CLI 옵션, 핵심 기능 추가
  - `fix:` 버그 수정, lint/Clippy 에러 수정, 예외 처리 개선
  - `refactor:` 복잡도 감소, 모듈 분리, 클론 코드 제거 등 구조 개선 (동작 변경 없음)
  - `docs:` 문서화, `CHANGELOG.md`, `README.md`, 가이드 업데이트
  - `test:` 테스트 케이스 추가 및 검증 로직 보강
  - `chore:` 빌드 스크립트, 패키징 설정, 패키지 메타데이터 변경
- **CHANGELOG 및 문서 동기화 의무**:
  - 기능 변경, UI/UX 개선, 정책 추가, 버전 변경이 일어날 때마다 **반드시 [`CHANGELOG.md`](CHANGELOG.md)에 상세 변경 내역을 기록**하고 필요한 경우 `README.md`도 즉시 동기화한다.

---

## 3. 런타임·의존성 제약

- **고정 toolchain**: Rust는 `rust-toolchain.toml`의 1.99.1이며 모든 build·test·Clippy는
  `--locked`(`Cargo.lock`)로 실행한다. toolchain 변경은 native 배포 입력
  (`packaging/native/upstream-source-lock.json`의 표준 라이브러리 catalogue·std file tree)과
  release 검사를 함께 갱신하는 별도 변경이다.
- **의존성**: 새 crate는 license와 upstream 고지를 확인한 뒤 추가한다. 고지는 실제 build의
  Cargo metadata로 생성되며 누락은 `missing_upstream_notice`로 남는다 — 저작권자·연도를 지어내지 않는다.
  native 도구(Ruff/ty)나 runtime 구성 요소를 바꾸면 `packaging/native/`의 digest·license·notice를
  실제로 다시 검증한다.
- **Python 없는 제품**: ici 소유 Python 분석 엔진·worker·embedded CPython·Python fallback을 제품에
  넣지 않는다. 프로젝트 Python/pytest/coverage/mypy는 외부 검사 도구이며, 제한 probe는 ADR-0008
  허용 목록만 쓴다. 자체 AST/규칙 계산을 Python에 위임하지 않는다.
- **개발 harness Python**: `tests/rust-parity/**` capture/verify driver와 `quality-zoo/runner`는
  개발 전용이다. 제품 archive에 들어가지 않으며 Ruff(`target-version = "py310"`)와 각 단위 시험을 따른다.
- **시스템 CA 및 TLS** *(전 경로 적용)*: 시스템/사설 CA·proxy(`HTTPS_PROXY`/`NO_PROXY`,
  `SSL_CERT_FILE`/`SSL_CERT_DIR`)를 지원한다. 루트 저장소를 임의로 대체하거나 TLS 검증을 끄지 않는다.
  설치·명시 setup 외에 분석 중 다운로드·암묵 설치를 하지 않는다.

---

## 4. 패키징 및 배포

- **산출물**: `ici-<version>-x86_64-unknown-linux-musl.tar.gz`(`ici.native-distribution` v1 manifest,
  `bin/ici`, 검증된 `tools/bin/{ruff,ty}`, `licenses/`)와 `.sha256`. `scripts/build-native.sh`만 만든다.
- **고정 입력**: build 입력은 `scripts/prepare-native-inputs.sh`가 `packaging/native`의 digest로 확인한
  것만 쓴다. build script는 compiler나 도구를 암묵적으로 설치하지 않는다.
- **exact source와 재현성**: release archive는 깨끗한 exact-source tree에서 만든다(`--allow-dirty`는 개발
  snapshot). 같은 host의 두 독립 build(`--build-id`)는 byte 동일해야 한다. manifest가 build host의 kernel·
  CPU·compiler digest를 기록하므로 다른 host 재build의 동일 digest는 주장하지 않는다.
- **정적 실행 파일과 고지**: core와 동봉 도구는 ELF interpreter·`DT_NEEDED`가 없어야 하고, archive는
  Rust/musl/LLVM 고지와 `packaging/native/{sources,third-party}`를 바이트 그대로 포함해야 한다
  (`scripts/check-native-release.sh`).
- **설치·복구**: `install-native.sh`와 `ici setup`은 root 없이 XDG 경로에 설치하고, 검증 전에는 현재
  선택을 바꾸지 않으며, rollback은 사용자 설정·결과를 덮어쓰지 않는다.
- **공개**: `release-native.yml`이 같은 exact-main commit의 검증된 candidate bytes를 다시 build하지 않고
  게시한다. 새 Python wheel/sdist/pyz는 발행하지 않는다.
- **지원 주장**: Linux/WSL x86_64는 측정 범위의 `limited`다. 새 플랫폼·CPU·libc는 실제 검증 없이 지원한다고
  쓰지 않는다.

---

## 5. 코드 설계 원칙

1. **위치 추적 필수**: 모든 check/provider는 PASS/FAIL 여부와 무관하게 검사된 모든 대상의 프로젝트 상대
   경로와 1-based 라인(선택적 column)을 결과에 남긴다. parser offset은 byte로 보존하고 표시 위치 변환은
   source-map 경계 한 곳에서 한다.
2. **다중 리포터 분리**: 분석·실행 로직은 출력(터미널/Markdown/HTML/JSON/SARIF)과 분리되며, 저장된 결과
   (`ici.next.run` v2와 legacy projection)를 리포터 계층이 읽는다. 게시 실패가 분석 gate를 바꾸지 않는다.
3. **루트 권한 배제**: 어떤 명령어도 `sudo`나 루트 권한을 요구하지 않아야 한다.
4. **Zero-CDN HTML**: HTML 리포터는 외부 네트워크 연결 없이 100% 로컬 인라인 CSS/JS/SVG로 동작해야 한다.
5. **노이즈 최소화**: 통과(PASS)된 수많은 정상 항목이 리포트를 도배하지 않도록 스마트 그룹핑 및 이슈 중심(Issues-First) 뷰를 기본으로 한다.
6. **상태 축 분리**: 실행 완료·증거 수준·선택 범위·코드 위반·게시 상태를 독립적으로 표현한다. 부분 실행
   성공이나 미실행·불완전을 전체 게이트 성공으로 표시하지 않는다(불완전은 종료 코드 3).
7. **원본 보존**: 사용자 config·baseline·suppression·saved result를 새 형식으로 자동 덮어쓰지 않는다. 새
   state/cache는 `.ici/rust-v1` namespace를 쓰고 migration은 preview와 새 출력/백업을 제공한다.
8. **환경 분리**: 프로젝트 Python/GCC/Qt와 ici 분석 도구를 섞지 않는다. native tool pack 경로를 프로젝트
   검색 경로 앞에 전역 삽입하지 않는다. `devenv.csh` 등 셸 초기화 파일을 탐색·source하지 않는다.
9. **분석 관점 보존**: 19 descriptor·36 check와 기존 명령/설정/결과 계약의 유지·통합·폐기는 근거와 이전
   경로를 남긴다([이전 원장](docs/design/rust-replacement/inventory.md)). 옮기기 어렵다는 이유만으로
   기능을 삭제하지 않는다.

---

## 6. 테스트 및 품질 게이트

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
python3 scripts/rust_migration_inventory.py --check
(cd quality-zoo && python3.10 -m unittest discover -s tests -v)
uvx ruff check . && uvx ruff format --check .        # 남은 harness Python
```

- 위가 모든 변경의 기본 게이트다. workflow를 바꾸면 `actionlint`, shell script를 바꾸면 `shellcheck`도 실행한다.
- 실제 외부 도구가 필요한 시험은 `#[ignore = "explicit actual …"]`로 표시된 명시 게이트다. provider·tool·
  build adapter를 바꾸면 해당 도구를 준비하고 `--include-ignored`로 실제 실행한다. 도구가 없어 실행하지
  못했다면 그 사실과 사유를 PR 근거에 남긴다 — **실행하지 못한 게이트는 "통과"로 적지 않는다.**
  일반(non-ignored) 시험은 CI runner에 없는 도구나 host Python 패키지를 요구하지 않는다.
- 배포물(`scripts/build-native.sh`, `install-native.sh`, `prepare-native-inputs.sh`, `packaging/native/**`,
  `ici-distribution`)을 바꾸면 native candidate 검사(두 독립 build 동일, `check-native-release.sh`,
  `tests/rust-parity/distribution/verify_artifact.py`)를 실행한다.
- release 판정은 `--require-closed` 원장, exact-main native candidate, Quality Zoo native 인수를 요구한다.
- 시험 개수는 기능 동등성의 지표가 아니다. golden을 무조건 재생성해 회귀를 통과시키지 않는다.

---

## 7. 릴리스 버전 및 cadence 불변식

- `feature`, `test`, `refactor`, `docs` PR은 버전을 자동으로 올리거나 stable release를 자동으로
  만들지 않는다. PR 병합과 릴리스 결정은 별개다.
- `patch` 버전은 이미 공개된 stable artifact의 defect, security, compatibility 수정에만 사용한다.
- `minor` 버전은 사용자에게 보이는 하나의 응집된 roadmap checkpoint에만 사용하며, ici 전체
  gate, 실제 도구 E2E, candidate의 Quality Zoo 검증, PR/main CI·Pages, 문서·CHANGELOG
  동기화가 모두 끝난 뒤에만 결정한다.
- pre-release와 candidate artifact는 stable release가 아니며 stable artifact의 근거로 사용하지 않는다.
  공개 bytes는 검증한 exact-main candidate와 같은 bytes여야 한다.
- 하나의 PR이 하나의 릴리스를 의미하지 않는다. 여러 PR을 하나의 릴리스로 묶을 수 있고,
  릴리스가 필요 없는 PR은 별도 릴리스 없이 병합할 수 있다.
- 릴리스 판정은 ici가 소유한 Quality Zoo corpus로 한다(R13). toy-projects/Lens 같은 외부 저장소에
  ici 전용 환경파일이나 특수 폴더 구조를 요구하지 않으며, 외부 소비자 검증은 released/candidate ici의
  명시적 소비자 검증으로만 유지할 수 있다.
- 0.13.x Python 제품의 일반 유지보수는 종료됐다. 예외적 보안 수정은 이전 tag 기반 별도 작업으로 범위를 정한다.

---

## 8. 공유 host의 무거운 작업

- 여러 제품이 같은 host를 쓰는 동안 무거운 build·전체 suite·corpus는 공유
  `flock /tmp/paseo-products-20261028-heavy.lock`과 jobs/thread 상한 2를 사용한다. lock 파일은
  만들거나 지우지 않는다. 다른 제품 코드·사용자 자료·공유 cache는 수정/삭제하지 않는다.
- cargo target 디렉터리와 큰 출력은 `/tmp`(tmpfs)에 두지 않는다.

---

## 9. 역사 기록 — 종료된 stable/next 경로 규약

아래는 R9 cutover 전까지 유효했던 규약의 요약이다. 새 변경에는 적용하지 않으며, 과거 artifact와
문서를 해석할 때만 참조한다.

- **stable 경로**(v0.11.x–v0.13.x `dist/ici.pyz`): Python 3.10 하한(`requires-python = ">=3.10"`, ruff
  `py310`, `tomllib` 금지), 순수 파이썬 의존성(`py3-none-any` 휠만), `scripts/launcher.sh` polyglot
  런처와 `src/ici/core/env.py`의 `PYTHON_CANDIDATES` 순서 동기화(`tests/test_launcher.py`), 고정 `uv`
  버전의 재현 pyz build, `pytest`·`ruff`·`build-pyz.sh`·`smoke.sh` 게이트. v0.13.0 공개 pyz
  (`c179986f3d0eac110cf436a961458e56c0f4a218517e9883ff47cd67addc1cde`)는 frozen oracle이자 명시 복귀 대상이다.
- **next 경로**(ici-next, [docs/design/ici-next/](docs/design/ici-next/)): 전용 CPython bundle(ADR-0002)을
  목표로 한 설계. 환경 분리·셸 초기화 금지·폐쇄망·상태 축·`ici.next.run` schema id·분석 관점 보존·측정
  규율·PR 규약은 §3–§6에 이어졌고, bundle·embedded CPython은 ADR-0008로 대체됐다.
- **2026-10-28 전환 승인**: 범용 제품 전환([generalization-progress](docs/design/generalization-progress.md))과
  Rust 단일 제품 전환([실행 계획](docs/design/rust-replacement-execution-plan.md), ADR-0008). R8 인수와 R9
  cutover의 근거는 실행 계획과 [제거 체크리스트](docs/design/rust-replacement/r9-removal-checklist.md)에 있다.
