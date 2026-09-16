# idk integration contract — ici producer side (WP26 / #224)

`ici next verify`가 생산하는 두 산출물과 그것을 소비하는 UI(이하 idk) 사이의 계약을
정리한다. 이 문서는 **ici가 소유하는 절반**이다 — producer 계약과, 그 계약만으로 동작하는
fixture consumer(`tests/idkconsumer.py`). idk 어댑터와 UI는 idk 저장소의 별도 이슈/승인
사항이며, 이 fixture가 통과했다고 idk 통합이 완료된 것으로 기록하지 않는다.

## 1. 호출 계약

idk는 준비된 환경에서 ici를 실행한다. ici는 셸 초기화 파일을 탐색·해석·source하지
않으므로(AGENTS §8), idk 측에서 PATH/작업 디렉터리를 확정한 채 호출한다.

```
ici next verify [--result PATH] [--events PATH] [--json] \
    [--component ID ...] [--python] [--cpp] [--profile fast|standard|deep] \
    [--require-full] [--baseline PATH] [--no-cache]
```

- `cwd`는 워크스페이스 루트(`ici.toml`이 있는 곳)다. `ici.toml` 발견 규칙은
  SPEC-01을 따른다.
- stdout은 사람이 읽는 요약(또는 `--json` 시 result 문서), stderr는 진단이다.
- 이벤트 스트림은 `--events PATH`로 지정된 파일에만 쓰인다 — stdout/stderr에
  JSONL이 섞이지 않는다(SPEC-04 §6).
- 종료 코드: `0` PASS, `1` FAIL, `2` 설정/입력 오류, `3` INCOMPLETE(필수 증거 부족),
  `130` 사용자 취소(SIGINT/SIGTERM).
- 독립 실행과 idk 경유 실행의 selected scope·result 의미는 동일하다 — 계약이
  분기하는 곳이 없다. `tests/test_next_consumer.py`가 이를 검증한다.

## 2. 결과 문서 — authoritative

`--result`가 가리키는 파일(기본 `.ici/next/result.json`)은
`schema_id="ici.next.run"`, `schema_version=1` 문서다. 스키마:
`src/ici/schemas/ici-next-run-v1.schema.json`.

consumer가 해석해야 하는 축:

- `gate.selected` / `gate.workspace` — 선택 범위와 워크스페이스 전체의 판정이
  분리돼 있다. 부분 scope의 PASS를 전체 PASS로 표시하지 않는다.
- `scope.selected_components` / `omitted_components` / `full_required_satisfied` —
  어떤 범위가 평가됐고 무엇이 빠졌는가.
- `execution.required_complete` / `cancelled` / `blocked_task_ids` /
  `failed_task_ids` / `reused_task_ids` — 필수 작업이 끝났는가, 어떤 task가
  왜 못 끝났는가. INCOMPLETE는 결과에 finding이 있어도 verdict가 아니다.
- `findings[]` — `rule_id`, `severity`, `confidence`, `provider`,
  `primary_location{path,start_line,...}`, `component_id`, `variant`,
  `task_id`, `suppression`, `limitations`. `component_id`는 plan이 task를
  세운 component로 스탬핑된다(제공자가 직접 스탬핑한 경우 그 값이 우선).
- `producer.ici_version` — 이 문서를 만든 ici 버전.

모르는 `schema_id`는 거부한다 — 비어 있는 PASS로 읽지 않는다(consumer도 동일하게
`ContractError`를 올린다).

## 3. 이벤트 스트림 — advisory

`schema_id="ici.next.event"`, `schema_version=1`, JSONL. 한 줄이 한 `RunEvent`:
`run_id`, 단조증가 `seq`, ISO-8601 `timestamp`, `event_type`, optional
`task_id`/`component_id`/`message`.

이벤트 타입: `run.started`, `plan.ready`, `task.started`, `task.progress`,
`task.completed`, `diagnostic`, `run.completed`.

생명주기 의미:

- `task.started`는 **작업이 실제로 시작됐을 때만** 나온다 — 취소됐거나, 선행
  task 실패로 blocked됐거나, 캐시가 답을 재사용한 task는 started를 보고하지
  않는다. `task.completed`는 모든 task에 나오며 `message`가 최종 상태
  (`succeeded`/`failed`/`blocked`/`cancelled`/`cache hit …`)를 담는다.
  started 없이 completed만 있는 task는 "실행 없이 확정됐다"로 읽는다.
