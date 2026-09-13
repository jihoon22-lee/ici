"""A single self-contained HTML file for one result.

Two constraints shape all of it, and both come from #206.

**Offline.** *"외부 URL이 없다"*. No stylesheet link, no font, no script, no
image src — not because a CDN is slow but because ici is for machines that
cannot reach one, and a report that renders differently there is a report that
lies about itself in the one place it matters. The test greps the output for
any scheme-bearing URL rather than checking a list of known ones.

**Derived, never re-measured.** The page is built from the stored result, so
what it shows is what was saved. A renderer that recomputed anything — even a
count — could disagree with the JSON beside it, and the two would be equally
official.

The UI is deliberately small; #206 says the detail comes in a later WP. What it
must not do is drop a fact: an INCOMPLETE run shows its reasons, a finding shows
where it is, and a limitation that was recorded is on the page.
"""

from __future__ import annotations

import html
from collections.abc import Iterable

from ici.domain.enums import GateVerdict
from ici.domain.finding import Finding
from ici.domain.result import RunResult

__all__ = ["render"]

_VERDICT_NOTE = {
    GateVerdict.PASS: "required checks completed with no violations",
    GateVerdict.FAIL: "required checks completed and found violations",
    GateVerdict.INCOMPLETE: "the run did not finish what it was asked to do",
    GateVerdict.NOT_EVALUATED: "no verdict was reached for this scope",
}

_STYLE = """
:root { color-scheme: light dark; --fg: #16181d; --bg: #ffffff; --muted: #5b6472;
        --line: #d8dde5; --pass: #14733c; --fail: #a5121b; --incomplete: #8a5400; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #e6e8ec; --bg: #14161a; --muted: #9aa3b2; --line: #2c313a;
          --pass: #57d98a; --fail: #ff8d8d; --incomplete: #f0b44a; }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px; background: var(--bg); color: var(--fg);
       font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 900px; margin: 0 auto; }
h1 { font-size: 1.35rem; margin: 0 0 4px; }
h2 { font-size: 1.05rem; margin: 28px 0 8px; }
.verdict { display: inline-block; padding: 2px 10px; border-radius: 999px;
           font-weight: 650; letter-spacing: .02em; border: 1px solid currentColor; }
.PASS { color: var(--pass); } .FAIL { color: var(--fail); }
.INCOMPLETE { color: var(--incomplete); } .NOT_EVALUATED { color: var(--muted); }
.muted { color: var(--muted); }
table { border-collapse: collapse; width: 100%; margin-top: 6px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line);
         vertical-align: top; }
th { font-weight: 600; color: var(--muted); font-size: .82rem;
     text-transform: uppercase; letter-spacing: .04em; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .9em; }
ul { margin: 6px 0; padding-left: 20px; }
.empty { color: var(--muted); font-style: italic; }
"""


def render(result: RunResult, title: str = "ici verification") -> str:
    """One self-contained page for ``result``."""

    verdict = result.gate.selected
    sections = [
        _summary(result),
        _reasons(result),
        _findings(result.findings),
        _metrics(result),
        _limitations(result.limitations),
    ]
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_t(title)}</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n<body>\n<main>\n"
        f"<h1>{_t(title)}</h1>\n"
        f'<p><span class="verdict {verdict.value}">{verdict.value}</span> '
        f'<span class="muted">{_t(_VERDICT_NOTE[verdict])}</span></p>\n'
        + "".join(sections)
        + "</main>\n</body>\n</html>\n"
    )


def _summary(result: RunResult) -> str:
    rows = [
        ("run", result.run_id),
        ("ici", result.producer.ici_version),
        ("scope", result.scope.kind.value),
        ("components", ", ".join(result.scope.selected_components) or "—"),
        ("exit code", str(result.gate.exit_code)),
        ("required work complete", "yes" if result.execution.required_complete else "no"),
        ("violations", "yes" if result.gate.has_violations else "no"),
    ]
    if result.producer.bundle_digest:
        rows.insert(2, ("bundle", result.producer.bundle_digest))
    body = "".join(f"<tr><th>{_t(k)}</th><td><code>{_t(v)}</code></td></tr>" for k, v in rows)
    return f"<h2>Run</h2>\n<table>{body}</table>\n"


def _reasons(result: RunResult) -> str:
    if not result.gate.reasons:
        return ""
    # Shown whatever the verdict. An INCOMPLETE run that does not say why is
    # the report equivalent of an empty pass.
    items = "".join(f"<li>{_t(reason)}</li>" for reason in result.gate.reasons)
    return f"<h2>Why</h2>\n<ul>{items}</ul>\n"


def _findings(findings: tuple[Finding, ...]) -> str:
    if not findings:
        return '<h2>Findings</h2>\n<p class="empty">none recorded</p>\n'
    rows = "".join(
        "<tr>"
        f"<td><code>{_t(item.rule_id)}</code></td>"
        f"<td><code>{_t(item.primary_location.path)}:{item.primary_location.start_line}</code></td>"
        f"<td>{_t(item.severity)}</td>"
        f"<td>{_t(item.message)}</td>"
        "</tr>"
        for item in findings
    )
    return (
        f"<h2>Findings ({len(findings)})</h2>\n<table>"
        "<tr><th>rule</th><th>where</th><th>severity</th><th>message</th></tr>"
        f"{rows}</table>\n"
    )


def _metrics(result: RunResult) -> str:
    if not result.metrics:
        return ""
    rows = "".join(
        f"<tr><th>{_t(item.name)}</th><td><code>{_number(item.value)}"
        f"{' ' + _t(item.unit) if item.unit else ''}</code></td>"
        f'<td class="muted">{_ratio(item)}</td></tr>'
        for item in result.metrics
    )
    return f"<h2>Measurements</h2>\n<table>{rows}</table>\n"


def _limitations(limitations: Iterable[str]) -> str:
    items = [item for item in limitations if item]
    if not items:
        return ""
    # Never dropped. A limitation recorded during the run and left off the page
    # is a fact the reader would have wanted and cannot now ask for.
    body = "".join(f"<li>{_t(item)}</li>" for item in items)
    return f"<h2>Not covered</h2>\n<ul>{body}</ul>\n"


def _ratio(measurement: object) -> str:
    numerator = getattr(measurement, "numerator", None)
    denominator = getattr(measurement, "denominator", None)
    if numerator is None or not denominator:
        return ""
    return f"{numerator} of {denominator}"


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _t(value: object) -> str:
    return html.escape(str(value), quote=True)
