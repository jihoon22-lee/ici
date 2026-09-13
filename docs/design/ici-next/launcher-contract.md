# 런처 계약: 격리와 보존을 동시에

| | |
|---|---|
|상태|**측정 완료.** 합성 번들 테스트 18건 + 실제 번들 실측.|
|근거 이슈|[WP04 #202](https://github.com/jihoon22-lee/ici/issues/202) 작업 3·5, 인수 기준 2·3|
|구현|[`scripts/bundle/launcher.sh`](../../../scripts/bundle/launcher.sh)|
|검증|[`tests/test_bundle_launcher.py`](../../../tests/test_bundle_launcher.py)|

## 서로 당기는 두 요구

|요구|이유|
|---|---|
|**보존**|프로젝트 자식에게 **사용자가 실제로 갖고 있던 환경**을 넘겨야 한다. core가 필요로 한 환경이 아니라|
|**격리**|상속된 `PYTHONPATH`가 core의 모듈을 **가려서는 안 된다**|

둘 다 얻는 것이 핵심이다. 순서가 그것을 만든다 — **보존이 먼저** 일어나고, 그 다음 격리한다.

## 실측

`import하면 raise하는 typer.py`를 담은 `PYTHONPATH`로 실행한 결과:

```
core PYTHONPATH        = None          ← 격리: core가 가려지지 않는다
ICI_ENTRY_PYTHONPATH   = /tmp/poison   ← 보존: 원래 값이 남아 있다
ICI_ENTRY_VIRTUAL_ENV  = /fake/venv
QTDIR                  = /opt/qt6      ← 건드리지 않는다
SSL_CERT_FILE          = <보존>
```

**Python 관련 3개(`PYTHONPATH`·`PYTHONHOME`·`VIRTUAL_ENV`)만 지운다.** Qt·툴체인·나머지는
core까지 그대로 간다 — C++ 분석이 사용자가 설정한 툴체인을 물려받으려면 그래야 한다.

`SSL_CERT_FILE`/`SSL_CERT_DIR`는 **의도적으로 지우지 않는다.** WP01이 이 런타임의 OpenSSL이
정적 링크라 그 변수를 지우면 cafile이 `None`이 되는 것을 측정했다 — "격리"를 이유로 지웠다면
내부 서버에 대한 TLS가 조용히 깨졌을 것이다.

## bundle 경로를 export하지 않는 이유

bundle의 `app`·`app/vendor`는 `-c` 안에서 `sys.path`에 넣는다. `PYTHONPATH`로 export하면
**ici가 실행하는 모든 도구에 번들의 vendored 패키지가 밀려들어 간다.**

## 재배치

|시나리오|근거|
|---|---|
|다른 위치로 이동|✓|
|공백 포함 경로|✓|
|심볼릭링크로 실행|`readlink -f` 덕분. 없으면 `BUNDLE`이 링크가 있는 디렉터리로 풀려 런타임을 못 찾는다|
|두 버전 병행|각자 자기 root를 푼다 — 두 번째 설치가 첫 번째를 가로채지 않는다|

## install directory에 쓰지 않는다 (작업 5)

실제 번들에서 분석을 돌린 뒤 **번들 안에 새로 쓰인 파일 0개**, 프로젝트에도 남는 것 없음.

이것을 **사는 것은 PR A의 사전 컴파일**이다. bytecode가 미리 있지 않으면 첫 실행이
install directory에 `__pycache__`를 쓴다 — 여기서는 무해하지만 **WP01이 측정한 read-only
마운트에서는 불가능하다.** 테스트가 그 인과를 명시한다.

## 하드코딩된 런타임 버전을 고쳤다

spike 런처는 `bin/python3.13`을, `assemble_bundle.py`는 `bin/python3`을 읽고 있었다.
**둘이 갈라져 있어서** 런타임 버전이 오르면 런처만 깨진다. 이제 둘 다 `bin/python3`
(심볼릭링크)을 쓰고, 테스트가 런처 본문에서 버전 한정 이름을 금지한다.

## 런처가 파일이 된 이유

spike는 런처를 빌드 스크립트 안 heredoc으로 갖고 있었다 — **읽을 수도 테스트할 수도 없다.**
이제 `scripts/bundle/launcher.sh` 한 파일이고 spike가 그것을 복사한다. 두 벌을 두면 갈라지는데,
위의 `python3.13`/`python3` 불일치가 정확히 그렇게 생겼다.

## 이 계약을 바꿀 때의 규칙

1. 변수를 **추가로 지우려면** 그것이 프로젝트 자식에게 필요 없다는 근거를 남긴다.
   `SSL_CERT_FILE`이 반례다.
2. 격리를 강화할 때 **보존이 깨지지 않는지** 같이 측정한다. 둘은 서로 당긴다.
3. 런처를 복제하지 않는다.
