// code:web-lib-006:funnel-filters
// Client-safe date-range helpers shared by the funnel views.

export const FUNNEL_STORAGE_KEY = 'sahaja_funnel_filters';

export const DATE_RANGES = ['1d', '3d', '7d', '14d', '30d', '60d', '90d', 'all'] as const;
export type DateRange = (typeof DATE_RANGES)[number];

export interface FunnelFilters {
  city: string;
  programCode: string;
  dateRange: DateRange;
}

export const DEFAULT_FUNNEL_FILTERS: FunnelFilters = { city: 'all', programCode: 'all', dateRange: 'all' };

function localDateString(date: Date) {
  const offsetDate = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return offsetDate.toISOString().slice(0, 10);
}

export function getDateRangeBounds(dateRange: DateRange, now = new Date()) {
  if (dateRange === 'all') return { startDate: '', endDate: '' };
  const days = Number.parseInt(dateRange, 10);
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - days);
  return { startDate: localDateString(start), endDate: localDateString(now) };
}

export function dateRangeLabel(dateRange: DateRange) {
  return dateRange === 'all' ? 'Tất cả' : dateRange;
}

/**
 * Client-safe chronological parser that converts UI timestamps (e.g., '10:38 PM',
 * 'Yesterday', 'Mar 29, 2026, 2:51 PM', 'Sun 4:11 PM') or ISO strings into epoch ms.
 */
export function parseRealDate(ts?: string | null): number {
  if (!ts) return 0;
  const now = new Date();

  // Clean string
  const cleanTs = ts.replace(/\u202f/g, ' ').trim();

  // Fallbacks for standard ISO/JS parsable Strings (like "Mar 29, 2026, 2:51 PM" or "2026-09-14 19:24:29")
  const standardParse = new Date(cleanTs.replace(' at ', ' ')).getTime();
  if (!isNaN(standardParse) && standardParse > 0) return standardParse;

  const lowerTs = cleanTs.toLowerCase();

  if (lowerTs === 'now' || lowerTs.includes('vài giây') || lowerTs === 'vừa xong') return now.getTime();
  const mMatchRel = lowerTs.match(/(\d+)\s*(m|phút)/);
  if (mMatchRel) return now.getTime() - parseInt(mMatchRel[1], 10) * 60000;
  const hMatchRel = lowerTs.match(/(\d+)\s*(h|giờ)/);
  if (hMatchRel) return now.getTime() - parseInt(hMatchRel[1], 10) * 3600000;
  const dMatchRel = lowerTs.match(/(\d+)\s*(d|ngày)/);
  if (dMatchRel) return now.getTime() - parseInt(dMatchRel[1], 10) * 86400000;

  let timeMs = 0;
  let timeString = null;
  const timeMatch = cleanTs.match(/(\d{1,2}:\d{2}\s*(?:am|pm)?)/i);
  if (timeMatch) {
    timeString = timeMatch[1].toUpperCase();
    const tDate = new Date(`1970-01-01 ${timeString}`);
    if (!isNaN(tDate.getTime())) {
      timeMs = tDate.getHours() * 3600000 + tDate.getMinutes() * 60000;
    }
  } else {
    timeMs = 12 * 3600000;
  }

  const dayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();

  // Just time -> Today
  if (timeString && cleanTs.toUpperCase() === timeString) {
    return dayStart + timeMs;
  }

  // Yesterday
  if (lowerTs.includes('yesterday') || lowerTs.includes('hôm qua')) {
    return dayStart - 86400000 + timeMs;
  }

  const weekDays = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];
  const vnDays = ['cn', 't2', 't3', 't4', 't5', 't6', 't7'];

  let wIndex = weekDays.findIndex(wd => lowerTs.startsWith(wd));
  if (wIndex === -1) wIndex = vnDays.findIndex(wd => lowerTs.startsWith(wd));

  if (wIndex !== -1) {
    const todayIdx = now.getDay();
    let diff = todayIdx - wIndex;
    if (diff <= 0) diff += 7; // it was the past week
    return dayStart - diff * 86400000 + timeMs;
  }

  const mMatch = cleanTs.match(/(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{1,2})(?:,?\s*(\d{4}))?/i);
  if (mMatch) {
    const year = mMatch[3] ? parseInt(mMatch[3], 10) : now.getFullYear();
    const month = mMatch[1];
    const day = parseInt(mMatch[2], 10);
    const parsed = new Date(`${month} ${day}, ${year} ${timeString || ''}`);
    if (!isNaN(parsed.getTime())) {
      if (!mMatch[3] && parsed.getTime() > now.getTime()) {
        parsed.setFullYear(year - 1);
      }
      return parsed.getTime();
    }
  }

  return 0;
}

