// Development harness: load a native report into a minimal DOM, run its own inline script and
// open every on-demand section. Prints JSON describing exactly what a browser user would see.
// Usage: node lazy-dom-check.cjs report.html [filter-query]
'use strict';
const fs = require('node:fs'), vm = require('node:vm');
const VOID = new Set(['meta', 'input', 'br', 'hr', 'img', 'link']);
const RAW = new Set(['script', 'style']);
const entities = text => text.replace(/&(amp|lt|gt|quot|#39);/g, (_, name) =>
  ({amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'"})[name]);
class Text { constructor(text) { this.data = text; this.parentNode = null; } get textContent() { return this.data; } }
const camel = name => name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
const kebab = name => name.replace(/[A-Z]/g, letter => '-' + letter.toLowerCase());
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.childNodes = []; this.parentNode = null; this.attributes = {}; this.listeners = {};
    const element = this;
    this.dataset = new Proxy({}, {
      get(_, key) { const value = element.attributes['data-' + kebab(String(key))]; return value === undefined ? undefined : value; },
      set(_, key, value) { element.attributes['data-' + kebab(String(key))] = String(value); return true; },
      has(_, key) { return ('data-' + kebab(String(key))) in element.attributes; },
    });
  }
  get id() { return this.attributes.id; }
  get className() { return this.attributes.class || ''; } set className(value) { this.attributes.class = value; }
  get type() { return this.attributes.type; } set type(value) { this.attributes.type = value; }
  get hidden() { return 'hidden' in this.attributes; } set hidden(value) { if (value) this.attributes.hidden = ''; else delete this.attributes.hidden; }
  get open() { return 'open' in this.attributes; } set open(value) { if (value) this.attributes.open = ''; else delete this.attributes.open; }
  get value() { return this.attributes.value === undefined ? '' : this.attributes.value; } set value(value) { this.attributes.value = value; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] === undefined ? null : this.attributes[name]; }
  appendChild(child) { child.parentNode = this; this.childNodes.push(child); return child; }
  replaceChildren(...children) { this.childNodes = []; children.forEach(child => this.appendChild(child)); }
  get textContent() { return this.childNodes.map(child => child.textContent).join(''); }
  set textContent(value) { this.childNodes = []; this.appendChild(new Text(String(value))); }
  addEventListener(type, callback) { (this.listeners[type] = this.listeners[type] || []).push(callback); }
  dispatch(type, event = {}) { return Promise.all((this.listeners[type] || []).map(callback => callback({target: this, ...event}))); }
  matches(selector) {
    return selector.split(',').some(part => {
      const match = /^([a-z]*)((?:\[[a-z-]+\])*)$/.exec(part.trim());
      if (!match) throw new Error('unsupported selector ' + part);
      if (match[1] && match[1].toUpperCase() !== this.tagName) return false;
      return (match[2].match(/\[([a-z-]+)\]/g) || []).every(item => item.slice(1, -1) in this.attributes);
    });
  }
  closest(selector) { let current = this; while (current instanceof Element) { if (current.matches(selector)) return current; current = current.parentNode; } return null; }
  *walk() { for (const child of this.childNodes) if (child instanceof Element) { yield child; yield* child.walk(); } }
  querySelectorAll(selector) { return [...this.walk()].filter(element => element.matches(selector)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function parse(html) {
  const root = new Element('#document'); let current = root, index = 0;
  while (index < html.length) {
    if (html.startsWith('<!', index)) { index = html.indexOf('>', index) + 1; continue; }
    if (html.startsWith('</', index)) {
      const end = html.indexOf('>', index), tag = html.slice(index + 2, end).toLowerCase();
      while (current !== root && current.tagName !== tag.toUpperCase()) current = current.parentNode;
      if (current !== root) current = current.parentNode;
      index = end + 1; continue;
    }
    if (html[index] === '<') {
      const tag = /^<([a-zA-Z0-9]+)/.exec(html.slice(index))[1].toLowerCase();
      let cursor = index + 1 + tag.length; const element = new Element(tag);
      for (;;) {
        while (/\s/.test(html[cursor])) cursor++;
        if (html[cursor] === '>') { cursor++; break; }
        const name = /^[^\s=>]+/.exec(html.slice(cursor))[0]; cursor += name.length;
        let value = '';
        if (html[cursor] === '=') {
          cursor++;
          if (html[cursor] === '"') { const end = html.indexOf('"', cursor + 1); value = html.slice(cursor + 1, end); cursor = end + 1; }
          else { const raw = /^[^\s>]*/.exec(html.slice(cursor))[0]; value = raw; cursor += raw.length; }
        }
        element.attributes[name.toLowerCase()] = entities(value);
      }
      current.appendChild(element); index = cursor;
      if (RAW.has(tag)) {
        const end = html.indexOf('</' + tag + '>', index);
        element.appendChild(new Text(html.slice(index, end))); index = end + tag.length + 3;
      } else if (!VOID.has(tag)) current = element;
      continue;
    }
    const end = html.indexOf('<', index), stop = end < 0 ? html.length : end;
    current.appendChild(new Text(entities(html.slice(index, stop)))); index = stop;
  }
  return root;
}
async function main() {
  const [file, query] = process.argv.slice(2);
  const html = fs.readFileSync(file, 'utf8');
  if (/<script[^>]+src=|<link |https?:\/\/[^"'\s]*\.(js|css)/.test(html)) throw new Error('external resource reference');
  const root = parse(html);
  const scripts = root.querySelectorAll('script').filter(script => !script.attributes.id);
  const listeners = {};
  const document = {
    getElementById: id => [...root.walk()].find(element => element.attributes.id === id) || null,
    querySelectorAll: selector => root.querySelectorAll(selector),
    createElement: tag => new Element(tag),
    createTextNode: text => new Text(text),
    addEventListener: (type, callback) => (listeners[type] = listeners[type] || []).push(callback),
  };
  const context = {document, window: {location: {href: ''}, open() {}}, navigator: {clipboard: {writeText: () => Promise.resolve()}},
    atob, Blob, Response, DecompressionStream, JSON, Promise, Uint8Array, Number, String, Object, Array, Set, Math, Error};
  for (const script of scripts) vm.runInNewContext(script.textContent, context);
  const settle = () => new Promise(resolve => setTimeout(resolve, 0));
  const sections = [];
  const lazy = root.querySelectorAll('details[data-lazy]');
  for (const details of lazy) { details.open = true; await details.dispatch('toggle'); }
  for (const list of root.querySelectorAll('[data-lazy-findings]')) {
    const details = list.closest('details'); if (details) { details.open = true; await details.dispatch('toggle'); }
  }
  for (let round = 0; round < 5; round++) await settle();
  for (const details of lazy) {
    sections.push({pointer: details.attributes['data-lazy'], indices: details.attributes['data-indices'] === undefined ? null : details.attributes['data-indices'],
      state: details.attributes['data-hydrated'], text: details.childNodes.slice(1).map(child => child.textContent).join('')});
  }
  const cards = root.querySelectorAll('[data-lazy-findings]').map(list => ({pointers: JSON.parse(list.attributes['data-lazy-findings']),
    state: list.attributes['data-hydrated'], texts: list.childNodes.map(child => child.textContent)}));
  const raw = document.getElementById('raw-result'); let saved = null;
  if (raw) { raw.closest('details').open = true; await raw.closest('details').dispatch('toggle'); for (let round = 0; round < 5; round++) await settle(); saved = JSON.parse(raw.textContent); }
  let filtered = null;
  const filter = document.getElementById('finding-filter');
  if (filter && query !== undefined) {
    filter.value = query; await filter.dispatch('input');
    const rows = root.querySelectorAll('[data-search-row]');
    filtered = {shown: rows.filter(row => !row.hidden).length, total: rows.length, label: document.getElementById('filter-count').textContent};
  }
  const payload = document.getElementById('ici-result');
  process.stdout.write(JSON.stringify({sections, cards, saved, filtered, declared_bytes: Number(payload.attributes['data-bytes'])}));
}
main().catch(error => { process.stderr.write(String(error && error.stack || error) + '\n'); process.exit(1); });
