// Run: node tests/test_pending_history.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('../web/node_modules/typescript');
for (const ext of ['.ts', '.tsx']) require.extensions[ext] = (mod, file) => {
  mod._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
    jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  }}).outputText, file);
};
const {pendingHistoryFromObservation: parse} = require('../web/src/lib/pending-history.ts');
const {PendingMessageHistory} = require('../web/src/components/pending-message-history.tsx');
const React = require('../web/node_modules/react');
const {renderToStaticMarkup} = require('../web/node_modules/react-dom/server');
const payload = JSON.stringify({messages:[{source_id:'mid.1', sender:'Page', body:'<script>alert(1)</script>', timestamp:'9:00 AM'}], issues:[{source_id:'mid.1', reason:'unverified_actor'}]});
const history = parse(payload, '2026-09-21T12:00:00Z', []);
assert.equal(history.messages.length, 1);
assert.equal(history.messages[0].sender, undefined);
assert.equal(history.messages[0].timestamp, undefined);
assert.equal(parse(payload, 'now', [{sourceId:'mid.1', senderConfidence:'explicit'}]), null);
assert.equal(parse(payload, 'now', [{sourceId:'mid.1', senderConfidence:'unknown'}]).messages.length, 1);
assert.deepEqual(parse('bad json', 'now', []).reasons, ['observation_decode_failed']);
const html = renderToStaticMarkup(React.createElement(PendingMessageHistory, {history}));
assert.ok(html.includes('Chờ xác minh'));
assert.ok(html.includes('không phải thời gian gửi tin'));
assert.ok(!html.includes('<script>'));
assert.ok(!html.includes('9:00 AM'));
assert.equal(renderToStaticMarkup(React.createElement(PendingMessageHistory, {history:null})), '');
console.log('Pending history: 10 assertions passed (uncertainty, canonical dedup, malformed payload, escaping, empty state).');
