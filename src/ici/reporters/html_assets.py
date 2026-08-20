"""Embedded CSS and JavaScript Assets for Zero-CDN Standalone HTML Report."""

HTML_CSS = """
  :root {
    --bg: #090d16;
    --card-bg: #111827;
    --card-hover: #172033;
    --border: #1f293d;
    --border-highlight: #334155;
    --text: #f3f4f6;
    --text-muted: #9ca3af;
    --primary: #38bdf8;
    --pass: #10b981;
    --warn: #f59e0b;
    --fail: #ef4444;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans KR", Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 2.5rem 1.5rem;
    line-height: 1.6;
  }
  .container { max-width: 1240px; margin: 0 auto; }

  /* Header */
  .header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 2rem;
    padding-bottom: 1.5rem;
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
    gap: 1rem;
  }
  .brand { display: flex; align-items: center; gap: 0.75rem; }
  .brand-logo {
    font-size: 1.5rem;
    background: linear-gradient(135deg, #0ea5e9, #38bdf8);
    padding: 0.35rem 0.75rem;
    border-radius: 8px;
    font-weight: 800;
    color: #04101e;
    letter-spacing: -0.05em;
  }
  .brand-info h1 { font-size: 1.5rem; font-weight: 700; color: #ffffff; }
  .brand-info p { font-size: 0.875rem; color: var(--text-muted); }

  .header-actions { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }

  /* Editor Link Selector */
  .editor-pref-wrapper {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    background: var(--card-bg);
    border: 1px solid var(--border);
    padding: 0.35rem 0.75rem;
    border-radius: 8px;
    font-size: 0.825rem;
  }
  .editor-label { color: var(--text-muted); font-weight: 600; }
  .editor-select {
    background: #090e17;
    color: var(--primary);
    border: 1px solid var(--border-highlight);
    border-radius: 6px;
    padding: 0.25rem 0.5rem;
    font-size: 0.825rem;
    font-weight: 600;
    cursor: pointer;
    outline: none;
  }
  .editor-select:focus { border-color: var(--primary); }

  .status-banner {
    display: inline-flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.5rem 1.35rem;
    border-radius: 9999px;
    font-weight: 700;
    font-size: 1rem;
    letter-spacing: 0.05em;
  }
  .status-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
  }

  /* Stats Grid */
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 1.25rem;
    margin-bottom: 2.25rem;
  }
  .card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
  }
  .stat-label { font-size: 0.8125rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }
  .stat-value { font-size: 1.85rem; font-weight: 700; margin-top: 0.35rem; color: #fff; }
  .stat-sub { font-size: 0.95rem; color: var(--text-muted); font-weight: 400; }

  .mini-progress-bg {
    width: 100%;
    height: 6px;
    background: #1e293b;
    border-radius: 9999px;
    margin-top: 0.6rem;
    overflow: hidden;
  }
  .mini-progress-fill { height: 100%; border-radius: 9999px; }

  /* Tabs Navigation */
  .tabs {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1.5rem;
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.5rem;
    overflow-x: auto;
  }
  .tab-btn {
    background: transparent;
    border: none;
    color: var(--text-muted);
    font-size: 0.95rem;
    font-weight: 600;
    padding: 0.6rem 1.25rem;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.2s ease;
    white-space: nowrap;
  }
  .tab-btn:hover { color: var(--text); background: var(--card-hover); }
  .tab-btn.active {
    color: var(--primary);
    background: rgba(56, 189, 248, 0.1);
    border-bottom: 2px solid var(--primary);
  }
  .tab-content { display: none; }
  .tab-content.active { display: block; }

  /* Tables */
  table {
    width: 100%;
    border-collapse: collapse;
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
  }
  th, td { padding: 1.1rem 1.25rem; text-align: left; border-bottom: 1px solid var(--border); }
  th {
    background: #0d131f;
    color: var(--text-muted);
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 700;
  }
  tr:last-child td { border-bottom: none; }
  .text-right { text-align: right; }
  .text-muted { color: var(--text-muted); }
  .badge {
    display: inline-block;
    padding: 0.25rem 0.65rem;
    border-radius: 6px;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.05em;
  }
  .engine-name { font-size: 1.05rem; }
  .engine-summary-text { font-size: 0.95rem; color: #e5e7eb; }

  .jump-tab-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    background: rgba(56, 189, 248, 0.08);
    border: 1px solid rgba(56, 189, 248, 0.3);
    color: var(--primary);
    padding: 0.3rem 0.75rem;
    border-radius: 6px;
    font-size: 0.8rem;
    font-weight: 600;
    cursor: pointer;
    margin-top: 0.45rem;
    transition: all 0.2s;
  }
  .jump-tab-btn:hover {
    background: rgba(56, 189, 248, 0.18);
    border-color: var(--primary);
  }

  /* Interactive Location Links & Copy */
  .loc-link-group { display: inline-flex; align-items: center; gap: 0.3rem; }
  .loc-link {
    color: var(--primary);
    text-decoration: none;
    font-weight: 600;
    cursor: pointer;
  }
  .loc-link:hover { text-decoration: underline; }

  .btn-copy-loc {
    background: transparent;
    border: none;
    color: var(--text-muted);
    font-size: 0.75rem;
    cursor: pointer;
    padding: 0.15rem 0.3rem;
    border-radius: 4px;
    transition: all 0.15s;
    opacity: 0.7;
  }
  .btn-copy-loc:hover {
    background: #1e293b;
    color: #fff;
    opacity: 1;
  }

  .target-details { margin-top: 0.6rem; }
  .target-details summary {
    cursor: pointer;
    color: var(--primary);
    font-size: 0.835rem;
    font-weight: 600;
    outline: none;
    user-select: none;
  }
  .targets-list { margin-top: 0.6rem; display: flex; flex-direction: column; gap: 0.45rem; }
  .target-item {
    background: #090e17;
    padding: 0.55rem 0.8rem;
    border-radius: 6px;
    border-left: 3px solid var(--border);
    font-size: 0.825rem;
  }
  .target-sym { color: #a78bfa; font-weight: 500; margin-left: 0.4rem; }
  .target-msg { color: var(--text-muted); margin-left: 0.5rem; }

  .snippet {
    margin-top: 0.6rem;
    background: #030712;
    padding: 0.75rem 1rem;
    border-radius: 6px;
    overflow-x: auto;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 0.775rem;
    line-height: 1.45;
    color: #e2e8f0;
    border: 1px solid var(--border);
    white-space: pre;
  }

  /* Clone Group Cards */
  .clone-group-card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1.25rem;
    margin-bottom: 1.25rem;
  }
  .clone-group-title {
    font-size: 0.95rem;
    font-weight: 700;
    color: var(--warn);
    margin-bottom: 0.6rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .clone-occurrences { display: flex; flex-wrap: wrap; gap: 0.6rem; margin-bottom: 0.75rem; }
  .occ-pill {
    background: #172033;
    padding: 0.25rem 0.7rem;
    border-radius: 6px;
    font-size: 0.8125rem;
    border: 1px solid var(--border-highlight);
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
  }

  /* Line Charts & Real Tree View */
  .line-charts-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1.5rem;
  }
  @media (max-width: 900px) {
    .line-charts-grid { grid-template-columns: 1fr; }
  }
  .chart-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; }
  .chart-title { font-size: 1.05rem; font-weight: 700; margin-bottom: 1.25rem; color: #fff; }

  .tree-full-card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.5rem;
  }
  .tree-header-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1.25rem;
    flex-wrap: wrap;
    gap: 0.75rem;
  }
  .tree-search-input {
    background: #090e17;
    border: 1px solid var(--border-highlight);
    color: #fff;
    padding: 0.45rem 0.85rem;
    border-radius: 6px;
    font-size: 0.825rem;
    width: 280px;
    outline: none;
  }
  .tree-search-input:focus { border-color: var(--primary); }
  .tree-scroll-container {
    max-height: 800px;
    overflow-y: auto;
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: 8px;
  }

  .ratio-bar-wrapper { margin-bottom: 0.5rem; }
  .ratio-bar {
    display: flex;
    height: 20px;
    border-radius: 9999px;
    overflow: hidden;
    margin: 0.75rem 0;
    background: #1e293b;
  }
  .ratio-legend { display: flex; gap: 1.5rem; font-size: 0.8125rem; }
  .legend-item { display: flex; align-items: center; gap: 0.45rem; }
  .legend-dot { width: 10px; height: 10px; border-radius: 50%; }

  .top-file-row { margin-bottom: 0.95rem; }
  .top-file-info { display: flex; justify-content: space-between; font-size: 0.825rem; margin-bottom: 0.3rem; }
  .top-bar-bg { height: 8px; background: #1e293b; border-radius: 9999px; overflow: hidden; }
  .top-bar-fill { height: 100%; border-radius: 9999px; background: #38bdf8; }

  /* Real Tree Table */
  .tree-table { width: 100%; font-size: 0.85rem; border: none; border-radius: 0; }
  .tree-table th { padding: 0.85rem 1.1rem; background: #0c121e; position: sticky; top: 0; z-index: 10; }
  .tree-table td { padding: 0.65rem 1.1rem; border-bottom: 1px solid #172033; }
  .tree-folder-row { background: #0c121e; font-weight: 700; color: #38bdf8; }
  .tree-file-row:hover { background: #172033; }
  .tree-indent { display: inline-block; }
  /* Test Suite Cards */
  .test-suite-card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1.25rem;
    margin-bottom: 1.25rem;
  }
  .test-suite-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.85rem;
    padding-bottom: 0.6rem;
    border-bottom: 1px solid var(--border);
  }
  .test-cases-list {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
  .test-case-row {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    background: #090e17;
    padding: 0.45rem 0.8rem;
    border-radius: 6px;
    font-size: 0.825rem;
  }
  .test-case-name { color: #f3f4f6; font-weight: 600; }
  .test-case-msg { color: var(--text-muted); font-size: 0.8rem; margin-left: auto; }

  /* Module Coverage Table */
  .cov-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  .cov-table th {
    text-align: left;
    padding: 0.6rem 0.9rem;
    color: var(--text-muted);
    font-weight: 600;
    border-bottom: 1px solid var(--border);
    background: rgba(255, 255, 255, 0.02);
    white-space: nowrap;
  }
  .cov-table th.num, .cov-table td.num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .cov-table td { padding: 0.55rem 0.9rem; border-bottom: 1px solid var(--border); }
  .cov-table tbody tr:hover { background: var(--card-hover); }
  .cov-table tfoot td {
    font-weight: 700;
    border-top: 2px solid var(--border-highlight);
    border-bottom: none;
  }
  .cov-pct-cell { display: flex; align-items: center; gap: 0.55rem; min-width: 150px; }
  .cov-pct { min-width: 3.4rem; font-weight: 600; font-variant-numeric: tabular-nums; }
  .cov-bar-bg {
    flex: 1;
    height: 6px;
    border-radius: 9999px;
    background: #1f293d;
    overflow: hidden;
  }
  .cov-bar-fill { height: 100%; border-radius: 9999px; }

  /* Issues View */
  .issues-header-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1.25rem;
    flex-wrap: wrap;
    gap: 0.75rem;
  }
  .issue-item {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1.1rem 1.35rem;
    margin-bottom: 0.85rem;
  }
  .issue-header { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.4rem; font-size: 0.85rem; }
  .issue-engine { font-weight: 700; color: #94a3b8; }
  .issue-msg { font-size: 0.9rem; color: #e2e8f0; }
  .issue-snippet-details { margin-top: 0.65rem; }
  .issue-snippet-summary {
    cursor: pointer;
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--primary);
    background: #090e17;
    border: 1px solid var(--border);
    padding: 0.3rem 0.7rem;
    border-radius: 6px;
    display: inline-block;
    user-select: none;
    transition: all 0.15s ease;
  }
  .issue-snippet-summary:hover {
    background: #172033;
    border-color: var(--primary);
  }
  .empty-clean {
    padding: 3.5rem;
    text-align: center;
    background: var(--card-bg);
    border: 1px dashed var(--border);
    border-radius: 12px;
    color: var(--pass);
    font-size: 1.15rem;
    font-weight: 600;
  }

  /* Complexity Leaderboard */
  .cc-card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1.25rem;
    margin-bottom: 1.25rem;
  }
  .cc-header-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1.25rem;
    flex-wrap: wrap;
    gap: 0.75rem;
  }
  .cc-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.6rem; }
  .cc-name { font-size: 1rem; font-weight: 700; color: #fff; }
  .cc-badge { padding: 0.25rem 0.65rem; border-radius: 6px; font-weight: 700; font-size: 0.8rem; }
  .cc-snippet-details { margin-top: 0.65rem; }
  .cc-snippet-summary {
    cursor: pointer;
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--primary);
    background: #090e17;
    border: 1px solid var(--border);
    padding: 0.3rem 0.7rem;
    border-radius: 6px;
    display: inline-block;
    user-select: none;
    transition: all 0.15s ease;
  }
  .cc-snippet-summary:hover {
    background: #172033;
    border-color: var(--primary);
  }

  /* Toast Notification */
  .toast {
    position: fixed;
    bottom: 2rem;
    right: 2rem;
    background: #1e293b;
    color: #fff;
    padding: 0.75rem 1.25rem;
    border-radius: 8px;
    border: 1px solid var(--primary);
    box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5);
    font-size: 0.875rem;
    font-weight: 600;
    opacity: 0;
    transform: translateY(20px);
    transition: all 0.25s ease;
    pointer-events: none;
    z-index: 9999;
  }
  .toast.show {
    opacity: 1;
    transform: translateY(0);
  }

  .footer { margin-top: 3.5rem; text-align: center; color: var(--text-muted); font-size: 0.8125rem; }
"""

