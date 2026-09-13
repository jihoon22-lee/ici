"""Execution-side infrastructure: processes, artifacts, locks, caches.

ARCH section 3 gives this package that job. WP02 PR B (#200) opens it with the
result and event store, because writing a result is writing an artifact and
SPEC-04 section 5 requires that write to be atomic. The process runner (#205),
the DAG (#208) and the cache (#209) land here later.

Unlike ``ici.domain``, this package does I/O — that is the whole reason it is
separate.
"""

from __future__ import annotations
