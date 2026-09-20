# code:test-web-008:journey-transition-rules
"""
Unit test for Journey Transition Rules navigation and filter contract:
- journey-transition-rules.tsx uses Next.js Link (not plain <a> tags) for client-side navigation
- Generates valid /seekers?journeyStage= links with proper encoding
- Retains journey-stage-link and journey-rule-link classes and a11y labels
- Ensures city & date-range persistence through FunnelFilterBar on /seekers
"""
import os
import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

def test_journey_transition_rules_uses_next_link():
    rules_file = ROOT_DIR / "web" / "src" / "components" / "journey-transition-rules.tsx"
    assert rules_file.exists(), "journey-transition-rules.tsx must exist"
    
    content = rules_file.read_text(encoding="utf-8")
    
    # Must import Link from 'next/link'
    assert "import Link from 'next/link';" in content or 'import Link from "next/link";' in content, (
        "journey-transition-rules.tsx must import Link from 'next/link' for client-side routing"
    )
    
    # Must NOT have raw <a href for navigation links
    assert "<a\n" not in content and "<a " not in content, (
        "Must use <Link> instead of plain <a> tags to prevent hard document reloads in embedded browser"
    )
    
    # Must use <Link with /seekers?journeyStage=
    assert "<Link" in content
    assert 'href={`/seekers?journeyStage=${encodeURIComponent(t.toStage)}`}' in content
    assert 'className="journey-stage-link"' in content
    assert 'className="journey-rule-link"' in content
    assert "Xem seekers ở giai đoạn {t.toStage} →" in content

def test_seekers_table_journey_stage_and_filters_coexistence():
    table_file = ROOT_DIR / "web" / "src" / "components" / "seekers-table.tsx"
    assert table_file.exists(), "seekers-table.tsx must exist"
    
    content = table_file.read_text(encoding="utf-8")
    
    # Verifies journeyStage param extraction from searchParams
    assert "searchParams.get('journeyStage')" in content
    
    # Verifies FunnelFilterBar is present to preserve city & dateRange from localStorage
    assert "<FunnelFilterBar" in content
    assert "onFilterChange={setFilterState}" in content
    
    # Verifies filtering applies city, date range, and journeyStage together
    assert "filterState.city" in content
    assert "isDateInRange" in content
    assert "journeyStage" in content

def test_journey_transitions_engine_definitions():
    engine_file = ROOT_DIR / "web" / "src" / "lib" / "journey-engine.ts"
    assert engine_file.exists(), "journey-engine.ts must exist"
    
    content = engine_file.read_text(encoding="utf-8")
    assert "export const JOURNEY_TRANSITIONS: JourneyTransition[]" in content
    assert "fromStage" in content
    assert "toStage" in content
    assert "export function normalizeJourneyStage" in content

def test_journey_redirects_to_stats_and_flow_normalizes_stages():
    page_file = ROOT_DIR / "web" / "src" / "app" / "journey" / "page.tsx"
    flow_file = ROOT_DIR / "web" / "src" / "components" / "journey-flow.tsx"
    
    page_content = page_file.read_text(encoding="utf-8")
    flow_content = flow_file.read_text(encoding="utf-8")
    
    assert "redirect('/stats#journey')" in page_content
    assert "normalizeJourneyStage" in flow_content, "JourneyFlow must normalize leadStage when aggregating stage counts"


def test_journey_flow_applies_every_shared_funnel_filter():
    flow_file = ROOT_DIR / "web" / "src" / "components" / "journey-flow.tsx"
    content = flow_file.read_text(encoding="utf-8")

    # Journey uses the persisted FunnelFilterBar state, so every dimension must
    # constrain its node counts—not just city and date range.
    assert "filterState.programCode !== 'all' && s.programCode !== filterState.programCode" in content
    assert "availablePrograms={PROGRAMS}" in content
