# ici — Integrated CI Engine

개발 환경(WSL/Linux)과 **사내 폐쇄망**(RHEL 8.10/CentOS, tcsh/bash), **GitHub Actions**에서 같은 정책·결과 계약으로 동작하는 C++/Python CI/CD 품질 게이트입니다. OS·컴파일러·Python·검증 도구의 가용성과 버전은 실행 증거로 기록되며, 환경이 다르면 실제 결과도 달라질 수 있습니다.
단일 ZipApp 실행 파일(`ici.pyz`, 약 2MB) 또는 self-contained 번들로 배포됩니다.

```bash
$ ici verify --result result.json   # exit 0 pass / 1 fail / 3 incomplete
$ ici report --result result.json --out report.html
$ ici doctor                        # 각 check가 필요로 하는 도구와 현재 상태
```

ici는 **실행한 것만 판정한다**: 필수 check가 끝나지 못한 run은 `INCOMPLETE`이지
`PASS`가 아니며, 실행 중 download·pip·설치를 호출하지 않습니다. 결과 문서는
`schema_id="ici.next.run"`이고 실행 완료·증거 수준·범위·finding·게시 상태가 서로
다른 축으로 기록됩니다.

### 현재 릴리스

공개 stable 릴리스는 [v0.11.0](https://github.com/jihoon22-lee/ici/releases/tag/v0.11.0)이며
`ici.pyz`의 SHA-256은 `334bcda1bf127ff18ca1931cb55a8ac6e62af2d687ac90ab13c6af2e5499bc1f`다.

`main`에는 아직 stable로 승인되지 않은 후속 범위가 있다. package/wheel contract, deep
test-quality 관측, SARIF 출력, ELF binary compatibility, typed integration case,
compiler-backed C++ 분석과 gcov JSON coverage 정책이 여기에 해당한다. 대부분 설정에서
명시적으로 켜야 하며, 켜지 않으면 기존 동작은 바뀌지 않는다.

버전별 변경과 각 항목의 CI·Pages 실측 증거는 아래에 있다. README는 그 증거를 복사하지
않는다.

- [CHANGELOG](CHANGELOG.md) — 버전별 변경과 릴리스 증거
- [ici-next 설계](docs/design/ici-next/README.md) — **전환의 계획·설계·결정 기록.** 개발 마일스톤은 완료됐고 RHEL/GHES 현장 인수는 [#265](https://github.com/jihoon22-lee/ici/issues/265)가 추적한다
- [인수인계 문서](docs/superpowers/2026-08-30-handover.md) — 2026-09-04 시점의 맥락과 결정 이유 *(시점 기록)*
- [workthrough](workthrough/) — 개별 작업의 실측 기록
- [CI/CD 연동 가이드의 candidate 채널](docs/ci-integration.md#5-candidate-채널-stable-release가-아님) — candidate artifact와 Quality Zoo 인수 절차

### 릴리스 정책

- `feature`·`test`·`refactor`·`docs` PR은 버전 변경이나 stable release를 자동으로 만들지 않습니다.
- `patch`는 이미 공개된 stable artifact의 defect·security·compatibility 수정에만 사용합니다.
- `minor`는 사용자에게 보이는 응집된 roadmap checkpoint이며, ici 전체 gate·실제 도구 E2E·candidate cross-repo/toy 검증·PR/main CI·Pages·문서/CHANGELOG가 모두 끝난 뒤에만 정합니다.
- pre-release/candidate artifact는 stable이 아니며, 하나의 PR이 하나의 릴리스를 뜻하지 않습니다. `v0.10.1`과 공개된 `v0.10.2`는 공개 결함에 한정한 corrective stabilization입니다.
- `v0.11.0`은 I4-4, real toy-projects/quality-zoo 검증(I5·I6·I7·I8·I9)을 닫은 뒤 냅니다. **I4-3은 두 항목이 열린 채로 남습니다** — C++ complexity/cognitive를 AST 기반으로 바꾸는 것과 whole-program dead-symbol reachability입니다. 둘 다 없는 같은 인프라(C++ AST 접근)를 필요로 하고, 둘 다 **없는 기능이 아니라 이미 내보내는 값의 정밀도** 문제라, 한계를 명시적으로 표시한 채 배포합니다: 해당 metric은 `bounded-cpp-tokens` estimate로 표기되고 `metric_confidence`로 등급이 함께 나가며, 전체 목록은 [한계 인벤토리](docs/engine-reference.md)에 있습니다. 이 두 항목이 닫히기 전까지 그 숫자를 exact 측정값으로 인용하면 안 됩니다.

---

## 📚 문서 허브 (Documentation Hub)

| 문서 | 설명 | 바로가기 |
|---|---|---|
| **🧭 ici-next 설계** | **전환의 canonical source.** 목표 아키텍처·SPEC 5종·ADR·실측 기록. 개발 완료, 현장 인수는 [#265](https://github.com/jihoon22-lee/ici/issues/265) | [docs/design/ici-next/](docs/design/ici-next/README.md) |
| **🚀 사용자 가이드** | 설치, `ici.toml` 설정, CLI 사용법, 결과 문서 읽는 법 | [docs/user-guide.md](docs/user-guide.md) |
| **📏 체크 레퍼런스** | Python 16종 / C++ 16종 check와 finding→gate 규칙 | [docs/engine-reference.md](docs/engine-reference.md) |
| **⚙️ CI/CD 연동 가이드** | GitHub Actions job 구조, dogfood·publish 분리, candidate 채널 | [docs/ci-integration.md](docs/ci-integration.md) |
| **🏛️ 시스템 아키텍처** | 현재 레이어 구조와 계약 지점 | [docs/architecture.md](docs/architecture.md) |
| **🗂️ 품질 분석기 실행 계획** | *(superseded — ici-next가 대체)* v0.11.0까지의 로드맵 기록 | [ici 마스터 계획](docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md) · [toy-projects 마스터 계획](https://github.com/jihoon22-lee/toy-projects/blob/main/docs/superpowers/plans/2026-08-30-product-portfolio-master-plan.md) |
| **📋 변경 이력 (CHANGELOG)** | 버전별 상세 릴리스 노트 및 마일스톤 | [CHANGELOG.md](CHANGELOG.md) |
| **📜 개발 및 기여 규약** | 브랜칭 전략, 커밋 룰, 런타임 제약 및 불변식 | [AGENTS.md](AGENTS.md) |

---
## 🚀 핵심 특징

1. **Fail-closed 게이트**: 필수 check가 실행되지 못하거나 끝나지 못한 run은 `PASS`가
   아니라 `INCOMPLETE`다. finding이 있어도 `INCOMPLETE`를 덮지 않는다.
2. **오프라인·결정적 도구 선택**: `verify`/`plan`/`doctor`/`report`는 네트워크를
   열지 않고 설치를 호출하지 않는다. 번들로 실행되면(`ICI_BUNDLE_ROOT`) 분석 도구는
   번들 소속본이거나 unavailable이며, PATH에 뭐가 있느냐로 결과가 달라지지 않는다.
3. **선언된 인터프리터**: `[components.*.python] executable`은 workspace 루트에
   앵커되어 검증된다. `sys.executable`은 프로젝트 인터프리터의 fallback이 아니다.
4. **ici는 빌드하지 않는다**: C++ check는 선언된 build tree(`[builds.*]` —
   compile DB·ctest·gcov·ELF 산출물)를 읽는다. 빌드가 없으면 blocked이고,
   blocked 필수 check는 `INCOMPLETE`다.
5. **다섯 축의 결과 문서**: `ici.next.run` 결과는 실행 완료·증거 수준·선택 범위·
   finding·게시 상태를 따로 기록한다. 부분 실행은 전체 판정으로 위장되지 않는다.
6. **Fingerprint baseline**: `--baseline`/`ici diff`로 신규·해소·잔존 finding을
   비교한다. fingerprint는 checkout 경로와 OS 구분자에 무관하다.
7. **Zero-CDN HTML**: `ici report`의 페이지는 외부 에셋이 없어 폐쇄망에서도 연다 —
   smoke.sh가 생성물을 기계적으로 검증한다.

## 💻 빠른 설치 및 사용법

```bash
# 산출물 복사 및 실행 권한 부여
mkdir -p ~/.local/bin
cp dist/ici.pyz ~/.local/bin/ici && chmod +x ~/.local/bin/ici

# 프로젝트 루트에서
ici init          # ici.toml 골격 작성
ici doctor        # check별 필요 도구와 현재 상태
ici plan          # 실행 없이 선택될 check와 blocked 사유 확인
ici verify        # 실행 + .ici/next/result.json 기록
ici report        # HTML(+SARIF) 렌더
```

소스 빌드: `./scripts/build-pyz.sh`(hermetic 재현 빌드) → `./scripts/smoke.sh`.

종료 코드: `0` PASS · `1` FAIL(위반) · `2` 요청 거절 · `3` INCOMPLETE · `130` 취소.

## 📋 명령어 일람

| 명령어 | 설명 |
|---|---|
| `ici init` | `ici.toml` 골격 작성 (`--preview`, `--force`) |
| `ici migrate` | stable 형식 설정을 next 스키마로 변환 (`--write`, `--output`) |
| `ici doctor` | 선택된 check의 도구·인터프리터 가용성 |
| `ici plan` | 선택 check·해결된 도구·blocked 사유 (`--require-full`은 부분 범위를 실패로) |
| `ici verify` | check 실행 + 결과 저장 (`--result`, `--baseline`, `--profile`, `--python`/`--cpp`/`--component`, `--no-cache`, `--events`) |
| `ici report` | 저장된 결과 렌더 (`--out` HTML, `--sarif`) |
| `ici diff <baseline>` | 두 저장 결과의 finding delta |
| `ici publish` | 결과 페이지를 설정된 백엔드(gh-pages/GHES)로 게시 |
| `ici next …` | 같은 명령의 호환 별칭 |

자세한 옵션·설정 형식·결과 문서는 [사용자 가이드](docs/user-guide.md)와
[체크 레퍼런스](docs/engine-reference.md)에 있다.
