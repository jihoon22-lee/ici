"""Application flows: init, doctor, plan, verify, report.

This layer joins the pieces and owns none of them. It asks ``config`` what was
declared, ``toolchain`` which tool that resolves to, ``execution`` to run it,
the providers to read it, and ``policy`` what the answer means. Its own job is
the order, and the honesty of what comes out when a step could not happen.
"""
