# I2-4 분석 결과 캐시와 재현성 게이트

## 목표

반복되는 `ici verify`가 입력과 증거가 완전히 같은 엔진 결과만 안전하게 재사용하도록
user-local cache를 추가했다. 프로젝트 checkout은 읽기 전용으로 유지하고, 손상되거나
불완전한 entry는 성공 결과로 취급하지 않으며, 기존 `ici.result/v3` 소비자와 호환한다.

## 구현

책임은 세 모듈로 나눴다.

- `cache_identity.py`: project root, source/build config 내용과 mode, effective ici config,
  toolchain, engine descriptor·implementation, build variant, ici version을 key로 만든다.
- `cache_codec.py`: 최대 32 MiB의 untrusted JSON을 strict decode한다. duplicate key,
  `NaN`/`Infinity`, 비정규 파일과 symlink를 거부하고 finding·tool evidence·support matrix·
  artifact manifest를 타입별로 재구성·검증한다.
- `cache.py`: user-local `entries-v1`의 load/store/inventory/clear를 담당한다. 임시 파일에
  전체 entry를 쓰고 flush·`fsync`한 뒤 `os.replace`하며 새 directory/file은 0700/0600이다.

`VerifyOrchestrator`는 각 descriptor 실행 전에 cache를 조회한다. 완전한
`PASS`/`WARN`/`FAIL`만 저장하며 `ERROR`/`SKIP`/`NOT_RUN`, timeout, truncated output,
tool error, variant/config/toolchain이 다른 manifest, 변경·누락된 artifact는 miss로 처리한다.
`--no-cache`, `ici cache`, `ici cache --clear`를 추가했고 console·Markdown·HTML에는 hit 수,
JSON에는 optional `cache_hit`/nullable `cache_key`를 노출했다.

## 구현 중 발견해 막은 회귀

- report serializer는 legacy target을 finding으로 투영한다. 그 투영 결과까지 저장하면 hit 때
  native finding과 중복되므로 cache entry에는 native finding만 기록하도록 분리했다.
- 기본 `verify_report.json`과 engine별 `*_report.json`을 일반 JSON 입력으로 해시하면 report
  생성 자체가 다음 key를 바꾼다. 생성 report 이름을 source 후보에서 제외하고 회귀 테스트를
  추가했다.
- 자체 표준 검증이 새 코드의 mypy 오류 두 건과 source digest 함수 중첩 경고를 찾았다.
  변수 타입을 분리하고 cache narrowing을 명시했으며, 입력 발견·record 생성을 helper로 나눴다.
- hash 중 파일 identity가 바뀌면 해당 실행의 cache 전체를 끄고, oversize·duplicate-key·
  non-finite·entry/entries-dir symlink 공격은 모두 안전한 miss로 격하한다.

## 검증 결과

- Python 3.10 전체 테스트: `935 passed in 49.70s`
- Ruff: `check .` 및 `format --check .` 통과, 120 files formatted
- 표준 프로필 최초 실행: 118.49초, 12 engines, cache hits 0
- 동일 입력 재실행: 2.38초, cache hits 12
- cache metadata를 제외한 두 result SHA-256:
  `95af9c5122442411da60da0371b0938b89ca2095b562e02b08fe05f5eeb5bd70`
- 두 report의 finding 수: 각각 3,497
- 두 번째 HTML: 4,095,550 bytes, external refs 0
- pyz 이중 빌드 SHA-256:
  `6a629f9b162fdacbe84a82cd861eac622aebc47f3a9cae00915387e53fc21c16`
- 재현 빌드 전후 project source status unchanged, 전체 smoke 통과

## 남은 외부 증거

로컬 구현과 게이트는 완료됐다. 이 브랜치의 PR, required CI/Merge Gate, sticky comment와 실제
ici/viewer Pages HTTP·Zero-CDN 확인, main 병합 및 release evidence는 PR 단계에서 기록한다.
