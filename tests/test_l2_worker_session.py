"""Unit tests for the Phase 2 parallel-fetch worker session bootstrap:
`attach_worker_session` tab-role/prefer_new_tab wiring, and that concurrent
attaches are serialized by the shared `_ATTACH_LOCK`.

No live CDP/Chrome is ever used -- all Playwright objects are fakes.

# code:test-inbox-parallel-fetch-001:worker-session
"""
import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fb_pipeline.contracts.l1_session import CDP_URL, WORKER_TAB_ROLE_PREFIX
from fb_pipeline.session.l2_bootstrap import attach_worker_session


PAGE_ID = "1548373332058326"
INBOX_URL = f"https://business.facebook.com/latest/inbox/all?asset_id={PAGE_ID}"


class TestAttachWorkerSessionWiring(unittest.TestCase):
    """(a) attach_worker_session passes tab_role and prefer_new_tab=True."""

    def test_passes_worker_tab_role_and_prefer_new_tab(self):
        captured = {}

        def fake_attach(playwright, page_id, inbox_url, cdp_url=CDP_URL,
                         prefer_new_tab=True, tab_role=None):
            captured['page_id'] = page_id
            captured['inbox_url'] = inbox_url
            captured['prefer_new_tab'] = prefer_new_tab
            captured['tab_role'] = tab_role
            return "FAKE_SESSION"

        with patch(
            "fb_pipeline.session.l2_bootstrap.attach_to_authorized_session",
            side_effect=fake_attach,
        ) as mock_attach:
            result = attach_worker_session(object(), PAGE_ID, INBOX_URL, worker_index=3)

        mock_attach.assert_called_once()
        self.assertEqual(result, "FAKE_SESSION")
        self.assertEqual(captured['tab_role'], f"{WORKER_TAB_ROLE_PREFIX}3")
        self.assertEqual(captured['tab_role'], "scan_inbox_worker:3")
        self.assertTrue(captured['prefer_new_tab'])
        self.assertEqual(captured['page_id'], PAGE_ID)
        self.assertEqual(captured['inbox_url'], INBOX_URL)

    def test_worker_index_is_embedded_per_worker(self):
        captured = []

        def fake_attach(playwright, page_id, inbox_url, cdp_url=CDP_URL,
                         prefer_new_tab=True, tab_role=None):
            captured.append(tab_role)
            return "FAKE_SESSION"

        with patch(
            "fb_pipeline.session.l2_bootstrap.attach_to_authorized_session",
            side_effect=fake_attach,
        ):
            attach_worker_session(object(), PAGE_ID, INBOX_URL, worker_index=1)
            attach_worker_session(object(), PAGE_ID, INBOX_URL, worker_index=2)

        self.assertEqual(captured, ["scan_inbox_worker:1", "scan_inbox_worker:2"])


class _ConcurrencyTracker:
    """Counts overlapping entries into a guarded section."""

    def __init__(self):
        self._lock = threading.Lock()
        self.active = 0
        self.violations = 0

    def enter(self):
        with self._lock:
            self.active += 1
            if self.active > 1:
                self.violations += 1

    def leave(self):
        with self._lock:
            self.active -= 1


class _FakePage:
    def __init__(self, url):
        self.url = url

    def content(self):
        return "<html>Inbox</html>"

    def evaluate(self, *_args, **_kwargs):
        # No page already carries a matching mas_tab_role marker.
        return None

    def goto(self, *_args, **_kwargs):
        pass

    def wait_for_timeout(self, _ms):
        pass


class _FakeContext:
    """Simulates the CDP browser context whose `pages` enumeration and
    `new_page()` creation race when several worker threads attach at once
    without the module-level `_ATTACH_LOCK`."""

    def __init__(self, tracker: _ConcurrencyTracker, page_id: str):
        self._tracker = tracker
        self._page_id = page_id
        self.pages = []

    def new_page(self):
        self._tracker.enter()
        try:
            # Hold the "critical section" briefly so two unsynchronized
            # threads would overlap here if the lock did not serialize them.
            time.sleep(0.05)
            page = _FakePage(f"https://business.facebook.com/latest/inbox/all?asset_id={self._page_id}")
            self.pages.append(page)
            return page
        finally:
            self._tracker.leave()


class _FakeBrowser:
    def __init__(self, context):
        self.contexts = [context]


