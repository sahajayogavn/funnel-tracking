// code:web-db-001:postgres-connector
// Server-only PostgreSQL pool. DATABASE_URL must point at the shared database.
import 'server-only';
import { Pool, types, type PoolClient, type QueryResultRow } from 'pg';

// Preserve the SQLite-era values at the one boundary where node-postgres
// otherwise changes them.  The web layer has always parsed JSON text itself,
// represents identity values as JavaScript numbers, and passes legacy
// timestamps to formatters expecting a string.  Keeping that contract avoids
// runtime-only failures that TypeScript cannot see.
types.setTypeParser(3802, value => value); // jsonb
types.setTypeParser(20, value => Number(value)); // int8 / BIGINT
types.setTypeParser(1114, value => value); // timestamp without time zone
types.setTypeParser(1184, value => value); // timestamp with time zone

declare global { var __funnelPgPool: Pool | undefined; }

function databaseUrl(): string {
  const value = process.env.DATABASE_URL;
  if (!value) throw new Error('DATABASE_URL is required (for example postgresql://…@10.0.1.42:5432/funnel_tracking)');
  if (!/^postgres(?:ql)?:\/\//i.test(value)) throw new Error('DATABASE_URL must be a PostgreSQL connection URL');
  return value;
}

export function getDb(): Pool {
  if (!globalThis.__funnelPgPool) {
    globalThis.__funnelPgPool = new Pool({
      connectionString: databaseUrl(), max: Number(process.env.DATABASE_POOL_MAX || 10),
      idleTimeoutMillis: 30_000, connectionTimeoutMillis: 10_000,
      application_name: 'funnel-tracking-web',
      options: '-c timezone=UTC',
    });
  }
  return globalThis.__funnelPgPool;
}

/** Convert legacy SQLite positional placeholders without changing quoted question marks. */
export function sqlitePlaceholders(sql: string): string {
  let index = 0; let quote: "'" | '"' | null = null; let lineComment = false; let blockComment = false; let output = '';
  for (let i = 0; i < sql.length; i += 1) {
    const char = sql[i];
    if (lineComment) { output += char; if (char === '\n') lineComment = false; }
    else if (blockComment) { output += char; if (char === '*' && sql[i + 1] === '/') { output += sql[++i]; blockComment = false; } }
    else if (quote) { output += char; if (char === quote) { if (sql[i + 1] === quote) { output += sql[++i]; } else quote = null; } }
    else if (char === '-' && sql[i + 1] === '-') { output += char + sql[++i]; lineComment = true; }
    else if (char === '/' && sql[i + 1] === '*') { output += char + sql[++i]; blockComment = true; }
    else if (char === "'" || char === '"') { quote = char; output += char; }
    else if (char === '?') { output += `$${++index}`; } else output += char;
  }
  return output;
}

/**
 * PostgreSQL folds unquoted identifiers to lowercase. The SQLite queries that
 * predate the migration use camelCase aliases (for example `AS queueType`),
 * so pg returns `queuetype` while the TypeScript callers correctly expect
 * `queueType`. Preserve those explicit aliases at the database boundary while
 * legacy queries are incrementally converted to quoted PostgreSQL aliases.
 */
function restoreCamelCaseAliases<T extends QueryResultRow>(sql: string, rows: T[]): T[] {
  const aliases = new Map<string, string>();
  for (const match of sql.matchAll(/\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\b/gi)) {
    const alias = match[1];
    if (alias !== alias.toLowerCase()) aliases.set(alias.toLowerCase(), alias);
  }
  if (aliases.size === 0) return rows;

  return rows.map(row => {
    for (const [postgresName, applicationName] of aliases) {
      if (postgresName in row && !(applicationName in row)) {
        (row as Record<string, unknown>)[applicationName] = row[postgresName];
      }
    }
    return row;
  });
}

/**
 * SQLite returned DATETIME columns as strings. `pg` returns JavaScript Dates
 * for PostgreSQL timestamp columns, but the dashboard's persisted-data types
 * and client-safe date helpers intentionally use ISO strings. Normalize at
 * the boundary so server-rendered pages and API responses have one contract.
 */
function serializeTimestampColumns<T extends QueryResultRow>(rows: T[]): T[] {
  return rows.map(row => {
    for (const [column, value] of Object.entries(row)) {
      if (value instanceof Date) (row as Record<string, unknown>)[column] = value.toISOString();
    }
    return row;
  });
}

export async function query<T extends QueryResultRow = QueryResultRow>(sql: string, values: unknown[] = []): Promise<T[]> {
  const rows = (await getDb().query<T>(sqlitePlaceholders(sql), values)).rows;
  return restoreCamelCaseAliases(sql, serializeTimestampColumns(rows));
}
export async function queryOne<T extends QueryResultRow = QueryResultRow>(sql: string, values: unknown[] = []): Promise<T | undefined> { return (await query<T>(sql, values))[0]; }
export async function execute(sql: string, values: unknown[] = []): Promise<{ changes: number }> {
  const result = await getDb().query(sqlitePlaceholders(sql), values); return { changes: result.rowCount || 0 };
}

/** Run related writes on one checked-out client, with an automatic rollback. */
export async function transaction<T>(work: (client: PoolClient) => Promise<T>): Promise<T> {
  const client = await getDb().connect();
  try {
    await client.query('BEGIN');
    const value = await work(client);
    await client.query('COMMIT');
    return value;
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
}
