#!/usr/bin/env python3
"""
Sync upcoming events from `memory/agent_memory/su-kien.md` into the `events` table.
code:events-import-001

The EventPipeline reads `events`; nothing populated it, so the route never ran.
Each "## … Sự kiện sắp diễn ra …" section becomes one row per listed city.
Sections whose date is already in the past are skipped. Idempotent on
(name, city, event_date).

Usage:
    .venv/bin/python tools/l5_events_sync.py            # dry-run
    .venv/bin/python tools/l5_events_sync.py --apply
"""
import argparse
import os
import re
import sys
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection, setup_database

SU_KIEN_PATH = os.path.join(PROJECT_ROOT, "memory", "agent_memory", "su-kien.md")


def _field(body: str, name: str) -> str:
    match = re.search(rf"\*\*{re.escape(name)}\*\*:\s*(.+)", body)
    return match.group(1).strip() if match else ""


def _event_date(text: str) -> str | None:
    """First dd/mm/yyyy → ISO; else 'Tháng M/YYYY' → first day of month."""
    match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if match:
        d, m, y = match.groups()
        try:
            return date(int(y), int(m), int(d)).isoformat()
        except ValueError:
            pass
    match = re.search(r"[Tt]háng\s+(\d{1,2})/(\d{4})", text)
    if match:
        m, y = match.groups()
        return date(int(y), int(m), 1).isoformat()
    return None


def parse_upcoming_events(markdown: str, today: date | None = None) -> list[dict]:
    today = today or date.today()
    events = []
    for chunk in re.split(r"^## ", markdown, flags=re.MULTILINE)[1:]:
        heading, _, body = chunk.partition("\n")
        if "sắp diễn ra" not in heading.lower():
            continue
        event_date = _event_date(f"{heading}\n{body}")
        if not event_date or date.fromisoformat(event_date) < today.replace(day=1):
            continue
        name = re.sub(r"^[^\w]*Sự kiện sắp diễn ra\s*[—-]\s*", "", heading).strip() or heading.strip()
        cities = [c.strip() for c in re.split(r"[,/]", _field(body, "Khu vực")) if c.strip()] or ["Unknown"]
        description = " ".join(line.strip("- ").strip() for line in body.splitlines() if line.strip())[:1000]
        for city in cities:
            events.append({"name": name, "city": city, "event_date": event_date, "description": description})
    return events


def sync(apply: bool) -> dict:
    with open(SU_KIEN_PATH, "r", encoding="utf-8") as handle:
        events = parse_upcoming_events(handle.read())
    inserted = 0
    if apply:
        conn = get_db_connection()
        setup_database(conn)
        for ev in events:
            exists = conn.execute(
                "SELECT 1 FROM events WHERE name=? AND city=? AND event_date=?",
                (ev["name"], ev["city"], ev["event_date"]),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO events (name, city, event_date, description) VALUES (?, ?, ?, ?)",
                (ev["name"], ev["city"], ev["event_date"], ev["description"]),
            )
            inserted += 1
        conn.commit()
        conn.close()
    return {"parsed": len(events), "inserted": inserted, "applied": apply, "events": events}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = sync(apply=args.apply)
    print({k: v for k, v in result.items() if k != "events"})
    for ev in result["events"]:
        print(f"  {ev['event_date']}  {ev['city']:<14} {ev['name'][:60]}")


if __name__ == "__main__":
    main()
