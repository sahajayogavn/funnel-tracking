// code:web-db-001:sqlite-connector
// Direct connection to existing FrankenSQLite database
// SERVER-ONLY — do not import this from client components
import 'server-only';
import Database from 'better-sqlite3';
import path from 'path';

const DB_PATH = path.resolve(process.cwd(), '..', 'memory', 'agent_memory', 'frankensqlite.db');

declare global {
  var __db: Database.Database | undefined;
}

export function getDb(): Database.Database {
  // Retrospective [2026-04-06]: SQLite Ghost Handle Crash
  // Fix: Engineered a self-healing connection loop that validates `sqlite_master`.
  // Root Cause: During development, if `rm -f frankensqlite.db*` is manually executed, Next.js's dev server HMR (Hot Module Replacement) stubbornly clings to the orphaned memory pointer (inode). This previously caused `no such table: threads` until the local dev server was hard restarted.
  if (globalThis.__db) {
    try {
      // If the developer ran `rm -f` in bash, the lingering WAL handle will crash or lose schema.
      // We test for life. If it fails, we gracefully close the ghost and rebuild.
      globalThis.__db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='threads'").get();
    } catch {
      try { globalThis.__db.close(); } catch {}
      globalThis.__db = undefined;
    }
  }

  if (!globalThis.__db) {
    // The Python worker and the Next.js process share this WAL database. Give
    // short worker transactions time to finish instead of surfacing SQLITE_BUSY
    // to the MAS controls after better-sqlite3's 5-second default timeout.
    globalThis.__db = new Database(DB_PATH, { fileMustExist: true, timeout: 30_000 });
    globalThis.__db.pragma('busy_timeout = 30000');
    globalThis.__db.pragma('journal_mode = WAL');
  }
  
  return globalThis.__db;
}
