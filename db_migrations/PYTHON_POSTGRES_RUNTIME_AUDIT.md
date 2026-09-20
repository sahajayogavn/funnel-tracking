# Python PostgreSQL runtime audit

Audit date: 2026-09-19. Scope: current Python DB compatibility boundary and
representative queue workflow, against an isolated PostgreSQL 17 database on
`10.0.1.42`. No query in this audit targeted `funnel_tracking`.

## Live results

| Check | Result |
| --- | --- |
| Baseline import of current SQLite snapshot | Pass: 28 tables and 18,053 rows; per-table row counts matched. |
| qmark DB-API parameters and mapping rows | Pass (`SELECT ? AS answer`). |
| `PRAGMA table_info` compatibility probe | Pass. |
| Bound SQLite interval modifier | Pass: `datetime('now', ?)` becomes `CURRENT_TIMESTAMP + %s::interval`. |
| Queue `enqueue_action` → `approve_action` → `claim_next_action` | Pass after the optional-filter fix below. |

## Failure found and fixed in the compatibility boundary

`tools/l5_action_queue.py` used optional filters of the form
`(? IS NULL OR page_id=?)`. PostgreSQL cannot infer the type of the standalone
first parameter. Live failure was:

```
IndeterminateDatatype: could not determine data type of parameter $2
```

The adapter now translates standalone legacy null-test placeholders to
`%s::text IS NULL`, with a regression test in
`tests/test_postgres_db_adapter.py`. This unblocks both `claim_next_action`
and `peek_next_approved`.

## Remaining implementation risk

`PgConnection.close()` currently returns a connection after a read-only query
without explicitly rolling it back. `psycopg_pool` safely rolls it back, but
the live audit emitted `rolling back returned connection ... [INTRANS]`.
Patch the compatibility boundary to rollback before `putconn()` (while
preserving the explicit commit paths) before enabling long-running production
workers, to prevent noisy logs and avoid retaining an idle transaction until
pool reset.

SQLite-only schema maintenance functions in
`fb_pipeline/persistence/l4_sqlite_store.py` remain intentionally outside the
PostgreSQL startup path. `db.connect()` does not invoke them for a PostgreSQL
URL. Do not call `setup_database`, `setup_comment_database`, or their
SQLite-catalog inspection helpers directly after cutover; schema ownership is
the migration artifact.
