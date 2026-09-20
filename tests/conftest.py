"""Test-wide database isolation.

The developer .env may legitimately select the shared PostgreSQL runtime. Unit
tests retain their historical isolated SQLite fixtures unless a test explicitly
opts into a dedicated TEST_POSTGRES_URL.
"""

import pytest


@pytest.fixture(autouse=True)
def _default_to_sqlite_for_tests(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "")
    # Unit tests intentionally exercise SQLite fixtures. Production processes
    # set this guard after the PostgreSQL cutover to reject such a fallback.
    monkeypatch.setenv("FUNNEL_REQUIRE_POSTGRES", "0")
    monkeypatch.setenv("FUNNEL_SQLITE_DIR", str(tmp_path / "sqlite"))
