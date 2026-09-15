// code:web-types-001:shared-types
// Client-safe types — NO server-only imports

export interface Seeker {
  id: number;
  name: string;
  threadId?: string;  // users.thread_id for DM seekers
  fbProfileUrl: string | null;
  fbUserId: string | null;
  phone: string | null;
  email: string | null;
  city: string;
  leadStage: string;
  firstSeen: string;
  lastInteraction: string;
  source: 'dm' | 'comment';
  lastMessageTimestampText?: string | null;
  lastMessageDate?: string | null;
}

export interface Post {
  id: string;
  pageId: string;
  postName: string | null;
  postUrl: string | null;
  lastSynced: string | null;
  createdAt: string;
}

export interface ThreadRow {
  id: string;
  pageId: string;
  threadName: string | null;
  lastSynced: string | null;
  createdAt: string;
}

export interface CommentRow {
  id: number;
  postId: string;
  commenterName: string;
  commentText: string | null;
  fbProfileUrl: string | null;
  fbUserId: string | null;
  isReply: number;
  commentDate: string | null;
  timestamp: string;
}

export interface MessageRow {
  id: number;
  threadId: string;
  sender: string | null;
  content: string | null;
  messageTimestamp: string | null;
  timestamp: string;
}

export type JourneyStage =
  | 'User'
  | 'Seeker'
  | 'Seeker_Public_Program'
  | 'Seeker_18_Weeks'
  | 'Seed'
  | 'Sahaja_Yogi'
  | 'Sahaja_Yogi_Dedicated'
  | 'Sahaja_Mahayogi';

export interface TouchPoint {
  type: 'comment' | 'message' | 'reply' | 'ad_click' | 'ad_message';
  detail: string;
  date: string;
  source: string;
  postId?: string;    // For linking to FB post
  threadId?: string;  // For linking to FB inbox thread
}

// Full seeker detail for /seekers/[id] page
export interface SeekerDetail {
  seeker: Seeker;
  messages: MessageRow[];
  comments: (CommentRow & { postName?: string; postUrl?: string })[];
  adSource: { content: string; matchedPostId?: string; matchedPostName?: string } | null;
  messageCount: number;
  commentCount: number;
}

export const JOURNEY_STAGES: { key: JourneyStage; label: string; description: string }[] = [
  { key: 'User', label: 'User', description: 'First interaction: DM or comment on Page' },
  { key: 'Seeker', label: 'Seeker', description: 'Provided phone number to register' },
  { key: 'Seeker_Public_Program', label: 'Public Program', description: 'Attending public meditation programs' },
  { key: 'Seeker_18_Weeks', label: '18-Week Course', description: 'Committed to 18-week deep learning course' },
  { key: 'Seed', label: 'Seed', description: 'Becoming the foundation of Sahaja Yoga' },
  { key: 'Sahaja_Yogi', label: 'Sahaja Yogi', description: 'Practicing Sahaja Yoga regularly' },
  { key: 'Sahaja_Yogi_Dedicated', label: 'Dedicated Yogi', description: 'Fully dedicated practitioner' },
  { key: 'Sahaja_Mahayogi', label: 'Sahaja Mahayogi', description: 'Highest level of spiritual dedication' },
];

export interface CanonicalStage {
  number: number;
  key: string;
  label: string;
  description: string;
  color: string;
}

export const CANONICAL_STAGES: CanonicalStage[] = [
  { number: 1, key: 'Intake', label: 'Intake', description: 'Tiếp nhận / Tương tác ban đầu', color: '#a78bfa' },
  { number: 2, key: 'Seeker', label: 'Seeker', description: 'Quan tâm & tìm hiểu', color: '#818cf8' },
  { number: 3, key: 'Seeker_Public_Program', label: 'Public Program', description: 'Đã đăng ký lớp cộng đồng', color: '#22d3ee' },
  { number: 4, key: 'Seeker_18_Weeks', label: '18-Week Course', description: 'Khóa học chuyên sâu 18 tuần', color: '#34d399' },
  { number: 5, key: 'Seed', label: 'Seed', description: 'Nền tảng thực hành đều đặn', color: '#10b981' },
  { number: 6, key: 'Sahaja_Yogi', label: 'Sahaja Yogi', description: 'Thực hành đều đặn cùng tập thể', color: '#fbbf24' },
  { number: 7, key: 'Sahaja_Mahayogi', label: 'Sahaja Mahayogi', description: 'Tận tâm & hướng dẫn cộng đồng', color: '#f43f5e' },
];

