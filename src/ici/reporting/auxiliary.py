"""Render imported evidence beside a run, without changing the run's verdict."""

import html
import json


def render_auxiliary(documents) -> str:
    sections = []
    for document in documents:
        provenance = {key: value for key, value in document.items() if key != "records"}
        sections.append(
            "<section><h2>Supporting evidence — imported, NOT_EVALUATED</h2>"
            "<p>External evidence does not complete or change the run gate.</p>"
            "<details><summary>Source, time and scope</summary><pre>"
            + html.escape(json.dumps(provenance, indent=2, ensure_ascii=False))
            + "</pre></details><details><summary>"
            + str(len(document["records"]))
            + " imported records</summary><pre>"
            + html.escape(json.dumps(document["records"], indent=2, ensure_ascii=False))
            + "</pre></details></section>"
        )
    return "".join(sections)
