"""A single self-contained HTML file for one result.

Two constraints shape all of it, and both come from #206 and #222.

**Offline.** *"외부 URL이 없다"*. No stylesheet link, no font, no script, no
image src — not because a CDN is slow but because ici is for machines that
cannot reach one, and a report that renders differently there is a report that
lies about itself in the one place it matters. The test greps the output for
any scheme-bearing URL rather than checking a list of known ones.

**Derived, never re-measured.** The page is built from the stored result via
:func:`~ici.reporting.view_model.view_model`, so what it shows is what was
saved. A renderer that recomputed anything — even a count — could disagree
with the JSON beside it, and the two would be equally official. What the page
must not do is drop a fact: an INCOMPLETE run shows why, a cancelled run says
it was cancelled, blocked and failed tasks are named, suppressed findings keep
their mark, and a finding's evidence level is visible rather than flattened
into a checkmark.
"""

from __future__ import annotations

import html

from ici.domain.result import RunResult
from ici.reporting.view_model import RunView, view_model

__all__ = ["render"]

_STYLE = """
:root { color-scheme: light dark; --fg: #16181d; --bg: #ffffff; --muted: #5b6472;
        --line: #d8dde5; --pass: #14733c; --fail: #a5121b; --incomplete: #8a5400;
        --mark: #6b21a8; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #e6e8ec; --bg: #14161a; --muted: #9aa3b2; --line: #2c313a;
          --pass: #57d98a; --fail: #ff8d8d; --incomplete: #f0b44a;
          --mark: #c39af0; }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px; background: var(--bg); color: var(--fg);
       font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 960px; margin: 0 auto; }
h1 { font-size: 1.35rem; margin: 0 0 4px; }
h2 { font-size: 1.05rem; margin: 28px 0 8px; }
.verdict { display: inline-block; padding: 2px 10px; border-radius: 999px;
           font-weight: 650; letter-spacing: .02em; border: 1px solid currentColor; }
.PASS { color: var(--pass); } .FAIL { color: var(--fail); }
.INCOMPLETE { color: var(--incomplete); } .NOT_EVALUATED { color: var(--muted); }
.muted { color: var(--muted); }
.mark { color: var(--mark); font-weight: 600; }
table { border-collapse: collapse; width: 100%; margin-top: 6px; }
.scroll { overflow-x: auto; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line);
         vertical-align: top; }
th { font-weight: 600; color: var(--muted); font-size: .82rem;
     text-transform: uppercase; letter-spacing: .04em; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .9em; }
ul { margin: 6px 0; padding-left: 20px; }
.empty { color: var(--muted); font-style: italic; }
dl.scope { display: grid; grid-template-columns: max-content 1fr; gap: 2px 18px;
           margin: 6px 0; }
dl.scope dt { color: var(--muted); } dl.scope dd { margin: 0; }
input[type="search"] { width: 100%; max-width: 320px; padding: 6px 10px;
  margin-top: 6px; border: 1px solid var(--line); border-radius: 6px;
  background: var(--bg); color: var(--fg); font: inherit; }
input[type="search"]:focus { outline: 2px solid var(--pass); outline-offset: 1px; }
"""


def _filter_script() -> str:
    """The one script the page carries — a text filter for the findings table.

    It is inline because the page must work with no network and no
    Content-Security-Policy exception to argue with. It touches only rows it
    was built beside; a finding is hidden by the reader's filter, never by
    the report.
    """

    return (
        "<script>(function(){var f=document.getElementById('finding-filter');"
        "if(!f)return;var rows=document.querySelectorAll('#findings tr[data-x]');"
        "var n=document.getElementById('finding-shown');"
        "f.addEventListener('input',function(){var q=f.value.toLowerCase();"
        "var shown=0;rows.forEach(function(r){"
        "var on=r.getAttribute('data-x').indexOf(q)>=0;"
        "r.style.display=on?'':'none';if(on)shown++;});"
        "if(n)n.textContent=shown+' of '+rows.length+' shown';});})();"
        "</script>"
    )


def render(result: RunResult, title: str = "ici verification") -> str:
    """One self-contained page for ``result``."""

    view = view_model(result)
    sections = [
        _scope(view),
        _execution(view),
        _baseline(view),
        _reasons(view),
        _findings(view),
        _metrics(view),
        _limitations(view),
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
        f'<p><span class="verdict {view.verdict}">{view.verdict}</span> '
        f'<span class="muted">{_t(view.verdict_note)}</span></p>\n'
        + "".join(sections)
        + _filter_script()
        + "</main>\n</body>\n</html>\n"
    )


def _scope(view: RunView) -> str:
    """What this run covered — and what it did not, kept on the first screen."""

    rows = [
        ("run", view.run_id),
        ("ici", view.ici_version),
        ("scope", view.scope_kind),
        ("selected", ", ".join(view.selected_components) or "—"),
        ("required work complete", "yes" if view.required_complete else "no"),
        ("violations", "yes" if view.has_violations else "no"),
        ("exit code", str(view.exit_code)),
    ]
    if view.bundle_digest:
        rows.insert(2, ("bundle", view.bundle_digest))
    if view.selected_languages:
        rows.append(("languages", ", ".join(view.selected_languages)))
    if view.omitted_components:
        rows.append(("not selected", ", ".join(view.omitted_components)))
    if view.required_components and set(view.required_components) != set(view.selected_components):
        rows.append(("required components", ", ".join(view.required_components)))
    if view.scope_kind == "full":
        rows.append(
            (
                "required scope satisfied",
                "yes" if view.full_required_satisfied else "no",
            )
        )
    body = "".join(f"<dt>{_t(k)}</dt><dd><code>{_t(v)}</code></dd>" for k, v in rows)
    return f'<h2>Run</h2>\n<dl class="scope">{body}</dl>\n'


