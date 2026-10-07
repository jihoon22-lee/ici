"""Shared analysis cores — parsers, tokenizers, and measurement primitives.

These modules were ``ici.engines._*`` before the stable engine shells were
separated from the analysis they wrap. They carry no engine classes and no
CLI knowledge: next-path ``adapters``/``languages`` import them directly, and
the remaining stable shells do too until the cutover completes.

The naming convention inside the package: a module relocated whole from
``ici.engines`` keeps a plain name (``coverage_support``, ``pytest_output``),
while a primitive extracted for sharing during the transition keeps the
``_`` prefix (``_ruff_output``, ``_dup_clustering``) as an implementation
detail whose shape may still move.
"""
