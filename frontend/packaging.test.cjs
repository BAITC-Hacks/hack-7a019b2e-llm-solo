'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { assets } = require('./build.cjs');
const read = name => fs.readFileSync(path.join(__dirname, name), 'utf8').replace(/\r\n/g, '\n');

test('shipped bundles exactly match the editable source modules', () => {
  for (const [name, content] of Object.entries(assets())) assert.equal(read(name), content, name);
  new vm.Script(read('app.js'));
});
test('Muha four-file export includes all runtime assets and the lion', () => {
  const html = read('index.html');
  const refs = [...html.matchAll(/(?:src|href)="(\.\/[^"#]+)"/g)].map(m => m[1].split('?')[0]);
  assert.deepEqual(refs.filter(p => p !== './').sort(), ['./app.js', './data.js', './styles.css']);
  assert.match(read('app.js'), /TraceAssistant/);
  assert.match(read('styles.css'), /agent-pet/);
  assert.match(html, /id="agent-pet-button"/);
  assert.match(read('app-source.js'), /fetch\('\.\.\/graph\.json'/);
  assert.doesNotMatch(read('app-source.js'), /\.\.\/out\/graph\.json/);
});
