"""Database compatibility boundary for the staged SQLite -> PostgreSQL cutover.

``DATABASE_URL`` is deliberately opt-in: absent (or with a ``sqlite`` URL),
callers keep using the local FrankenSQLite file.  A ``postgresql://`` URL uses
the synchronous psycopg 3 pool, while presenting the small DB-API surface the
legacy raw-SQL callers rely on (execute/cursor/commit/rollback/close and rows
addressable both by index and column name).

This module is a bridge, not a claim that arbitrary SQLite SQL is PostgreSQL
SQL.  It covers the common legacy constructs so call sites can be migrated in
small, independently testable batches.

Universal ID: code:postgres-cutover-001:python-db-boundary
"""

from __future__ import annotations

import atexit
import os
import re
import sqlite3
import threading
import json
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv


POSTGRES_SCHEMES = ("postgres://", "postgresql://")
_pool_lock = threading.Lock()
_pool: Any | None = None
_pool_url: str | None = None


def close_postgres_pool() -> None:
    """Release pool worker threads when a short-lived worker exits."""
    global _pool, _pool_url
    with _pool_lock:
        if _pool is not None:
            _pool.close(timeout=5)
            _pool = None
            _pool_url = None


atexit.register(close_postgres_pool)


def database_url() -> str | None:
    """Return the configured URL, loading this project's non-overriding .env.

    Existing worker scripts invoke Python directly rather than sourcing .env.
    This keeps an explicitly exported process value authoritative while making
    the configured cutover URL available to every legacy connection factory.
    """
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    url = os.environ.get("DATABASE_URL", "").strip() or None
    is_postgres_url = bool(url and url.lower().startswith(POSTGRES_SCHEMES))
    if os.environ.get("FUNNEL_REQUIRE_POSTGRES", "").lower() in {"1", "true", "yes", "on"} and not is_postgres_url:
        raise RuntimeError(
            "PostgreSQL cutover is enforced: DATABASE_URL must be a postgresql:// URL. "
            "Refusing unsafe SQLite fallback."
        )
    return url


def using_postgres(url: str | None = None) -> bool:
    """True only for the explicitly supported PostgreSQL URL schemes."""
    return (url if url is not None else database_url() or "").lower().startswith(POSTGRES_SCHEMES)


class PgRow(Sequence[Any], Mapping[str, Any]):
    """Tuple-like and mapping-like row compatible with ``sqlite3.Row`` use."""

    def __init__(self, values: Sequence[Any], names: Sequence[str]):
        self._values = tuple(values)
        self._names = tuple(names)
        self._by_name = dict(zip(self._names, self._values))

    def __getitem__(self, key: int | slice | str) -> Any:
        if isinstance(key, str):
            return self._by_name[key]
        return self._values[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self):
        return self._names


def _pg_row_factory(cursor: Any) -> Callable[[Sequence[Any]], PgRow]:
    # Psycopg invokes row factories for command-only statements too, where
    # ``description`` is None. No row will be built in that case, but the
    # factory still must be constructible.
    names = tuple(column.name for column in (cursor.description or ()))
    def make_row(values: Sequence[Any]) -> PgRow:
        # Legacy callers consistently treat *_json SQLite TEXT columns as
        # strings and call json.loads themselves. Psycopg decodes JSONB to a
        # dict/list by default, so retain the established contract at this one
        # boundary instead of changing every consumer during cutover.
        normalized = tuple(
            json.dumps(value, ensure_ascii=False)
            if name.endswith("_json") and isinstance(value, (dict, list))
            else value.isoformat(sep=" ")
            if isinstance(value, datetime)
            else value.isoformat()
            if isinstance(value, date)
            else value
            for name, value in zip(names, values)
        )
        return PgRow(normalized, names)
    return make_row


def _replace_qmarks(sql: str) -> str:
    """Translate qmark parameters without touching SQL string literals."""
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(sql):
        char = sql[i]
        if quote:
            out.append(char)
            if char == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:  # SQL escaped quote
                    out.append(sql[i + 1])
                    i += 1
                else:
                    quote = None
        elif char in ("'", '"'):
            quote = char
            out.append(char)
        elif char == "?":
            out.append("%s")
        else:
            out.append(char)
        i += 1
    return "".join(out)


