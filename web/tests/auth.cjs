// Run: node --experimental-strip-types --test tests/auth.cjs
const { test } = require("node:test");
const assert = require("node:assert/strict");

test("answers require both correct values and normalize case/whitespace", async () => {
  const { correctAnswers } = await import("../src/lib/auth.ts");
  assert.equal(correctAnswers("Shri Adi Shakti", "bandhan"), true);
  assert.equal(correctAnswers(" SHRI  ADI SHAKTI ", " \tBaNdHaN\n"), true);
  for (const [identity, technique] of [["Shri Buddha", "bandhan"], ["Shi Jesus", "bandhan"], ["Shri Adi Shakti", "b"], ["Shri Adi Shakti", "band han"], [null, "bandhan"], ["Shri Adi Shakti", {}]]) {
    assert.equal(correctAnswers(identity, technique), false);
  }
});

test("sessions reject tampering, expiration, malformed cookies and changed secrets", async () => {
  process.env.AUTH_SECRET = "test-only-session-signing-key-123456789";
  const { createSession, validSession, SESSION_MAX_AGE } = await import("../src/lib/auth.ts");
  const now = Date.now();
  const token = createSession(now);
  assert.equal(validSession(token, now), true);
  assert.equal(validSession(token, now + SESSION_MAX_AGE * 1000), false);
  assert.equal(validSession(token.replace("v1.", "v1.9"), now), false);
  for (const invalid of [undefined, "true", "", token + "x", "v1.9999999999.fake.fake"]) {
    assert.equal(validSession(invalid, now), false);
  }
  process.env.AUTH_SECRET = "different-test-only-session-signing-key";
  assert.equal(validSession(token, now), false);
});

const base = process.env.AUTH_TEST_URL;
test("HTTP gate, login cookie, returning visits, and direct API access", { skip: !base }, async () => {
  const anonymous = await fetch(`${base}/seekers?test=auth`, { redirect: "manual" });
  assert.equal(anonymous.status, 307);
  assert.equal(new URL(anonymous.headers.get("location"), base).searchParams.get("next"), "/seekers?test=auth");
  assert.equal((await fetch(`${base}/api/not-found`)).status, 401);
  const loginPage = await (await fetch(`${base}/login`)).text();
  assert.ok(loginPage.includes("Shri Mataji là ai?"));
  assert.ok(!loginPage.includes('class="sidebar"'));
  const login = (identity, technique) => fetch(`${base}/api/auth`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ identity, technique }) });
  const wrong = await login("Shri Buddha", "bandhan");
  assert.equal(wrong.status, 401);
  assert.equal(wrong.headers.get("set-cookie"), null);
  assert.equal((await login("Shri Adi Shakti", "wrong")).status, 401);
  const success = await login("Shri Adi Shakti", "  BaNdHaN  ");
  assert.equal(success.status, 200);
  const cookie = success.headers.get("set-cookie");
  assert.match(cookie, /HttpOnly/i);
  assert.match(cookie, /SameSite=lax/i);
  assert.match(cookie, /Max-Age=31536000/i);
  const headers = { Cookie: cookie.split(";")[0] };
  for (let visit = 0; visit < 2; visit++) {
    assert.equal((await fetch(`${base}/not-found`, { headers, redirect: "manual" })).status, 404);
  }
  assert.equal((await fetch(`${base}/api/not-found`, { headers })).status, 404);
  assert.equal((await fetch(`${base}/api/not-found`, { headers: { Cookie: "sahaja_session=true" } })).status, 401);
  const { session } = await success.json();
  const restored = await fetch(`${base}/api/auth/restore`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session }) });
  assert.equal(restored.status, 200);
  assert.match(restored.headers.get("set-cookie"), /HttpOnly/i);
  assert.equal((await fetch(`${base}/api/auth/restore`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session: `${session}tampered` }) })).status, 401);
});
