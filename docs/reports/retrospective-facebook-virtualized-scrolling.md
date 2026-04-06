---
id: doc:report-scrolling-fix-001
title: Retrospective: Eliminating "Stupid Scrolling" & Facebook Virtualized Inbox Container Failures
date: 2026-04-06
---

# Retrospective: Eliminating "Stupid Scrolling" in Facebook Automation

## Overview

The Facebook Inbox pipeline (`l5_fetch_fb_messages.py` -> `l3_inbox.py`) exhibited extremely brittle behavior labeled by developers as "stupid scrolling," where the Playwright agent would enter an endless loop of blind `page.mouse.wheel(0, 300)` down strokes (sometimes looping 150 times over several minutes) failing to target and click incoming unread threads.

This issue specifically impacted historical deep scraping where thread targets existed but failed intersection boundaries.

## Root Causes Identified

1. **Strict Text Match Brittle Logic (Fix 1 & 2):**
   * **Problem:** Earlier logic strictly evaluated `previewText` (e.g. "Customer: Xin chào") against what was captured in Stage 1. However, Facebook dynamically lazy-re-renders lines, prepends "You:", or changes timestamps across stages. If exact text didn't match, the thread was deemed "not found" and trigged the scroll fallback.
   * **Solution:** "Relaxed Candidates Filter". By extracting `data-hovercard` as a permanent `fb_url`, we can achieve 1:1 ID routing. For fallback matching, if EXACTLY `name === targetName` within a unique DOM snapshot, the script automatically triggers `click()`, bypassing volatile preview content.

2. **Hardcoded React Virtualization DOM (Fix 3):**
   * **Problem:** Previous scroll fallback injected a static evaluation string mapping `const cards = Array.from(document.querySelectorAll('div[role="navigation"] div[role="gridcell"]'))`. When Facebook A/B tested rolling out `div._5_n1` or `div[role="listitem"]` for threads, `cards` resolved to an empty array.
   * **Consequence:** `cards.length === 0` meant the fallback returned `scrollTop = -1`. The Python runner assumed the DOM was broken and repeatedly executed blind `mouse.wheel`, triggering the infinite loop.
   * **Solution:** Parameterized the Javascript string template to consume `config.threadSelector` uniformly across the entire stack via `thread_card_selector()`, exactly mirroring Stage 1 detection capabilities.

3. **Failed Layout Scroller Identification (Fix 4 - The Core Bug):**
   * **Problem:** Stage 2's Python validation assumed it could climb the DOM `parent.parentElement` and check `parent.scrollHeight > parent.clientHeight` to locate the native virtual scroll container box. However, Facebook's latest virtual intersection mechanism sets container bounds such that `scrollHeight` strictly equals `clientHeight`, dynamically translating inner contents instead.
   * **Consequence:** `parent.scrollHeight > parent.clientHeight` NEVER returned `true`. Verification reset and traversal scripts all resulted in `-1`, silently masking that thread target 0 ('Van Huynh') was not scrolled back into view. 
   * **Solution:** Restructured the scroll algorithm. The DOM climber now formally checks `window.getComputedStyle(parent).overflowY`. If it explicitly detects `scroll` or `auto`, it confirms the bounding box even when raw DOM geometries collapse.

## Implementation Standard for Future Scrapers

Whenever automating virtualized Infinity Lists (React/Vue/Angular), ALWAYS implement fallback traversal logic utilizing `window.getComputedStyle`:
```javascript
// DO NOT rely solely on raw heights, which fail against CSS transforms/translates:
if (parent.scrollHeight > parent.clientHeight || ['auto', 'scroll'].includes(window.getComputedStyle(parent).overflowY)) {
    return parent; // Valid Virtual Container
}
```

Additionally, **NEVER** use static array selectors inside fallback boundaries. Always propagate centralized configuration variables such as `config.threadSelector` into `page.evaluate()` handlers.

**Tag ID mapping:** #code:bugfix-fb-scroll-001
