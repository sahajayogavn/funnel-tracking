"""
Fixture loader utility for E2E pipeline tests.
code:test-fixtures-001

Loads JSON fixture files and seeds in-memory SQLite databases
for repeatable, isolated tests.
"""
import json
import os
import sqlite3

FIXTURES_DIR = os.path.dirname(os.path.abspath(__file__))


def load_fixture(name: str) -> dict:
    """Load a JSON fixture by name (without .json extension)."""
    path = os.path.join(FIXTURES_DIR, f"{name}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def create_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with full schema (inbox + comments)."""
    import sys
    project_root = os.path.dirname(os.path.dirname(FIXTURES_DIR))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from fb_pipeline.persistence.l4_sqlite_store import setup_database

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    setup_database(conn)
    return conn


def seed_fixture_into_db(conn: sqlite3.Connection, fixture: dict) -> dict:
    """Seed a fixture's data into the test DB using L3 pipeline functions.

    Returns the persist_thread_record result dict.
    """
    import sys
    project_root = os.path.dirname(os.path.dirname(FIXTURES_DIR))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from fb_pipeline.contracts.l1_inbox import detect_city, extract_user_info
    from fb_pipeline.inbox.l3_pipeline import (
        build_thread_record,
        enrich_thread_record,
        persist_thread_record,
    )

    meta = fixture["metadata"]
    thread_record = build_thread_record(meta["page_id"], fixture["visible_thread"])

    enriched = enrich_thread_record(
        thread_record,
        fixture["js_messages"],
        extract_user_info=extract_user_info,
        detect_city=detect_city,
        ad_context=fixture.get("ad_context", ""),
        fb_url=meta.get("fb_url", ""),
        ad_ids=fixture.get("ad_ids", []),
    )

    result = persist_thread_record(conn, enriched, detect_city=detect_city)
    return result
