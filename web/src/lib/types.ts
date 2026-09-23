// code:web-types-001:shared-types
// Client-safe types — NO server-only imports

export interface Seeker {
  id: number;
  // Facebook's display name, used to locate the person in Meta Inbox.
  name: string;
  // Registration name explicitly supplied by the seeker in the conversation.
  realName?: string | null;
  threadId?: string;  // users.thread_id for DM seekers
  // Zero-based position from the Meta inbox sidebar. Lower is newer.
  inboxSortIndex?: number | null;
  // Absolute timestamp of the latest Inbox message, regardless of sender.
  lastMessageAt?: string | null;
  fbProfileUrl: string | null;
  fbUserId: string | null;
  phone: string | null;
  email: string | null;
  city: string;
  // LLM-selected class code from the fixed programme catalogue. Empty means
  // city is known but no specific class was stated in the conversation.
  programCode?: string | null;
  leadStage: string;
  firstSeen: string;
  lastInteraction: string;
  source: 'dm' | 'comment';
  lastMessageTimestampText?: string | null;
  lastMessageDate?: string | null;
  classificationStatus?: 'pending' | 'done' | 'unknown';
  // Latest active outbound draft, surfaced in the table so an operator does
  // not need to open the seeker sidebar just to review MAS output.
  pendingMessage?: string | null;
  pendingMessageKind?: string | null;
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
  // Absolute Facebook event time resolved during ingestion. This is distinct
  // from `timestamp`, which is when the scraper wrote the row to SQLite.
  messageAt?: string | null;
  sourceId?: string | null;
  /** True only when Meta's source id is confirmed as an MAS-proposed message. */
  masProposed?: boolean;
  senderConfidence?: string | null;
  timePrecision?: string | null;
  replyToMessageId?: string | null;
  quotedSender?: string | null;
  quotedText?: string | null;
  reactionAnnotationJson?: string | null;
  reactions?: {
    actor?: string | null; emoji?: string | null; count?: number | null;
  }[];
  // Sequence captured from Facebook's message panel. It is only a tie-breaker
  // after a Facebook timestamp has been interpreted chronologically.
  seq?: number | null;
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
  pendingHistory?: PendingHistory | null;
}

export interface PendingHistory {
  observedAt: string;
  reasons: string[];
  messages: { sourceId: string | null; content: string; reasons: string[] }[];
}

export const JOURNEY_STAGES: { key: JourneyStage; label: string; description: string }[] = [
  { key: 'User', label: 'Tiếp nhận (User)', description: 'Contacts ở giai đoạn đầu, chưa chuyển sang Seeker' },
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

  const rawDiffMs = toDate.getTime() - dateObj.getTime();
  if (rawDiffMs < 0) {
    // If date is slightly in the future due to clock drift (<= 60s), treat as 1m
    if (Math.abs(rawDiffMs) <= 60000) return '1m';
    // If significantly in the future, do not display a misleading '1m ago'
    return 'now';
  }

  const diffMs = rawDiffMs;
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

export interface LlmCall {
  id: number;
  trace_id: string;
  parent_call_id: number | null;
  seq_in_trace: number;
  attempt: number;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  trigger: string;
  route: string;
  route_group: string;
  agent_name: string | null;
  model: string | null;
  page_id: string | null;
  subject_type: string | null;
  subject_id: string | null;
  subject_label: string | null;
  dry_run: boolean;
  system_prompt: string | null;
  messages_json: string | null;
  state_json: string | null;
  tools_json: string | null;
  response_text: string | null;
  response_json: string | null;
  sanitized_text: string | null;
  status: string;
  error: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  outcome_type: string | null;
  outcome_ref: string | null;
}
