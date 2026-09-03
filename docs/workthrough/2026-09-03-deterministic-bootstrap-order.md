# Deterministic ZipApp bootstrap order — 2026-09-03

## 목적

동일한 소스 tree를 서로 다른 checkout 경로에서 빌드했을 때 raw ZipApp의 bootstrap entry
순서가 달라지는 문제를 재현하고, archive member 순서를 명시적인 계약으로 고정한다. 이
workthrough의 범위는 entry ordering이며, zlib 버전이나 플랫폼 전체의 byte identity를
주장하지 않는다. stable `ici v0.10.2`는 유지하며 이 작업만으로 release를 만들지 않는다.

## 재현

PR #149 head와 squash-merged main의 tree object는 모두
`cb9e37fb9a150ef1008582e92b53e0afea599a12`로 동일했고 locked package-tool graph도 같았지만,
최종 launcher-prefixed ZipApp의 member 순서는 다르게 관찰됐다.

| 빌드 | `_bootstrap/` 관련 관찰 순서 | 최종 ZipApp SHA-256 |
|---|---|---|
| local checkout | `filelock` → `interpreter` → `environment` | `0379ed9f88eaa20a0123c78e0afb67114601775cebb0e538a59a06a6d6a94f6e` |
| candidate run `33730141563` | `environment` → `filelock` → `interpreter` | `8296166d3a288c63efc1c096bdb61bbd58380c64cd889f423c7bb35eb2ae04ab` |

차이는 shiv `1.0.8`이 `importlib.resources`의 `iterdir` 결과를 정렬 없이 소비한 데서
비롯됐다. 파일시스템 enumeration 순서는 checkout 환경에 따라 달라질 수 있으므로, 같은
입력의 archive가 서로 다른 member order를 가질 수 있었다.

Candidate 자체의 provenance와 내부 계약은 유효했지만, 이 cross-checkout divergence를 발견한
뒤 해당 digest를 toy-projects selector로 승격하지 않았다. 이 수정이 main에서 재검증된 뒤 새
candidate를 발급한다.

## 변경

- `build-pyz.sh`는 caller의 `python3`가 아닌 이미 선택된 helper Python으로
  `scripts/run_shiv.py`를 실행한다.
- wrapper는 shiv의 private bootstrap resource iterator 결과를 archive member 이름으로
  정렬한 뒤 shiv CLI에 위임한다.
- reproducibility verifier는 duplicate member를 거부하고
  `site-packages/` → `_bootstrap/` → `environment.json` → `__main__.py` 순서를 요구한다.
- focused purity assertions는 raw `-m shiv` 경로가 되살아나지 않고, wrapper의 정렬과
  verifier의 duplicate/order checks가 유지되는지 확인한다.

## 검증 상태

| Gate | 결과 |
|---|---|
| Bootstrap/build focused regression | `tests/test_pyz_assembly.py`, `tests/test_purity.py`, `tests/test_shiv_order.py` — 51 passed |
| Full Python 3.10 | 2,189 passed, 7 environment-dependent C++ tool skips |
| Static quality | Ruff check/format 197 files, mypy 109 sources, shell syntax, actionlint — PASS |
| Adversarial two-build verifier | 2,292,199 bytes, SHA-256 `424108397858470b1209bc2749b580a858fb06c8b09aaa2e4772c94e43690bb5`, byte-identical |
| Cross-path/source-mtime | 별도 절대 경로, 2002-03-04로 바꾼 `src/`·`scripts/` mtime, hostile caller environment에서 같은 SHA-256 — PASS; 임시 tree 삭제 |
| Packaged smoke | 직접/Python 3.10 실행, doctor/env, artifact identity, self verification, Zero-CDN — PASS |
| Version/release | `0.10.2` 유지, tag/release 없음 |

PR/exact-main CI·Pages와 새 candidate의 cross-run digest는 아직 remote acceptance 전이므로
별도로 확정한다. 기존 candidate `33730141563`은 이 원인을 드러낸 재현 증거이지 새
toy-projects selector의 입력이 아니다.
