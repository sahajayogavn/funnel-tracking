---
id: doc:qa-seekers-lazy-load-001
satisfies: code:web-page-002:seekers-lazy-load-001, code:web-lib-012:use-infinite-slice, code:web-api-001:seekers-api:activity-batch
date: 2026-09-23
author: independent QA agent (read-only; no production code changed, nothing committed)
---

# QA report: Seekers page lazy rendering (2026-09-23)

**Verdict: FAIL, one functional bug.** 26 of 27 checks passed. When the filter, search or sort returns to a combination used earlier in the session, the page brings back the old, larger row count instead of resetting to 50 rows (details in BUG-1). Everything else works as specified: the initial 50-row window, loading more by scrolling and by the button, counts, filter and sort semantics, journeyStage, shift-select across pages, activity-batch only for visible rows, polling limited to visible rows, and API validation.

Environment: dev server `http://localhost:9995`, DB snapshot of 1921 seekers (all `dm`, all `classificationStatus=pending`, city `Unknown`, `programCode=null`). Headless Google Chrome via Playwright (`.venv`). The session was created through `POST /api/auth` using the quiz answers from `web/tests/auth.cjs`. No MAS or send buttons were clicked. The only requests made were GETs plus the auth POST.

Evidence (all in `tmp/qa_seekers/`):
- `e2e_seekers_lazy.py`: the E2E script.
- `e2e_results.json`: results plus a network sample.
- `compute.mts`: an independent filter and sort computed from `/api/seekers?action=list` with the same helpers (`isDateInRange`, `parseRealDate`, `getStageNumber`).
- `repro_stale_window.py`: repro for BUG-1.
- Screenshots: `a_initial.png`, `b_loaded_more.png`, `c_search.png`, `d_program.png`, `d_7d.png`, `e_sort_name.png`, `f_journey_stage.png`, `g_shift_select.png`, `bug_stale_window.png`.

## 1. Static checks

| Check | Result |
|---|---|
| `cd web && npx tsc --noEmit` | PASS (clean) |
| `pytest tests/test_seekers_lazy_load.py tests/test_seekers_ui_improvements.py tests/test_web_filters_static.py tests/test_seeker_timeline_component.py -q` | 37 passed, 1 failed |
| The failing test `test_seekers_table_last_message_datetime_sorting` is pre-existing | CONFIRMED. `git show HEAD:web/src/components/seekers-table.tsx \| grep "handleSort('lastMessageDate')\|Last Message"` returns nothing, so HEAD already lacks that header and the failure is not caused by this change. |

## 2. Code review

| Area | Result | Notes |
|---|---|---|
| Filter and sort semantics | PASS | The `useMemo` body matches the previous code except that it reads `deferredSearch`. The deps `[seekers, filterState, deferredSearch, journeyStage, sortField, sortDir]` are complete. The runtime order matched an independent recomputation for the default sort, name sort, 7d, and both journey stages. |
| Hook deps and IntersectionObserver cleanup | PASS | The observer is disconnected in cleanup. It is re-created when `visibleCount`, `hasMore`, `loadMore` or `rootMargin` change, and it uses the scroll container as root. |
| Reset on filter, search, sort or journeyStage change | **FAIL (BUG-1)** | Resetting works for a new key, but a key that was used before restores its old count. Scroll-to-top works in both cases. |
| Shift-select index | PASS | `visibleSeekers` is `sorted.slice(0, n)`, so `idx` is an index into `sorted`. The range is built from `sorted`. |
| activity-batch validation | PASS (minor note N-2) | Covers the 100-name cap, malformed JSON, dedup, verbatim JSON names and the auth gate. |
| Polling limited to visible rows | PASS | `refreshClassificationProgress` filters `visibleSeekers`. The banner count still covers the whole list. |

### BUG-1 (medium): the window is not reset when the reset key returns to an earlier value

- **Location:** `web/src/lib/use-infinite-slice.ts:49-50`, the derived reset `const requested = windowState.key === resetKey ? windowState.count : pageSize;`
- **Root cause:** `windowState` is only written in `loadMore`. When the key goes A → B, the count for B is derived as `pageSize`, but nothing records that A was left, so `windowState` still holds `{key: A, count: 200}`. When the key returns to A, for example when the search is cleared, the filter is reset to "all", or the sort is toggled back, the stale count 200 matches again.
- **Repro** (`tmp/qa_seekers/repro_stale_window.py`, output verbatim):
  ```
  initial            {'rows': 50,  'footer': 'Đang hiển thị 50 / 1921',  'top': 0}
  after 3x Tải thêm  {'rows': 200, 'footer': 'Đang hiển thị 200 / 1921', 'top': 11701}
  search 'nguyen'    {'rows': 50,  'footer': 'Đang hiển thị 50 / 172',   'top': 0}
  search cleared     {'rows': 200, 'footer': 'Đang hiển thị 200 / 1921', 'top': 0}   <-- expected 50
  30d                {'rows': 50,  'footer': 'Đang hiển thị 50 / 309',   'top': 0}
  back to all        {'rows': 200, 'footer': 'Đang hiển thị 200 / 1921', 'top': 0}   <-- expected 50
  ```
