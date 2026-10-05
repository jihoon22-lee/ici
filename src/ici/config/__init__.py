"""Workspace configuration for the ici-next path.

The package reads ``schema_version = 1`` documents — see ``schema`` for the
grammar, ``composition`` for layering, ``discovery`` for root finding, and
``migration`` for the stable-to-next preview command. The v0.11-era global
``DEFAULT_CONFIG``/``load_config`` surface is gone: reads happen per command
through ``ici.config.composition`` and never write a user-level default.
"""
