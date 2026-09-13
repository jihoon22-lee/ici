"""Reporters: showing a stored result, and never producing one.

The architecture rule is one line — *"reporter는 provider를 실행하지 않는다"* —
and #206 item 5 repeats it as "분석과 render 호출은 분리한다". A reporter that
could run a tool would make looking at a result capable of changing it, and two
people reading "the same" report could be reading different runs.

So everything here takes a :class:`~ici.domain.result.RunResult` and returns
text. There is no path from this package to ``execution`` or to a provider.
"""
