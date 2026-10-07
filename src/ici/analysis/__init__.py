"""Shared analysis cores — parsers, tokenizers, and measurement primitives.

These modules were ``ici.engines._*`` before the stable engine shells were
separated from the analysis they wrap. They carry no engine classes and no
CLI knowledge: next-path ``adapters``/``languages`` import them directly, and
the remaining stable shells do too until the cutover completes.

The ``_`` module prefix is the package-internal convention kept from
``ici.engines``: a module whose consumers all live inside ``ici`` keeps the
underscore even though it is imported across subpackage boundaries. Rename a
module only when it graduates to a public package API.
"""
