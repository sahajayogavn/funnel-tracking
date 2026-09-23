// code:web-lib-012:use-infinite-slice
// Client-side lazy rendering helper: render only the first `visibleCount`
// items of a (fully filtered + sorted) list and grow it by `pageSize` when a
// sentinel element scrolls into view. The full list stays the source of truth
// for counts, range selection and batch actions — only rendering is sliced.
'use client';

import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';

export const DEFAULT_PAGE_SIZE = 50;

// code:web-lib-012:use-infinite-slice:next-visible-count
/** Pure helper: the next visible count after one "load more" step, clamped to `total`. */
export function nextVisibleCount(current: number, total: number, pageSize: number = DEFAULT_PAGE_SIZE): number {
  const safeTotal = Math.max(0, Math.floor(total));
  const safePage = Math.max(1, Math.floor(pageSize));
  const safeCurrent = Math.max(0, Math.floor(current));
  return Math.min(safeTotal, safeCurrent + safePage);
}

// code:web-lib-012:use-infinite-slice:clamp-visible-count
/** Pure helper: how many items are actually rendered for a requested count. */
export function clampVisibleCount(requested: number, total: number): number {
  return Math.max(0, Math.min(Math.floor(requested), Math.max(0, Math.floor(total))));
}

export interface SliceWindowState {
  key: string;
  count: number;
}

// code:web-lib-012:use-infinite-slice:sync-window
/**
 * Pure state transition: whenever the reset key differs from the key the
 * window was last recorded under, the window resets to the first page and is
 * re-tagged with the new key. Returns the SAME object when nothing changes so
 * callers can skip a state update. This makes A→B→A land on a fresh first
 * page instead of resurrecting A's old count.
 */
export function syncWindowState(state: SliceWindowState, resetKey: string, pageSize: number = DEFAULT_PAGE_SIZE): SliceWindowState {
  return state.key === resetKey ? state : { key: resetKey, count: Math.max(1, Math.floor(pageSize)) };
}

// code:web-lib-012:use-infinite-slice:grow-window
/** Pure state transition for one "load more" step under the current key. */
export function growWindowState(state: SliceWindowState, resetKey: string, total: number, pageSize: number = DEFAULT_PAGE_SIZE): SliceWindowState {
  const synced = syncWindowState(state, resetKey, pageSize);
  return { key: resetKey, count: nextVisibleCount(synced.count, total, pageSize) };
}

interface UseInfiniteSliceOptions {
  /** Length of the full (filtered + sorted) list. */
  total: number;
  /** Any value that, when it changes, resets the window to the first page and scrolls to top. */
  resetKey: string;
  /** Scroll container used as the IntersectionObserver root (null → viewport). */
  rootRef?: RefObject<HTMLElement | null>;
  pageSize?: number;
  /** Pre-load distance before the sentinel becomes visible. */
  rootMargin?: string;
}

// code:web-lib-012:use-infinite-slice:hook
export function useInfiniteSlice({
  total,
  resetKey,
  rootRef,
  pageSize = DEFAULT_PAGE_SIZE,
  rootMargin = '400px 0px',
}: UseInfiniteSliceOptions) {
  // The count is tagged with the reset key it was grown under. Every key
  // change (not only ones followed by a load-more) re-tags the state with a
  // fresh first page, using React's "adjust state during render" pattern.
  const [windowState, setWindowState] = useState<SliceWindowState>({ key: resetKey, count: pageSize });
  const syncedWindow = syncWindowState(windowState, resetKey, pageSize);
  if (syncedWindow !== windowState) setWindowState(syncedWindow);
  const visibleCount = clampVisibleCount(syncedWindow.count, total);
  const hasMore = visibleCount < total;
  const sentinelRef = useRef<HTMLTableRowElement | null>(null);

  const loadMore = useCallback(() => {
    setWindowState(previous => growWindowState(previous, resetKey, total, pageSize));
  }, [resetKey, total, pageSize]);

  // Reset scroll position whenever filters / search / sort change.
  useEffect(() => {
    const root = rootRef?.current;
    if (root && typeof root.scrollTo === 'function') root.scrollTo({ top: 0 });
  }, [resetKey, rootRef]);

  // Observe the sentinel. Re-created whenever visibleCount changes so a
  // sentinel that is still on screen after a page loads fires again.
  useEffect(() => {
    if (!hasMore) return;
    const sentinel = sentinelRef.current;
    if (!sentinel || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      entries => {
        if (entries.some(entry => entry.isIntersecting)) loadMore();
      },
      { root: rootRef?.current ?? null, rootMargin },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, loadMore, rootMargin, rootRef, visibleCount]);

  return { visibleCount, hasMore, loadMore, sentinelRef };
}
