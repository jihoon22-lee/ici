# ADR-0002 — 전용 런타임을 포함한 압축 디렉터리로 배포한다

- 상태: **accepted (evidence pending)** — 방향은 결정. 정확한 버전·digest·지원 하한은 시험 후 고정.
- 결정 시점: WP00 ([#198](https://github.com/jihoon22-lee/ici/issues/198))
- 근거 이슈: [ARCH #192 §1](https://github.com/jihoon22-lee/ici/issues/192),
  [SPEC-02 #194 §1·§2](https://github.com/jihoon22-lee/ici/issues/194)
- 관련 요구사항: R02, R03, R11
- 담당 WP: [#202](https://github.com/jihoon22-lee/ici/issues/202)

## 결정

ici를 **전용 CPython을 포함한 압축 디렉터리**로 배포한다. 구조는
[spec-02 §1](../spec-02-distribution-execution.md)이 정의한다.

```
ici-<version>-linux-x86_64/
  bin/ici  runtime/python/  app/
  tools/python-static/  tools/cpp-static/
  templates/  schemas/  licenses/  manifest.json
```

- install directory는 **읽기 전용이어도 동작해야 한다.**
- `verify`/`init`/`doctor`/`plan`/`report` 중 다운로드·pip·uv 설치를 호출하지 않는다.
- 기존 `dist/ici.pyz`는 **전환 기간의 안정 배포물로 보존한다.**

## 대안

| 대안 | 기각 사유 |
|---|---|
|현행 pyz + 시스템 인터프리터 탐색 유지|R03이 요구하는 전용 런타임이 아니다. 대상 환경의 Python 버전에 계속 종속된다. `scripts/launcher.sh`가 후보 6개를 순차 탐색하는 방식은 여러 Python이 있는 환경에서 선택이 불투명하다|
|각 프로젝트 `.venv`에 ici를 설치|R02가 줄이려는 반복 설치 의존 그 자체다. 현행 ruff·mypy 탐색이 이미 이 문제를 갖는다|
|시스템 패키지(rpm/deb) 배포|root 권한이 필요하다. R11의 "root 불필요"와 충돌한다|
|`.venv` 디렉터리를 그대로 복사|경로 이동 시 깨진다. spec-02 §1이 이 방식으로 이동 가능성을 주장하지 말라고 명시한다|
|단일 실행 파일 (PyInstaller 등)|네이티브 정적 도구를 함께 담기 어렵고, 읽기 전용 설치·도구별 절대 경로 요구와 맞추기 복잡하다|

## 근거

1. **두 실행 환경을 분리해야 한다.** ici core의 분석 도구와 프로젝트의 Python/GCC/Qt는 서로
   오염되어서는 안 된다([ARCH §6](../architecture.md)). 전용 런타임이 있으면 core가 프로젝트
   site-packages에 의존하지 않고, 프로젝트 테스트는 실제 프로젝트 Python으로 돌릴 수 있다.
2. **현행에 실제 위반이 있다.** `engines/test_interpreter.py:13`이 `.venv` 후보 실패 시
   `sys.executable`로 fallback한다. 즉 프로젝트 테스트가 ici core의 인터프리터로 실행될 수 있다.
   `lint.py:529`·`type_check.py:155`는 ruff·mypy를 프로젝트 `.venv`에서 찾는다.
   → [inventory/execution-flow.md §4](../inventory/execution-flow.md) 1·2·3행
3. **재현성 불변식이 이미 있고 작동한다.** `scripts/build-pyz.sh:38`이
   `EXPECTED_UV_VERSION="0.12.5"`를 정확히 고정한다. 이번 측정 환경의 uv는 0.8.17이었고,
   이 고정이 잘못된 버전으로 빌드하는 것을 막았다. 이 자산은 spec-02 §1의
   "build input lock digest"로 발전시킬 대상이다.
   → [inventory/baseline-measurements.md §3.2](../inventory/baseline-measurements.md)

## 호환 영향

| 영향 | 내용 |
|---|---|
|배포물 추가|새 bundle이 추가된다. 기존 pyz는 전환 기간 유지|
|`ICI_PYTHON` 환경변수|새 bundle 경로에서는 불필요해진다. pyz 경로에서는 계속 유효|
|`scripts/launcher.sh` ↔ `core/env.py` 순서 일치 불변식|pyz 경로에만 적용된다. AGENTS §4에서 범위를 명시함 → [ADR-0003](0003-agents-invariant-scoping.md)|
|순수 wheel 제약|새 bundle 경로에서는 네이티브 정적 도구를 담을 수 있다. pyz 경로에서는 계속 금지|

**두 런타임 경로를 영구 유지한다고 약속하지 않는다.** 제거 시점은 보류 항목 10번이다.

## 복구

새 bundle이 지원표를 충족하지 못하면 기존 pyz 경로로 되돌린다. 그러기 위해:

- pyz 빌드 스크립트·launcher·재현성 검증을 **삭제하지 않고 유지한다.**
- `.github/workflows/` 4개 워크플로를 유지한다.
- 이번 WP00에서 위 항목을 하나도 제거하지 않았다.

## 보류 항목 (release blocker 표시)

| # | 항목 | 결정 조건 | blocker |
|---|---|---|---|
|1|CPython 정확한 버전·패치·배포 digest (3.13 / python-build-standalone은 **후보**)|실제 bundle 제작 후 실행 시험|**예**|
|2|RHEL 8.10에서 runtime과 LLVM/Clang/clazy 등 네이티브 패키지 호환|현장 확인. **개발용 Ubuntu/WSL 통과로 대체할 수 없다**|**예**|
|3|배포 CPU/ISA 하한 (보수적 ISA 우선 후보)|현장 CPU/ABI 확인|**예**|
|4|bundle에 담을 정적 도구 조합과 재배포 라이선스|제작 단계 검토|**예**|
|10|기존 pyz 경로의 제거 시점|새 bundle이 지원표 충족 후 별도 PR|아니오|

**이 값들을 추정으로 확정하지 않는다.** 현재 측정 상태는
[inventory/baseline-measurements.md §4](../inventory/baseline-measurements.md)에 있고,
pyz 재현성조차 이번 환경에서 **검증하지 못했다.**