def _execution(view: RunView) -> str:
    """Blocked, failed and cancelled work — the facts a verdict hides."""

    items: list[str] = []
    if view.cancelled:
        items.append("<li><strong>the run was cancelled</strong></li>")
    for task in view.blocked_tasks:
        items.append(f"<li>blocked: <code>{_t(task)}</code></li>")
    for task in view.failed_tasks:
        items.append(f"<li>failed: <code>{_t(task)}</code></li>")
    if not items:
        return ""
    reused = ""
    if view.reused_tasks:
        reused = f'<p class="muted">{len(view.reused_tasks)} task(s) reused stored results</p>'
    return f"<h2>Execution</h2>\n<ul>{''.join(items)}</ul>{reused}\n"


def _baseline(view: RunView) -> str:
    baseline = view.baseline
    if baseline.state == "none":
        return ""
    if baseline.state == "incompatible":
        return (
            "<h2>Baseline</h2>\n"
            f'<p class="mark">incompatible — {_t(baseline.reason)}</p>\n'
            f'<p class="muted">compared against <code>{_t(baseline.origin)}</code></p>\n'
        )
    return (
        "<h2>Baseline</h2>\n"
        f"<p>{baseline.new} new, {baseline.unchanged} unchanged, "
        f"{baseline.resolved} resolved, {baseline.carried} carried</p>\n"
        f'<p class="muted">compared against <code>{_t(baseline.origin)}</code></p>\n'
    )


def _reasons(view: RunView) -> str:
    if not view.gate_reasons:
        return ""
    # Shown whatever the verdict. An INCOMPLETE run that does not say why is
    # the report equivalent of an empty pass.
    items = "".join(f"<li>{_t(reason)}</li>" for reason in view.gate_reasons)
    return f"<h2>Why</h2>\n<ul>{items}</ul>\n"


def _findings(view: RunView) -> str:
    findings = view.findings
    if not findings:
        return '<h2>Findings</h2>\n<p class="empty">none recorded</p>\n'
    rows = "".join(_finding_row(row) for row in findings)
    suppressed = (
        f' <span class="muted">({view.suppressed_count} suppressed)</span>'
        if view.suppressed_count
        else ""
    )
    return (
        f"<h2>Findings ({len(findings)}){suppressed}</h2>\n"
        '<input type="search" id="finding-filter" placeholder="filter findings"'
        ' aria-label="filter findings">'
        ' <span class="muted" id="finding-shown" aria-live="polite"></span>\n'
        '<div class="scroll"><table id="findings">'
        '<tr><th scope="col">rule</th><th scope="col">where</th>'
        '<th scope="col">severity</th><th scope="col">confidence</th>'
        '<th scope="col">evidence</th>'
        '<th scope="col">message</th><th scope="col">marks</th></tr>'
        f"{rows}</table></div>\n"
    )


def _finding_row(row) -> str:
    marks: list[str] = []
    if row.suppressed:
        marks.append(
            f"suppressed — {_t(row.suppression_reason)}"
            + (
                f' <span class="muted">({_t(row.suppression_origin)})</span>'
                if row.suppression_origin
                else ""
            )
        )
    if row.baseline_state:
        marks.append(_t(row.baseline_state))
    if row.component:
        marks.append(_t(row.component))
    evidence = (
        _t(row.evidence) if row.evidence != "MEASURED" else ""
    )  # measured is the baseline; mark the exception
    haystack = _t(
        " ".join(
            item
            for item in (
                row.rule,
                row.location,
                row.severity,
                row.message,
                row.provider,
                row.component,
                row.confidence,
                row.evidence,
            )
            if item
        ).lower()
    )
    return (
        f'<tr data-x="{haystack}">'
        f"<td><code>{_t(row.rule)}</code><br>"
        f'<span class="muted">{_t(row.provider)}</span></td>'
        f"<td><code>{_t(row.location)}</code></td>"
        f"<td>{_t(row.severity)}</td>"
        f'<td class="muted">{_t(row.confidence)}</td>'
        f'<td class="muted">{evidence}</td>'
        f"<td>{_t(row.message)}</td>"
        f'<td class="muted">{"<br>".join(marks)}</td>'
        "</tr>"
    )


def _metrics(view: RunView) -> str:
    if not view.metrics:
        return ""
    rows = "".join(
        f"<tr><th>{_t(item.name)}</th><td><code>{_t(item.value)}"
        f"{' ' + _t(item.unit) if item.unit else ''}</code></td>"
        f'<td class="muted">{_t(item.raw)}</td></tr>'
        for item in view.metrics
    )
    return f'<h2>Measurements</h2>\n<div class="scroll"><table>{rows}</table></div>\n'


def _limitations(view: RunView) -> str:
    if not view.limitations:
        return ""
    # Never dropped. A limitation recorded during the run and left off the page
    # is a fact the reader would have wanted and cannot now ask for.
    body = "".join(f"<li>{_t(item)}</li>" for item in view.limitations)
    return f"<h2>Not covered</h2>\n<ul>{body}</ul>\n"


def _t(value: object) -> str:
    return html.escape(str(value), quote=True)
