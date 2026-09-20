# SQLite to PostgreSQL baseline migration

This directory contains the one-way baseline migration tool used for the
Funnel Tracking PostgreSQL cutover. It creates a PostgreSQL schema from
the SQLite catalog, copies a consistent SQLite backup, resets identity
sequences, and compares table row counts.  It does not change application
code or delete the SQLite source.

## Current production state

Cutover is complete: PostgreSQL at `10.0.1.42` is authoritative. Production
processes set `FUNNEL_REQUIRE_POSTGRES=1`, which makes the Python connection
boundary reject a missing or SQLite `DATABASE_URL`. The legacy SQLite source is
retained read-only as an audit snapshot; tests receive per-test temporary
SQLite databases and must not write that snapshot.

## Connection contract

All future services must receive a single environment variable:

```sh
DATABASE_URL=postgresql://funnel_tracking:<password>@10.0.1.42:5432/funnel_tracking
```

Do not commit this URL or its password. PostgreSQL is the authoritative
database. Do not point a production process at
`memory/agent_memory/frankensqlite.db`.

The baseline intentionally maps SQLite `DATETIME` declarations to `timestamp
without time zone`. Existing values represent Vietnam-local, naive timestamps;
this avoids silently shifting MAS time-aware data during import. Columns
declared as SQLite `TEXT` remain text, including legacy message display times.
Columns whose names end in `_json` become `jsonb` and must contain valid JSON.

## Required dependency

Install the PostgreSQL extra before an actual import:

```sh
pip install -e '.[postgres]'
```

## Safe rehearsal and import

Always stop every SQLite writer before taking the final snapshot. `--dry-run`
only reads SQLite and prints the planned catalog; it never opens PostgreSQL.

```sh
python db_migrations/sqlite_to_postgres.py \
  --sqlite memory/agent_memory/frankensqlite.db --dry-run

python db_migrations/sqlite_to_postgres.py \
  --sqlite memory/agent_memory/frankensqlite.db \
  --database-url "$DATABASE_URL" \
  --create-schema --verify
```

The target must be empty unless `--allow-nonempty` is explicitly supplied.
That guard prevents an accidental mixed/duplicate migration. A retry after a
partial run should use a newly-created empty database, not `--allow-nonempty`.

## Verification scope

`--verify` compares the row count of every application table and exits nonzero
on any mismatch. It also validates JSON before writing; FK rows are copied in
parent-first order, so PostgreSQL's immediate FK checks validate them during
the copy. This is an import integrity check, not an
application compatibility test. Run the Python and web PostgreSQL integration
suites and manually validate time-sensitive MAS flows after any migration
change.
