import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

export const SESSION_COOKIE = "sahaja_session";
export const SESSION_MAX_AGE = 60 * 60 * 24 * 365;

function secret(): string {
  if (process.env.AUTH_SECRET) return process.env.AUTH_SECRET;
  // Persist the local key across server restarts. Deployments with multiple
  // instances must provide the same AUTH_SECRET to every instance.
  const directory = path.join(process.cwd(), ".auth");
  const filename = path.join(directory, "session-secret");
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  try {
    writeFileSync(filename, randomBytes(32).toString("hex"), { flag: "wx", mode: 0o600 });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
  }
  return readFileSync(filename, "utf8").trim();
}

function signature(payload: string): string {
  return createHmac("sha256", secret()).update(payload).digest("base64url");
}

export function createSession(now = Date.now()): string {
  const payload = `v1.${Math.floor(now / 1000) + SESSION_MAX_AGE}.${randomBytes(16).toString("hex")}`;
  return `${payload}.${signature(payload)}`;
}

export function validSession(token: string | undefined, now = Date.now()): boolean {
  if (!token || token.length > 256) return false;
  const parts = token.split(".");
  if (parts.length !== 4 || parts[0] !== "v1" || !/^\d+$/.test(parts[1]) || !/^[a-f0-9]{32}$/.test(parts[2])) return false;
  if (Number(parts[1]) <= Math.floor(now / 1000)) return false;
  const expected = Buffer.from(signature(parts.slice(0, 3).join(".")));
  const actual = Buffer.from(parts[3]);
  return expected.length === actual.length && timingSafeEqual(expected, actual);
}

export function correctAnswers(identity: unknown, technique: unknown): boolean {
  const normalize = (value: unknown) => typeof value === "string" ? value.normalize("NFKC").trim().replace(/\s+/g, " ").toLowerCase() : "";
  return normalize(identity) === "shri adi shakti" && normalize(technique) === "bandhan";
}
