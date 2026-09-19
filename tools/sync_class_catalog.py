#!/usr/bin/env python3
"""Mirror the configured Google Sheet class catalog into ``lop-hoc.md``.

The Sheet URL is deliberately read from the local-only ``.env`` through
``tools.env_manager``; do not put it in source control or command history.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.env_manager import load_credentials


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "memory" / "agent_memory" / "lop-hoc.md"
CANONICAL_FIELDS = (
    ("Thành phố", "city"),
    ("Thông tin chung", "general_info"),
    ("Khai Giảng", "start_date_text"),
    ("Thời gian học", "schedule_text"),
    ("Online/Offline", "delivery_mode"),
    ("Địa chỉ", "address"),
    ("Liên hệ/người Lead", "contact_leads"),
    ("Link nhóm Zalo", "zalo_group_url"),
    ("Thông tin khác", "notes"),
    ("Link Ads", "ads_url"),
)


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _header_key(value: str) -> str:
    return _normalise(value).casefold()


def _export_url(sheet_url: str) -> str:
    parsed = urllib.parse.urlparse(sheet_url)
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", parsed.path)
    if not match:
        raise ValueError("CLASS_CATALOG_SHEET_URL không phải Google Sheet hợp lệ")
    query = urllib.parse.parse_qs(parsed.query)
    fragment = urllib.parse.parse_qs(parsed.fragment)
    gid = (query.get("gid") or fragment.get("gid") or ["0"])[0]
    return f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=csv&gid={urllib.parse.quote(gid)}"


def fetch_rows(sheet_url: str) -> list[dict[str, str]]:
    request = urllib.request.Request(_export_url(sheet_url), headers={"User-Agent": "SYVN-class-catalog-sync/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read().decode("utf-8-sig")
    matrix = [[_normalise(cell) for cell in row] for row in csv.reader(io.StringIO(payload))]
    if not matrix:
        raise ValueError("Worksheet không có hàng tiêu đề")
    expected = {_header_key(label) for label, _ in CANONICAL_FIELDS}
    # The first row is a sheet title; the following row is the real header.
    # Accept a conventional CSV too by falling back to the first row.
    header_index = next(
        (i for i, row in enumerate(matrix) if len(expected.intersection(_header_key(cell) for cell in row)) >= 2),
        0,
    )
    headers = matrix[header_index]
    return [
        {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers)) if headers[i]}
        for row in matrix[header_index + 1:]
        if any(row)
    ]


def render_catalog(rows: list[dict[str, str]], synced_at: str) -> str:
    if not rows:
        raise ValueError("Worksheet không có lớp nào; giữ nguyên lop-hoc.md")
    checksum = hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()[:12]
    lines = [
        "# Thông tin các Lớp Học (Courses)",
        "",
        "> Tệp này được tạo tự động từ Google Sheet danh mục lớp học. Không sửa trực tiếp; chạy `python tools/sync_class_catalog.py` để đồng bộ lại.",
        "",
        f"_Đồng bộ lúc: {synced_at} · {len(rows)} lớp · snapshot: `{checksum}`_",
    ]
    for index, row in enumerate(rows, start=1):
        values = {_header_key(key): value for key, value in row.items()}
        def get(label: str) -> str:
            return values.get(_header_key(label), "")
        city = get("Thành phố") or "Chưa phân loại"
        schedule = get("Thời gian học")
        mode = get("Online/Offline")
        heading_bits = [city]
        if schedule:
            heading_bits.append(schedule)
        elif mode:
            heading_bits.append(mode)
        lines.extend(["", f"## {' — '.join(heading_bits)}", ""])
        display_labels = {"zalo_group_url": "Nhóm Zalo"}
        for sheet_label, markdown_label in CANONICAL_FIELDS:
            value = get(sheet_label)
            if value:
                lines.append(f"**{display_labels.get(markdown_label, sheet_label)}**: {value}")
        extras = [(key, value) for key, value in row.items() if _header_key(key) not in {_header_key(label) for label, _ in CANONICAL_FIELDS} and value]
        for key, value in extras:
            lines.append(f"**{key}**: {value}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Đồng bộ Google Sheet danh mục lớp vào lop-hoc.md")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ kiểm tra dữ liệu, không ghi tệp")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Đường dẫn Markdown đầu ra")
    parser.add_argument("--sheet-url", help="Ghi đè URL Sheet đã cấu hình cho lần chạy này")
    args = parser.parse_args()
    sheet_url = (args.sheet_url or load_credentials().get("CLASS_CATALOG_SHEET_URL", "")).strip()
    if not sheet_url:
        raise ValueError("Thiếu CLASS_CATALOG_SHEET_URL trong .env")
    rows = fetch_rows(sheet_url)
    content = render_catalog(rows, datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"))
    if args.dry_run:
        print(f"Đã đọc hợp lệ {len(rows)} lớp; sẽ ghi {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=args.output.parent, delete=False) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    temp_path.replace(args.output)
    print(f"Đã đồng bộ {len(rows)} lớp vào {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, urllib.error.URLError) as error:
        print(f"Đồng bộ thất bại: {error}", file=sys.stderr)
        raise SystemExit(1)
