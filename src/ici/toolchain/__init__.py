"""Choosing which tool to run, and recording why (WP06 #204).

Separate from ``ici.core.toolchain``, which is the stable path's probe cache.
This package answers a different question: not "is gcc installed" but "which
interpreter runs *this project's* tests, and what do I tell a user who wants a
different one".
"""
