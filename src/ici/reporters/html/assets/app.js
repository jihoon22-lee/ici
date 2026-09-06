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
  const normalized = String(absPath || '').replace(/\\/g, '/');
  return normalized.split('/').map((segment) => {
    if (/^[A-Za-z]:$/.test(segment)) return segment;
    return encodeLocationComponent(segment);
  }).join('/');
}

function toFileUri(encodedPath) {
  return /^[A-Za-z]:\//.test(encodedPath)
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

function filterTreeFiles(query, tableId) {
  const q = query.toLowerCase().trim();
  const rows = document.querySelectorAll('#' + tableId + ' tbody tr');
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
    return;
  }
  if (event.target.id === 'lineAllFilesToggle') {
    const showAll = event.target.checked;
    document.querySelectorAll('[data-line-view]').forEach(el => {
      el.style.display = (el.dataset.lineView === 'all') === showAll ? '' : 'none';
    });
  }
});

document.addEventListener('input', (event) => {
  const searchInput = event.target.closest('.tree-search-input[data-tree-target]');
  if (searchInput) {
    filterTreeFiles(searchInput.value, searchInput.dataset.treeTarget);
  }
});

document.addEventListener('DOMContentLoaded', () => {
  const pref = getEditorPref();
  const select = document.getElementById('editorSelect');
  if (select) select.value = pref;
});

// Per-axis issue filtering and sorting.
//
// This works over the rows already rendered into the page rather than over the
// JSON inventory, so filtering never changes what the report contains -- it
// only changes what is shown. The bounded large-report view keeps its own
// paginated path and is left alone.
function iciIssueRows() {
  return Array.from(document.querySelectorAll('.issue-item[data-engine]'));
}

function iciIssueSeverityRank(value) {
  const order = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO'];
  const index = order.indexOf((value || '').toUpperCase());
  return index === -1 ? order.length : index;
}

function iciApplyIssueFilters() {
  const bar = document.querySelector('[data-issue-filters]');
  if (!bar) return;
  const value = (id) => {
    const el = document.getElementById(id);
    return el ? el.value.trim().toLowerCase() : '';
  };
  const engine = value('ici-issue-engine');
  const severity = value('ici-issue-severity');
  const category = value('ici-issue-category');
  const rule = value('ici-issue-rule');
  const file = value('ici-issue-file');
  const sort = value('ici-issue-sort');

  const rows = iciIssueRows();
  let shown = 0;
  rows.forEach((row) => {
    const data = row.dataset;
    const matches =
      (!engine || (data.engine || '').toLowerCase() === engine) &&
      (!severity || (data.severity || '').toLowerCase() === severity) &&
      (!category || (data.category || '').toLowerCase() === category) &&
      (!rule || (data.rule || '').toLowerCase() === rule) &&
      (!file || (data.file || '').toLowerCase().includes(file));
    row.style.display = matches ? '' : 'none';
    if (matches) shown += 1;
  });

  if (sort && sort !== 'default') {
    const parent = rows.length ? rows[0].parentNode : null;
    if (parent) {
      const sorted = rows.slice().sort((left, right) => {
        if (sort === 'severity') {
          return iciIssueSeverityRank(left.dataset.severity) - iciIssueSeverityRank(right.dataset.severity);
        }
        const key = sort === 'engine' ? 'engine' : 'file';
        return (left.dataset[key] || '').localeCompare(right.dataset[key] || '');
      });
      sorted.forEach((row) => parent.appendChild(row));
    }
  }

  const count = document.getElementById('ici-issue-filter-count');
  if (count) {
    count.textContent = shown === rows.length
      ? rows.length + ' issue rows'
      : 'Showing ' + shown + ' of ' + rows.length + ' issue rows';
  }
}

document.addEventListener('change', (event) => {
  if (event.target.closest('[data-issue-filters]')) iciApplyIssueFilters();
});

document.addEventListener('input', (event) => {
  if (event.target.id === 'ici-issue-file') iciApplyIssueFilters();
});

document.addEventListener('DOMContentLoaded', iciApplyIssueFilters);
