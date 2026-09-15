# code:test-web-009:seekers-ui-improvements
"""
Unit tests for Seeker UI Improvements:
1. Last Message sorting uses actual datetime (not display text) and displays sort indicators.
2. Recent Messages in seeker short detail pane is a bounded scrollable container.
3. Duplicated summary information (phone/email/stage/stars/intake/first-last message) is removed from short preview,
   while full information is preserved on /seekers/:id.
4. Short preview replaces overly detailed 7-stage grid display with compact journey-map/status treatment.
"""
import re
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent

def test_seekers_table_last_message_datetime_sorting():
    table_file = ROOT_DIR / "web" / "src" / "components" / "seekers-table.tsx"
    assert table_file.exists(), "seekers-table.tsx must exist"
    content = table_file.read_text(encoding="utf-8")

    # 1. Header click sorts by lastMessageDate
    assert "handleSort('lastMessageDate')" in content, "Table header must trigger sort on lastMessageDate"
    assert "sortField === 'lastMessageDate'" in content, "Header must show sort direction indicator"
    assert "Last Message" in content

    # 2. Sorting comparator compares actual numeric timestamps
    assert "getSeekerTime" in content or "parseRealDate" in content
    assert "timeA - timeB" in content or "timeB - timeA" in content

    # 3. Default sort field is lastMessageDate
    assert "useState<SortField>('lastMessageDate')" in content or "useState('lastMessageDate')" in content

def test_seekers_table_recent_messages_bounded_scrollable():
    table_file = ROOT_DIR / "web" / "src" / "components" / "seekers-table.tsx"
    content = table_file.read_text(encoding="utf-8")

    # Bounded scrollable area for Recent Messages
    assert "sidebar-recent-messages" in content or "maxHeight" in content
    assert "overflowY: 'auto'" in content or 'overflowY: "auto"' in content
    # Ensures max-height constraint is present on recent messages container
    assert re.search(r"maxHeight:\s*'2[0-9]{2}px'", content) is not None, "Recent messages must have bounded maxHeight"

def test_seekers_table_removes_duplicated_summary_info():
    table_file = ROOT_DIR / "web" / "src" / "components" / "seekers-table.tsx"
    content = table_file.read_text(encoding="utf-8")

    # The short preview sidebar must NOT contain the duplicated Profile info grid
    # which previously had Phone, Email, Stage, SevenStarProgress, and First / Last Msg
    assert "{/* Profile info */}" not in content, "Profile info grid comment must be removed from short preview"
    # Ensure First / Last Msg block was removed from sidebar
    assert "First / Last Msg" not in content, "Duplicated First / Last Msg must not exist in short preview"

def test_short_preview_uses_compact_journey_treatment():
    table_file = ROOT_DIR / "web" / "src" / "components" / "seekers-table.tsx"
    content = table_file.read_text(encoding="utf-8")

    # Sidebar must pass compact={true} to SeekerJourneyTimeline
    assert "<SeekerJourneyTimeline" in content
    assert "compact={true}" in content, "SeekerJourneyTimeline in short preview must use compact={true}"

def test_seeker_journey_timeline_compact_vs_full_contract():
    timeline_file = ROOT_DIR / "web" / "src" / "components" / "seeker-journey-timeline.tsx"
    assert timeline_file.exists(), "seeker-journey-timeline.tsx must exist"
    content = timeline_file.read_text(encoding="utf-8")

    # Prop interface must include compact?: boolean
    assert "compact?:" in content or "compact?: boolean" in content
    assert "compact = false" in content or "compact?: boolean" in content

    # When compact, grid cards are omitted and compact status description is rendered
    assert "compact ?" in content or "{compact ? (" in content or "compact &&" in content
    assert "seeker-timeline-grid" in content, "Full 7-stage grid must still be available for non-compact mode"

def test_seeker_detail_preserves_full_information():
    detail_file = ROOT_DIR / "web" / "src" / "components" / "seeker-detail.tsx"
    assert detail_file.exists(), "seeker-detail.tsx must exist"
    content = detail_file.read_text(encoding="utf-8")

    # /seekers/:id must maintain phone, email, stage, SevenStarProgress, First Seen, Last Active
    assert "Phone" in content
    assert "Email" in content
    assert "Stage" in content
    assert "SevenStarProgress" in content
    assert "First Seen" in content
    assert "Last Active" in content

    # Uses full SeekerJourneyTimeline (not compact)
    assert "<SeekerJourneyTimeline seeker={seeker} />" in content

def test_funnel_filters_exports_parse_real_date():
    filters_file = ROOT_DIR / "web" / "src" / "lib" / "funnel-filters.ts"
    assert filters_file.exists(), "funnel-filters.ts must exist"
    content = filters_file.read_text(encoding="utf-8")

    assert "export function parseRealDate" in content
    assert "isDateInRange" in content
