// code:web-api-005:graph-details
import { NextResponse } from 'next/server';
import { query, queryOne } from '@/lib/db';

export const dynamic = 'force-dynamic';

const PAGE_NAME = '1548373332058326';

const KNOWN_AD_TITLES: Record<string, string> = {
  '6908777851414': '[Đà Nẵng] 3 giờ sáng — bạn lại thức giấc. Có cách nào khác không? 3 giờ sáng. Bạn lại thức giấc. Đầu óc chạy vòng vòng —...',
  '6892367141614': '[Hà Nội] LỚP THIỀN miễn phí hàng tuần dành cho người mới tại Vương Thừa Vũ',
  '6930299765389': '[Hà Nội] LỚP THIỀN miễn phí hàng tuần dành cho người mới tại Vương Thừa Vũ',
  '6910952274814': 'Ngủ đủ giấc mà vẫn mệt? Bạn đang mất cân bằng. Bạn ngủ đủ giấc — mà sáng dậy vẫn mệt? Bạn không ốm — nhưng cũng chẳng thấy khoẻ?',
  '6590531354214': '🌿 Chương trình Thiền & Âm nhạc MIỄN PHÍ tại Đà Nẵng, Hội An và Huế – Tháng 4/2026 🎶',
  '6880610198214': '🌿 Chương trình Thiền & Âm nhạc MIỄN PHÍ tại Đà Nẵng, Hội An và Huế – Tháng 4/2026 🎶',
};

const tableCache = new Map<string, boolean>();
async function tableExists(tableName: string): Promise<boolean> {
  if (tableCache.has(tableName)) return tableCache.get(tableName)!;
  try {
    const row = await queryOne('SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ?', [tableName]);
    const exists = !!row; tableCache.set(tableName, exists); return exists;
  } catch {
    return false;
  }
}

