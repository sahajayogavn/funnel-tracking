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

def test_network_graph_contract():
    graph_file = ROOT_DIR / "web" / "src" / "components" / "network-graph.tsx"
    content = graph_file.read_text(encoding="utf-8")
    
    assert "<FunnelFilterBar" in content
    assert "<input" not in content
    assert "containerRef" in content
