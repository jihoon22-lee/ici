# bundle 재현성: 1040건 → 0건

| | |
|---|---|
|상태|**측정 완료.** 동일 입력 두 빌드가 바이트 단위로 같다.|
|근거 이슈|[WP04 #202](https://github.com/jihoon22-lee/ici/issues/202) 작업 4|
|구현|[`scripts/assemble_bundle.py`](../../../scripts/assemble_bundle.py)|
|검증|[`tests/test_bundle_assembly.py`](../../../tests/test_bundle_assembly.py)|

## 출발점

[WP01 spike](spikes/wp01-runtime-environment.md)는 standalone bundle이 **돈다**는 것을
증명했다. 같은 bundle을 **두 번** 만들 수 있다는 것은 증명하지 않았다.
동일 입력으로 두 번 빌드해 재니 **1040개 파일**이 달랐다.

|원인|파일 수|
|---|---|
|`.pyc` — mtime 기반 무효화 (PEP 552 `flags=0`)|1033|
|`app/vendor/bin/*` — shebang에 빌드 출력 절대 경로|3|
|`*.dist-info/RECORD` — 위 스크립트의 해시를 기록|3|
|`manifest.json` — 위 digest들을 반영|1|

이슈가 이름 댄 두 가지(timestamp, build path)와 정확히 일치한다. 그래서 digest에서 가리는
대신 **원인에서 제거**했다.

## 네 단계, 각각 측정으로 유도됐다

### 1. hash 기반 무효화 — 1033건

`compileall --invalidation-mode unchecked-hash`. `checked-hash`가 아닌 이유는 bundle이
불변이기 때문이다 — import마다 소스를 다시 해싱하는 비용을 치를 이유가 없다. 답이 바뀔 수 없다.

미리 컴파일하는 것은 재현성 너머의 이유도 있다. **WP01이 read-only 마운트에서 도는 것을
확인했는데, 거기서는 없는 `.pyc`를 쓸 수 없다.**

### 2. `co_filename` 재작성 — 헤더가 같아진 뒤에도 본문이 달랐다

무효화만으로는 부족했다. 헤더가 일치한 뒤에도 본문이 달랐는데, **모든 code object가
`co_filename`, 즉 컴파일된 절대 경로를 담기 때문**이다. 두 빌드 경로가 같은 길이라
**파일 크기까지 같았고** 바이트 비교로만 보였다.

`-s <root> -p /ici-bundle/<name>`이 이를 bundle 상대 경로로 바꾼다.
부수 효과가 하나 더 있다 — bundled 모듈의 traceback이 릴리스가 빌드된 디렉터리가 아니라
bundle 기준 경로를 말한다.

**빌드 경로는 평문으로 보이는 스크립트 3개가 아니라 1000여 개 `.pyc` 전부에 박혀 있었다.**

### 3. 강제 재컴파일 — stdlib 41개가 살아남았다

`__pycache__`를 지우고 **번들 인터프리터로** compileall을 돌리면, 그 인터프리터가 기동하며
`argparse`·`enum`·`functools` 등을 import해 mtime 기반 `.pyc`를 새로 쓴다. compileall은
그것을 "최신"으로 보고 건너뛴다. **정규화 과정이 스스로를 무효화한다.** `-f`가 해결한다.

### 4. RECORD 정리 — 마지막 3건

`app/vendor/bin/*`를 지우자 RECORD가 **존재하지 않는 파일의 해시**를 가리키게 됐고,
그 해시가 빌드마다 달랐다. 해당 행을 지운다.

이건 재현성만의 문제가 아니다. #202 인수 기준은 **배포 체크섬이 배포 파일과 일치할 것**을
요구하는데, 지워진 스크립트를 기술하는 RECORD는 **아무도 검증할 수 없는 체크섬**이다.

(`.pyc` 행은 손댈 필요가 없다 — wheel이 원래 해시와 크기를 비워 기록한다.)

## `app/vendor/bin/`을 지워도 되는 근거

추측하지 않고 확인했다.

- 런처(`bin/ici`)는 인터프리터를 직접 실행하고 그 디렉터리를 **읽지 않는다**.
- 지운 뒤 `ici --version`과 `ici doctor --brief`가 정상 동작한다.
- `verify`가 쓰는 ruff는 `tools/python-static/`에 있지 거기 있지 않다.

의존성의 부수적 entry point이고, **bundle이 담아야 할 것은 필요한 것**이다.

## 라이선스 간극도 함께 메웠다

spike는 `licenses/`에 CPython LICENSE만 넣었고(그것도 실패를 무시하며),
**모든 bundled 의존성이 라이선스 없이 배포되고 있었다.** 이제 각 `.dist-info`에서
`LICENSE`/`LICENCE`/`COPYING`/`NOTICE`를 전부 수집한다 — 실측 13개.
여러 라이선스를 싣는 패키지(`packaging`의 APACHE/BSD)도 전부 가져온다.

## 결과

```
정규화 전 차이: 1040건
정규화 후 차이:    0건
```

정규화된 bundle이 정상 동작한다: `ici --version` → `ici 0.11.0`,
`ici doctor --brief` → caps PASS, 번들의 `ruff 0.15.8` 사용.

## 이 문서를 갱신하는 규칙

1. 새 비결정성이 발견되면 **원인과 파일 수를 측정해** 표에 추가한다. 추정하지 않는다.
2. digest에서 가리는 방식으로 해결하지 않는다. 원인에서 제거한다.
3. bundle에서 무언가를 뺄 때는 **빼도 도는지 확인한 근거**를 남긴다.