HTML_JS = """
function getEditorPref() {
  return localStorage.getItem('ici_editor_pref') || 'vscode';
}

function setEditorPref(val) {
  localStorage.setItem('ici_editor_pref', val);
  showToast('🛠️ Preferred action set to: ' + val);
}

function toggleAllDetails(selector) {
  const all = document.querySelectorAll(selector);
  if (!all.length) return;
  const anyOpen = Array.from(all).some(d => d.open);
  all.forEach(d => d.open = !anyOpen);
  showToast(anyOpen ? '📁 Folded all code snippets' : '📂 Expanded all code snippets');
}

function showToast(msg) {
  const toast = document.getElementById('toast');
  if (!toast) return;
  toast.textContent = msg;
  toast.className = 'toast show';
  setTimeout(() => {
    toast.className = 'toast';
  }, 2400);
}

function copyLoc(relPath, line, ev) {
  if (ev) ev.stopPropagation();
  const text = relPath + (line ? ':' + line : '');
  navigator.clipboard.writeText(text).then(() => {
    showToast('📋 Copied "' + text + '" (ready for gvim/CLI)');
  }).catch(() => {
    showToast('📋 ' + text);
  });
}

function encodeLocationComponent(value) {
  return encodeURIComponent(String(value)).replace(/[!'()*]/g, (char) =>
    '%' + char.charCodeAt(0).toString(16).toUpperCase()
  );
}

function encodeLocationPath(absPath) {
  const normalized = String(absPath || '').replace(/\\\\/g, '/');
  return normalized.split('/').map((segment) => {
    if (/^[A-Za-z]:$/.test(segment)) return segment;
    return encodeLocationComponent(segment);
  }).join('/');
}

function toFileUri(encodedPath) {
  return /^[A-Za-z]:\\//.test(encodedPath)
    ? 'file:///' + encodedPath
    : 'file://' + encodedPath;
}

function openLoc(absPath, relPath, line) {
  const pref = getEditorPref();
  const lineNo = line || 1;
  const encodedPath = encodeLocationPath(absPath);
  const encodedQueryPath = encodeURIComponent(absPath).replace(/[!'()*]/g, (char) =>
    '%' + char.charCodeAt(0).toString(16).toUpperCase()
  );
  const fileUri = toFileUri(encodedPath);

  if (pref === 'copy') {
    copyLoc(relPath, line);
    return;
  }

  if (pref === 'vscode') {
    window.location.href = 'vscode://file/' + encodedPath + ':' + lineNo;
    showToast('🚀 Opening in VS Code: ' + relPath + ':' + lineNo);
  } else if (pref === 'cursor') {
    window.location.href = 'cursor://file/' + encodedPath + ':' + lineNo;
    showToast('⚡ Opening in Cursor: ' + relPath + ':' + lineNo);
  } else if (pref === 'pycharm') {
    window.location.href = 'idea://open?file=' + encodedQueryPath + '&line=' + lineNo;
    showToast('🐍 Opening in PyCharm/IntelliJ...');
  } else if (pref === 'sublime') {
    window.location.href = 'subl://' + encodedPath + ':' + lineNo;
    showToast('🪟 Opening in Sublime Text...');
  } else if (pref === 'file') {
    window.open(fileUri, '_blank');
  }
}

function switchTab(tabId, btnElem) {
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));

  if (btnElem) {
    btnElem.classList.add('active');
  } else {
    const btn = document.getElementById('btn-' + tabId.replace('tab-', ''));
    if (btn) btn.classList.add('active');
  }

  const target = document.getElementById(tabId);
  if (target) target.classList.add('active');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function filterTreeFiles(query) {
  const q = query.toLowerCase().trim();
  const rows = document.querySelectorAll('#fileTreeTable tbody tr');
  if (!q) {
    rows.forEach(r => r.style.display = '');
    return;
  }
  rows.forEach(r => {
    if (r.classList.contains('tree-folder-row')) {
      r.style.display = '';
    } else {
      const text = r.textContent.toLowerCase();
      r.style.display = text.includes(q) ? '' : 'none';
    }
  });
}

// All report interactions are delegated from static listeners. Data-derived
// values are read from escaped data-* attributes, never interpolated into JS.
document.addEventListener('click', (event) => {
  const target = event.target;
  const tabButton = target.closest('[data-tab-target]');
  if (tabButton) {
    event.preventDefault();
    switchTab(tabButton.dataset.tabTarget, tabButton);
    return;
  }

  const locationLink = target.closest('.loc-link[data-abs-path]');
  if (locationLink) {
    event.preventDefault();
    openLoc(locationLink.dataset.absPath, locationLink.dataset.relPath, Number(locationLink.dataset.line));
    return;
  }

  const copyButton = target.closest('.btn-copy-loc[data-rel-path]');
  if (copyButton) {
    copyLoc(copyButton.dataset.relPath, Number(copyButton.dataset.line), event);
    return;
  }

  const toggleButton = target.closest('[data-toggle-details]');
  if (toggleButton) {
    toggleAllDetails(toggleButton.dataset.toggleDetails);
  }
});

document.addEventListener('change', (event) => {
  if (event.target.id === 'editorSelect') {
    setEditorPref(event.target.value);
  }
});

document.addEventListener('input', (event) => {
  if (event.target.id === 'treeSearchInput') {
    filterTreeFiles(event.target.value);
  }
});

document.addEventListener('DOMContentLoaded', () => {
  const pref = getEditorPref();
  const select = document.getElementById('editorSelect');
  if (select) select.value = pref;
});
"""
