import { NextRequest, NextResponse } from 'next/server';
import { getDb } from '@/lib/db';

const DEFAULT_PAGE_ID = '1548373332058326';

const WARMUP_TEMPLATES: Record<string, string> = {
  Intake: 'Xin chào bạn! Chúng mình có các lớp thiền Sahaja Yoga hoàn toàn MIỄN PHÍ hàng tuần. Bạn có muốn tìm hiểu thêm về thời gian và địa điểm không ạ? 🧘',
  Seeker: 'Chào bạn! Khóa học thiền Sahaja Yoga căn bản sắp bắt đầu buổi tiếp theo. Không biết bạn đã sắp xếp được thời gian tham gia chưa ạ? 🙏',
  Seeker_Public_Program: 'Chào bạn! Tuần này chúng mình có buổi thiền cộng đồng đặc biệt. Rất mong được đón tiếp bạn tham gia cùng mọi người nhé! 🌸',
  Seeker_18_Weeks: 'Chào bạn! Nhóm thiền 18 tuần rất nhớ bạn. Hãy tiếp tục duy trì thực hành đều đặn để cảm nhận sự bình an sâu sắc nhé! ✨',
  Seed: 'Chào bạn! Chúc mừng bạn đã hoàn thành khóa học căn bản. Bạn có muốn tham gia buổi thiền tập thể tuần này để củng cố thói quen thiền định không ạ? 🌿',
};

