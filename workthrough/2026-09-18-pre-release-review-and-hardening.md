# 배포 전 검토 — deep 프로필 자체검증 회귀 발견·수정, 문서 최신화

> PR: [#267](https://github.com/jihoon22-lee/ici/pull/267)
> 브랜치: `fix/next-artifact-anchoring`

## 배경

마일스톤 마감(cutover PR #264) 이후 사용자 요청으로 배포 전 전체 문서 최신화와
추가 버그·오류 검토를 수행했다. 검토 과정에서 `./dist/ici.pyz verify --profile deep`
재실행이 **Suite FAIL**을 보였고, 그 원인을 추적해 세 계열의 결함을 수정했다.

## 발견과 수정

### 1. 프로젝트 스위트 subprocess env에 인터프리터 bin dir 부재 — `test` FAIL

- 증상: deep 프로필에서 `test` 엔진이 38개 테스트 실패(전부 `ici next`
  e2e 테스트). 스위트는 `uv run pytest`에서는 전부 통과했다.
- 원인: `test`/`sanitize` 엔진이 스위트를 실행하는 subprocess env가
  `os.environ.copy()` 뿐이라 PATH에 `.venv/bin`이 없었다. `ici verify`가
  pyz로 직접 실행되면(개발자 venv 비활성 상태) 스위트 안의 `next verify`가
  mypy·ruff를 PATH에서 못 찾아 `python.type` 등이 INCOMPLETE가 되고
  exit 3으로 실패했다.
- 수정: `test_interpreter._build_python_test_env`와
  `_sanitize_python_scope`가 해석된 인터프리터(`python_cmd[0]`)의 부모
  디렉터리를 PATH 맨 앞에 둔다. bare 실행 파일명(parent == `.`)에는
  적용하지 않는다.
- 테스트: `test_python_test_env_puts_the_project_venv_tools_on_path`.

### 2. deadline 소진 후 자식 reap 누락 — `sanitize` FAIL

- 증상: sanitize 엔진이 "Memory / Resource Defect" 1건 보고.
- 원인: `core/runner._wait_process`가 `remaining <= 0`이면 `wait()` 없이
  반환했다. 타임아웃 kill은 이미 deadline을 소진한 뒤에 일어나므로, kill된
  자식이 reap되지 않고 GC에서
  `ResourceWarning: subprocess N is still running`으로 표면화됐다.
- 수정: deadline 소진 경로에서도 bounded reap(`wait(timeout=0.5)`)을
  시도한다. kill이 결과를 결정하고 reap은 회수만 한다.
- 테스트: `test_a_timed_out_child_is_reaped_not_left_a_zombie`.

### 3. weak-hash false positive + 공유 mutable 기본값 — `security`/`resource` WARN

- 증상: `security` 엔진이 `hashlib.sha1` fingerprint 7곳을
  "SHA-1 is not collision resistant"로 신고, `resource` 엔진이
  `application/verify`의 `task_components: Mapping = {}` 기본값을 지적.
- 판단: finding fingerprint는 dedup key이지 보안 토큰이 아니다 — 룰의
  false positive. `usedforsecurity=False`는 stdlib이 이 용도로 제공하는
  명시적 opt-out이다.
- 수정: `_risky_call_rule`이 `usedforsecurity=False` 키워드를 인식해
  hashlib md5/sha1 호출을 건너뛴다. 자체 fingerprint 7곳에 키워드를
  명시하고 `task_components` 기본값을 `None`으로 변경했다.
- 테스트: `test_usedforsecurity_false_marks_a_digest_as_identity_not_crypto`,
  `test_a_timed_out_child_is_reaped_not_left_a_zombie`.

### 4. next report/diff 경로 앵커 불일치 (선행 커밋들)

- `next report`/`next diff`가 상대 경로를 cwd 기준으로 resolve하던 것을
  `verify`/`publish`와 같은 워크스페이스 루트 기준으로 통일했다. 하위
  디렉터리에서 `verify --html`·`next report`·`--publish`가 어긋나던 문제를
  해소하고, config 오류(exit 2)에서는 결과 합성을 시도하지 않는다.

## 검증

- `./dist/ici.pyz verify --profile deep --report` 재측정:
  **Suite WARN — 3 engine(s): line, cognitive, complexity** (Pass 11 / Warn 3 /
  Fail 0 / Error 0 / Skip 2, TEM 4.81, test 4118/4133) — 부채 문서의 기록과
  정확히 일치.
- `pytest` 전체 green, `ruff check`/`format` clean, `mypy` 257 files clean,
  `build-pyz.sh` + `smoke.sh` 통과.

## 문서 갱신

- `self-verification-debt.md`: 측정일과 실측값을 2026-09-18로 갱신하고,
  스냅샷에 없던 non-PASS가 회귀로 처리된 경과를 기록했다.
- `roadmap.md`·`spec-05`: 최종 인수 체크리스트를 실측 상태로 갱신 —
  `[x]`는 자동화 근거가 있는 항목, `[ ]`는 #265 현장 확인 대기.
- README·user-guide·spec-01·ci-integration: cutover 이후 동작 반영.
- `field-acceptance.md`·`wp28-verification.md`·`idk-integration.md`:
  미수행 항목이 #265를 가리키도록 정리.

## 남은 것

- RHEL 8.10·GHES·idk 실환경 검증과 stable 경로 제거 승인은 #265(마일스톤 밖)
  에서 계속 추적한다.
