(() => {
  'use strict';
  const payload = document.getElementById('ici-result');
  let saved;
  const getSaved = () => saved || (saved = JSON.parse(payload.textContent));
  const raw = document.getElementById('raw-result');
  if (payload && raw) raw.closest('details').addEventListener('toggle', () => {
    if (!raw.closest('details').open || raw.textContent) return;
    try { raw.textContent = JSON.stringify(getSaved(), null, 2); }
    catch (_) { raw.textContent = 'Saved result could not be displayed'; }
  });
  const filter = document.getElementById('finding-filter');
  if (filter) {
    const rows = [...document.querySelectorAll('[data-search-row]')];
    const count = document.getElementById('filter-count');
    filter.addEventListener('input', () => {
      const query = filter.value.toLocaleLowerCase();
      let shown = 0;
      for (const row of rows) {
        const scope = row.closest('[data-line-scope]');
        row.hidden = !row.textContent.toLocaleLowerCase().includes(query) ||
          (row.dataset.sourceScope && row.dataset.sourceScope !== 'source' &&
            scope.querySelector('[data-scope-toggle]').value === 'source');
        if (!row.hidden) shown++;
      }
      if (count) count.textContent = ` ${shown} of ${rows.length} rows shown`;
    });
  }
  document.querySelectorAll('[data-scope-toggle]').forEach(select => select.addEventListener('change', () => {
    select.closest('[data-line-scope]').querySelectorAll('[data-source-scope]').forEach(row => {
      row.hidden = (select.value === 'source' && row.dataset.sourceScope !== 'source') ||
        (filter && !row.textContent.toLocaleLowerCase().includes(filter.value.toLocaleLowerCase()));
    });
  }));
  const navigationElement = document.getElementById('ici-source-navigation');
  const navigation = navigationElement ? JSON.parse(navigationElement.textContent) : null;
  const component = value => encodeURIComponent(value).replace(/[!'()*]/g,
    character => '%' + character.charCodeAt(0).toString(16).toUpperCase());
  const relativePath = path => path && path !== '[external]' && !/[\\:\u0000-\u001f\u007f]/.test(path) &&
    !path.startsWith('/') && path.split('/').every(part => part && part !== '.' && part !== '..');
  const sourceControls = root => {
    if (!navigation) return;
    root.querySelectorAll('[data-copy-location][data-path]').forEach(copy => {
      if (copy.dataset.sourceDecorated || !relativePath(copy.dataset.path)) return;
      copy.dataset.sourceDecorated = 'true';
      const path = copy.dataset.path, line = String(Number(copy.dataset.line) || 1);
      if (navigation.root) {
        const open = document.createElement('a'); open.href = '#'; open.textContent = 'Open';
        open.dataset.openSource = path; open.dataset.line = line;
        copy.parentNode.appendChild(document.createTextNode(' ')); copy.parentNode.appendChild(open);
      }
      if (navigation.repository_url) {
        const link = document.createElement('a'); link.textContent = 'Source';
        link.href = navigation.repository_url + '/blob/' + component(navigation.commit_ref) + '/' +
          path.split('/').map(component).join('/') + '#L' + line;
        link.target = '_blank'; link.rel = 'noopener noreferrer';
        copy.parentNode.appendChild(document.createTextNode(' ')); copy.parentNode.appendChild(link);
      }
    });
  };
  sourceControls(document);
  document.addEventListener('click', event => {
    const link = event.target.closest('[data-open-source]');
    if (!link || !navigation || !navigation.root || !relativePath(link.dataset.openSource)) return;
    event.preventDefault();
    const selector = document.getElementById('editor-select');
    const choice = selector ? selector.value : 'copy';
    const line = String(Number(link.dataset.line) || 1), path = link.dataset.openSource;
    const absolute = navigation.root.replace(/\\/g, '/').replace(/\/$/, '') + '/' + path;
    const encoded = absolute.split('/').map(part => /^[A-Za-z]:$/.test(part) ? part : component(part)).join('/');
    let uri;
    if (choice === 'vscode' || choice === 'cursor') uri = choice + '://file/' + encoded + ':' + line;
    else if (choice === 'pycharm') uri = 'idea://open?file=' + component(absolute) + '&line=' + line;
    else if (choice === 'sublime') uri = 'subl://' + encoded + ':' + line;
    else if (choice === 'file') {
      const file = /^[A-Za-z]:\//.test(encoded) ? 'file:///' + encoded : 'file://' + encoded;
      window.open(file, '_blank', 'noopener,noreferrer'); return;
    } else {
      const copy = link.parentNode.querySelector('[data-copy-location]'); if (copy) copy.click(); return;
    }
    window.location.href = uri;
  });
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-copy-location]');
    if (!button) return;
    try { await navigator.clipboard.writeText(button.dataset.copyLocation); button.textContent = 'Copied'; }
    catch (_) { button.textContent = 'Select the location to copy'; }
  });
  const groupPayload = document.getElementById('ici-groups');
  if (!groupPayload) return;
  const data = JSON.parse(groupPayload.textContent), documentData = getSaved();
  const findings = Array.isArray(documentData.results)
    ? documentData.results.flatMap(engine => (engine.findings || []).map(finding => ({...finding, evidence: finding.evidence || engine.evidence})))
    : documentData.findings || [];
  const node = (tag, text) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = String(text);
    return element;
  };
  const root = document.getElementById('issue-groups');
  const previous = document.getElementById('group-prev'), next = document.getElementById('group-next');
  const label = document.getElementById('group-page');
  let page = 0, selected = data.groups;
  const searchable = data.groups.map(group => {
    const finding = findings[group.indices[0]] || {};
    return [group.rule || finding.rule_id, group.message || finding.message,
      finding.primary_location && finding.primary_location.path,
      finding.severity, finding.confidence, JSON.stringify(group.provenance || [])].join(' ').toLocaleLowerCase();
  });
  const render = () => {
    const pages = Math.max(1, Math.ceil(selected.length / data.page_size));
    page = Math.min(page, pages - 1);
    root.replaceChildren();
    for (const group of selected.slice(page * data.page_size, (page + 1) * data.page_size)) {
      const finding = findings[group.indices[0]] || {}, span = finding.primary_location || {};
      const article = node('article'); article.className = 'finding';
      article.appendChild(node('h3', `${group.rule || finding.rule_id} · ${finding.severity || ''}`));
      const location = `${span.path || 'unknown'}:${span.start_line || 1}${span.start_column ? ':' + span.start_column : ''}`;
      const paragraph = node('p'); paragraph.appendChild(node('code', location));
      if (span.path !== '[external]') {
        const copy = node('button', 'Copy'); copy.type = 'button'; copy.dataset.copyLocation = location; copy.dataset.path = span.path; copy.dataset.line = String(span.start_line || 1);
        copy.setAttribute('aria-label', `Copy location ${location}`); paragraph.appendChild(copy);
      }
      article.appendChild(paragraph);
      article.appendChild(node('p', group.message || finding.message || ''));
      article.appendChild(node('p', `${group.indices.length} original findings represented · ${finding.evidence || ''} · ${finding.confidence || ''}`));
      const details = node('details'); details.appendChild(node('summary', 'Complete saved evidence and provenance'));
      details.addEventListener('toggle', () => {
        if (!details.open || details.querySelector('pre')) return;
        details.appendChild(node('pre', JSON.stringify({provenance: group.provenance || [], locations: group.locations || [],
          findings: group.indices.map(index => findings[index])}, null, 2)));
      });
      article.appendChild(details); root.appendChild(article); sourceControls(article);
    }
    label.textContent = `Page ${page + 1} of ${pages} · ${selected.length} of ${data.groups.length} issue groups`;
    previous.disabled = page === 0; next.disabled = page + 1 === pages;
  };
  previous.addEventListener('click', () => { page--; render(); });
  next.addEventListener('click', () => { page++; render(); });
  filter.addEventListener('input', () => {
    const query = filter.value.toLocaleLowerCase();
    selected = data.groups.filter((_, index) => searchable[index].includes(query)); page = 0; render();
  });
  render();
})();
