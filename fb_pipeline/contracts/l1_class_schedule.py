"""
Fixed weekly class schedule derived from the program catalogue.
code:route-class-reminder-001:schedule

``PROGRAM_CODES`` (``l1_program_catalog``) is the value the city/program
classifier writes into ``users.program_code``; each code encodes
``<time>-<weekday tokens>-<place>-<city>`` (e.g. ``20h-T3-Hoàng Quốc Việt-HN``,
``21h-T3-T5-T7-Online-HN``).  This module turns those codes into concrete
sessions and enriches them with address / Zalo link from ``lop-hoc.md`` when a
matching section exists.  Pure functions; no DB.
"""
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

from fb_pipeline.contracts.l1_program_catalog import PROGRAM_CODES

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOP_HOC_PATH = os.path.join(PROJECT_ROOT, "memory", "agent_memory", "lop-hoc.md")

_WEEKDAY_TOKENS = {"T2": 0, "T3": 1, "T4": 2, "T5": 3, "T6": 4, "T7": 5, "CN": 6}
_WEEKDAY_VI = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
_CITY_NAMES = {"HN": "Hà Nội", "HCM": "TP. Hồ Chí Minh", "ĐN": "Đà Nẵng", "HA": "Hội An", "NA": "Nghệ An"}
_TIME_RE = re.compile(r"^(\d{1,2})h(\d{2})?$")


@dataclass
class ClassDefinition:
    program_code: str
    city: str
    city_code: str
    place: str
    hour: int
    minute: int
    weekdays: list[int]
    is_online: bool
    address: str = ""
    zalo_url: str = ""
    label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClassSession:
    class_key: str
    program_code: str
    session_date: str          # YYYY-MM-DD
    starts_at: str             # YYYY-MM-DD HH:MM:SS
    weekday_vi: str
    time_label: str            # 20h00
    city: str
    place: str
    is_online: bool
    address: str = ""
    zalo_url: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_program_code(code: str) -> ClassDefinition | None:
    parts = code.split("-")
    if len(parts) < 4:
        return None
    time_match = _TIME_RE.match(parts[0].strip())
    if not time_match:
        return None
    hour = int(time_match.group(1))
    minute = int(time_match.group(2) or 0)
    weekdays = []
    idx = 1
    while idx < len(parts) and parts[idx].strip() in _WEEKDAY_TOKENS:
        weekdays.append(_WEEKDAY_TOKENS[parts[idx].strip()])
        idx += 1
    city_code = parts[-1].strip()
    place = "-".join(parts[idx:-1]).strip()
    if not weekdays or not place:
        return None
    is_online = place.lower() == "online"
    return ClassDefinition(
        program_code=code, city=_CITY_NAMES.get(city_code, city_code), city_code=city_code,
        place=place, hour=hour, minute=minute, weekdays=weekdays, is_online=is_online,
        label=f"{parts[0]} {'/'.join(_WEEKDAY_VI[d] for d in weekdays)} — {place} ({_CITY_NAMES.get(city_code, city_code)})",
    )


