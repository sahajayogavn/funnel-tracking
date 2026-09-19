# code:test-web-006:funnel-filters-python-test
"""
Unit test for AGY worker C scope:
- /queues action buttons contract
- /graph filter radio ranges (1d, 3d, 7d, 14d, 30d, 60d, 90d, all)
- No manual date input
- City & date range persistence
"""
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

def test_funnel_filters_contract():
    funnel_filters_file = ROOT_DIR / "web" / "src" / "lib" / "funnel-filters.ts"
    content = funnel_filters_file.read_text(encoding="utf-8")
    
    assert "['1d', '3d', '7d', '14d', '30d', '60d', '90d', 'all']" in content
    assert "export const FUNNEL_STORAGE_KEY = 'sahaja_funnel_filters'" in content
    assert "getStoredFilters" in content
    assert "saveStoredFilters" in content

def test_funnel_filter_bar_contract():
    filter_bar_file = ROOT_DIR / "web" / "src" / "components" / "funnel-filter-bar.tsx"
    content = filter_bar_file.read_text(encoding="utf-8")
    
    # Must NOT have manual date inputs
    assert '<input type="date"' not in content
    assert 'type="date"' not in content
    assert '<input' not in content
    
    # Must have radio role and aria attributes
    assert 'role="radiogroup"' in content
    assert 'role="radio"' in content
    assert 'aria-checked={isActive}' in content
    assert 'saveStoredFilters' in content

def test_action_queues_contract():
    action_queues_file = ROOT_DIR / "web" / "src" / "components" / "action-queues.tsx"
    content = action_queues_file.read_text(encoding="utf-8")
    
    # Verify 5 buttons with modifier classes
    assert "recommendation-action--all" in content
    assert "recommendation-action--reply" in content
    assert "recommendation-action--comment" in content
    assert "recommendation-action--warmup" in content
    assert "recommendation-action--event" in content

def test_globals_css_contract():
    css_file = ROOT_DIR / "web" / "src" / "app" / "globals.css"
    content = css_file.read_text(encoding="utf-8")
    
    assert ".recommendation-actions {" in content
    assert ".recommendation-action {" in content
    assert "min-width: max-content;" in content
    assert "@media (max-width: 860px)" in content
    assert "@media (max-width: 520px)" in content
    assert ".funnel-range-pills {" in content
    assert ".queue-grid--with-sidebar {\n  grid-template-columns: 1fr;\n}" in content

def test_network_graph_contract():
    graph_file = ROOT_DIR / "web" / "src" / "components" / "network-graph.tsx"
    content = graph_file.read_text(encoding="utf-8")
    
    assert "<FunnelFilterBar" in content
    assert "<input" not in content
    assert "containerRef" in content

def test_action_queues_seeker_sidebar_contract():
    action_queues_file = ROOT_DIR / "web" / "src" / "components" / "action-queues.tsx"
    content = action_queues_file.read_text(encoding="utf-8")

    # Verify interactive username and sidebar components
    assert "queue-item-username" in content
    assert "queue-seeker-sidebar" in content
    assert "Lý do trong hàng đợi (MAS Proof)" in content
    assert "getMasReasonDetails" in content
    assert "handleTogglePin" in content
    assert "handleMouseEnter" in content
    assert "handleMouseLeave" in content
    assert "SeekerJourneyTimeline" in content


def test_action_queue_seeker_links_preserve_meta_thread_ids():
    action_queues_file = ROOT_DIR / "web" / "src" / "components" / "action-queues.tsx"
    content = action_queues_file.read_text(encoding="utf-8")

    # Thread IDs can exceed JavaScript's safe integer range, so links must use
    # the original targetId string rather than a number converted from it.
    assert "function queueSeekerDetailUrl(item: ActionQueueItem)" in content
    assert "const targetId = item.targetId || item.targetName || '';" in content
    assert "href={queueSeekerDetailUrl(item)}" in content


def test_action_queue_delete_actions_have_visible_labels():
    action_queues_file = ROOT_DIR / "web" / "src" / "components" / "action-queues.tsx"
    content = action_queues_file.read_text(encoding="utf-8")

    assert "Xóa đã chọn" in content
    assert "<span>Xóa</span>" in content

def test_queries_payload_and_seeker_lookup_contract():
    queries_file = ROOT_DIR / "web" / "src" / "lib" / "queries.ts"
    content = queries_file.read_text(encoding="utf-8")

    assert "payloadJson" in content
    assert "payload_json AS payloadJson" in content
    assert "u.thread_id = ?" in content
    assert "u.thread_name = ?" in content


def test_recommendations_refuse_template_fallback_for_care_paths():
    route_file = ROOT_DIR / "web" / "src" / "app" / "api" / "action-queue" / "recommendations" / "route.ts"
    content = route_file.read_text(encoding="utf-8")

    assert "!['all', 'warmup', 'event', 'care'].includes(request.type)" in content
    assert "Không tạo template outbound cho Care" in content


def test_regenerate_preserves_proactive_purpose_and_scope():
    action_queues_file = ROOT_DIR / "web" / "src" / "components" / "action-queues.tsx"
    content = action_queues_file.read_text(encoding="utf-8")

    assert "carePurpose?: 'class_reminder' | 'warmup' | 'event'" in content
    assert "handleRunRecommendations('care'" in content
    assert "payload.session?.program_code" in content
    assert "payload.event_id" in content


def test_message_history_ui_does_not_reassign_sender_from_prose():
    queries_file = ROOT_DIR / "web" / "src" / "lib" / "queries.ts"
    content = queries_file.read_text(encoding="utf-8")

    assert "Sender ownership is decided at ingestion" in content
    assert "text.includes('hoàn toàn miễn phí')" not in content
    assert "text.includes('bạn ạ')" not in content


def test_message_history_ui_prefers_resolved_event_time_and_renders_quote_as_evidence():
    component_file = ROOT_DIR / "web" / "src" / "components" / "messenger-message-list.tsx"
    content = component_file.read_text(encoding="utf-8")

    assert "messengerDateLabel(message.eventAt) || messengerDateLabel(message.timestamp)" in content
    assert "Quoted reply evidence" in content
    assert "quotedText?: string | null" in content
