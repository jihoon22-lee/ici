const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const callbacks = [], children = []; let copied = 0, opened;
const parent = {appendChild(child) { children.push(child); child.parentNode = this; },querySelector() { return copy; }};
const copy = {dataset:{copyLocation:"src/café's [x].cpp:12",path:"src/café's [x].cpp",line:'12'},parentNode:parent,click(){copied++;}};
const selector = {value:'vscode'};
const document = {
  getElementById(id) { if(id==='ici-source-navigation')return {textContent:JSON.stringify({root:'/check out',repository_url:'https://github.example/org/repo',commit_ref:'abc'})}; if(id==='editor-select')return selector; return null; },
  querySelectorAll(query) { return query==='[data-copy-location][data-path]' ? [copy] : []; },
  createElement(tag) { return {tagName:tag,dataset:{},setAttribute(){},appendChild(){}}; },
  createTextNode(text) { return {textContent:text}; },
  addEventListener(name,callback) { callbacks.push(callback); }
};
const window = {location:{href:''},open(...args){opened=args;}};
vm.runInNewContext(fs.readFileSync('crates/ici-reporting/assets/native.js','utf8'),{document,window,navigator:{clipboard:{writeText(){return Promise.resolve();}}}});
const open = children.find(child=>child.dataset&&child.dataset.openSource);
const source = children.find(child=>child.textContent==='Source');
assert.equal(source.href,'https://github.example/org/repo/blob/abc/src/caf%C3%A9%27s%20%5Bx%5D.cpp#L12');
function click(choice){selector.value=choice;callbacks[0]({target:{closest(){return open;}},preventDefault(){}});return window.location.href;}
assert.equal(click('vscode'),'vscode://file//check%20out/src/caf%C3%A9%27s%20%5Bx%5D.cpp:12');
assert.equal(click('cursor'),'cursor://file//check%20out/src/caf%C3%A9%27s%20%5Bx%5D.cpp:12');
assert.equal(click('pycharm'),'idea://open?file=%2Fcheck%20out%2Fsrc%2Fcaf%C3%A9%27s%20%5Bx%5D.cpp&line=12');
assert.equal(click('sublime'),'subl:///check%20out/src/caf%C3%A9%27s%20%5Bx%5D.cpp:12');
click('file');assert.equal(opened[0],'file:///check%20out/src/caf%C3%A9%27s%20%5Bx%5D.cpp');
click('copy');assert.equal(copied,1);
const before=window.location.href;open.dataset.openSource='../escape';click('vscode');assert.equal(window.location.href,before);
process.stdout.write('PASS: explicit VS Code, Cursor, PyCharm, Sublime, browser-file, Copy, repository links and traversal refusal\n');
