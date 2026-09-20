// PostgreSQL integration assertions belong in the deployment test suite. This
// keeps deterministic date-range behavior unit-testable without SQLite.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const ts = require('typescript');
const m = new Module('stats-test');
m.require = name => name === './db'
  ? { query: async () => [], queryOne: async () => undefined }
  : name === './programs' ? { normalizeProgramCity: city => city } : require(name);
m._compile(ts.transpileModule(fs.readFileSync('src/lib/stats.ts', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, 'stats-test.js');
const { statsRange } = m.exports;
(async () => {
  const range = await statsRange('7', new Date('2026-09-19T07:00:00Z'));
  assert.equal(range.from, '2026-09-13');
  assert.equal(range.days, 7);
  assert.equal((await statsRange('invalid')).days, 7);
  assert.equal((await statsRange('30', new Date('2026-09-19T18:00:00Z'))).today, '2026-09-20');
  console.log('Stats range checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
