# `ici next` 게시 워크플로와 GHES 호환표 (WP25)

- 근거 이슈: [WP25 #223](https://github.com/jihoon22-lee/ici/issues/223),
  [spec-04 §7](spec-04-results-integration.md)
- 상태: **구현·mock 테스트 완료. GHES 실제 환경 검증은 미수행** — 아래
  호환표의 "미확인" 항목이 정직한 현재 상태다.

## 1. 권한 경계 — analyze와 publish는 다른 job이다

PR 소스를 실행한 job에 게시용 자격증명을 주지 않는다. 분석 job은 결과
artifact만 남기고, 게시 job은 신뢰된 base ref의 코드에서 결과를 읽어
밀어 넣는다. `pull_request` 이벤트로 온 코드에 `contents: write` 토큰이
붙는 순간, PR을 연 누구든 저장소에 쓸 수 있다 — 그래서 둘을 나눈다.

```yaml
# 예시 — 사내 GHES Actions. ref는 실제 검증된 버전으로 고정한다.
jobs:
  analyze:
    runs-on: [self-hosted, restricted]
    permissions:
      contents: read            # 게시 권한 없음
    steps:
      - uses: actions/checkout@<pinned-sha>
        with: { ref: ${{ github.event.pull_request.head.sha }} }
      - run: ici next verify || true      # FAIL/INCOMPLETE도 게시 대상
      - run: ici next report
      - uses: actions/upload-artifact@<pinned-sha>
        with:
          name: ici-result
          path: .ici/next/

  publish:
    needs: analyze
    runs-on: [self-hosted]
    permissions:
      contents: write           # gh-pages push
      pull-requests: write      # sticky comment
    steps:
      # 신뢰된 base ref의 ici를 설치한다 — PR 코드에 publish를 맡기지 않는다.
      - uses: actions/checkout@<pinned-sha>
        with: { ref: ${{ github.event.pull_request.base.sha }} }
      - uses: actions/download-artifact@<pinned-sha>
        with: { name: ici-result, path: .ici/next }
      - env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: ici next publish --result .ici/next/result.json --out .ici/next/result.html
```

`ici next publish` 자체가 경계를 지킨다: 저장된 `result.json`과
`result.html`만 읽고 프로세스·provider를 실행하지 않으며, 결과 파일을
수정하지 않는다(발행 결과는 `.ici/next/publish.json`에 별도 기록).

## 2. 설정

```toml
[publish]
repo = "org/repo"                      # 생략 시 GITHUB_REPOSITORY
api_url = "https://ghes.internal/api/v3"   # 생략 시 GITHUB_API_URL; https만
server_url = "https://ghes.internal"       # run 링크용
branch = "gh-pages"
token_env = "GITHUB_TOKEN"             # 변수 '이름' — 값을 파일에 쓰면 거부
```

- 게시하지 않는 workspace는 `[publish]`를 쓰지 않는다 — `next publish`를
  명시 호출했는데 목적지가 없으면 NOT_CONFIGURED + exit 2(조용한 성공은
  게시로 위장하지 않는다).
- 업로드 경로는 `ici/<workspace>/pr-<n>/<head-sha>/index.html` — head SHA가
  경로에 들어가므로 늦게 끝난 이전 run이 현재 head의 리포트를 덮지 못한다.
- sticky 댓글 마커는 `<!-- ici-next:<workspace> -->`로 repo+PR+workspace에
  안정적이며, 댓글 본문의 `head:`/`run:` 메타데이터로 stale head와
  out-of-order attempt 덮어쓰기를 차단한다.

## 3. GHES / runner / action 호환표

|항목|상태|근거|
|---|---|---|
|Contents API PUT 파일|tested (mock)|`adapters/ghes.py` 계약 + 19개 mock 테스트|
|Issue comment upsert·페이지네이션|tested (mock)|`find_comment` 20페이지 한계까지 순회|
|PR head SHA 조회·stale 차단|tested (mock)|`/pulls/{n}` head.sha 비교|
|Pages `html_url` 링크|tested (mock)|미활성 시 "다운로드 경로"로 명시 구분|
|TLS/CA|시스템 CA (stdlib `urllib`)|사내 인터셉션 CA는 runner OS 신뢰 저장소에 등록 필요|
|실제 GHES 버전별 API 차이|**미확인**|사내 GHES 버전 확인 전 지원 완료로 쓰지 않는다|
|actions/upload-artifact·download-artifact GHES 호환|**미확인**|버전별 제약은 현장에서 고정한다 — 공개 github.com CI의 major를 그대로 복사하지 않는다|
|self-hosted runner에서의 end-to-end|**미확인**|연결 가능한 테스트 저장소/runner 검증은 별도 권한 범위|

## 4. 실패와 재시도

- 업로드 실패·댓글 실패 → `publish.json`에 `FAILED` + 사유, exit 1.
  `next publish`를 다시 실행하면 저장된 결과를 다시 읽어 재시도한다 —
  분석은 반복되지 않는다.
- `quality` verdict(FAIL/INCOMPLETE)와 publish 상태는 별개다 —
  `publish.json.state`만 게시 성패를 말하고 result의 `gate`는 불변이다.
- artifact가 저장소 밖이거나 64 MiB를 넘거나 result가 모르는 스키마면
  네트워크 호출 전에 거부한다(exit 2, `PublishError`).