export function getStageNumber(leadStage?: string | null): number {
  if (!leadStage) return 1;
  const s = leadStage.toLowerCase().trim();
  if (s === 'user' || s === 'intake') return 1;
  if (s === 'seeker' || s === 'follower' || s === 'curious seeker' || s === 'curious') return 2;
  if (s === 'seeker_public_program' || s === 'public program seeker' || s === 'public program' || s === 'registered' || s === 'attending') return 3;
  if (s === 'seeker_18_weeks' || s === '18-week seeker' || s === '18-week course' || s === 'deep learner' || s === '18 week seeker') return 4;
  if (s === 'seed') return 5;
  if (s === 'sahaja_yogi' || s === 'sahaja yogi') return 6;
  if (s === 'sahaja_yogi_dedicated' || s === 'sahaja_mahayogi' || s === 'sahaja mahayogi' || s === 'dedicated yogi') return 7;
  return 1;
}

export function getStageLabel(stageNum: number): string {
  const found = CANONICAL_STAGES.find(s => s.number === stageNum);
  return found ? found.label : 'Intake';
}

/**
 * Compact Relative Date Format Convention:
 * Formats elapsed duration between `sinceDate` and `toDate` into an ultra-compact string:
 * - Empty / invalid: '—'
 * - < 1 hour: '[diffMin]m' (e.g. '1m', '45m')
 * - < 24 hours: '[diffHour]h' (e.g. '1h', '18h')
 * - < 30 days: '[diffDays]d' (e.g. '1d', '3d', '28d')
 * - < 365 days: '[months]m[remDays]d' if remDays > 0 (e.g. '1m3d'), else '[months]m' (e.g. '2m')
 * - >= 365 days: '[years]y[remMonths]m' if remMonths > 0 (e.g. '1y3m'), else '[years]y' (e.g. '1y')
 */
export function formatRelativeElapsed(sinceDate?: string | number | Date | null, toDate: Date = new Date()): string {
  if (!sinceDate) return '—';
  let dateObj: Date;
  if (typeof sinceDate === 'string') {
    const cleanStr = sinceDate.trim().replace(/\u202f/g, ' ').replace(' at ', ' ');
    // Ensure ISO-compatible format for cross-browser parsing (Safari, etc.)
    const isoCandidate = cleanStr.includes('T') ? cleanStr : cleanStr.replace(' ', 'T');
    const parsed = new Date(isoCandidate);
    if (isNaN(parsed.getTime())) {
      const fallback = new Date(cleanStr);
      if (isNaN(fallback.getTime())) return '—';
      dateObj = fallback;
    } else {
      dateObj = parsed;
    }
  } else if (typeof sinceDate === 'number') {
    dateObj = new Date(sinceDate);
  } else {
    dateObj = sinceDate;
  }

  const diffMs = Math.max(0, toDate.getTime() - dateObj.getTime());
  const diffMin = Math.floor(diffMs / 60000);
  const diffHour = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffHour < 24) {
    if (diffHour === 0) return `${Math.max(1, diffMin)}m`;
    return `${diffHour}h`;
  }
  if (diffDays < 30) {
    return `${diffDays}d`;
  }
  if (diffDays < 365) {
    const months = Math.floor(diffDays / 30);
    const remDays = diffDays % 30;
    return remDays > 0 ? `${months}m${remDays}d` : `${months}m`;
  }
  const years = Math.floor(diffDays / 365);
  const remMonths = Math.floor((diffDays % 365) / 30);
  return remMonths > 0 ? `${years}y${remMonths}m` : `${years}y`;
}

