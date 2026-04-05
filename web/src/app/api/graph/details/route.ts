import { NextResponse } from 'next/server';
import Database from 'better-sqlite3';
import path from 'path';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url);
    const id = searchParams.get('id');
    const type = searchParams.get('type');

    if (!id || !type) {
      return NextResponse.json({ error: 'Missing id or type parameters' }, { status: 400 });
    }

    const dbPath = path.join(process.cwd(), '..', 'memory', 'agent_memory', 'frankensqlite.db');
    const db = new Database(dbPath, { readonly: true });
    
    // Default page ID
    const PAGE_NAME = '1548373332058326';

    if (type === 'user') {
      // 1. Get User Profile info
      const userInfo = db.prepare(`
        SELECT u.thread_name, u.phone, u.email, u.fb_url, u.city, u.lead_stage, 
               u.first_seen, u.last_interaction, t.id as thread_id
        FROM users u
        JOIN threads t ON u.thread_id = t.id
        WHERE u.thread_name = ?
      `).get(id) as any;

      if (!userInfo) {
        db.close();
        return NextResponse.json({ error: 'User not found' }, { status: 404 });
      }

      // 2. Expand fb_url if needed
      let fullFbUrl = userInfo.fb_url;
      if (fullFbUrl && !fullFbUrl.startsWith('http')) {
        fullFbUrl = `https://facebook.com/${fullFbUrl}`;
      }
      userInfo.fb_url = fullFbUrl;

      // 3. Get message history for this user thread
      const messages = db.prepare(`
        SELECT sender, content, message_timestamp 
        FROM messages 
        WHERE thread_id = ?
        ORDER BY message_timestamp ASC, seq ASC
        LIMIT 50
      `).all(userInfo.thread_id);

      db.close();
      return NextResponse.json({
        profile: userInfo,
        messages: messages
      });
    } 
    
    else if (type === 'post' || type === 'ad') {
      // For both ad clusters and organic posts, first try to resolve to a known post
      let postId = id;
      let adContent = null;
      
      if (type === 'ad') {
        const adInfo = db.prepare(`SELECT post_id, ad_content FROM ad_posts WHERE ad_id = ?`).get(id) as any;
        if (adInfo) {
          if (adInfo.post_id) {
            postId = adInfo.post_id;
          }
          adContent = adInfo.ad_content;
        }
      }

      // 1. Get post details
      const postInfo = db.prepare(`
        SELECT post_name, post_url, created_at, last_synced_time
        FROM posts
        WHERE id = ?
      `).get(postId) as any;

      // 2. Get comments for this post
      let comments: any[] = [];
      let commentStats: any = { total: 0, unique_users: 0 };
      
      if (postInfo) {
        comments = db.prepare(`
          SELECT commenter_name, comment_text, comment_timestamp, is_reply
          FROM comments
          WHERE post_id = ? AND commenter_name != ?
          ORDER BY comment_timestamp ASC
          LIMIT 50
        `).all(postId, PAGE_NAME);
        
        const statsRow = db.prepare(`
          SELECT COUNT(id) as total, COUNT(DISTINCT commenter_name) as unique_users
          FROM comments
          WHERE post_id = ? AND commenter_name != ?
        `).get(postId, PAGE_NAME) as any;
        
        if (statsRow) {
          commentStats = statsRow;
        }
      }

      db.close();
      return NextResponse.json({
        post: postInfo || { post_name: type === 'ad' ? (adContent || 'Ad Content Hidden') : 'Unknown Post', is_orphan: true },
        stats: commentStats,
        comments: comments
      });
    }

    db.close();
    return NextResponse.json({ error: 'Invalid type parameter' }, { status: 400 });

  } catch (error) {
    console.error('Error in /api/graph/details route:', error);
    return NextResponse.json(
      { error: 'Internal server error while fetching details' },
      { status: 500 }
    );
  }
}