class _FakePlaywright:
    def __init__(self, tracker: _ConcurrencyTracker, page_id: str):
        self._context = _FakeContext(tracker, page_id)
        self._browser = _FakeBrowser(self._context)
        self.chromium = self

    def connect_over_cdp(self, _cdp_url):
        return self._browser


class TestAttachWorkerSessionConcurrency(unittest.TestCase):
    """(b) two threads attaching concurrently serialise on `_ATTACH_LOCK`.

    Exercises the real `attach_to_authorized_session` (not mocked) via
    `attach_worker_session`, against fake Playwright objects only.
    """

    def test_concurrent_worker_attaches_never_overlap_in_new_page(self):
        tracker = _ConcurrencyTracker()
        playwright = _FakePlaywright(tracker, PAGE_ID)
        results = {}
        errors = []

        def run(worker_index):
            try:
                results[worker_index] = attach_worker_session(
                    playwright, PAGE_ID, INBOX_URL, worker_index=worker_index
                )
            except Exception as exc:  # pragma: no cover - surfaced via assertion below
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(i,)) for i in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertEqual(tracker.violations, 0, "concurrent attaches overlapped inside the locked section")
        self.assertEqual(set(results.keys()), {1, 2})
        self.assertEqual(results[1].tab_role, "scan_inbox_worker:1")
        self.assertEqual(results[2].tab_role, "scan_inbox_worker:2")
        # Both worker tabs were created (no accidental tab reuse across roles).
        self.assertEqual(len(playwright._context.pages), 2)


if __name__ == '__main__':
    unittest.main()


# code:test-inbox-parallel-fetch-001:worker-session
class TestStampTabRole(unittest.TestCase):
    """Retrospective [2026-09-16]: page.goto wipes the DOM role marker, so the
    worker must re-stamp it after every task or each run leaks new tabs."""

    def test_stamp_tab_role_evaluates_marker(self):
        from fb_pipeline.session.l2_bootstrap import stamp_tab_role

        class _Page:
            def __init__(self):
                self.calls = []

            def evaluate(self, script, arg=None):
                self.calls.append((script, arg))

        page = _Page()
        stamp_tab_role(page, "scan_inbox_worker:3")
        self.assertEqual(len(page.calls), 1)
        self.assertIn("masTabRole", page.calls[0][0])
        self.assertEqual(page.calls[0][1], "scan_inbox_worker:3")

    def test_stamp_tab_role_noop_without_role_and_swallows_errors(self):
        from fb_pipeline.session.l2_bootstrap import stamp_tab_role

        class _Broken:
            def evaluate(self, *_):
                raise RuntimeError("closed")

        stamp_tab_role(_Broken(), None)   # no role → no call, no raise
        stamp_tab_role(_Broken(), "x")    # error swallowed


# code:test-inbox-parallel-fetch-001:worker-session
class TestOrchestratorSkipsRoleTabs(unittest.TestCase):
    """Retrospective [2026-09-16]: prefer_new_tab=False must never pick a
    role-marked worker tab, or the orchestrator and a worker share one tab."""

    def test_url_based_selection_skips_role_tabs(self):
        from unittest.mock import patch
        from fb_pipeline.session import l2_bootstrap

        inbox_url = "https://business.facebook.com/latest/inbox/all?asset_id=1"

        class _Page:
            def __init__(self, url, role):
                self.url, self._role = url, role
            def evaluate(self, script, *a):
                if "masTabRole" in script and "=" not in script.split("||")[0]:
                    return self._role
                return None
            def goto(self, *a, **k): pass
            def wait_for_timeout(self, *a): pass
            def title(self): return "Meta Business Suite"
            def content(self): return ""

        worker_tab = _Page(inbox_url + "&selected_item_id=5", "scan_inbox_worker:3")
        plain_tab = _Page(inbox_url, "")

        class _Ctx:
            pages = [worker_tab, plain_tab]
            def new_page(self): raise AssertionError("should reuse the plain tab")

        class _Browser:
            contexts = [_Ctx()]

        with patch.object(l2_bootstrap, "connect_to_cdp_browser", return_value=_Browser()):
            session = l2_bootstrap.attach_to_authorized_session(None, "1", inbox_url, prefer_new_tab=False)
        self.assertIs(session.page, plain_tab)