export interface FacebookMessageLike {
  messageTimestamp?: string | null;
  content?: string | null;
  seq?: number | null;
}

/**
 * Sort message bubbles in Facebook timeline order.
 *
 * Facebook deliberately uses abbreviated labels in the inbox: a message from
 * last Sunday is shown as `Sun 8:42 PM`, Monday as `Mon 6:57 AM`, etc. Those
 * labels must be compared as dates, not as strings or database insertion IDs.
 * The ad-reply system event is sometimes emitted after the bubble cluster even
 * though it is the first event at that timestamp, so it gets a deterministic
 * tie-breaker before the captured sequence.
 */
export function sortFacebookMessages<T extends FacebookMessageLike>(messages: readonly T[]): T[] {
  return messages
    .map((message, index) => ({
      message,
      index,
      facebookTime: parseRealDate(message.messageTimestamp),
      sequence: message.seq ?? index,
      isAdReply: /replied to an ad\.?$/i.test((message.content || '').trim()),
    }))
    .sort((a, b) => {
      if (a.facebookTime > 0 && b.facebookTime > 0 && a.facebookTime !== b.facebookTime) {
        return a.facebookTime - b.facebookTime;
      }

      if (a.facebookTime === b.facebookTime && a.facebookTime > 0 && a.isAdReply !== b.isAdReply) {
        return a.isAdReply ? -1 : 1;
      }

      return a.sequence - b.sequence || a.index - b.index;
    })
    .map(item => item.message);
}

export function isDateInRange(value: string | null | undefined, dateRange: DateRange) {
  if (dateRange === 'all') return true;
  if (!value) return false;
  const clean = value.trim().replace(/\u202f/g, ' ').replace(' at ', ' ');
  const isoCandidate = clean.includes('T') ? clean : clean.replace(' ', 'T');
  let timestamp = new Date(isoCandidate).getTime();
  if (Number.isNaN(timestamp)) {
    timestamp = new Date(clean).getTime();
  }
  if (Number.isNaN(timestamp) || timestamp <= 0) {
    timestamp = parseRealDate(value);
  }
  if (Number.isNaN(timestamp) || timestamp <= 0) return false;
  const { startDate, endDate } = getDateRangeBounds(dateRange);
  const start = new Date(`${startDate}T00:00:00`).getTime();
  const end = new Date(`${endDate}T23:59:59.999`).getTime();
  return timestamp >= start && timestamp <= end;
}

export function parseStoredFilters(value: string | null): FunnelFilters {
  if (!value) return DEFAULT_FUNNEL_FILTERS;
  try {
    const stored = JSON.parse(value) as Partial<FunnelFilters> & { startDate?: string; endDate?: string };
    if (stored.dateRange && DATE_RANGES.includes(stored.dateRange)) {
      return { city: stored.city || 'all', programCode: stored.programCode || 'all', dateRange: stored.dateRange };
    }
    // Gracefully migrate the previous date-input persistence shape.
    const duration = stored.startDate && stored.endDate
      ? Math.round((new Date(stored.endDate).getTime() - new Date(stored.startDate).getTime()) / 86_400_000)
      : NaN;
    const dateRange = DATE_RANGES.find(range => range !== 'all' && Number.parseInt(range, 10) === duration) || 'all';
    return { city: stored.city || 'all', programCode: stored.programCode || 'all', dateRange };
  } catch {
    return DEFAULT_FUNNEL_FILTERS;
  }
}

export function getStoredFilters(): FunnelFilters {
  if (typeof window === 'undefined') return DEFAULT_FUNNEL_FILTERS;
  try {
    const stored = localStorage.getItem(FUNNEL_STORAGE_KEY);
    return parseStoredFilters(stored);
  } catch {
    return DEFAULT_FUNNEL_FILTERS;
  }
}

export function saveStoredFilters(filters: FunnelFilters): void {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem(FUNNEL_STORAGE_KEY, JSON.stringify(filters));
    window.dispatchEvent(new Event('sahaja_funnel_filter_changed'));
  } catch {
    // ignore
  }
}
