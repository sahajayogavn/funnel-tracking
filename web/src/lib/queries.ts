// code:web-db-002:data-queries
// Server-side query functions that read from FrankenSQLite
import { getDb } from './db';
import type { Seeker, SeekerDetail, Post, CommentRow, MessageRow, ThreadRow, TouchPoint } from './types';
import { parseRealDate } from './funnel-filters';

// ── FB URL normalization ──
// All FB URLs like facebook.com/SahajaVietnam?__cft__[0]=... are the same page.
// Strip tracking params to identify unique profiles.
function normalizeFbUrl(url: string | null): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    // Remove all tracking params
    u.search = '';
    return u.toString();
  } catch {
    // If URL is malformed, try simple regex
    return url.split('?')[0];
  }
}

// Page's own FB profile — filter this out from seekers

const PAGE_NAME = 'Thiền Sahaja Yoga Việt Nam';

function tableExists(tableName: string): boolean {
  const db = getDb();
  const result = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name=?").get(tableName);
  return !!result;
}

export type ActionQueueItem = {
  id: number; queueType: string; targetType: string; targetId: string | null;
  targetName: string | null; actionText: string | null; reactionType: string | null;
  status: string; approvalSource: string | null; errorText: string | null; createdAt: string;
};

export function getActionQueueItems(): ActionQueueItem[] {
  if (!tableExists('action_queue')) return [];
  return getDb().prepare(`
    SELECT id, queue_type AS queueType, target_type AS targetType, target_id AS targetId,
           target_name AS targetName, action_text AS actionText, reaction_type AS reactionType,
           status, approval_source AS approvalSource, error_text AS errorText, created_at AS createdAt
    FROM action_queue
    WHERE status NOT IN ('executed', 'rejected')
    ORDER BY queue_type, id
  `).all() as ActionQueueItem[];
}

export function getSeekerActionQueueItems(targetId?: string | null, targetName?: string | null): ActionQueueItem[] {
  if (!tableExists('action_queue')) return [];
  const db = getDb();
  if (!targetId && !targetName) return [];
  return db.prepare(`
    SELECT id, queue_type AS queueType, target_type AS targetType, target_id AS targetId,
           target_name AS targetName, action_text AS actionText, reaction_type AS reactionType,
           status, approval_source AS approvalSource, error_text AS errorText, created_at AS createdAt
    FROM action_queue
    WHERE (target_id = ? OR (target_name IS NOT NULL AND target_name = ?))
    ORDER BY id DESC
  `).all(targetId || '', targetName || '') as ActionQueueItem[];
}

// ── Heuristics to fix older scraped messages ──
// Scraper may have incorrectly recorded Page messages as "Customer" due to DOM changes
function normalizeMessageSender(content: string | null, originalSender: string | null): string | null {
  if (!originalSender) return null;
  if (originalSender === 'Page' || originalSender === 'Auto_Page') return 'Page';
  
  if (!content) return originalSender;
  
  const text = content.toLowerCase();
  
  // Known Page/Admin reply patterns:
  if (text.includes('chúng tôi có thể giúp gì cho bạn?')) return 'Page';
  if (text.includes('hoàn toàn miễn phí')) return 'Page';
  if (text.includes('khóa học thiền ở')) return 'Page';
  if (text.includes('thời gian: 20h')) return 'Page';
  if (text.includes('tối thứ 3 hàng tuần')) return 'Page';
  if (text.includes('bạn để lại họ tên và số điện thoại')) return 'Page';
  if (text.includes('lớp ở vương thừa vũ')) return 'Page';
  if (text.includes('bạn ạ')) return 'Page';
  if (text.includes('nhé bạn')) return 'Page';
  if (text.includes('chủ nhật đầu tiên')) return 'Page';
  if (text.includes('inbox page')) return 'Page';
  if (text.includes('tổng là 12 tuần')) return 'Page';

  return originalSender;
}

// ── Seekers (unified from users + comment_users) ──