def translate_sql(sql: str) -> str:
    """Translate the safe, recurrent SQLite subset used by runtime callers.

    Complex DDL is intentionally not translated here: PostgreSQL schema is
    owned by the migration artifact and must not silently drift at startup.
    """
    translated = _replace_qmarks(sql)
    # PostgreSQL cannot infer a bound parameter used only as ``? IS NULL``.
    # Optional legacy filters use it with text identifiers (page/thread/etc.).
    translated = re.sub(r"%s\s+IS\s+NULL", "%s::text IS NULL", translated, flags=re.I)
    translated = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", translated, flags=re.I)
    if re.match(r"\s*INSERT\s+INTO\b", translated, flags=re.I) and re.search(r"\bON\s+CONFLICT\b", translated, flags=re.I) is None:
        # SQLite's INSERT OR IGNORE means ignore every uniqueness conflict.
        # This is appended only after a complete INSERT statement; current
        # callers do not combine it with RETURNING.
        if re.search(r"\bINSERT\s+OR\s+IGNORE\b", sql, flags=re.I):
            translated = translated.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    translated = _translate_datetime_calls(translated)
    translated = re.sub(r"\browid\b", "id", translated, flags=re.I)
    # JSON was migrated as JSONB.  Cast retains compatibility if a legacy DB
    # was provisioned with TEXT before the final DDL is applied.
    def json_extract(match: re.Match[str]) -> str:
        column, path = match.group(1), match.group(2).split(".")
        if len(path) == 1:
            return f"({column}::jsonb ->> '{path[0]}')"
        return f"({column}::jsonb #>> '{{{','.join(path)}}}')"

    translated = re.sub(
        r"json_extract\(\s*([\w.]+)\s*,\s*'\$\.([\w]+(?:\.[\w]+)*)'\s*\)",
        json_extract,
        translated,
        flags=re.I,
    )
    translated = re.sub(
        r"julianday\(\s*'now'\s*\)\s*-\s*julianday\(\s*([\w.]+)\s*\)",
        r"(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - \1::timestamp)) / 86400.0)",
        translated,
        flags=re.I,
    )
    return translated


def _translate_datetime_calls(sql: str) -> str:
    """Map SQLite datetime/date calls used by the runtime to PostgreSQL.

    SQLite permits an arbitrary list of textual modifiers. The application only
    uses ``localtime`` and signed hour/day windows; reject no input here, but
    leave unfamiliar calls visible to PostgreSQL rather than guessing semantics.
    """
    def replace(match: re.Match[str]) -> str:
        function, args = match.group(1).lower(), match.group(2)
        parts = [part.strip() for part in args.split(",")]
        if not parts:
            return match.group(0)
        source = parts[0]
        modifiers = [part.strip().strip("'") for part in parts[1:]]
        if source.strip("'").lower() == "now":
            value = "CURRENT_TIMESTAMP"
        elif modifiers:
            return match.group(0)
        else:
            value = f"{source}::timestamp"
        for modifier in modifiers:
            if modifier.lower() == "localtime":
                value = f"({value} AT TIME ZONE 'Asia/Ho_Chi_Minh')"
            elif modifier == "%s":
                # Runtime retention queries bind windows such as ``'-7 days'``
                # rather than spelling the interval in SQL.
                value = f"({value} + %s::interval)"
            elif re.fullmatch(r"[+-]\d+\s+(?:hours?|days?|minutes?|seconds?)", modifier, re.I):
                value = f"({value} + INTERVAL '{modifier}')"
            else:
                return match.group(0)
        return f"({value})::date" if function == "date" else value

    return re.sub(r"\b(datetime|date)\(\s*([^()]*)\s*\)", replace, sql, flags=re.I)


def _pragma_table_info(sql: str) -> str | None:
    match = re.match(r"\s*PRAGMA\s+table_info\s*\(\s*([A-Za-z_][\w]*)\s*\)\s*;?\s*$", sql, re.I)
    if not match:
        return None
    table = match.group(1)
    return (
        "SELECT ordinal_position - 1 AS cid, column_name AS name, data_type AS type, "
        "CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull, "
        "column_default AS dflt_value, 0 AS pk "
        "FROM information_schema.columns "
        f"WHERE table_schema = current_schema() AND table_name = '{table}' "
        "ORDER BY ordinal_position"
    )


def _translate_metadata_sql(sql: str) -> str:
    pragma = _pragma_table_info(sql)
    if pragma:
        return pragma
    # A small number of feature probes use SQLite's catalog. Keep these probes
    # working rather than making every caller branch on the backend.
    if re.search(r"FROM\s+sqlite_master", sql, flags=re.I):
        return re.sub(
            r"SELECT\s+1\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'\s+AND\s+name\s*=\s*%s",
            "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = %s",
            _replace_qmarks(sql),
            flags=re.I,
        )
    return translate_sql(sql)


