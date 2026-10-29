(() => {
  'use strict';
  const payload = document.getElementById('ici-result');
  let savedPromise;
  // The saved result is embedded once as raw DEFLATE + base64 and inflated locally.
  const saved = () => savedPromise || (savedPromise = (async () => {
    const binary = atob(payload.textContent.trim());
    const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
    return JSON.parse(await new Response(stream).text());
  })());
  const unavailable = 'This browser cannot expand the embedded saved result; open the saved JSON result instead.';
  const node = (tag, text) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = String(text);
    return element;
  };
  const plain = value => typeof value === 'string' ? value : value === null || value === undefined ? '\u2014' : JSON.stringify(value);
  const order = (left, right) => {
    const a = [...left], b = [...right];
    for (let index = 0; index < Math.min(a.length, b.length); index++) {
      const difference = a[index].codePointAt(0) - b[index].codePointAt(0);
      if (difference) return difference;
    }
    return a.length - b.length;
  };
  const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const copyLocation = (path, line, column) => {
    const text = `${path}:${line}${Number.isInteger(column) && column >= 0 ? ':' + column : ''}`;
    const span = node('span'); span.appendChild(node('code', text));
    if (path === '[external]') return span;
    const copy = node('button', 'Copy'); copy.type = 'button';
    copy.dataset.path = path; copy.dataset.line = String(line); copy.dataset.copyLocation = text;
    copy.setAttribute('aria-label', `Copy location ${text}`);
    span.appendChild(document.createTextNode(' ')); span.appendChild(copy);
    return span;
  };
  // Mirrors the server-side generic renderer: objects, tables, lists, preformatted text.
  const rich = value => {
    if (isObject(value)) {
      const list = node('dl');
      for (const key of Object.keys(value).sort(order)) { list.appendChild(node('dt', key)); const item = node('dd'); item.appendChild(rich(value[key])); list.appendChild(item); }
      return list;
    }
    if (Array.isArray(value)) {
      if (!value.length) { const empty = node('span', 'none recorded'); empty.className = 'muted'; return empty; }
      if (value.every(isObject)) {
        const keys = [...new Set(value.flatMap(Object.keys))].sort(order);
        if (keys.length > 64) { const list = node('ol'); for (const item of value) { const entry = node('li'); entry.appendChild(rich(item)); list.appendChild(entry); } return list; }
        const scroll = node('div'); scroll.className = 'scroll';
        const table = node('table'), head = node('thead'), header = node('tr'), body = node('tbody');
        for (const key of keys) header.appendChild(node('th', key));
        head.appendChild(header); table.appendChild(head);
        for (const item of value) {
          const row = node('tr'); row.dataset.searchRow = '';
          for (const key of keys) {
            const cell = node('td');
            if ((key === 'file_path' || key === 'path') && Number.isInteger(item.start_line) && item.start_line >= 0) {
              cell.appendChild(copyLocation(typeof item[key] === 'string' ? item[key] : 'unknown', item.start_line, item.start_column));
            } else cell.appendChild(rich(item[key]));
            row.appendChild(cell);
          }
          body.appendChild(row);
        }
        table.appendChild(body); scroll.appendChild(table); return scroll;
      }
      const list = node('ul');
      for (const item of value) { const entry = node('li'); entry.appendChild(rich(item)); list.appendChild(entry); }
      return list;
    }
    if (typeof value === 'string' && /[\r\n]/.test(value)) return node('pre', value);
    return document.createTextNode(plain(value));
  };
  const card = finding => {
    const article = node('article'); article.className = 'finding'; article.dataset.searchRow = '';
    const span = finding.primary_location || {};
    article.appendChild(node('h3', `${plain(finding.rule_id)} \u00b7 ${plain(finding.severity)}`));
    const where = node('p');
    where.appendChild(copyLocation(typeof span.path === 'string' ? span.path : 'unknown',
      Number.isInteger(span.start_line) && span.start_line >= 0 ? span.start_line : 1, span.start_column));
    where.appendChild(document.createTextNode(' ' + plain(finding.message)));
    article.appendChild(where); article.appendChild(node('p', plain(finding.explanation)));
    const marks = [];
    for (const key of ['evidence', 'confidence', 'component_id', 'task_id', 'variant']) {
      if (typeof finding[key] === 'string' && finding[key]) marks.push(`${key}: ${finding[key]}`);
    }
    const suppression = finding.suppression || {};
    if (suppression.suppressed === true) marks.push(`suppressed: ${plain(suppression.reason)} (${plain(suppression.origin)})`);
    const muted = node('p', marks.join(' \u00b7 ')); muted.className = 'muted'; article.appendChild(muted);
    for (const key of ['remediation', 'snippet', 'metrics', 'related_locations', 'fixes', 'limitations', 'suppression']) {
      const value = finding[key];
      if (value === undefined || value === null || value === '' || (Array.isArray(value) && !value.length) ||
        (isObject(value) && !Object.keys(value).length)) continue;
      const details = node('details'); details.appendChild(node('summary', key)); details.appendChild(rich(value));
      article.appendChild(details);
    }
    return article;
  };
  const resolve = (data, pointer) => pointer === '' ? data : pointer.slice(1).split('/')
    .reduce((value, key) => value === null || value === undefined ? undefined : value[key.replace(/~1/g, '/').replace(/~0/g, '~')], data);
  let sourceControls = () => {};
  const hydrate = element => {
    if (element.dataset.hydrated) return Promise.resolve();
    element.dataset.hydrated = 'pending';
    return saved().then(data => {
      if (element.dataset.lazyFindings !== undefined) {
        for (const [pointer, engine] of JSON.parse(element.dataset.lazyFindings)) {
          const finding = {...resolve(data, pointer)};
          if (engine !== null) finding.evidence = (resolve(data, engine) || {}).evidence;
          element.appendChild(card(finding));
        }
      } else {
        let value = resolve(data, element.dataset.lazy);
        if (element.dataset.indices !== undefined) value = element.dataset.indices.split(' ').map(index => value[Number(index)]);
        element.appendChild(rich(value));
      }
      element.dataset.hydrated = 'true'; sourceControls(element);
    }, () => { element.appendChild(node('p', unavailable)); element.dataset.hydrated = 'failed'; });
  };
  document.querySelectorAll('details[data-lazy]').forEach(details => details.addEventListener('toggle', () => { if (details.open) hydrate(details); }));
  document.querySelectorAll('[data-lazy-findings]').forEach(list => {
    const details = list.closest('details');
    if (details) details.addEventListener('toggle', () => { if (details.open) hydrate(list); }); else hydrate(list);
  });
  let expanded;
  const expandAll = () => expanded || (expanded = Promise.all([...document.querySelectorAll('[data-lazy],[data-lazy-findings]')].map(hydrate)));
  const raw = document.getElementById('raw-result');
  if (payload && raw) raw.closest('details').addEventListener('toggle', () => {
    if (!raw.closest('details').open || raw.textContent) return;
    saved().then(value => { raw.textContent = JSON.stringify(value, null, 2); }, () => { raw.textContent = unavailable; });
  });
  const filter = document.getElementById('finding-filter');
  const matches = row => !filter || row.textContent.toLocaleLowerCase().includes(filter.value.toLocaleLowerCase());
  if (filter) {
    const count = document.getElementById('filter-count');
    filter.addEventListener('input', async () => {
      // Searching covers the complete saved inventory, including sections not yet opened.
      if (filter.value) await expandAll();
      const rows = [...document.querySelectorAll('[data-search-row]')];
      let shown = 0;
      for (const row of rows) {
        const scope = row.closest('[data-line-scope]');
        row.hidden = !matches(row) ||
          (row.dataset.sourceScope && row.dataset.sourceScope !== 'source' &&
            scope.querySelector('[data-scope-toggle]').value === 'source');
        if (!row.hidden) shown++;
      }
      if (count) count.textContent = ` ${shown} of ${rows.length} rows shown`;
    });
  }
  document.querySelectorAll('[data-scope-toggle]').forEach(select => select.addEventListener('change', () => {
    select.closest('[data-line-scope]').querySelectorAll('[data-source-scope]').forEach(row => {
      row.hidden = (select.value === 'source' && row.dataset.sourceScope !== 'source') || !matches(row);
    });
  }));
  const navigationElement = document.getElementById('ici-source-navigation');
  const navigation = navigationElement ? JSON.parse(navigationElement.textContent) : null;
  const component = value => encodeURIComponent(value).replace(/[!'()*]/g,
    character => '%' + character.charCodeAt(0).toString(16).toUpperCase());
  const relativePath = path => path && path !== '[external]' && !/[\\:\u0000-\u001f\u007f]/.test(path) &&
    !path.startsWith('/') && path.split('/').every(part => part && part !== '.' && part !== '..');
  sourceControls = root => {
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
  const data = JSON.parse(groupPayload.textContent);
  saved().then(documentData => {
  const findings = Array.isArray(documentData.results)
    ? documentData.results.flatMap(engine => (engine.findings || []).map(finding => ({...finding, evidence: finding.evidence || engine.evidence})))
    : documentData.findings || [];
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
  }, () => { document.getElementById('issue-groups').appendChild(node('p', unavailable)); });
})();
