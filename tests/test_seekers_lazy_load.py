# code:test-seekers-lazy-load-001
"""
Contract tests for Seekers page Phase 1 client-side lazy rendering
(code:web-page-002:seekers-lazy-load-001, code:web-lib-012:use-infinite-slice,
code:web-api-001:seekers-api:activity-batch).

Static checks follow the style of tests/test_seekers_ui_improvements.py; the
pure pagination helper is additionally executed with Node's type stripping
when a compatible Node (>= 22.6) is available.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"
TABLE_FILE = WEB_DIR / "src" / "components" / "seekers-table.tsx"
HOOK_FILE = WEB_DIR / "src" / "lib" / "use-infinite-slice.ts"
ROUTE_FILE = WEB_DIR / "src" / "app" / "api" / "seekers" / "route.ts"


@pytest.fixture(scope="module")
def table() -> str:
    return TABLE_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def hook() -> str:
    return HOOK_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def route() -> str:
    return ROUTE_FILE.read_text(encoding="utf-8")


# code:test-seekers-lazy-load-001:memo-sorted
def test_sorted_is_memoized_over_filter_inputs(table):
    assert "code:web-page-002:seekers-lazy-load-001" in table
    assert "const sorted = useMemo(() => [...seekers]" in table
    assert "[seekers, filterState, deferredSearch, journeyStage, sortField, sortDir]" in table
    # Exactly one filter/sort pipeline (the old per-render copy is gone).
    assert table.count("[...seekers]") == 1
    # Filter semantics preserved.
    for needle in (
        "isDateInRange(s.lastMessageDate || s.lastMessageTimestampText || s.lastInteraction || s.firstSeen, filterState.dateRange)",
        "getStageNumber(journeyStage)",
        "s.realName?.toLowerCase().includes(q)",
        "s.email?.toLowerCase().includes(q)",
        "getSeekerTime",
    ):
        assert needle in table, needle


# code:test-seekers-lazy-load-001:deferred-search
def test_search_is_deferred_but_input_stays_bound(table):
    assert "useDeferredValue(search)" in table
    assert "value={search}" in table
    assert "const q = deferredSearch.toLowerCase();" in table


# code:test-seekers-lazy-load-001:render-slice
def test_table_renders_only_visible_slice(table):
    assert "useInfiniteSlice(" in table
    assert "sorted.slice(0, visibleCount)" in table
    assert "visibleSeekers.map((seeker, idx) =>" in table
    assert "sorted.map((seeker, idx)" not in table
    assert "Đang hiển thị {visibleCount} / {sorted.length}" in table
    assert "Tải thêm" in table
    assert "onClick={loadMore}" in table


# code:test-seekers-lazy-load-001:sentinel
def test_intersection_observer_sentinel(table, hook):
    assert "ref={sentinelRef}" in table
    assert "ref={tableScrollRef}" in table
    assert "rootRef: tableScrollRef" in table
    assert "new IntersectionObserver(" in hook
    assert "observer.observe(sentinel)" in hook
    assert "observer.disconnect()" in hook
    assert "root: rootRef?.current ?? null" in hook
    assert "DEFAULT_PAGE_SIZE = 50" in hook
    assert "rootMargin: '400px 0px'" in table


# code:test-seekers-lazy-load-001:reset
def test_window_resets_on_filter_search_sort_change(table, hook):
    assert "JSON.stringify([filterState, deferredSearch, journeyStage, sortField, sortDir])" in table
    assert "resetKey: lazyResetKey" in table
    # Stale count is ignored when the key changes, and the list scrolls to top.
    assert "const syncedWindow = syncWindowState(windowState, resetKey, pageSize);" in hook
    assert "if (syncedWindow !== windowState) setWindowState(syncedWindow);" in hook
    assert "growWindowState(previous, resetKey, total, pageSize)" in hook
    assert re.search(r"scrollTo\(\{ top: 0 \}\);\s*\}, \[resetKey, rootRef\]\);", hook)


# code:test-seekers-lazy-load-001:full-list-features
def test_selection_counts_and_batch_use_full_sorted(table):
    assert "sorted.slice(start, end + 1)" in table
    assert "filteredCount={sorted.length}" in table
    assert "const selectedDmThreadIds = sorted" in table


# code:test-seekers-lazy-load-001:polling-visible
def test_classification_polling_limited_to_visible_rows(table):
    match = re.search(
        r"const refreshClassificationProgress = useCallback\(async \(\) => \{(.*?)\}, \[([^\]]*)\]\);",
        table,
        re.S,
    )
    assert match, "refreshClassificationProgress must exist"
    body, deps = match.group(1), match.group(2)
    # Ids come from the visible window only, via a ref keyed by a stable string.
    assert re.search(r"const visiblePendingThreadKey = useMemo\(\(\) => visibleSeekers", table)
    assert "visiblePendingThreadKeyRef.current.split(',')" in body
    # Existing merge behaviour retained.
    assert "initialClassificationByThread.get(classification.threadId)" in body


# code:test-seekers-lazy-load-001:polling-cadence
def test_classification_polling_cadence_is_stable(table):
    match = re.search(
        r"const refreshClassificationProgress = useCallback\(async \(\) => \{.*?\}, \[([^\]]*)\]\);",
        table,
        re.S,
    )
    assert match
    deps = match.group(1)
    # Load-more / classification updates change visibleSeekers; they must not
    # recreate the callback (which would restart the interval + extra poll).
    assert "visibleSeekers" not in deps
    assert "seekers" not in deps.replace("initialClassificationByThread", "")
    assert "}, [hasVisiblePendingClassifications, refreshClassificationProgress]);" in table
    assert "}, [pendingClassifications, refreshClassificationProgress]);" not in table


# code:test-seekers-lazy-load-001:activity-batch
def test_activity_fetch_is_batched_for_visible_rows(table, route):
    assert "initialSeekers.slice(0, 20)" not in table
    assert "action: 'activity-batch'" in table
    assert "visibleSeekers.map(seeker => seeker.name)" in table
    assert "Promise.all(chunks.map(" in table
    # Non-OK HTTP responses are retryable, not cached as empty activity.
    assert "if (!res.ok) throw new Error(" in table
    assert "names.forEach(name => requested.delete(name))" in table
    assert "ACTIVITY_BATCH_SIZE = 100" in table
    assert "code:web-api-001:seekers-api:activity-batch" in route
    assert "action === 'activity-batch'" in route
    assert "ACTIVITY_BATCH_MAX = 100" in route
    assert "Promise.all(" in route
    # JSON that is not an array of strings is rejected (400).
    assert "!Array.isArray(parsed) || !parsed.every(value => typeof value === 'string')" in route
    assert "if (/^[[{]/.test(raw.trim())) {" in route
    # Legacy single-name action kept.
    assert "action === 'activity' && name" in route


def _node_supports_strip_types() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return False
    m = re.match(r"v(\d+)\.(\d+)", out.strip())
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= (22, 6)


# code:test-seekers-lazy-load-001:pure-helper
@pytest.mark.skipif(not _node_supports_strip_types(), reason="Node >= 22.6 required for TS type stripping")
def test_next_visible_count_pure_helper_runtime():
    script = (
        f"import {{ nextVisibleCount, clampVisibleCount }} from {json.dumps(str(HOOK_FILE))};"
        "console.log(JSON.stringify(["
        "nextVisibleCount(50, 120, 50),"
        "nextVisibleCount(100, 120, 50),"
        "nextVisibleCount(120, 120, 50),"
        "nextVisibleCount(0, 0, 50),"
        "nextVisibleCount(10, 1000, 0),"
        "clampVisibleCount(50, 10),"
        "clampVisibleCount(50, 500),"
        "clampVisibleCount(50, -3)"
        "]));"
    )
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30, cwd=str(WEB_DIR),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip()) == [100, 120, 120, 0, 11, 10, 50, 0]


# code:test-seekers-lazy-load-001:window-a-b-a
@pytest.mark.skipif(not _node_supports_strip_types(), reason="Node >= 22.6 required for TS type stripping")
def test_window_state_a_b_a_resets_to_first_page():
    """QA repro: load 200 rows under key A, switch to B (search), back to A → must show 50, not 200."""
    script = (
        f"import {{ syncWindowState, growWindowState }} from {json.dumps(str(HOOK_FILE))};"
        "const out = [];"
        "let s = { key: 'A', count: 50 };"
        "for (let i = 0; i < 3; i++) s = growWindowState(s, 'A', 1921, 50);"
        "out.push(s.count);"                                   # 200 under A
        "const same = syncWindowState(s, 'A', 50); out.push(same === s);"  # no-op returns same object
        "s = syncWindowState(s, 'B', 50); out.push(s.key, s.count);"      # B → first page
        "s = syncWindowState(s, 'A', 50); out.push(s.key, s.count);"      # back to A → still first page
        "s = growWindowState(s, 'A', 1921, 50); out.push(s.count);"       # grows from 50
        "s = growWindowState({ key: 'A', count: 300 }, 'C', 120, 50); out.push(s.key, s.count);"  # stale key grow
        "console.log(JSON.stringify(out));"
    )
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30, cwd=str(WEB_DIR),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip()) == [200, True, "B", 50, "A", 50, 100, "C", 100]