function cleanAdSnippet(adId: string, content: string | null): string {
  if (KNOWN_AD_TITLES[adId]) {
    return KNOWN_AD_TITLES[adId];
  }
  if (!content) return `Ad Campaign #${adId}`;

  let cleaned = content;
  cleaned = cleaned.replace(/This chat contains a reply to your ad\.?/gi, '');
  cleaned = cleaned.replace(/This chat contains a reply to Thiền Sahaja Yoga Việt Nam\.?/gi, '');
  cleaned = cleaned.replace(/.*đã trả lời về một bài viết\. Xem bài viết/gi, '');
  cleaned = cleaned.replace(/^[0-9\/, a-zA-Z:-]+(?:AM|PM)?\s*/i, '');
  cleaned = cleaned.replace(/.*replied to an ad\.?/gi, '');
  cleaned = cleaned.replace(/Chào.*Bạn đang quan tâm tới lớp thiền.*/gi, '');
  cleaned = cleaned.replace(/Tên đầy đủ của bạn là gì\?.*/gi, '');
  cleaned = cleaned.replace(/Sắp xong rồi! Hãy trả lời nốt câu hỏi.*/gi, '');
  cleaned = cleaned.replace(/Số điện thoại của bạn là gì\?.*/gi, '');
  cleaned = cleaned.replace(/Địa chỉ email của bạn là gì\?.*/gi, '');
  cleaned = cleaned.replace(/Send answer automatically next time\? Save this response.*/gi, '');
  cleaned = cleaned.trim().replace(/\s+/g, ' ');

  if (!cleaned || cleaned.length < 15) {
    return `Ad Campaign #${adId}`;
  }
  return cleaned.length > 120 ? cleaned.slice(0, 120) + '…' : cleaned;
}

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const id = searchParams.get('id');
    const type = searchParams.get('type');

    if (!id || !type) {
      return NextResponse.json({ error: 'Missing id or type parameters' }, { status: 400 });
    }

    if (type === 'user') {
      interface UserInfoRow {
        thread_name: string;
        phone: string | null;
        email: string | null;
        fb_url: string | null;
        city: string | null;
        lead_stage: string | null;
        first_seen: string | null;
        last_interaction: string | null;
        thread_id: string;
      }

      let userInfo: UserInfoRow | undefined = undefined;

      if (await tableExists('users') && await tableExists('threads')) {
        userInfo = await queryOne<UserInfoRow>(`
          SELECT u.thread_name, u.phone, u.email, u.fb_url, u.city, u.lead_stage, 
                 u.first_seen, u.last_interaction, t.id as thread_id
          FROM users u
          JOIN threads t ON u.thread_id = t.id
          WHERE u.thread_name = ?
          ORDER BY u.last_interaction DESC
          LIMIT 1
        `, [id]);

        if (!userInfo && /^\d+$/.test(id)) {
          userInfo = await queryOne<UserInfoRow>(`
            SELECT u.thread_name, u.phone, u.email, u.fb_url, u.city, u.lead_stage, 
                   u.first_seen, u.last_interaction, t.id as thread_id
            FROM users u
            JOIN threads t ON u.thread_id = t.id
            WHERE u.id = ?
            LIMIT 1
          `, [parseInt(id, 10)]);
        }
      }

      // Fallback: check comment_users if not found in DM users
      if (!userInfo && await tableExists('comment_users')) {
        interface CommentUserRow {
          commenter_name: string;
          fb_profile_url: string | null;
          phone: string | null;
          city: string | null;
          first_seen: string | null;
          last_seen: string | null;
        }
        const cu = await queryOne<CommentUserRow>(`
          -- PostgreSQL preserves the canonical comment_users field name
          -- (last_interaction); SQLite's old UI fallback called it last_seen.
          SELECT commenter_name, fb_profile_url, phone, city, first_seen,
                 last_interaction AS last_seen
          FROM comment_users
          WHERE commenter_name = ?
          LIMIT 1
        `, [id]);

        if (cu) {
          userInfo = {
            thread_name: cu.commenter_name,
            phone: cu.phone,
            email: null,
            fb_url: cu.fb_profile_url,
            city: cu.city,
            lead_stage: 'Intake',
            first_seen: cu.first_seen,
            last_interaction: cu.last_seen,
            thread_id: '',
          };
        }
      }

      if (!userInfo) {
        return NextResponse.json({ error: 'User not found' }, { status: 404 });
      }

      let fullFbUrl = userInfo.fb_url;
      if (fullFbUrl && !fullFbUrl.startsWith('http')) {
        fullFbUrl = `https://facebook.com/${fullFbUrl}`;
      }
      userInfo.fb_url = fullFbUrl;

      let messages: { sender: string; content: string; message_timestamp: string; message_at: string | null }[] = [];
      if (userInfo.thread_id && await tableExists('messages')) {
        messages = await query(`
          SELECT sender, content, message_timestamp, message_at
          FROM messages 
          WHERE thread_id = ? AND kind = 'message'
          ORDER BY COALESCE(message_at, timestamp) ASC, seq ASC
          LIMIT 50
        `, [userInfo.thread_id]) as { sender: string; content: string; message_timestamp: string; message_at: string | null }[];
      }

      return NextResponse.json({
        profile: userInfo,
        messages,
      });
    }

    if (type === 'ad') {
      interface AdRow {
        ad_id: string;
        post_id: string | null;
        ad_content: string | null;
        city: string | null;
        resolved_at: string | null;
      }

      let adRow: AdRow | undefined = undefined;
      if (await tableExists('ad_posts')) {
        adRow = await queryOne<AdRow>(`
          SELECT ad_id, post_id, ad_content, city, resolved_at 
          FROM ad_posts 
          WHERE ad_id = ?
        `, [id]);
      }

      const cleanTitle = cleanAdSnippet(id, adRow?.ad_content || null);
      let postInfo: { post_name: string | null; post_url: string | null; created_at: string | null; last_synced_time: string | null; is_orphan?: boolean } | null = null;
      let comments: { commenter_name: string; comment_text: string; comment_timestamp: string; is_reply: number }[] = [];
      let commentStats: { total: number; unique_users: number } = { total: 0, unique_users: 0 };

      // Check linked organic post if post_id exists and posts table is available
      const linkedPostId = adRow?.post_id;
      if (linkedPostId && await tableExists('posts')) {
        postInfo = await queryOne<NonNullable<typeof postInfo>>(`
          SELECT post_name, post_url, created_at, last_synced_time
          FROM posts
          WHERE id = ?
        `, [linkedPostId]) ?? null;
      }

      // Check comments from linked post if available
      if (linkedPostId && await tableExists('comments')) {
        comments = await query(`
          SELECT commenter_name, comment_text, comment_timestamp, is_reply
          FROM comments
          WHERE post_id = ? AND commenter_name != ?
          ORDER BY comment_timestamp ASC
          LIMIT 50
        `, [linkedPostId, PAGE_NAME]) as typeof comments;

        const statsRow = await queryOne<{ total: number; unique_users: number }>(`
          SELECT COUNT(id) as total, COUNT(DISTINCT commenter_name) as unique_users
          FROM comments
          WHERE post_id = ? AND commenter_name != ?
        `, [linkedPostId, PAGE_NAME]);

        if (statsRow) {
          commentStats = statsRow;
        }
      }

      // If no post comments found, retrieve seekers associated with this ad via user_ad_ids
      if (comments.length === 0 && await tableExists('user_ad_ids') && await tableExists('users') && await tableExists('threads')) {
        const adSeekersStats = await queryOne<{ seeker_count: number; thread_count: number }>(`
          SELECT 
            COUNT(DISTINCT u.thread_name) as seeker_count,
            COUNT(DISTINCT uai.thread_id) as thread_count
          FROM user_ad_ids uai
          JOIN threads t ON uai.thread_id = t.id
          JOIN users u ON u.thread_id = t.id
          WHERE uai.ad_id = ? AND u.thread_name IS NOT NULL AND u.thread_name != ?
        `, [id, PAGE_NAME]);

        if (adSeekersStats) {
          commentStats = {
            total: adSeekersStats.thread_count || 0,
            unique_users: adSeekersStats.seeker_count || 0,
          };
        }

        interface SeekerInquiryRow {
          thread_name: string;
          city: string | null;
          phone: string | null;
          lead_stage: string | null;
          last_interaction: string | null;
          thread_id: string;
        }

        const recentSeekers = await query<SeekerInquiryRow>(`
          SELECT u.thread_name, u.city, u.phone, u.lead_stage, u.last_interaction, t.id as thread_id
          FROM user_ad_ids uai
          JOIN threads t ON uai.thread_id = t.id
          JOIN users u ON u.thread_id = t.id
          WHERE uai.ad_id = ? AND u.thread_name IS NOT NULL AND u.thread_name != ?
          ORDER BY u.last_interaction DESC
          LIMIT 10
        `, [id, PAGE_NAME]);

        const hasMessages = await tableExists('messages');
        comments = await Promise.all(recentSeekers.map(async s => {
          let msgText = '';
          if (hasMessages && s.thread_id) {
            const firstMsg = await queryOne<{ content: string }>(`
              SELECT content FROM messages 
              WHERE thread_id = ? AND sender != ? AND content IS NOT NULL AND TRIM(content) != ''
              ORDER BY message_timestamp ASC, seq ASC
              LIMIT 1
            `, [s.thread_id, PAGE_NAME]);
            if (firstMsg?.content) {
              msgText = firstMsg.content;
            }
          }
          if (!msgText) {
            const parts: string[] = [];
            if (s.phone) parts.push(`📞 ${s.phone}`);
            if (s.city) parts.push(`📍 ${s.city}`);
            if (s.lead_stage) parts.push(`Stage: ${s.lead_stage}`);
            msgText = parts.join(' | ') || 'Đã gửi tin nhắn từ quảng cáo';
          }
          return {
            commenter_name: s.thread_name,
            comment_text: msgText,
            comment_timestamp: s.last_interaction || '',
            is_reply: 0,
          };
        }));
      }

      return NextResponse.json({
        post: postInfo || {
          post_name: cleanTitle,
          post_url: null,
          created_at: adRow?.resolved_at || null,
          last_synced_time: null,
          is_orphan: true,
        },
        stats: commentStats,
        comments,
      });
    }

    if (type === 'post') {
      interface PostRow {
        post_name: string | null;
        post_url: string | null;
        created_at: string | null;
        last_synced_time: string | null;
      }
      let postInfo: PostRow | undefined = undefined;
      if (await tableExists('posts')) {
        postInfo = await queryOne<PostRow>(`
          SELECT post_name, post_url, created_at, last_synced_time
          FROM posts
          WHERE id = ?
        `, [id]);
      }

      interface CommentRow {
        commenter_name: string;
        comment_text: string;
        comment_timestamp: string;
        is_reply: number;
      }
      let comments: CommentRow[] = [];
      let commentStats: { total: number; unique_users: number } = { total: 0, unique_users: 0 };

      if (await tableExists('comments')) {
        comments = await query<CommentRow>(`
          SELECT commenter_name, comment_text, comment_timestamp, is_reply
          FROM comments
          WHERE post_id = ? AND commenter_name != ?
          ORDER BY comment_timestamp ASC
          LIMIT 50
        `, [id, PAGE_NAME]);

        const statsRow = await queryOne<{ total: number; unique_users: number }>(`
          SELECT COUNT(id) as total, COUNT(DISTINCT commenter_name) as unique_users
          FROM comments
          WHERE post_id = ? AND commenter_name != ?
        `, [id, PAGE_NAME]);

        if (statsRow) {
          commentStats = statsRow;
        }
      }

      return NextResponse.json({
        post: postInfo || {
          post_name: `Post ${id.length > 12 ? id.slice(0, 12) + '...' : id}`,
          post_url: null,
          created_at: null,
          last_synced_time: null,
          is_orphan: true,
        },
        stats: commentStats,
        comments,
      });
    }

    return NextResponse.json({ error: 'Invalid type parameter' }, { status: 400 });
  } catch (error) {
    console.error('Error in /api/graph/details route:', error);
    return NextResponse.json(
      { error: 'Internal server error while fetching details' },
      { status: 500 }
    );
  }
}
