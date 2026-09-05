# WSL ext4 개발 워킹트리 이관

## Overview

Windows 드라이브의 DrvFS 워킹트리에서 WSL 내부 ext4 clone으로 개발 환경을 옮기면서,
파일 실행 권한과 머신 고정 문서 링크를 정리했다. Python 환경과 모든 배포 산출물은 새
워킹트리에서 재생성하고 프로젝트의 Python/C++/Qt 검증을 다시 수행했다.

## Context

- DrvFS에서는 `core.filemode=false`여서 실행 비트 누락이 드러나지 않았다.
- 새 ext4 clone에서는 문서에 적힌 `./scripts/build-pyz.sh`와 `./scripts/smoke.sh`가
  `100644` 모드 때문에 직접 실행되지 않았다.
- 개발 규약의 CHANGELOG 링크가 특정 머신의 절대 경로를 사용하고 있었다.
- 기존 가상환경과 빌드 캐시는 경로가 내장될 수 있으므로 복사하지 않고 재생성해야 했다.

## Changes Made

### 실행 권한 기록

다음 entrypoint의 Git mode를 `100644`에서 `100755`로 변경했다.

- `scripts/build-pyz.sh`
- `scripts/launcher.sh`
- `scripts/smoke.sh`
- `tools/ici`

### 경로 독립 문서화

- `AGENTS.md`: CHANGELOG 링크를 머신 고정 `file://` URL에서 `CHANGELOG.md` 상대 링크로
  변경했다.
- `CHANGELOG.md`: ext4 실행 권한 수정의 원인과 영향을 `Unreleased`에 기록했다.

### 환경 재구성

- Python 3.10.21을 사용해 `uv sync --frozen --group dev`로 `.venv`를 새로 만들었다.
- `dist/`, Python build 디렉터리, CMake 빌드 디렉터리를 새 워킹트리에서 재생성했다.
- 원격에 없는 로컬 브랜치 `chore/drop-viewer-workarounds`도 별도 Git ref로 보존했다.

## Code Examples

문서 링크는 저장소 위치와 무관하게 동작한다.

```markdown
[`CHANGELOG.md`](../CHANGELOG.md)
```

품질 게이트의 직접 실행 형태도 ext4 clone에서 그대로 사용할 수 있다.

```bash
./scripts/build-pyz.sh
./scripts/smoke.sh
./tools/ici --version
```

## Verification Results

### Python 품질 게이트

```text
ruff check: PASS
ruff format --check: 91 files already formatted
pytest: 559 passed
Python: 3.10.21
```

### 패키징과 스모크

```text
두 번의 연속 zipapp 빌드 SHA-256:
8e249d5e4fd7999fec729987a288f55507a01ed854be142f0953d6709d446f20

dist/ici == dist/ici.pyz
Python 3.10 직접 실행: PASS
Zero-CDN HTML 검증: PASS
```

### C++/Qt viewer

- 정적 `icirv` Release 빌드와 실제 report parse: PASS
- `icirv-gui` Qt6 Release 빌드: PASS
- 실제 report를 연 8초 offscreen smoke: PASS
- viewer 통합 검증: 적용 가능한 엔진 전체 PASS, C++ tests 3/3

루트 self-verification은 기존 코드 기준 경고 5개를 보고했지만 FAIL/ERROR는 0개였다. 별도
pytest, ruff, 패키징 게이트는 모두 통과했으며 이 경고는 이관으로 생긴 회귀가 아니다.

## Next Steps

- PR CI에서 동일 품질 게이트가 통과한 뒤 squash merge한다.
- 기능 동작에 필요한 추가 이관 작업은 없다.