class PgCursor:
    def __init__(self, cursor: Any, connection: "PgConnection"):
        self._cursor = cursor
        self._connection = connection
        self._lastrowid: int | None = None
        self._returns_id = False

    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] | None = None):
        translated = _translate_metadata_sql(sql)
        # SQLite's BEGIN IMMEDIATE is a writer-lock acquisition. PostgreSQL's
        # MVCC has no equivalent database-wide lock; normal BEGIN paired with
        # conditional UPDATE/row locks is the safe portable transaction start.
        if re.match(r"\s*BEGIN\s+(?:IMMEDIATE|EXCLUSIVE)\s*;?\s*$", translated, re.I):
            translated = "BEGIN"
        # Legacy busy_timeout calls are harmless no-ops with PostgreSQL.
        if re.match(r"\s*PRAGMA\s+(busy_timeout|journal_mode)", sql, re.I):
            translated = "SELECT 1"
            params = None
        self._cursor.execute(translated, params)
        self._lastrowid = None
        self._returns_id = bool(re.search(r"\bRETURNING\s+id\b", translated, re.I))
        return self

    def executemany(self, sql: str, params_seq: Sequence[Sequence[Any]]):
        self._cursor.executemany(_translate_metadata_sql(sql), params_seq)
        self._lastrowid = None
        self._returns_id = False
        return self

    @property
    def lastrowid(self) -> int | None:
        # PostgreSQL LASTVAL() is session-global and can return an unrelated
        # sequence value after ON CONFLICT DO NOTHING.  Only an explicit
        # RETURNING id is safe in a pooled connection.
        if self._lastrowid is None and self._returns_id:
            value = self._cursor.fetchone()
            self._lastrowid = int(value[0]) if value else None
        return self._lastrowid

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, *args: Any):
        return self._cursor.__exit__(*args)


class PgConnection:
    """Synchronous pooled connection with the SQLite callers' expected API."""

    def __init__(self, connection: Any, pool: Any):
        self._connection = connection
        self._pool = pool
        self._closed = False

    def cursor(self, *args: Any, **kwargs: Any) -> PgCursor:
        kwargs.setdefault("row_factory", _pg_row_factory)
        return PgCursor(self._connection.cursor(*args, **kwargs), self)

    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] | None = None) -> PgCursor:
        cursor = self.cursor()
        return cursor.execute(sql, params)

    def executemany(self, sql: str, params_seq: Sequence[Sequence[Any]]) -> PgCursor:
        cursor = self.cursor()
        return cursor.executemany(sql, params_seq)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        if not self._closed:
            try:
                # Most legacy callers explicitly commit, but a read-only
                # SELECT still opens a PostgreSQL transaction. Never hand an
                # INTRANS/INERROR connection to the pool: the next caller must
                # start with a clean transaction boundary.
                self._connection.rollback()
            finally:
                self._pool.putconn(self._connection)
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            self.rollback() if exc_type else self.commit()
        finally:
            self.close()


def _get_postgres_pool(url: str) -> Any:
    global _pool, _pool_url
    with _pool_lock:
        if _pool is not None and _pool_url == url:
            return _pool
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "PostgreSQL selected by DATABASE_URL but psycopg_pool is not installed. "
                "Install the project's postgres extra: pip install '.[postgres]'."
            ) from exc
        if _pool is not None:
            _pool.close()
        _pool = ConnectionPool(
            conninfo=url,
            min_size=1,
            max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "8")),
            # Keep legacy datetime('now') semantics deterministic even if the
            # container or host timezone changes.
            kwargs={"options": "-c timezone=UTC"},
            open=True,
        )
        _pool_url = url
        return _pool


def connect(
    memory_dir: str | None = None,
    logger: Any = None,
    *,
    comment_schema: bool = False,
    initialize: bool = True,
    sqlite_filename: str = "frankensqlite.db",
    sqlite_connect: Callable[..., sqlite3.Connection] = sqlite3.connect,
):
    """Open the configured DB. SQLite setup remains the safe default."""
    url = database_url()
    if using_postgres(url):
        pool = _get_postgres_pool(url or "")
        conn = PgConnection(pool.getconn(), pool)
        # PostgreSQL DDL belongs to db_migrations and is never executed by
        # application startup; this avoids recreating the old SQLite schema.
        if logger:
            logger.debug("Using PostgreSQL database connection%s", " (comment)" if comment_schema else "")
        return conn
    if memory_dir is None:
        # Tests must never silently mutate the retained production SQLite
        # snapshot.  The production default remains unchanged; conftest sets
        # this explicit directory to a per-test temporary path.
        memory_dir = os.environ.get("FUNNEL_SQLITE_DIR") or os.path.join("memory", "agent_memory")
    os.makedirs(memory_dir, exist_ok=True)
    db_path = os.path.join(memory_dir, sqlite_filename)
    conn = sqlite_connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.row_factory = sqlite3.Row
    # Avoid an import cycle: l4 owns SQLite-only DDL.
    if initialize:
        from fb_pipeline.persistence import l4_sqlite_store
        if comment_schema:
            l4_sqlite_store.setup_comment_database(conn)
        else:
            l4_sqlite_store.setup_database(conn, logger=logger)
    return conn
