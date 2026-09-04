"""Bounded, client-hydrated rendering for very large HTML issue inventories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ici.reporters.html.utils import HtmlIssue

LARGE_REPORT_FINDING_THRESHOLD = 2_000
LARGE_REPORT_INITIAL_ROWS = 50
MAX_EMBEDDED_JSON_BYTES = 64 * 1024 * 1024


def canonical_finding_count(issues: list[HtmlIssue]) -> int:
    """Return the number of actionable canonical findings represented by issues."""

    return sum(issue.original_finding_count for issue in issues)


def _location_payload(location: Any, base: Path) -> dict[str, Any]:
    """Serialize a source location using only JSON primitives."""

    payload: dict[str, Any] = {
        "path": location.path,
        "start_line": location.start_line,
        "end_line": location.end_line,
        "start_column": location.start_column,
        "end_column": location.end_column,
        "label": location.label,
    }
    if location.path != "[external]":
        payload["absolute_path"] = str((base / location.path).resolve())
    else:
        payload["absolute_path"] = ""
    return payload


def _issue_payload(issue: HtmlIssue, base: Path) -> dict[str, Any]:
    """Serialize the complete display projection needed by the browser."""

    absolute_path = ""
    if issue.file_path and issue.file_path != "[external]":
        absolute_path = str((base / issue.file_path).resolve())
    return {
        "engine_name": issue.engine_name,
        "badge": issue.badge,
        "status": issue.status.value,
        "file_path": issue.file_path,
        "absolute_path": absolute_path,
        "start_line": issue.start_line,
        "end_line": issue.end_line,
        "rule_id": issue.rule_id,
        "message": issue.message,
        "snippet": issue.snippet,
        "related_locations": [
            _location_payload(location, base) for location in issue.related_locations
        ],
        "original_finding_count": issue.original_finding_count,
        "provenance": list(issue.provenance),
    }


def serialize_large_report_data(issues: list[HtmlIssue], base: Path) -> str:
    """Return JSON safe for an HTML ``application/json`` script element.

    Escaping ``<`` (and its common companions) as JSON unicode escapes prevents
    a finding containing ``</script>`` from terminating the data element.  The
    size limit is measured on the actual UTF-8 payload that will be embedded.
    """

    payload = {
        "schema_version": "ici.html-report/v1",
        "finding_count": canonical_finding_count(issues),
        "findings": [_issue_payload(issue, base) for issue in issues],
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    escaped = (
        serialized.replace("&", r"\u0026")
        .replace("<", r"\u003c")
        .replace(">", r"\u003e")
        .replace("\u2028", r"\u2028")
        .replace("\u2029", r"\u2029")
    )
    size = len(escaped.encode("utf-8"))
    if size > MAX_EMBEDDED_JSON_BYTES:
        raise ValueError(
            "large HTML report data exceeds the 64 MiB embedded JSON limit "
            f"({size} > {MAX_EMBEDDED_JSON_BYTES} bytes)"
        )
    return escaped


HTML_LARGE_REPORT_JS = r"""function createLargeIssueElement(tagName, className, value) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  if (value !== undefined && value !== null) element.textContent = String(value);
  return element;
}

function largeIssueStatusColor(status) {
  return {
    PASS: '#10b981',
    WARN: '#f59e0b',
    FAIL: '#ef4444',
    ERROR: '#dc2626',
    SKIP: '#9ca3af'
  }[status] || '#9ca3af';
}

function largeIssueLocation(path, line, endLine, absolutePath, label) {
  const displayPath = String(path || '');
  const start = Number(line) || 1;
  const end = Number(endLine) || start;
  let display = label || (displayPath + ':L' + start);
  if (!label && end > start) display += '-L' + end;

  if (!displayPath || displayPath === '[external]') {
    const plain = createLargeIssueElement('span', 'issue-no-location');
    const code = createLargeIssueElement('code', '', display);
    plain.appendChild(code);
    return plain;
  }

  const group = createLargeIssueElement('span', 'loc-link-group');
  const link = createLargeIssueElement('a', 'loc-link');
  link.href = '#';
  link.dataset.absPath = String(absolutePath || '');
  link.dataset.relPath = displayPath;
  link.dataset.line = String(start);
  const code = createLargeIssueElement('code', '', display);
  link.appendChild(code);
  group.appendChild(link);

  const copy = createLargeIssueElement('button', 'btn-copy-loc', '📋');
  copy.type = 'button';
  copy.dataset.relPath = displayPath;
  copy.dataset.line = String(start);
  copy.setAttribute('aria-label', 'Copy location ' + display);
  copy.title = '경로 복사 (gvim/CLI용)';
  group.appendChild(copy);
  return group;
}

