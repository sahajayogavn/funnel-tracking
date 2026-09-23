/* eslint-disable @typescript-eslint/no-require-imports */
// Run: node --experimental-strip-types --test tests/return-to.cjs
const { test } = require("node:test");
const assert = require("node:assert/strict");

test("return-to retains the originally requested internal path, query and hash", async () => {
  const { returnToPath } = await import("../src/lib/return-to.ts");
  assert.equal(returnToPath("/seekers/42?tab=messages#latest"), "/seekers/42?tab=messages#latest");
  assert.equal(returnToPath("/stats"), "/stats");
});

test("return-to rejects external targets and login loops", async () => {
  const { returnToPath } = await import("../src/lib/return-to.ts");
  for (const target of [null, "", "https://evil.example", "//evil.example", "/\\evil.example", "/login", "/login/reset"]) {
    assert.equal(returnToPath(target), "/");
  }
});