- **Expected:** after any filter, search or sort change, including a change back to an earlier value, the table shows 50 rows with the scroll at the top.
- **Actual:** the scroll goes to the top, but the old count is restored (200 in the repro, up to 600 during the E2E run). The table therefore renders more rows than needed, which works against the point of the change. The data shown is still correct.
- **Suggested fix:** record the key change whenever the key differs. One option is the React-approved "adjust state while rendering" pattern: `if (windowState.key !== resetKey) setWindowState({ key: resetKey, count: pageSize });`. Another is to reset inside the existing `[resetKey]` effect. Add a test for the A → B → A case.
- The static tests in `tests/test_seekers_lazy_load.py` do not catch this.

### Minor notes (not blocking)

- **N-1:** `web/src/components/seekers-table.tsx:287`. A non-OK HTTP response, such as a 500, caches `[]` for those names and marks them as requested, so they are never retried. Only thrown errors (network failure or JSON parse error, line 295) are retried. A 5xx should probably be handled like a thrown error.
- **N-2:** `web/src/app/api/seekers/route.ts:11`. A JSON value that does not start with `[`, such as `names={"a":1}`, falls through to comma-splitting and returns 200 with key `{"a":1}`, when a 400 would be expected. This is harmless.
- **N-3:** `web/src/components/seekers-table.tsx:243`. `refreshClassificationProgress` depends on `visibleSeekers`, so every load-more or classification update re-creates the interval and fires an immediate poll. This is acceptable, but each batch of 50 rows loaded causes an extra request.
- **N-4 (observation):** when none of the visible rows are pending but rows further down are, the banner stays on and nothing polls until the user scrolls. This matches the stated design.

## 3. Browser E2E results

| Case | Result | Evidence |
|---|---|---|
| i. Load | INFO | Rows appeared about 1.43 s after `goto` (DOMContentLoaded to first rows, dev mode). There were 52 `<tr>` in total: header, 50 rows and the sentinel. Before this change, all 1921 rows were rendered. |
| a. Initial ≤50 rows, footer "Đang hiển thị 50 / 1921", N = filteredCount | PASS | 50 rows, footer `Đang hiển thị 50 / 1921`, filter bar `Hiển thị 1921 / 1921`. The order of the 50 rows equals the independent computation. |
| b. Scrolling loads more, and "Tải thêm" works | PASS | Row counts went 100, then 150 after scrolling, then 200 after the button. The footer stays in sync. |
| c. Search for a seeker beyond row 50 | PASS | "Nguyen Sy Chung" is at initial index 60. The search returns 1 row, as computed, and the footer shows `1 / 1`. |
| c. Clearing the search restores 50 rows and scroll at top | **FAIL (BUG-1)** | Scroll top is 0, but 300 rows remain. |
| d. Program filter | PASS | `20h30-Online-HCM` gives 0, matching the computation (all `programCode` values are null in the DB). The "No seekers found" state shows, the footer is hidden, and the sentinel is gone. |
| d. Date 7d / 1d / 30d | PASS | Results were 4 / 2 / 309 rows, all matching the computation. Row counts went from 500 before the filter to 4, 2 and 50. For 7d, the scroll was at the top and the order was exact. |
| e. Sort by Name | PASS | Rows went from 600 to 50 with the scroll at the top. The order equals the computed name-desc sort. (The 600 before the sort is itself BUG-1.) |
| f. `/seekers?journeyStage=Intake` and `?journeyStage=Seeker` | PASS | Intake: 1398, footer `50 / 1398`. Seeker: 523, footer `50 / 523`. Both match the computation. |
| g. Row click opens the sidebar | PASS | The sidebar name equals the name in the clicked row, and 1 row is selected. |
| g. Shift+click range after loading more | PASS | Clicking row 5 and shift-clicking row 121, with 200 rows rendered, selected 117 rows. The button reads "Chạy đề xuất MAS (117)" (`g_shift_select.png`). |
| h. activity-batch only for visible rows | PASS | Batches were sized 50 at first and then 50 per load-more step. After 200 visible rows, 200 names had been requested in total, with no duplicates. |
| h. classification-progress uses only visible pending thread IDs | PASS | Requests had 50 IDs at first and 200 IDs after 200 rows were visible. No request exceeded the visible count. |
| h. API: >100 names | PASS | Returns 400 `Too many names`, both for a JSON array and for a comma-separated list. |
| h. API: exactly 100 names | PASS | Returns 200. |
| h. API: malformed JSON `["a",` | PASS | Returns 400 `Invalid names parameter`. |
| h. API: valid names with a duplicate | PASS | Returns 200 with deduplicated keys and real histograms. |
| h. API: no names, or non-string items | PASS | Returns 200 `{"activity":{}}`. |
| h. API: unauthenticated | PASS | Returns 401. |

## 4. Summary

- **Blocking:** BUG-1 in `web/src/lib/use-infinite-slice.ts:49-50`, where the window is not reset when a filter, search or sort returns to an earlier value.
- **Non-blocking:** N-1 to N-4 above.
- The pre-existing test failure `test_seekers_table_last_message_datetime_sorting` has nothing to do with this change.