export function getAllSeekers(): Seeker[] {
  const db = getDb();

  // DM users — use threads as base table and LEFT JOIN to users for contact info
  // This ensures ALL thread interactions are counted, not just those with extracted user info
  const dmUsers = db.prepare(`
    SELECT
      MIN(u.id) AS id, t.id AS threadId, t.thread_name AS name,
      MAX(u.fb_url) AS fbProfileUrl,
      NULL AS fbUserId,
      MAX(u.phone) AS phone, MAX(u.email) AS email,
      COALESCE(
        MAX(CASE WHEN u.city != 'Unknown' AND u.city IS NOT NULL THEN u.city END),
        MAX(CASE WHEN ap.city != 'Unknown' AND ap.city IS NOT NULL THEN ap.city END),
        MAX(u.city),
        'Unknown'
      ) AS city,
      COALESCE(MAX(u.lead_stage), 'Intake') AS leadStage,
      MIN(COALESCE(u.first_seen, t.created_at)) AS firstSeen,
      MAX(COALESCE(u.last_interaction, t.last_synced_time)) AS lastInteraction,
      (SELECT message_timestamp FROM messages m WHERE m.thread_id = t.id ORDER BY m.id DESC LIMIT 1) AS lastMessageTimestampText,
      'dm' AS source
    FROM threads t
    LEFT JOIN users u ON u.thread_id = t.id
    LEFT JOIN user_ad_ids uai ON uai.thread_id = t.id
    LEFT JOIN ad_posts ap ON ap.ad_id = uai.ad_id
    WHERE t.thread_name IS NOT NULL
    GROUP BY t.id
  `).all() as (Seeker & { lastMessageTimestampText?: string | null })[];

  // Comment users (exclude page's own comments — currently all are page)
  let commentUsers: Seeker[] = [];
  if (tableExists('comment_users') && tableExists('comments')) {
    commentUsers = db.prepare(`
      SELECT
        cu.id, cu.commenter_name AS name, cu.fb_profile_url AS fbProfileUrl,
        cu.fb_user_id AS fbUserId, cu.phone, cu.email, cu.city,
        cu.lead_stage AS leadStage,
        MIN(c.comment_date) AS firstSeen,
        MAX(c.comment_date) AS lastInteraction,
        MAX(c.comment_date) AS lastMessageTimestampText,
        'comment' AS source
      FROM comment_users cu
      LEFT JOIN comments c ON c.commenter_name = cu.commenter_name
      WHERE cu.commenter_name != ?
      GROUP BY cu.id
    `).all(PAGE_NAME) as (Seeker & { lastMessageTimestampText?: string | null })[];
  }

  // Normalize FB URLs and compute dynamic journey stage
  const allSeekers = [...dmUsers, ...commentUsers].map(s => ({
    ...s,
    fbProfileUrl: normalizeFbUrl(s.fbProfileUrl),
    // Dynamic stage:
    // If DB has an advanced stage (Public Program, 18-Weeks, Seed, Yogi, Mahayogi), preserve it.
    // If DB is Intake / User or empty: has phone → 'Seeker', else → 'Intake'.
    leadStage: (() => {
      const stored = s.leadStage?.trim();
      const isBaseStage = !stored || stored === 'Intake' || stored === 'User';
      if (isBaseStage) {
        return (s.phone && s.phone.trim() !== '') ? 'Seeker' : (stored || 'Intake');
      }
      return stored;
    })(),

  }));

  // Deduplicate:
  // - DM users: same person can open multiple threads (e.g., from different ad clicks),
  //   so dedup by name. When duplicates found, keep the entry with most contact info.
  // - Comment users: dedup by name to avoid cross-channel duplicates
  const seen = new Map<string, { seeker: Seeker; idx: number }>();
  const result: Seeker[] = [];
  for (const s of allSeekers) {
    const key = s.source === 'dm'
      ? `dm-${s.name}`            // Same person may have multiple threads
      : `comment-${s.name}`;     // Comment users dedup by name
    if (s.name === PAGE_NAME) continue; // Skip the page itself
    if (!seen.has(key)) {
      seen.set(key, { seeker: s, idx: result.length });
      result.push(s);
    } else {
      // Keep the entry with more contact info (phone/email)
      const existing = seen.get(key)!;
      const existingScore = (existing.seeker.phone ? 1 : 0) + (existing.seeker.email ? 1 : 0);
      const newScore = (s.phone ? 1 : 0) + (s.email ? 1 : 0);

      // Also prioritize newer interaction if it has same score
      const newInteractionTime = s.lastMessageTimestampText ? new Date(s.lastMessageTimestampText).getTime() : 0;
      const oldInteractionTime = existing.seeker.lastMessageTimestampText ? new Date(existing.seeker.lastMessageTimestampText).getTime() : 0;

      if (newScore > existingScore || (newScore === existingScore && newInteractionTime > oldInteractionTime)) {
        result[existing.idx] = s;
        seen.set(key, { seeker: s, idx: existing.idx });
      }
    }
  }

  // Sanitize lastMessageTimestampText against scraped non-date texts (e.g. ad links)
  // and compute actual datetime for accurate sorting and display
  for (const s of result) {
    if (s.lastMessageTimestampText && !parseRealDate(s.lastMessageTimestampText)) {
      s.lastMessageTimestampText = null;
    }
    // Fall back to DB last_interaction if message timestamp is entirely busted
    if (!s.lastMessageTimestampText && s.lastInteraction) {
      s.lastMessageTimestampText = s.lastInteraction;
    }
    const realTime = parseRealDate(s.lastMessageTimestampText) || parseRealDate(s.lastInteraction) || parseRealDate(s.firstSeen) || 0;
    s.lastMessageDate = realTime > 0 ? new Date(realTime).toISOString() : null;
  }

  // Parse dates and sort chronologically, prioritizing actual datetime
  result.sort((a, b) => {
    const timeA = (a.lastMessageDate ? new Date(a.lastMessageDate).getTime() : 0) || parseRealDate(a.lastMessageTimestampText) || parseRealDate(a.lastInteraction) || 0;
    const timeB = (b.lastMessageDate ? new Date(b.lastMessageDate).getTime() : 0) || parseRealDate(b.lastMessageTimestampText) || parseRealDate(b.lastInteraction) || 0;
    return timeB - timeA;
  });

  return result;
}