function largeIssueRow(issue) {
  const item = createLargeIssueElement('div', 'issue-item');
  const header = createLargeIssueElement('div', 'issue-header');
  const color = largeIssueStatusColor(issue.status);
  const badge = createLargeIssueElement('span', 'badge', issue.badge || issue.status);
  badge.style.color = color;
  badge.style.border = '1px solid ' + color + '44';
  header.appendChild(badge);
  header.appendChild(createLargeIssueElement('span', 'issue-engine', '[' + (issue.engine_name || '') + ']'));
  if (issue.file_path) {
    header.appendChild(largeIssueLocation(
      issue.file_path,
      issue.start_line,
      issue.end_line,
      issue.absolute_path
    ));
  } else {
    header.appendChild(createLargeIssueElement('span', 'issue-no-location', 'engine result'));
  }
  header.appendChild(createLargeIssueElement('span', 'target-sym', '[' + (issue.rule_id || '') + ']'));
  item.appendChild(header);

  item.appendChild(createLargeIssueElement('div', 'issue-msg', issue.message || ''));
  const represented = Number(issue.original_finding_count) || 1;
  if (represented > 1) {
    item.appendChild(createLargeIssueElement(
      'div',
      'issue-provenance',
      represented + ' original findings represented without changing JSON or baseline inventory'
    ));
  }

  const provenance = Array.isArray(issue.provenance) ? issue.provenance : [];
  if (provenance.length) {
    item.appendChild(createLargeIssueElement('div', 'issue-provenance', 'Sources: ' + provenance.join(' · ')));
  }

  const related = Array.isArray(issue.related_locations) ? issue.related_locations : [];
  if (related.length) {
    const relatedBlock = createLargeIssueElement('div', 'issue-related');
    relatedBlock.appendChild(createLargeIssueElement('h3', 'issue-related-title', 'Related evidence'));
    const relatedList = createLargeIssueElement('ul');
    related.forEach((location) => {
      const relatedItem = createLargeIssueElement('li');
      relatedItem.appendChild(createLargeIssueElement('span', 'issue-related-location'));
      const controls = largeIssueLocation(
        location.path,
        location.start_line,
        location.end_line,
        location.absolute_path,
        location.path + ':L' + (Number(location.start_line) || 1)
      );
      relatedItem.firstChild.appendChild(controls);
      relatedItem.appendChild(createLargeIssueElement('span', 'issue-related-message', location.label || 'Related diagnostic location'));
      relatedList.appendChild(relatedItem);
    });
    relatedBlock.appendChild(relatedList);
    item.appendChild(relatedBlock);
  }

  if (issue.snippet) {
    const details = createLargeIssueElement('details', 'issue-snippet-details');
    const lines = String(issue.snippet).split(/\r?\n/).length;
    details.appendChild(createLargeIssueElement(
      'summary',
      'issue-snippet-summary',
      '📄 View Finding Code (' + lines + ' lines) ▾'
    ));
    const pre = createLargeIssueElement('pre', 'snippet');
    pre.appendChild(createLargeIssueElement('code', '', issue.snippet));
    details.appendChild(pre);
    item.appendChild(details);
  }
  return item;
}

function largeIssueSearchText(issue) {
  const related = Array.isArray(issue.related_locations) ? issue.related_locations : [];
  return [
    issue.engine_name,
    issue.badge,
    issue.status,
    issue.file_path,
    issue.rule_id,
    issue.message,
    issue.snippet,
    ...related.map((location) => [location.path, location.label].join(' '))
  ].map((value) => String(value || '')).join(' ').toLowerCase();
}

function initializeLargeReportIssues() {
  const dataElement = document.getElementById('ici-report-data');
  const list = document.getElementById('ici-report-issue-list');
  if (!dataElement || !list) return;

  let payload;
  try {
    payload = JSON.parse(dataElement.textContent || '{}');
  } catch (_error) {
    list.replaceChildren(createLargeIssueElement('div', 'empty-clean', 'Unable to load issue data.'));
    return;
  }
  const findings = Array.isArray(payload.findings) ? payload.findings : [];
  const search = document.getElementById('ici-report-search');
  const previous = document.getElementById('ici-report-previous');
  const next = document.getElementById('ici-report-next');
  const pageLabel = document.getElementById('ici-report-page');
  const countLabel = document.getElementById('ici-report-count');
  const state = { page: 1, query: '' };

  function matchingFindings() {
    const query = state.query.trim().toLowerCase();
    if (!query) return findings;
    return findings.filter((issue) => largeIssueSearchText(issue).includes(query));
  }

  function render() {
    const matching = matchingFindings();
    const pageCount = Math.max(1, Math.ceil(matching.length / 50));
    state.page = Math.min(Math.max(state.page, 1), pageCount);
    const first = (state.page - 1) * 50;
    const visible = matching.slice(first, first + 50);
    const fragment = document.createDocumentFragment();
    visible.forEach((issue) => fragment.appendChild(largeIssueRow(issue)));
    if (!visible.length) fragment.appendChild(createLargeIssueElement('div', 'empty-clean', '✨ No matching issues found.'));
    list.replaceChildren(fragment);
    if (previous) previous.disabled = state.page <= 1;
    if (next) next.disabled = state.page >= pageCount;
    if (pageLabel) pageLabel.textContent = 'Page ' + state.page + ' / ' + pageCount;
    if (countLabel) {
      const shown = matching.length ? (first + 1) + '-' + Math.min(first + 50, matching.length) : '0';
      countLabel.textContent = 'Showing ' + shown + ' of ' + matching.length + ' issue rows';
    }
  }

  if (search) {
    search.addEventListener('input', () => {
      state.query = search.value;
      state.page = 1;
      render();
    });
  }
  if (previous) previous.addEventListener('click', () => {
    state.page -= 1;
    render();
  });
  if (next) next.addEventListener('click', () => {
    state.page += 1;
    render();
  });
  render();
}

document.addEventListener('DOMContentLoaded', initializeLargeReportIssues);
"""