- 취소는 `diagnostic` 이벤트(`cancelled: <reason>`)로 기록된 뒤
  `run.completed`가 온다 — 스트림이 조용해지는 게 아니라 끝이 선언된다.

consumer 규칙:

- `seq` 간격·중복은 보고하되 고치지 않는다.
- 모르는 `event_type`은 skip한다 — 확장은 additive optional 필드로만 이뤄지며,
  breaking change는 `schema_version`을 올린다.
- 마지막 줄이 잘려 있으면(프로세스가 죽은 모습) 그 줄만 버리고 이전 이벤트는
  살린다.
- 스트림과 result가 어긋나면 **result가 이긴다**. 스트림은 진행 상황이고,
  최종 판정·finding·scope는 result에서만 읽는다. 잘린/빠진 이벤트가
  authoritative 결과를 바꾸지 않는다.

## 4. 소스 열기와 workspace 매핑

`primary_location.path`는 워크스페이스 기준 상대 경로다. idk는 자신의 workspace
매핑에 대입해 연다. 단, 워크스페이스 밖을 가리키는 경로(`..` 등)는 열지 않는다 —
fixture consumer의 `Location.resolve`가 이 거부를 구현한다.

## 5. 취소·재시도·부분 결과

- SIGINT/SIGTERM은 ici가 받아 자식 task까지 정리하고, 부분 result를 쓴 뒤
  `130`으로 종료한다. result의 `execution.cancelled`가 true이고 verdict는
  `INCOMPLETE`다.
- 재시도는 idk가 다시 `ici next verify`를 호출하는 것이다 — 부분 result 위에
  "이어서" 실행하는 계약은 없다. 캐시가 증거 수준에서 재사용하므로 재실행은
  싸다.
- result 파일이 없거나(실행 도중 사망) 읽을 수 없으면 그 사실을 그대로
  표시한다 — 없는 결과를 PASS로 간주하지 않는다.

## 6. 민감 값

- 이벤트와 result에 자격 증명·토큰·비밀 값을 쓰지 않는다. `message`에는 task
  상태와 사유만 온다.
- publish 자격 증명은 `token_env`가 가리키는 환경 변수로만 전달되며
  (`publish-workflow.md` 참조), 이벤트 스트림·result·로그에 기록되지 않는다.
- consumer는 받은 문자열을 데이터로만 표시한다 — HTML에 넣을 때 escape하는
  것은 consumer 측 책임이다(ici의 자체 HTML 리포터가 동일하게 escape한다).

## 7. 소유권 경계

| ici가 소유 | idk가 소유 |
|---|---|
| result/event 스키마와 producer 구현 | idk 저장소의 adapter·UI 코드 |
| `--events` 스트림의 생명주기 의미 | 이벤트→프로그레스 표시 변환 |
| `tests/idkconsumer.py` fixture | 실제 idk 소비 로직과 그 테스트 |
| 종료 코드·취소·부분 result 계약 | 취소 신호 전달·재시도 UX |

fixture consumer가 old/new/partial/cancelled 이벤트와 result를 처리하는 것으로
계약 측은 검증됐다. 실제 idk 통합(어댑터 구현, UI 연결)은 idk 저장소 작업이며
별도 추적한다 — 이 문서의 fixture 통과를 idk 통합 완료로 기록하지 않는다(#224 항목 7).

## 검증 근거

- `tests/test_cli_next_path.py` — 이벤트 순서, 캐시 재사용 시 started 부재,
  취소 시 diagnostic 이벤트.
- `tests/test_schedule.py` — started/completed 경계: blocked·cancelled·cache-hit
  unit은 started를 보고하지 않는다.
- `tests/test_next_result_io.py` — JSONL 코덱: 잘린 마지막 줄, seq 간격·중복,
  unknown type, foreign 문서.
- `tests/test_next_consumer.py` — idk 비의존 consumer가 result+events를 해석하고
  standalone verify와 동일한 verdict/scope/finding 의미를 얻는다.
