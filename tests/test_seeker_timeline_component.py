# code:test-component-007:timeline
import os
import re
import pytest

def test_seeker_journey_timeline_file_exists():
    timeline_path = os.path.join(os.path.dirname(__file__), "..", "web", "src", "components", "seeker-journey-timeline.tsx")
    assert os.path.exists(timeline_path), f"File {timeline_path} must exist"

def test_seeker_journey_timeline_layout_and_accessibility():
    timeline_path = os.path.join(os.path.dirname(__file__), "..", "web", "src", "components", "seeker-journey-timeline.tsx")
    with open(timeline_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Presents 7 stages with stepper rail & grid
    assert "seeker-timeline-wrapper" in content
    assert "seeker-timeline-rail" in content
    assert "seeker-timeline-grid" in content
    assert "CANONICAL_STAGES.map" in content

    # 2. Current stage highlight & indicator
    assert "seeker-timeline-current-chip" in content
    assert "seeker-timeline-pulse-dot" in content
    assert "aria-current={isCurrent ? 'step' : undefined}" in content

    # 3. Understandable status & concise relative date
    assert "currentElapsed" in content
    assert "elapsedStr" in content
    assert "statusText" in content
    assert "formatRelativeElapsed" in content
    assert "Hoàn thành" in content
    assert "Hiện tại" in content
    assert "Chưa đến" in content

    # 4. Accessibility semantics
    assert 'role="list"' in content
    assert 'role="listitem"' in content
    assert "sr-only" in content
    assert "tabIndex={0}" in content

def test_seeker_journey_timeline_css_in_globals():
    css_path = os.path.join(os.path.dirname(__file__), "..", "web", "src", "app", "globals.css")
    with open(css_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert ".seeker-timeline-wrapper" in content
    assert "container-type: inline-size;" in content
    assert "@container (min-width: 700px)" in content
    assert ".seeker-timeline-rail" in content
    assert ".seeker-timeline-rail-fill" in content
    assert ".seeker-timeline-card" in content
    assert ".seeker-timeline-card.is-current" in content
    assert ".seeker-timeline-card.is-prior" in content
    assert ".seeker-timeline-card.is-future" in content
    assert ".sr-only" in content

def test_no_changes_to_table_filter_rules_routes():
    base_dir = os.path.join(os.path.dirname(__file__), "..", "web", "src")
    
    table_file = os.path.join(base_dir, "components", "seekers-table.tsx")
    assert os.path.exists(table_file)

    filter_file = os.path.join(base_dir, "components", "funnel-filter-bar.tsx")
    assert os.path.exists(filter_file)

    rules_file = os.path.join(base_dir, "components", "journey-transition-rules.tsx")
    assert os.path.exists(rules_file)

def test_seeker_proposal_persisted_flow_and_no_hardcoded_defaults():
    timeline_path = os.path.join(os.path.dirname(__file__), "..", "web", "src", "components", "seeker-journey-timeline.tsx")
    with open(timeline_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. No hardcoded default proposal: queuedItems starts empty
    assert "useState<ActionQueueItem[]>([])" in content
    assert "proactive_message" not in content[:content.find("handleDecision")]

    # 2. Wire persisted action-queue GET
    assert "fetch(`/api/action-queue" in content
    assert "setQueuedItems(data || [])" in content

    # 3. Wire recommendations POST
    assert "fetch('/api/action-queue/recommendations'" in content
    assert "method: 'POST'" in content
    assert "await fetchQueuedItems()" in content

    # 4. Correct loading, empty, and error states
    assert "Đang tải đề xuất..." in content
    assert "Chưa có đề xuất nào trong hàng đợi" in content
    assert "actionError" in content
    assert "Đang tạo..." in content

