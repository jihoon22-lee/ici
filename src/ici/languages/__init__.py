"""Language packs: what each language contributes, and nothing it does on import.

The architecture is explicit that importing a language pack must not probe for
its tools. A pack declares what checks exist and how to build their work; it
does not go looking at the machine to find out whether they can run. That is
what keeps `ici --help` from depending on what happens to be installed.
"""