export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));
    const {
      type = 'all',
      threadId,
      seekerName,
      city,
      limit = 5,
    } = body;

    const db = getDb();
    const created: { id: number; queueType: string; targetId: string; targetName: string; actionText: string }[] = [];

    // Helper to insert into action_queue safely as pending
    const insertProposal = (
      queueType: string,
      targetType: string,
      targetId: string,
      targetName: string,
      actionText: string,
      payload: Record<string, unknown> = {}
    ) => {
      // Check existing pending proposal
      const existing = db.prepare(`
        SELECT id FROM action_queue
        WHERE target_id = ? AND status = 'pending' AND queue_type = ?
      `).get(targetId, queueType);

      if (existing) return null;

      const result = db.prepare(`
        INSERT INTO action_queue (queue_type, page_id, target_type, target_id, target_name, action_text, payload_json, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now'), datetime('now'))
      `).run(
        queueType,
        DEFAULT_PAGE_ID,
        targetType,
        targetId,
        targetName,
        actionText,
        JSON.stringify(payload)
      );
      return Number(result.lastInsertRowid);
    };

    // 1. Reply message recommendations
    if (type === 'all' || type === 'reply') {
      let query = `
        SELECT t.id AS thread_id, t.thread_name, m.sender, m.content
        FROM threads t
        JOIN messages m ON m.id = (
          SELECT id FROM messages WHERE thread_id = t.id ORDER BY id DESC LIMIT 1
        )
        WHERE m.sender NOT IN ('Page', 'Auto_Page')
      `;
      const params: (string | number)[] = [];
      if (threadId) {
        query += ` AND t.id = ?`;
        params.push(threadId);
      }
      query += ` ORDER BY t.last_synced_time DESC LIMIT ?`;
      params.push(Number(limit));

      const unreplied = db.prepare(query).all(...params) as { thread_id: string; thread_name: string; content: string }[];
      for (const row of unreplied) {
        const name = row.thread_name || seekerName || 'Seeker';
        const replyText = `Chào bạn ${name}! Cảm ơn bạn đã nhắn tin cho Thiền Sahaja Yoga Việt Nam. Tụi mình có các lớp thiền hoàn toàn miễn phí tại Hà Nội, TP.HCM, Đà Nẵng và Online qua Zoom. Bạn muốn tham gia lớp học trực tiếp hay online ạ? 🙏`;
        const id = insertProposal('reply_message', 'thread', row.thread_id, name, replyText, {
          source: 'recommendation_engine',
          trigger: 'unreplied_message',
          last_content: row.content,
        });
        if (id) created.push({ id, queueType: 'reply_message', targetId: row.thread_id, targetName: name, actionText: replyText });
      }
    }

    // 2. Comment reply recommendations
    if ((type === 'all' || type === 'comment') && !threadId) {
      try {
        const comments = db.prepare(`
          SELECT c.id, c.commenter_name, c.comment_text, c.post_id
          FROM comments c
          WHERE c.commenter_name NOT LIKE '%Sahaja%'
          ORDER BY c.id DESC LIMIT ?
        `).all(Number(limit)) as { id: number; commenter_name: string; comment_text: string; post_id: string }[];

        for (const c of comments) {
          const name = c.commenter_name || 'Bạn';
          const replyText = `Dạ chào ${name}, lớp thiền Sahaja Yoga hoàn toàn miễn phí ạ. Page đã gửi thông tin chi tiết qua tin nhắn, bạn kiểm tra hộp thư giúp Page nhé! 🙏`;
          const id = insertProposal('reply_comment', 'comment', String(c.id), name, replyText, {
            source: 'recommendation_engine',
            post_id: c.post_id,
          });
          if (id) created.push({ id, queueType: 'reply_comment', targetId: String(c.id), targetName: name, actionText: replyText });
        }
      } catch {
        // comments table may be empty or not present
      }
    }

    // 3. Warm-up / Reminders recommendations
    if (type === 'all' || type === 'warmup') {
      let query = `
        SELECT u.thread_id, u.thread_name, u.city, u.lead_stage, u.last_interaction
        FROM users u
        WHERE u.thread_id IS NOT NULL
      `;
      const params: (string | number)[] = [];
      if (threadId) {
        query += ` AND u.thread_id = ?`;
        params.push(threadId);
      }
      query += ` ORDER BY u.last_interaction ASC LIMIT ?`;
      params.push(Number(limit));

      const dormant = db.prepare(query).all(...params) as { thread_id: string; thread_name: string; city: string; lead_stage: string }[];
      for (const u of dormant) {
        const name = u.thread_name || seekerName || 'Seeker';
        const stage = u.lead_stage || 'Intake';
        const warmupText = WARMUP_TEMPLATES[stage] || WARMUP_TEMPLATES.Intake;
        const id = insertProposal('proactive_message', 'thread', u.thread_id, name, warmupText, {
          source: 'recommendation_engine',
          type: 'warmup',
          stage,
          city: u.city,
        });
        if (id) created.push({ id, queueType: 'proactive_message', targetId: u.thread_id, targetName: name, actionText: warmupText });
      }
    }

    // 4. Event notification recommendations
    if (type === 'all' || type === 'event') {
      let eventTitle = 'Chương trình Thiền & Âm nhạc';
      let eventCity = city || 'Đà Nẵng';
      let eventDate = 'Chủ Nhật hàng tuần';
      try {
        const ev = db.prepare(`SELECT * FROM events ORDER BY id DESC LIMIT 1`).get() as { name?: string; city?: string; event_date?: string } | undefined;
        if (ev) {
          if (ev.name) eventTitle = ev.name;
          if (ev.city) eventCity = ev.city;
          if (ev.event_date) eventDate = ev.event_date;
        }
      } catch {
        // events table fallback
      }

      let query = `
        SELECT u.thread_id, u.thread_name, u.city, u.lead_stage
        FROM users u
        WHERE u.thread_id IS NOT NULL
      `;
      const params: (string | number)[] = [];
      if (threadId) {
        query += ` AND u.thread_id = ?`;
        params.push(threadId);
      } else if (city && city !== 'all') {
        query += ` AND (u.city = ? OR u.city = 'Unknown')`;
        params.push(city);
      }
      query += ` ORDER BY u.last_interaction DESC LIMIT ?`;
      params.push(Number(limit));

      const eventSeekers = db.prepare(query).all(...params) as { thread_id: string; thread_name: string; city: string }[];
      for (const u of eventSeekers) {
        const name = u.thread_name || seekerName || 'Seeker';
        const loc = u.city !== 'Unknown' ? u.city : eventCity;
        const eventText = `Chào bạn ${name}! Sắp tới Sahaja Yoga có sự kiện '${eventTitle}' tại ${loc} vào ngày ${eventDate}. Chương trình hoàn toàn miễn phí, kính mời bạn cùng người thân tham gia trải nghiệm thiền định ạ! 🧘✨`;
        const id = insertProposal('proactive_message', 'thread', u.thread_id, name, eventText, {
          source: 'recommendation_engine',
          type: 'event',
          eventTitle,
          city: loc,
        });
        if (id) created.push({ id, queueType: 'proactive_message', targetId: u.thread_id, targetName: name, actionText: eventText });
      }
    }

    // 5. Targeted proposal fallback for single seeker
    if ((threadId || seekerName) && created.length === 0) {
      const existing = db.prepare(`
        SELECT id, queue_type AS queueType, target_id AS targetId, target_name AS targetName, action_text AS actionText
        FROM action_queue
        WHERE (target_id = ? OR (target_name IS NOT NULL AND target_name = ?)) AND status = 'pending'
        LIMIT 1
      `).get(threadId || '', seekerName || '') as { id: number; queueType: string; targetId: string; targetName: string; actionText: string } | undefined;

      if (existing) {
        return NextResponse.json({
          success: true,
          count: 0,
          proposals: [existing],
          message: 'Đã có đề xuất đang chờ duyệt trong hàng đợi.',
        });
      }

      // Contextual recommendation generation based on user stage
      let u: { thread_id: string; thread_name: string; city: string; lead_stage: string } | undefined;
      try {
        u = db.prepare(`
          SELECT u.thread_id, u.thread_name, u.city, u.lead_stage
          FROM users u
          WHERE (u.thread_id = ? OR u.thread_name = ?)
          LIMIT 1
        `).get(threadId || '', seekerName || '') as typeof u;
      } catch {
        // Users table might have custom schema
      }

      const name = u?.thread_name || seekerName || 'Seeker';
      const stage = u?.lead_stage || 'Intake';
      const targetIdVal = u?.thread_id || threadId || `seeker-${name}`;
      const warmupText = WARMUP_TEMPLATES[stage] || WARMUP_TEMPLATES.Intake;
      const id = insertProposal('proactive_message', 'thread', targetIdVal, name, warmupText, {
        source: 'recommendation_engine',
        type: 'warmup',
        stage,
        city: u?.city || city || 'Unknown',
      });
      if (id) {
        created.push({ id, queueType: 'proactive_message', targetId: targetIdVal, targetName: name, actionText: warmupText });
      }
    }

    return NextResponse.json({
      success: true,
      count: created.length,
      proposals: created,
      message: `Đã tạo an toàn ${created.length} đề xuất mới vào hàng đợi chờ duyệt (status: pending).`,
    });
  } catch (error) {
    console.error('Recommendations error:', error);
    return NextResponse.json({ error: 'Lỗi khi tạo đề xuất MAS' }, { status: 500 });
  }
}
