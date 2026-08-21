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