def _load_lop_hoc_sections(path: str = LOP_HOC_PATH) -> list[dict]:
    """Split lop-hoc.md into ``{heading, body}`` sections; empty list if missing."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return []
    sections = []
    for chunk in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        heading, _, body = chunk.partition("\n")
        sections.append({"heading": heading.strip(), "body": body})
    return sections


def _field(body: str, name: str) -> str:
    match = re.search(rf"\*\*{re.escape(name)}\*\*:\s*(.+)", body)
    return match.group(1).strip() if match else ""


def _enrich_from_lop_hoc(definition: ClassDefinition, sections: list[dict]) -> ClassDefinition:
    place_key = definition.place.lower()
    time_key = f"{definition.hour}h{definition.minute:02d}" if definition.minute else f"{definition.hour}h"
    for section in sections:
        heading = section["heading"].lower()
        body = section["body"].lower()
        # time might be formatted as 14:30 in the heading
        time_colon = f"{definition.hour}:{definition.minute:02d}"
        time_match = time_key in heading or f"{definition.hour}h" in heading or time_colon in heading
        
        if (place_key in heading or place_key in body) and time_match:
            definition.address = _field(section["body"], "Địa chỉ") or _field(section["body"], "Link Zoom")
            definition.zalo_url = _field(section["body"], "Nhóm Zalo")
            break
    return definition


def load_class_definitions(codes: tuple[str, ...] = PROGRAM_CODES, lop_hoc_path: str = LOP_HOC_PATH) -> list[ClassDefinition]:
    sections = _load_lop_hoc_sections(lop_hoc_path)
    definitions = []
    for code in codes:
        definition = parse_program_code(code)
        if definition:
            definitions.append(_enrich_from_lop_hoc(definition, sections))
    return definitions


def upcoming_sessions(now: datetime, window_hours: int = 36,
                      definitions: list[ClassDefinition] | None = None) -> list[ClassSession]:
    """Sessions starting in ``(now, now + window_hours]``, soonest first."""
    definitions = definitions if definitions is not None else load_class_definitions()
    horizon = now + timedelta(hours=window_hours)
    sessions = []
    for definition in definitions:
        for day_offset in range(0, (window_hours // 24) + 2):
            day = (now + timedelta(days=day_offset)).date()
            if day.weekday() not in definition.weekdays:
                continue
            starts_at = datetime(day.year, day.month, day.day, definition.hour, definition.minute)
            if starts_at <= now or starts_at > horizon:
                continue
            sessions.append(_session(definition, starts_at))
    sessions.sort(key=lambda s: s.starts_at)
    return sessions


def recent_sessions(now: datetime, lookback_hours: int = 24,
                    definitions: list[ClassDefinition] | None = None) -> list[ClassSession]:
    """Sessions that started in ``[now - lookback_hours, now)``, most recent first."""
    definitions = definitions if definitions is not None else load_class_definitions()
    floor = now - timedelta(hours=lookback_hours)
    sessions = []
    for definition in definitions:
        for day_offset in range(0, (lookback_hours // 24) + 2):
            day = (now - timedelta(days=day_offset)).date()
            if day.weekday() not in definition.weekdays:
                continue
            starts_at = datetime(day.year, day.month, day.day, definition.hour, definition.minute)
            if starts_at < floor or starts_at >= now:
                continue
            sessions.append(_session(definition, starts_at))
    sessions.sort(key=lambda s: s.starts_at, reverse=True)
    return sessions


def _session(definition: ClassDefinition, starts_at: datetime) -> ClassSession:
    time_label = f"{definition.hour}h{definition.minute:02d}"
    return ClassSession(
        class_key=definition.program_code,
        program_code=definition.program_code,
        session_date=starts_at.strftime("%Y-%m-%d"),
        starts_at=starts_at.strftime("%Y-%m-%d %H:%M:%S"),
        weekday_vi=_WEEKDAY_VI[starts_at.weekday()],
        time_label=time_label,
        city=definition.city,
        place=definition.place,
        is_online=definition.is_online,
        address=definition.address,
        zalo_url=definition.zalo_url,
    )


def describe_session(session: ClassSession, now: datetime | None = None) -> str:
    """'Tối nay Thứ Ba 20h00 — Hoàng Quốc Việt (Hà Nội)' style label."""
    now = now or datetime.now()
    starts = datetime.strptime(session.starts_at, "%Y-%m-%d %H:%M:%S")
    delta_days = (starts.date() - now.date()).days
    when = {0: "Hôm nay", 1: "Ngày mai", -1: "Hôm qua"}.get(delta_days, starts.strftime("%d/%m"))
    where = "Online (Zoom)" if session.is_online else f"{session.place} ({session.city})"
    return f"{when} {session.weekday_vi} {session.time_label} — {where}"


__all__ = [
    "ClassDefinition", "ClassSession", "describe_session", "load_class_definitions",
    "parse_program_code", "recent_sessions", "upcoming_sessions",
]
