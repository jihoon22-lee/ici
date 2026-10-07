# defect_bed

`tests/test_next_differential.py`의 Python 결함 parity 매트릭스가 쓰는 시드 fixture다 —
내부 텍스트 check 하나당 심은 결함 하나를 stable 엔진과 `next verify`가 모두 보고하는지
대조한다. 결함은 양쪽 경로의 임계값 어느 쪽도 넘도록 의도적으로 과장돼 있다.

|파일|심은 결함|stable 엔진|next check|
|---|---|---|---|
|`src/insecure.py`|`eval`·`pickle.loads`·`shell=True`|SecurityEngine|python.security|
|`src/swallow.py`|`except: pass` 삼킨 예외|ExceptionSafetyEngine|python.exception|
|`src/hot.py`|과잉 순환/인지 복잡도|ComplexityEngine|python.complexity|
|`src/dead.py`|미참조 private 함수|DeadCodeEngine|python.dead|
|`src/leaky.py`|닫지 않는 `open()`|ResourceEngine|python.resource|
|`src/oversized.py`|코드 라인 1000+ 초과|LineCountEngine|python.line|
|`src/pkg/alpha.py`·`beta.py`|상호 import 사이클|CycleEngine|python.cycle|
|`src/clone_a.py`·`clone_b.py`|동일 본문 clone 쌍|DuplicateEngine|python.dup|

clone 쌍은 파일-상태 parity가 아니라 occurrence 좌표 parity로 비교한다 — stable은
clone 그룹을 informational target으로 보고하기 때문이다. cycle은 primary 위치가
경로마다 다르다(stable: alpha, next: beta) — 비교는 primary + related 위치의 합집합으로
이뤄진다.