// ── Activity Histogram (interactions per day, last 365 days) ──

export function getSeekerActivity(seekerName: string): { date: string; count: number }[] {
  const db = getDb();

  // Messages by this user
  const msgActivity = db.prepare(`
    SELECT DATE(m.timestamp) AS date, COUNT(*) AS count
    FROM messages m
    JOIN threads t ON m.thread_id = t.id
    JOIN users u ON u.thread_id = t.id
    WHERE u.thread_name = ?
    GROUP BY DATE(m.timestamp)
  `).all(seekerName) as { date: string; count: number }[];

  // Comments by this user
  let cmtActivity: { date: string; count: number }[] = [];
  if (tableExists('comments')) {
    cmtActivity = db.prepare(`
      SELECT DATE(c.timestamp) AS date, COUNT(*) AS count
      FROM comments c
      WHERE c.commenter_name = ?
      GROUP BY DATE(c.timestamp)
    `).all(seekerName) as { date: string; count: number }[];
  }

  // Merge
  const map = new Map<string, number>();
  for (const row of [...msgActivity, ...cmtActivity]) {
    map.set(row.date, (map.get(row.date) || 0) + row.count);
  }
  return Array.from(map.entries())
    .map(([date, count]) => ({ date, count }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

// ── Posts ──

export function getAllPosts(): Post[] {
  if (!tableExists('posts')) return [];
  const db = getDb();
  return db.prepare('SELECT * FROM posts ORDER BY last_synced_time DESC').all() as Post[];
}

// ── Comments per post ──

export function getCommentsByPost(postId: string): CommentRow[] {
  if (!tableExists('comments')) return [];
  const db = getDb();
  return db.prepare('SELECT * FROM comments WHERE post_id = ? ORDER BY comment_date DESC').all(postId) as CommentRow[];
}

// ── Threads & Messages ──

export function getAllThreads(): ThreadRow[] {
  const db = getDb();
  return db.prepare('SELECT * FROM threads ORDER BY last_synced_time DESC').all() as ThreadRow[];
}

export function getMessagesByThread(threadId: string): MessageRow[] {
  const db = getDb();
  const rows = db.prepare(`
    SELECT id, thread_id AS threadId, sender, content,
           message_timestamp AS messageTimestamp, seq, timestamp
    FROM messages WHERE thread_id = ? ORDER BY id ASC
  `).all(threadId) as MessageRow[];

  return rows.map(r => ({
    ...r,
    sender: normalizeMessageSender(r.content, r.sender)
  }));
}

// ── Touch Points for a seeker ──

export function getSeekerTouchPoints(seekerName: string): TouchPoint[] {
  const db = getDb();

  const msgTouchPoints = db.prepare(`
    SELECT
      m.content AS detail,
      COALESCE(m.message_timestamp, m.timestamp) AS date,
      t.thread_name AS source,
      m.sender AS sender
    FROM messages m
    JOIN threads t ON m.thread_id = t.id
    JOIN users u ON u.thread_id = t.id
    WHERE u.thread_name = ?
    ORDER BY m.timestamp ASC
  `).all(seekerName) as { detail: string; date: string; source: string; sender: string }[];

  // Process messages: detect ad source, tag type accordingly
  const processed: TouchPoint[] = [];
  for (const msg of msgTouchPoints) {
    const isAdSource = msg.detail?.includes('[AD SOURCE]') || msg.detail?.includes('--- [AD SOURCE]');
    const normalizedSender = normalizeMessageSender(msg.detail, msg.sender);
    
    // Normal touchpoints shouldn't include Page messages except ad hooks
    if (normalizedSender === 'Page' && !isAdSource) continue;

    let detail = msg.detail || '';
    let tpType: TouchPoint['type'] = 'message';
    
    if (isAdSource) {
      tpType = 'ad_message';
      // Extract the ad content summary (first line after the marker)
      const adMatch = detail.match(/\[AD SOURCE\][^]*?(?:\n(.+))?/);
      detail = adMatch?.[1]?.trim() || 'Replied to ad post';
    }
    
    processed.push({ type: tpType, detail, date: msg.date, source: msg.source });
  }

  let cmtTouchPoints: TouchPoint[] = [];
  if (tableExists('comments') && tableExists('posts')) {
    cmtTouchPoints = db.prepare(`
      SELECT
        CASE WHEN c.is_reply = 1 THEN 'reply' ELSE 'comment' END AS type,
        c.comment_text AS detail,
        COALESCE(c.comment_date, c.timestamp) AS date,
        p.post_name AS source
      FROM comments c
      JOIN posts p ON c.post_id = p.id
      WHERE c.commenter_name = ?
      ORDER BY c.timestamp ASC
    `).all(seekerName) as TouchPoint[];
  }

  return [...processed, ...cmtTouchPoints].sort(
    (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime()
  );
}

// ── Full Seeker Detail (for /seekers/[id] page) ──
// code:web-db-002:seeker-detail

export function getSeekerById(seekerId: string): SeekerDetail | null {
  const db = getDb();

  // comment-{id} prefix → comment_users table
  const isComment = seekerId.startsWith('comment-');

  if (isComment) {
    if (!tableExists('comment_users')) return null;
    const commentUserId = seekerId.replace('comment-', '');
    const cuRow = db.prepare(`
      SELECT cu.id, cu.commenter_name AS name, cu.fb_profile_url AS fbProfileUrl,
             cu.fb_user_id AS fbUserId, cu.phone, cu.email, cu.city,
             cu.lead_stage AS leadStage, cu.first_seen AS firstSeen,
             cu.last_interaction AS lastInteraction, 'comment' AS source
      FROM comment_users cu WHERE cu.id = ?
    `).get(commentUserId) as Seeker | undefined;
    if (!cuRow) return null;

    cuRow.fbProfileUrl = normalizeFbUrl(cuRow.fbProfileUrl);
    cuRow.leadStage = (cuRow.phone && cuRow.phone.trim() !== '') ? 'Seeker' : 'User';

    let comments: (CommentRow & { postName?: string; postUrl?: string })[] = [];
    if (tableExists('comments') && tableExists('posts')) {
      comments = db.prepare(`
        SELECT c.*, p.post_name AS postName, p.post_url AS postUrl
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        WHERE c.commenter_name = ?
        ORDER BY c.timestamp ASC
      `).all(cuRow.name) as (CommentRow & { postName?: string; postUrl?: string })[];
    }

    const lastCmt = comments.length > 0 ? comments[comments.length - 1] : null;
    cuRow.lastMessageTimestampText = lastCmt?.commentDate || cuRow.lastInteraction;
    const commentRealTime = parseRealDate(cuRow.lastMessageTimestampText) || parseRealDate(cuRow.lastInteraction) || parseRealDate(cuRow.firstSeen) || 0;
    cuRow.lastMessageDate = commentRealTime > 0 ? new Date(commentRealTime).toISOString() : null;

    return {
      seeker: cuRow,
      messages: [],
      comments,
      adSource: null,
      messageCount: 0,
      commentCount: comments.length,
    };
  }

  // DM seeker — lookup by numeric users.id
  const uRow = db.prepare(`
    SELECT u.id, u.thread_id AS threadId, u.thread_name AS name, u.fb_url AS fbProfileUrl,
           NULL AS fbUserId, u.phone, u.email, u.city,
           u.lead_stage AS leadStage, u.first_seen AS firstSeen,
           u.last_interaction AS lastInteraction, 'dm' AS source
    FROM users u WHERE u.id = ?
  `).get(seekerId) as Seeker | undefined;
  if (!uRow) return null;

  uRow.fbProfileUrl = normalizeFbUrl(uRow.fbProfileUrl);
  uRow.leadStage = (uRow.phone && uRow.phone.trim() !== '') ? 'Seeker' : 'User';

  // Use thread_id for message lookups
  let messages = db.prepare(`
    SELECT id, thread_id AS threadId, sender, content,
           message_timestamp AS messageTimestamp, seq, timestamp
    FROM messages WHERE thread_id = ? ORDER BY id ASC
  `).all(uRow.threadId) as MessageRow[];
  
  messages = messages.map(r => ({
    ...r,
    sender: normalizeMessageSender(r.content, r.sender)
  }));

  // Check for ad source
  const adMsg = messages.find(m => m.content?.includes('[AD SOURCE]'));
  let adSource: SeekerDetail['adSource'] = null;
  if (adMsg) {
    const content = adMsg.content || '';
    // Try to match to a known post
    const postNameCache = tableExists('posts') ? db.prepare(`SELECT id, post_name FROM posts WHERE post_name IS NOT NULL`).all() as { id: string; post_name: string }[] : [];
    let matchedPost: { id: string; post_name: string } | undefined;
    for (const post of postNameCache) {
      const matchKey = post.post_name.slice(0, 60);
      if (content.includes(matchKey)) {
        matchedPost = post;
        break;
      }
    }
    adSource = {
      content,
      matchedPostId: matchedPost?.id,
      matchedPostName: matchedPost?.post_name,
    };
  }

  // Check if this DM user also commented (cross-channel)
  let comments: (CommentRow & { postName?: string; postUrl?: string })[] = [];
  if (tableExists('comments') && tableExists('posts')) {
    comments = db.prepare(`
      SELECT c.*, p.post_name AS postName, p.post_url AS postUrl
      FROM comments c
      JOIN posts p ON c.post_id = p.id
      WHERE c.commenter_name = ?
      ORDER BY c.timestamp ASC
    `).all(uRow.name) as (CommentRow & { postName?: string; postUrl?: string })[];
  }

  const lastMsg = messages.length > 0 ? messages[messages.length - 1] : null;
  uRow.lastMessageTimestampText = lastMsg?.messageTimestamp || null;
  const dmRealTime = parseRealDate(lastMsg?.messageTimestamp) || parseRealDate(lastMsg?.timestamp) || parseRealDate(uRow.lastInteraction) || parseRealDate(uRow.firstSeen) || 0;
  uRow.lastMessageDate = dmRealTime > 0 ? new Date(dmRealTime).toISOString() : null;

  return {
    seeker: uRow,
    messages,
    comments,
    adSource,
    messageCount: messages.length,
    commentCount: comments.length,
  };
}

// ── Graph Data: Page → Cities → Ad Groups → Posts → Users ──

const CITIES = ['Hà Nội', 'Bắc Ninh', 'Hải Phòng', 'Hưng Yên', 'Nghệ An', 'Đà Nẵng', 'TP. Hồ Chí Minh', 'Huế', 'Hội An', 'Online'];

// Hardcoded fallback map for active Ad campaigns where the Messenger API 
// entirely fails to return the organic post text inside the chat log payload.
const KNOWN_AD_TITLES: Record<string, string> = {
  '6908777851414': '[Đà Nẵng] 3 giờ sáng — bạn lại thức giấc. Có cách nào khác không? 3 giờ sáng. Bạn lại thức giấc. Đầu óc chạy vòng vòng —...',
  '6892367141614': '[Hà Nội] LỚP THIỀN miễn phí hàng tuần dành cho người mới tại Vương Thừa Vũ',
  '6930299765389': '[Hà Nội] LỚP THIỀN miễn phí hàng tuần dành cho người mới tại Vương Thừa Vũ',
  '6910952274814': 'Ngủ đủ giấc mà vẫn mệt? Bạn đang mất cân bằng. Bạn ngủ đủ giấc — mà sáng dậy vẫn mệt? Bạn không ốm — nhưng cũng chẳng thấy khoẻ?',
  '6590531354214': '🌿 Chương trình Thiền & Âm nhạc MIỄN PHÍ tại Đà Nẵng, Hội An và Huế – Tháng 4/2026 🎶',
  '6880610198214': '🌿 Chương trình Thiền & Âm nhạc MIỄN PHÍ tại Đà Nẵng, Hội An và Huế – Tháng 4/2026 🎶',
};

// code:web-db-002:city-detect-graph
// City keywords matching – mirrors Python CITY_KEYWORDS for consistency
const CITY_ABBREVIATIONS: Record<string, string[]> = {
  'TP. Hồ Chí Minh': ['HCM', 'TPHCM', 'TP.HCM', 'Hồ Chí Minh', 'Sài Gòn', 'Saigon', 'Xô Viết Nghệ Tĩnh', 'Bình Thạnh'],
  'Hà Nội': ['Ha Noi', 'Vương Thừa Vũ', 'Khương Đình', 'Thanh Xuân', 'Cầu Giấy', 'Đống Đa'],
  'Đà Nẵng': ['Da Nang'],
  'Huế': ['Hue'],
  'Hội An': ['Hoi An'],
  'Nghệ An': ['Nghe An', 'Vinh'],
  'Hải Phòng': ['Hai Phong'],
  'Online': ['online', 'ONLINE', 'zoom', 'Zoom', 'trực tuyến'],
};

export interface GraphNode {
  id: string;
  name: string;
  type: 'page' | 'city' | 'post' | 'ad' | 'user';
  val: number;
  color: string;
  fbUrl?: string;
  phone?: string;
  dbId?: number;  // users.id for /seekers/[id] routing
}

export interface GraphLink {
  source: string;
  target: string;
}

export interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}

export function getGraphData(filter?: { city?: string; startDate?: string; endDate?: string }): GraphData {
  const db = getDb();
  const nodes: GraphNode[] = [];
  const links: GraphLink[] = [];
  const nodeIds = new Set<string>();

  const targetCity = filter?.city && filter.city !== 'all' ? filter.city : null;
  const commentRangeParams: string[] = [];
  const dmRangeParams: string[] = [];
  const rangeClauses = (column: string, params: string[]) => {
    const clauses: string[] = [];
    if (filter?.startDate) { clauses.push(`AND datetime(${column}) >= datetime(?)`); params.push(filter.startDate); }
    if (filter?.endDate) { clauses.push(`AND datetime(${column}) <= datetime(?)`); params.push(`${filter.endDate} 23:59:59.999`); }
    return clauses.join(' ');
  };
  const commentRangePredicate = rangeClauses('COALESCE(c.comment_date, c.timestamp)', commentRangeParams);
  const dmRangePredicate = rangeClauses('u.last_interaction', dmRangeParams);

  const addNode = (node: GraphNode) => {
    if (!nodeIds.has(node.id)) {
      nodeIds.add(node.id);
      nodes.push(node);
    }
  };

  // Root: Page
  const pageId = 'page-root';
  addNode({ id: pageId, name: 'Thiền Sahaja Yoga Việt Nam', type: 'page', val: 30, color: '#10b981' });

  // City nodes
  for (const city of CITIES) {
    if (targetCity && targetCity !== city) continue;
    const cityId = `city-${city}`;
    addNode({ id: cityId, name: city, type: 'city', val: 20, color: '#3b82f6' });
    links.push({ source: pageId, target: cityId });
  }
  // Unknown city
  if (!targetCity || targetCity === 'Unknown') {
    const unknownCityId = 'city-Unknown';
    addNode({ id: unknownCityId, name: 'Other / Unknown', type: 'city', val: 15, color: '#6b7280' });
    links.push({ source: pageId, target: unknownCityId });
  }

  // Helper: detect city from text content (post name, ad content, etc)
  function detectCityFromText(text: string): string {
    for (const city of CITIES) {
      if (text.includes(city)) return city;
      // Check abbreviations
      const abbrevs = CITY_ABBREVIATIONS[city];
      if (abbrevs) {
        for (const abbr of abbrevs) {
          if (text.includes(abbr)) return city;
        }
      }
    }
    return 'Unknown';
  }

  // Get ALL posts that have comments
  let allPosts: { id: string; post_name: string; commenter_count: number }[] = [];
  if (tableExists('posts') && tableExists('comments')) {
    allPosts = db.prepare(`
      SELECT p.id, p.post_name,
             COUNT(DISTINCT c.commenter_name) AS commenter_count
      FROM posts p
      JOIN comments c ON c.post_id = p.id
      WHERE c.commenter_name != ?
      ${commentRangePredicate}
      GROUP BY p.id
      ORDER BY commenter_count DESC
    `).all(PAGE_NAME, ...commentRangeParams) as { id: string; post_name: string; commenter_count: number }[];
  }

  for (const post of allPosts) {
    const postCity = detectCityFromText(post.post_name || '');
    if (targetCity && postCity !== targetCity) continue;
    const cityId = `city-${postCity}`;
    const postNodeId = `post-${post.id}`;

    addNode({
      id: postNodeId,
      name: (post.post_name || `Post ${post.id.slice(0, 8)}`).slice(0, 60) + '...',
      type: 'post',
      val: Math.min(5 + post.commenter_count * 2, 15),
      color: '#f59e0b',
    });
    links.push({ source: cityId, target: postNodeId });

    // Get ALL actual commenters on this post (from comments table, not comment_users)
    const commenters = tableExists('comments') ? db.prepare(`
      SELECT DISTINCT c.commenter_name
      FROM comments c
      WHERE c.post_id = ? AND c.commenter_name != ?
      ${commentRangePredicate}
    `).all(post.id, PAGE_NAME, ...commentRangeParams) as { commenter_name: string }[] : [];

    for (const commenter of commenters) {
      // Look up user in comment_users for FB URL / phone data
      const userInfo = tableExists('comment_users') ? db.prepare(`
        SELECT cu.fb_profile_url, cu.phone
        FROM comment_users cu
        WHERE cu.commenter_name = ?
        LIMIT 1
      `).get(commenter.commenter_name) as { fb_profile_url: string | null; phone: string | null } | undefined : undefined;

      const userNodeId = `user-${commenter.commenter_name}`;
      addNode({
        id: userNodeId,
        name: commenter.commenter_name,
        type: 'user',
        val: 5,
        color: '#8b5cf6',
        fbUrl: userInfo?.fb_profile_url ? normalizeFbUrl(userInfo.fb_profile_url) ?? undefined : undefined,
        phone: userInfo?.phone ?? undefined,
      });
      links.push({ source: postNodeId, target: userNodeId });
    }
  }

  // Add DM users — link via ad_id grouping when available
  // code:web-db-002:ad-city-enrichment
  const dmUsers = db.prepare(`
    SELECT u.id AS db_id, u.thread_name, u.fb_url, u.phone,
           COALESCE(ap.city, u.city, 'Unknown') AS city,
           t.id AS thread_id,
           uai.ad_id, ap.ad_content
    FROM users u
    JOIN threads t ON u.thread_id = t.id
    LEFT JOIN user_ad_ids uai ON uai.thread_id = t.id
    LEFT JOIN ad_posts ap ON ap.ad_id = uai.ad_id
    WHERE u.thread_name IS NOT NULL AND u.thread_name != ?
    ${dmRangePredicate}
    ORDER BY u.last_interaction DESC
  `).all(PAGE_NAME, ...dmRangeParams) as { db_id: number; thread_name: string; fb_url: string | null; phone: string | null; city: string; thread_id: string; ad_id: string | null; ad_content: string | null }[];

  // Cache all post names for ad→post fuzzy matching
  const postNameCache = tableExists('posts') ? db.prepare(`SELECT id, post_name FROM posts WHERE post_name IS NOT NULL`).all() as { id: string; post_name: string }[] : [];

  // Group users by ad_id to create ad grouping nodes
  const adGroupMap = new Map<string, { ad_id: string; city: string; ad_content: string | null; users: typeof dmUsers }>();
  const noAdUsers: typeof dmUsers = [];
  // Track which users we've already processed (dedup by thread_name)
  const processedDmUsers = new Set<string>();

  // Helper to extract clean post text from raw ad_content notifications
  function getCleanAdSnippet(adId: string | null, content: string | null): string {
    // 0. Use explicit mapping if FB completely omitted the post from the chat log
    if (adId && KNOWN_AD_TITLES[adId]) {
      return KNOWN_AD_TITLES[adId];
    }

    if (!content) return 'Unknown Ad';

    // Compress whitespace/newlines for fuzzy matching
    const normalizedContent = content.replace(/\s+/g, ' ');

    // 1. Try to match with known posts to get the exact organic post name
    for (const post of postNameCache) {
      if (!post.post_name) continue;
      
      const normalizedPostName = post.post_name.replace(/\s+/g, ' ');
      // Take first 30 chars of normalized post name as match key
      const matchKey = normalizedPostName.slice(0, 30);
      
      // If we find a match, return up to 120 chars of the actual clean post name
      if (normalizedContent.includes(matchKey)) {
        return post.post_name.length > 120 
          ? post.post_name.slice(0, 120) + '…' 
          : post.post_name;
      }
    }

    // 2. Fallback: clean up FB notification boilerplate
    let cleaned = content;
    cleaned = cleaned.replace(/This chat contains a reply to your ad\.?/gi, '');
    cleaned = cleaned.replace(/.*đã trả lời về một bài viết\. Xem bài viết/gi, '');
    cleaned = cleaned.replace(/^[0-9\/, a-zA-Z:-]+(?:AM|PM)?\s*/i, ''); // Strip leading timestamps
    
    // Clean up typical form/chat lines that aren't real posts
    cleaned = cleaned.replace(/Chào.*Bạn đang quan tâm tới lớp thiền.*/gi, '');
    cleaned = cleaned.replace(/Tên đầy đủ của bạn là gì\?/gi, '');
    cleaned = cleaned.replace(/Sắp xong rồi! Hãy trả lời nốt câu hỏi.*/gi, '');
    cleaned = cleaned.replace(/Số điện thoại của bạn là gì\?/gi, '');
    cleaned = cleaned.replace(/Địa chỉ email của bạn là gì\?/gi, '');
    cleaned = cleaned.replace(/Send answer automatically next time\? Save this response/gi, '');
    
    cleaned = cleaned.trim().replace(/\s+/g, ' ');
    if (!cleaned || cleaned.length < 15) return `Ad Group (Hidden Post)`; // Generic fallback for useless chat fragments
    return cleaned.length > 120 ? cleaned.slice(0, 120) + '…' : cleaned;
  }

  for (const user of dmUsers) {
    if (processedDmUsers.has(user.thread_name)) continue;
    if (targetCity && user.city !== targetCity) continue;
    processedDmUsers.add(user.thread_name);

    if (user.ad_id) {
      const key = user.ad_id;
      if (!adGroupMap.has(key)) {
        // Detect city from ad_content if user.city is still Unknown
        let adCity = user.city;
        if (adCity === 'Unknown' && user.ad_content) {
          adCity = detectCityFromText(user.ad_content);
        }
        adGroupMap.set(key, { ad_id: user.ad_id, city: adCity, ad_content: user.ad_content, users: [] });
      }
      adGroupMap.get(key)!.users.push(user);
    } else {
      noAdUsers.push(user);
    }
  }

  // Create ad_id grouping nodes: City → Ad(ad_id) → Users
  for (const [adId, group] of adGroupMap) {
    const adCity = detectCityFromText(group.city || '');
    const targetCityId = `city-${adCity}`;
    const adNodeId = `ad-${adId}`;

    // Create ad grouping node with a clean snippet of the post content
    const adSnippet = getCleanAdSnippet(adId, group.ad_content);

    addNode({
      id: adNodeId,
      name: adSnippet,
      type: 'ad',
      val: Math.min(5 + group.users.length * 2, 15),
      color: '#f97316',  // Orange-500 to distinguish from regular posts
    });
    links.push({ source: targetCityId, target: adNodeId });

    // Link users to their ad group
    for (const user of group.users) {
      const userNodeId = `dm-user-${user.thread_name}`;
      addNode({
        id: userNodeId,
        name: user.thread_name,
        type: 'user',
        val: 5,
        color: '#ec4899',
        fbUrl: user.fb_url ? normalizeFbUrl(user.fb_url) ?? undefined : undefined,
        phone: user.phone ?? undefined,
        dbId: user.db_id,
      });
      links.push({ source: adNodeId, target: userNodeId });
    }
  }

  // Handle DM users without ad associations — same as before, try to link via message content
  for (const user of noAdUsers) {
    const userCity = detectCityFromText(user.city || '');
    const targetCityId = `city-${userCity}`;
    const userNodeId = `dm-user-${user.thread_name}`;

    addNode({
      id: userNodeId,
      name: user.thread_name,
      type: 'user',
      val: 5,
      color: '#ec4899',
      fbUrl: user.fb_url ? normalizeFbUrl(user.fb_url) ?? undefined : undefined,
      phone: user.phone ?? undefined,
      dbId: user.db_id,
    });

    // Try to match DM ad source to a post
    const adMessages = db.prepare(`
      SELECT m.content FROM messages m
      WHERE m.thread_id = ? AND m.content LIKE '%AD SOURCE%'
      LIMIT 1
    `).all(user.thread_id) as { content: string }[];

    let linkedToPost = false;
    if (adMessages.length > 0) {
      const adContent = adMessages[0].content;
      // Match ad content to a post by checking if post_name text appears in the ad
      for (const post of postNameCache) {
        const matchKey = post.post_name.slice(0, 60);
        if (adContent.includes(matchKey)) {
          const postNodeId = `post-${post.id}`;
          if (!nodeIds.has(postNodeId)) {
            const postCity = detectCityFromText(post.post_name);
            addNode({
              id: postNodeId,
              name: post.post_name.slice(0, 60) + '...',
              type: 'post',
              val: 8,
              color: '#f59e0b',
            });
            links.push({ source: `city-${postCity}`, target: postNodeId });
          }
          links.push({ source: postNodeId, target: userNodeId });
          linkedToPost = true;
          break;
        }
      }
    }

    // Fallback: link to city if no post match
    if (!linkedToPost) {
      links.push({ source: targetCityId, target: userNodeId });
    }
  }

  return { nodes, links };
}

// ── Stats ──

export function getDashboardStats() {
  const db = getDb();
  const totalDMUsers = tableExists('users') ? (db.prepare('SELECT COUNT(*) AS c FROM users').get() as { c: number }).c : 0;
  const totalCommentUsers = tableExists('comment_users') ? (db.prepare('SELECT COUNT(*) AS c FROM comment_users').get() as { c: number }).c : 0;
  const totalPosts = tableExists('posts') ? (db.prepare('SELECT COUNT(*) AS c FROM posts').get() as { c: number }).c : 0;
  const totalMessages = tableExists('messages') ? (db.prepare('SELECT COUNT(*) AS c FROM messages').get() as { c: number }).c : 0;
  const totalComments = tableExists('comments') ? (db.prepare('SELECT COUNT(*) AS c FROM comments').get() as { c: number }).c : 0;
  const totalThreads = tableExists('threads') ? (db.prepare('SELECT COUNT(*) AS c FROM threads').get() as { c: number }).c : 0;

  // Stage counts using same dedup logic as getAllSeekers
  const seekers = getAllSeekers();
  const userCount = seekers.filter(s => s.leadStage === 'User').length;
  const seekerCount = seekers.filter(s => s.leadStage === 'Seeker').length;

  return {
    totalSeekers: seekers.length,
    totalDMUsers,
    totalCommentUsers,
    totalPosts,
    totalMessages,
    totalComments,
    totalThreads,
    userStageCount: userCount,
    seekerStageCount: seekerCount,
  };
}
