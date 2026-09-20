import { NextRequest, NextResponse } from 'next/server';
import { execFile } from 'child_process';
import path from 'path';
import { execute, query as dbQuery, queryOne } from '@/lib/db';

const DEFAULT_PAGE_ID = '1548373332058326';
const PROJECT_ROOT = path.resolve(process.cwd(), '..');
const MAS_PYTHON = process.env.MAS_PYTHON || path.join(PROJECT_ROOT, '.venv', 'bin', 'python');
const MAS_SCRIPT = path.join(PROJECT_ROOT, 'tools', 'l5_mas_recommend.py');
const MAS_TIMEOUT_MS = Number(process.env.MAS_TIMEOUT_MS || 240_000);

type Proposal = { id: number; queueType: string; targetId: string; targetName: string; actionText: string; kind?: string; supersededIds?: number[] };
type MasResult = {
  status: 'ok' | 'error';
  error?: string;
  count?: number;
  proposals?: Proposal[];
  skipped?: { threadId: string; reason: string }[];
  supersededCount?: number;
};
type RecommendationJob = {
  id: number;
  status: 'queued' | 'running' | 'completed' | 'failed';
  phase: string;
  type: string;
  threadIds: string[];
  regenerate: boolean;
  createdAt: string;
  startedAt?: string | null;
  completedAt?: string | null;
  result?: Record<string, unknown> | null;
  error?: string | null;
};

// `mas_recommendation_jobs` is owned by the PostgreSQL migration DDL.  Routes
// must never create schema at request time: that caused SQLite/Web drift.

function formatJob(row: Record<string, unknown>): RecommendationJob {
  const request = JSON.parse(String(row.request_json || '{}')) as { type?: string; threadIds?: string[]; regenerate?: boolean };
  return {
    id: Number(row.id),
    status: row.status as RecommendationJob['status'],
    phase: String(row.phase || 'queued'),
    type: request.type || 'all',
    threadIds: request.threadIds || [],
    regenerate: Boolean(request.regenerate),
    createdAt: String(row.created_at),
    startedAt: row.started_at as string | null,
    completedAt: row.completed_at as string | null,
    result: row.result_json ? JSON.parse(String(row.result_json)) : null,
    error: row.error_text as string | null,
  };
}

// code:api-recommendations-001:mas-engine
// Spawns the Python MAS entrypoint (per-message Inbox MAS / WarmUpComposer / EventAdvertiser).
// The script only enqueues pending proposals; it never opens a browser or sends messages.
// With `regenerate` (queue-card "Chạy đề xuất MAS" on already-queued items) the
// script replaces the seeker's current pending/approved draft instead of skipping it.
function runMasEngine(opts: { type: string; threadIds: string[]; city?: string; programCode?: string; eventId?: string; instruction?: string; carePurpose?: string; regenerate?: boolean; jobId?: number }): Promise<MasResult> {
  const args = [MAS_SCRIPT, '--thread-ids', opts.threadIds.join(','), '--type', opts.type, '--page-id', DEFAULT_PAGE_ID];
  if (opts.jobId) args.push('--job-id', String(opts.jobId));
  if (opts.city && opts.city !== 'all') args.push('--city', opts.city);
  if (opts.programCode && opts.programCode !== 'all') args.push('--program-code', opts.programCode);
  if (opts.eventId) args.push('--event-id', opts.eventId);
  if (opts.instruction?.trim()) args.push('--instruction', opts.instruction.trim());
  if (opts.carePurpose) args.push('--purpose', opts.carePurpose);
  if (opts.regenerate) args.push('--regenerate');
  return new Promise(resolve => {
    execFile(
      MAS_PYTHON,
      args,
      { cwd: PROJECT_ROOT, timeout: MAS_TIMEOUT_MS, maxBuffer: 8 * 1024 * 1024, env: { ...process.env, PYTHONUNBUFFERED: '1' } },
      (err, stdout, stderr) => {
        // LiteLLM's logging worker may print to stdout after our JSON line, so
        // scan from the end for the last line that parses as a MAS result.
        const lines = String(stdout || '').trim().split('\n').map(l => l.trim()).filter(l => l.startsWith('{'));
        for (const line of lines.reverse()) {
          try {
            const parsed = JSON.parse(line) as MasResult;
            if (parsed && (parsed.status === 'ok' || parsed.status === 'error')) return resolve(parsed);
          } catch {
          }
        }
        const tail = String(stderr || '').trim().split('\n').slice(-3).join(' | ');
        resolve({ status: 'error', error: err ? `${err.message}${tail ? ` — ${tail}` : ''}` : `Unparseable MAS output: ${String(stdout || '').slice(-200)}` });
      }
    );
  });
}

const WARMUP_TEMPLATES: Record<string, string> = {
  Intake: 'Xin chào bạn! Chúng mình có các lớp thiền Sahaja Yoga hoàn toàn MIỄN PHÍ hàng tuần. Bạn có muốn tìm hiểu thêm về thời gian và địa điểm không ạ? 🧘',
  Seeker: 'Chào bạn! Khóa học thiền Sahaja Yoga căn bản sắp bắt đầu buổi tiếp theo. Không biết bạn đã sắp xếp được thời gian tham gia chưa ạ? 🙏',
  Seeker_Public_Program: 'Chào bạn! Tuần này chúng mình có buổi thiền cộng đồng đặc biệt. Rất mong được đón tiếp bạn tham gia cùng mọi người nhé! 🌸',
  Seeker_18_Weeks: 'Chào bạn! Nhóm thiền 18 tuần rất nhớ bạn. Hãy tiếp tục duy trì thực hành đều đặn để cảm nhận sự bình an sâu sắc nhé! ✨',
  Seed: 'Chào bạn! Chúc mừng bạn đã hoàn thành khóa học căn bản. Bạn có muốn tham gia buổi thiền tập thể tuần này để củng cố thói quen thiền định không ạ? 🌿',
};

const REPLY_LATE_HOURS = 24;
const REPLY_STALE_HOURS = 24 * 7;

type FallbackReplyContext = {
  content: string;
  messageAt: string | null;
  phone: string | null;
  email: string | null;
  city: string | null;
};

function hoursSinceVietnamTimestamp(timestamp: string | null): number | null {
  if (!timestamp) return null;
  // `message_at` is stored as an absolute Vietnam-local wall-clock time.
  const instant = new Date(`${timestamp.replace(' ', 'T')}+07:00`).getTime();
  if (Number.isNaN(instant)) return null;
  return Math.max(0, (Date.now() - instant) / (60 * 60 * 1000));
}

function createFallbackReply(context: FallbackReplyContext): { text?: string; reason?: string } {
  const ageHours = hoursSinceVietnamTimestamp(context.messageAt);
  if (ageHours !== null && ageHours > REPLY_STALE_HOURS) {
    return { reason: 'stale_customer_turn' };
  }

  const content = context.content.toLocaleLowerCase('vi-VN');
  const asksOnlineClass = /(?:đăng\s*k[ýi]|tham\s*gia|học).{0,40}online|online.{0,40}(?:đăng\s*k[ýi]|tham\s*gia|học)/u.test(content);
  const asksClass = asksOnlineClass || /lớp\s*thiền|lớp\s*học|đăng\s*k[ýi]|tham\s*gia/u.test(content);
  const hasContact = Boolean((context.phone || '').trim() || (context.email || '').trim());
  const latePrefix = ageHours !== null && ageHours > REPLY_LATE_HOURS
    ? 'Dạ mình xin lỗi bạn vì phản hồi muộn nhé. '
    : '';

  if (asksOnlineClass) {
    const nextStep = hasContact
      ? 'Mình đã ghi nhận nhu cầu học online của bạn và sẽ nhờ anh/chị trong CLB gửi thông tin buổi gần nhất nhé ạ.'
      : 'Bạn cho mình xin họ tên và số điện thoại/Zalo để CLB gửi thông tin buổi học online gần nhất nhé ạ.';
    return { text: `${latePrefix}Lớp thiền online của CLB hoàn toàn miễn phí ạ. ${nextStep} 🙏` };
  }

  if (asksClass) {
    const nextStep = hasContact
      ? 'Mình đã ghi nhận nhu cầu của bạn và sẽ nhờ anh/chị trong CLB gửi thông tin phù hợp nhé ạ.'
      : context.city && !['Unknown', 'Online'].includes(context.city)
        ? 'Bạn cho mình xin họ tên và số điện thoại/Zalo để CLB gửi thông tin lớp phù hợp nhé ạ.'
        : 'Bạn cho mình xin thành phố muốn tham gia, họ tên và số điện thoại/Zalo để CLB gửi thông tin lớp phù hợp nhé ạ.';
    return { text: `${latePrefix}Các lớp thiền của CLB hoàn toàn miễn phí ạ. ${nextStep} 🙏` };
  }

  return { text: `${latePrefix}Cảm ơn bạn đã nhắn cho CLB. Mình có thể hỗ trợ bạn thông tin về lớp thiền miễn phí hoặc lịch sinh hoạt nhé ạ. 🙏` };
}

// code:api-recommendations-001:template-engine
// Legacy rule-based engine (hard-coded templates). Used only as a fallback when
// the MAS process cannot run, or for non-selected bulk/comment requests.
async function runTemplateEngine(
  { type, threadId, targetThreadIds, selectedOnly, seekerName, city, limit }: {
    type: string; threadId?: string; targetThreadIds: string[]; selectedOnly: boolean;
    seekerName?: string; city?: string; limit: number;
  }
): Promise<{ created: Proposal[]; existing?: Proposal; skipped: { threadId: string; reason: string }[] }> {
    const created: Proposal[] = [];
    const skipped: { threadId: string; reason: string }[] = [];

    // Helper to insert into action_queue safely as pending
    const insertProposal = async (
      queueType: string,
      targetType: string,
      targetId: string,
      targetName: string,
      actionText: string,
      payload: Record<string, unknown> = {}
    ) => {
      // Check existing pending proposal
      const existing = await queryOne(`
        SELECT id FROM action_queue
        WHERE target_id = ? AND status = 'pending' AND queue_type = ?
      `, [targetId, queueType]);

      if (existing) return null;

      const result = await queryOne<{ id: number }>(`
        INSERT INTO action_queue (queue_type, page_id, target_type, target_id, target_name, action_text, payload_json, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', now(), now()) RETURNING id
      `, [
        queueType,
        DEFAULT_PAGE_ID,
        targetType,
        targetId,
        targetName,
        actionText,
        JSON.stringify(payload)
      ]);
      return result?.id;
    };

    // 1. Reply message recommendations
    if (type === 'all' || type === 'reply') {
      let query = `
        SELECT t.id AS thread_id, t.thread_name, m.sender, m.content,
               m.message_at AS message_at, u.phone AS phone, u.email AS email, u.city AS city
        FROM threads t
        JOIN messages m ON m.id = (
          SELECT id FROM messages
          WHERE thread_id = t.id AND kind = 'message'
          ORDER BY seq DESC, id DESC LIMIT 1
        )
        LEFT JOIN users u ON u.thread_id = t.id
        WHERE m.sender NOT IN ('Page', 'Auto_Page')
      `;
      const params: (string | number)[] = [];
      if (threadId) {
        query += ` AND t.id = ?`;
        params.push(threadId);
      } else if (selectedOnly) {
        query += ` AND t.id IN (${targetThreadIds.map(() => '?').join(', ')})`;
        params.push(...targetThreadIds);
      }
      query += ` ORDER BY t.last_synced_time DESC LIMIT ?`;
      params.push(Number(limit));

      const unreplied = await dbQuery(query, params) as Array<{
        thread_id: string; thread_name: string; content: string; message_at: string | null;
        phone: string | null; email: string | null; city: string | null;
      }>;
      for (const row of unreplied) {
        const name = row.thread_name || seekerName || 'Seeker';
        const fallback = createFallbackReply({
          content: row.content || '', messageAt: row.message_at,
          phone: row.phone, email: row.email, city: row.city,
        });
        if (!fallback.text) {
          skipped.push({ threadId: row.thread_id, reason: fallback.reason || 'no_safe_fallback' });
          continue;
        }
        const replyText = fallback.text;
        const id = await insertProposal('reply_message', 'thread', row.thread_id, name, replyText, {
          source: 'recommendation_engine',
          trigger: 'unreplied_message',
          last_content: row.content,
          customer_message_at: row.message_at,
          fallback_reason: 'llm_unavailable',
        });
        if (id) created.push({ id, queueType: 'reply_message', targetId: row.thread_id, targetName: name, actionText: replyText });
      }
    }

    // 2. Comment reply recommendations
    if ((type === 'all' || type === 'comment') && !threadId && !selectedOnly) {
      try {
        const comments = await dbQuery(`
          SELECT c.id, c.commenter_name, c.comment_text, c.post_id
          FROM comments c
          WHERE c.commenter_name NOT LIKE '%Sahaja%'
          ORDER BY c.id DESC LIMIT ?
        `, [Number(limit)]) as { id: number; commenter_name: string; comment_text: string; post_id: string }[];

        for (const c of comments) {
          const name = c.commenter_name || 'Bạn';
          const replyText = `Dạ chào ${name}, lớp thiền Sahaja Yoga hoàn toàn miễn phí ạ. Page đã gửi thông tin chi tiết qua tin nhắn, bạn kiểm tra hộp thư giúp Page nhé! 🙏`;
          const id = await insertProposal('reply_comment', 'comment', String(c.id), name, replyText, {
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
      } else if (selectedOnly) {
        query += ` AND u.thread_id IN (${targetThreadIds.map(() => '?').join(', ')})`;
        params.push(...targetThreadIds);
      }
      query += ` ORDER BY u.last_interaction ASC LIMIT ?`;
      params.push(Number(limit));

      const dormant = await dbQuery(query, params) as { thread_id: string; thread_name: string; city: string; lead_stage: string }[];
      for (const u of dormant) {
        const name = u.thread_name || seekerName || 'Seeker';
        const stage = u.lead_stage || 'Intake';
        const warmupText = WARMUP_TEMPLATES[stage] || WARMUP_TEMPLATES.Intake;
        const id = await insertProposal('proactive_message', 'thread', u.thread_id, name, warmupText, {
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
        const ev = await queryOne<{ name?: string; city?: string; event_date?: string }>(`SELECT * FROM events ORDER BY id DESC LIMIT 1`);
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
      } else if (selectedOnly) {
        query += ` AND u.thread_id IN (${targetThreadIds.map(() => '?').join(', ')})`;
        params.push(...targetThreadIds);
      } else if (city && city !== 'all') {
        query += ` AND (u.city = ? OR u.city = 'Unknown')`;
        params.push(city);
      }
      query += ` ORDER BY u.last_interaction DESC LIMIT ?`;
      params.push(Number(limit));

      const eventSeekers = await dbQuery(query, params) as { thread_id: string; thread_name: string; city: string }[];
      for (const u of eventSeekers) {
        const name = u.thread_name || seekerName || 'Seeker';
        const loc = u.city !== 'Unknown' ? u.city : eventCity;
        const eventText = `Chào bạn ${name}! Sắp tới Sahaja Yoga có sự kiện '${eventTitle}' tại ${loc} vào ngày ${eventDate}. Chương trình hoàn toàn miễn phí, kính mời bạn cùng người thân tham gia trải nghiệm thiền định ạ! 🧘✨`;
        const id = await insertProposal('proactive_message', 'thread', u.thread_id, name, eventText, {
          source: 'recommendation_engine',
          type: 'event',
          eventTitle,
          city: loc,
        });
        if (id) created.push({ id, queueType: 'proactive_message', targetId: u.thread_id, targetName: name, actionText: eventText });
      }
    }

    // 5. Targeted proposal fallback for single seeker
    if (!selectedOnly && (threadId || seekerName) && created.length === 0) {
      const existing = await queryOne<{ id: number; queueType: string; targetId: string; targetName: string; actionText: string }>(`
        SELECT id, queue_type AS queueType, target_id AS targetId, target_name AS targetName, action_text AS actionText
        FROM action_queue
        WHERE (target_id = ? OR (target_name IS NOT NULL AND target_name = ?)) AND status = 'pending'
        LIMIT 1
      `, [threadId || '', seekerName || '']);

      if (existing) {
        return { created, existing, skipped };
      }

      // Contextual recommendation generation based on user stage
      let u: { thread_id: string; thread_name: string; city: string; lead_stage: string } | undefined;
      try {
        u = await queryOne<{ thread_id: string; thread_name: string; city: string; lead_stage: string }>(`
          SELECT u.thread_id, u.thread_name, u.city, u.lead_stage
          FROM users u
          WHERE (u.thread_id = ? OR u.thread_name = ?)
          LIMIT 1
        `, [threadId || '', seekerName || '']);
      } catch {
        // Users table might have custom schema
      }

      const name = u?.thread_name || seekerName || 'Seeker';
      const stage = u?.lead_stage || 'Intake';
      const targetIdVal = u?.thread_id || threadId || `seeker-${name}`;
      const warmupText = WARMUP_TEMPLATES[stage] || WARMUP_TEMPLATES.Intake;
      const id = await insertProposal('proactive_message', 'thread', targetIdVal, name, warmupText, {
        source: 'recommendation_engine',
        type: 'warmup',
        stage,
        city: u?.city || city || 'Unknown',
      });
      if (id) {
        created.push({ id, queueType: 'proactive_message', targetId: targetIdVal, targetName: name, actionText: warmupText });
      }
    }

    return { created, skipped };
}

function summarizeSkipped(skipped: { threadId: string; reason: string }[]): string {
  const labels: Record<string, string> = {
    reminder_already_sent_for_session: 'Chưa phù hợp để nhắc lại: seeker đã được nhắc cho buổi học này; không tạo tin để tránh làm phiền',
    care_not_appropriate_now: 'Chưa phù hợp để liên hệ lúc này; không tạo tin nhắn',
    care_instruction_unresolved: 'Chưa phân tích được quyền nhắc lại; xem lỗi chi tiết trong /llm',
  };
  const counts = new Map<string, number>();
  for (const s of skipped) counts.set(s.reason, (counts.get(s.reason) || 0) + 1);
  return [...counts.entries()].map(([reason, n]) => `${labels[reason] || reason}×${n}`).join(', ');
}

async function runRecommendationJob(jobId: number) {
  // Claim once. A poll after a dev-server restart can safely resume a queued job.
  const claimed = await execute(`
    UPDATE mas_recommendation_jobs
    SET status = 'running', phase = 'preparing_context', started_at = COALESCE(started_at, now()), updated_at = now()
    WHERE id = ? AND status = 'queued'
  `, [jobId]);
  if (!claimed.changes) return;

  const row = await queryOne<{ request_json: string }>('SELECT request_json FROM mas_recommendation_jobs WHERE id = ?', [jobId]);
  if (!row) return;
  const request = JSON.parse(row.request_json) as { type: string; threadId?: string; threadIds: string[]; seekerName?: string; city?: string; programCode?: string; eventId?: string; instruction?: string; carePurpose?: string; limit: number; regenerate?: boolean };
  const setPhase = (phase: string) => execute('UPDATE mas_recommendation_jobs SET phase = ?, updated_at = now() WHERE id = ?', [phase, jobId]);

  try {
    const selectedOnly = request.threadIds.length > 0;
    let result: Record<string, unknown>;
    if (selectedOnly && ['all', 'reply', 'warmup', 'event', 'care'].includes(request.type)) {
      await setPhase('waiting_for_llm');
      const mas = await runMasEngine({ type: request.type, threadIds: request.threadIds, city: request.city, programCode: request.programCode, eventId: request.eventId, instruction: request.instruction, carePurpose: request.carePurpose, regenerate: request.regenerate, jobId });
      if (mas.status === 'ok') {
        await setPhase('saving_recommendations');
        const proposals = mas.proposals || [];
        const skipped = mas.skipped || [];
        const supersededCount = mas.supersededCount || 0;
        const skippedNote = skipped.length ? ` Bỏ qua ${skipped.length}: ${summarizeSkipped(skipped)}.` : '';
        const supersededNote = supersededCount ? ` Đã thay thế ${supersededCount} đề xuất cũ (đánh dấu rejected).` : '';
        result = {
          success: true, engine: 'inbox_mas', count: proposals.length, createdCount: proposals.length, proposals, skipped, supersededCount,
          message: `MAS đã tạo ${proposals.length} đề xuất mới vào hàng đợi chờ duyệt (status: pending).${supersededNote}${skippedNote}`,
          llmTraceUrl: `/llm?trace=${jobId}`,
        };
      } else if (!['all', 'warmup', 'event', 'care'].includes(request.type)) {
        await setPhase('creating_safe_fallback');
        const fallbackResult = await runTemplateEngine({ type: request.type, threadId: request.threadId, targetThreadIds: request.threadIds, selectedOnly, seekerName: request.seekerName, city: request.city, limit: request.limit });
        const fallback = fallbackResult.created;
        const fallbackSkipped = fallbackResult.skipped;
        const skippedNote = fallbackSkipped.length ? ` Bỏ qua ${fallbackSkipped.length}: ${summarizeSkipped(fallbackSkipped)}.` : '';
        result = {
          success: true, engine: 'template_fallback', count: fallback.length, createdCount: fallback.length, proposals: fallback,
          // Preserve the MAS failure for operators and the quality-review
          // runbook. A safe fallback draft is useful, but it is not proof that
          // the LLM path completed successfully.
          masError: mas.error || 'MAS returned an unspecified error',
          // The safe fallback is an intentional successful outcome for the
          // operator; don't surface the internal MAS failure as a user-facing
          // error after proposals were successfully created.
          skipped: fallbackSkipped,
          message: `Đã tạo ${fallback.length} đề xuất an toàn bằng template vào hàng đợi chờ duyệt.${skippedNote}`,
        };
      } else {
        throw new Error(mas.error || 'MAS không thể hoàn tất workflow đã kiểm chứng; không tạo template thay thế.');
      }
    } else {
      if (['all', 'warmup', 'event', 'care'].includes(request.type)) {
        throw new Error('Không tạo template outbound cho Care; hãy chọn seeker và chạy workflow MAS đã kiểm chứng.');
      }
      await setPhase('creating_safe_fallback');
      const { created, existing } = await runTemplateEngine({ type: request.type, threadId: request.threadId, targetThreadIds: request.threadIds, selectedOnly, seekerName: request.seekerName, city: request.city, limit: request.limit });
      result = existing
        ? { success: true, engine: 'template', count: 0, proposals: [existing], message: 'Đã có đề xuất đang chờ duyệt trong hàng đợi.' }
        : { success: true, engine: 'template', count: created.length, createdCount: created.length, proposals: created, message: `Đã tạo an toàn ${created.length} đề xuất mới vào hàng đợi chờ duyệt (status: pending).` };
    }
    await execute("UPDATE mas_recommendation_jobs SET status = 'completed', phase = 'completed', result_json = ?, completed_at = now(), updated_at = now() WHERE id = ?", [JSON.stringify(result), jobId]);
  } catch (error) {
    console.error('Recommendation job failed:', error);
    await execute("UPDATE mas_recommendation_jobs SET status = 'failed', phase = 'failed', error_text = ?, completed_at = now(), updated_at = now() WHERE id = ?", [error instanceof Error ? error.message : 'Lỗi không xác định khi chạy MAS', jobId]);
  }
}

export async function GET(request: NextRequest) {
  const jobId = Number(new URL(request.url).searchParams.get('jobId'));
  if (!Number.isInteger(jobId) || jobId < 1) return NextResponse.json({ error: 'jobId không hợp lệ' }, { status: 400 });
  const row = await queryOne<Record<string, unknown>>('SELECT * FROM mas_recommendation_jobs WHERE id = ?', [jobId]);
  if (!row) return NextResponse.json({ error: 'Không tìm thấy MAS job' }, { status: 404 });
  if (row.status === 'queued') void runRecommendationJob(jobId);
  return NextResponse.json({ job: formatJob(row) });
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));
    const {
      type = 'all',
      threadId,
      threadIds,
      seekerName,
      city,
      programCode,
      eventId,
      instruction,
      carePurpose,
      limit = 5,
      regenerate = false,
    } = body;

    const targetThreadIds = Array.isArray(threadIds)
      ? [...new Set(threadIds.filter((id): id is string => typeof id === 'string' && id.length > 0))]
      : [];
    const selectedOnly = targetThreadIds.length > 0;

    if (type === 'care' && (!['class_reminder', 'warmup', 'event'].includes(carePurpose) || (!regenerate && (typeof instruction !== 'string' || !instruction.trim())))) {
      return NextResponse.json({ error: 'Care cần mục đích hợp lệ; lệnh mới cần thêm chỉ dẫn vận hành.' }, { status: 400 });
    }
    const jobRequest = { type, threadId, threadIds: targetThreadIds, seekerName, city, programCode, eventId, instruction: typeof instruction === 'string' ? instruction.trim() : '', carePurpose, limit, selectedOnly, regenerate: selectedOnly && regenerate === true };
    const inserted = await queryOne<{ id: number }>("INSERT INTO mas_recommendation_jobs (status, phase, request_json) VALUES ('queued', 'queued', ?) RETURNING id", [JSON.stringify(jobRequest)]);
    if (!inserted) throw new Error('Không thể tạo MAS job');
    const jobId = Number(inserted.id);
    // Deliberately do not await: the request is now durable and the UI follows it via GET.
    void runRecommendationJob(jobId);
    const job = await queryOne<Record<string, unknown>>('SELECT * FROM mas_recommendation_jobs WHERE id = ?', [jobId]);
    if (!job) throw new Error('MAS job disappeared immediately after creation');
    return NextResponse.json({ job: formatJob(job) }, { status: 202 });
  } catch (error) {
    console.error('Recommendations error:', error);
    return NextResponse.json({ error: 'Lỗi khi tạo đề xuất MAS' }, { status: 500 });
  }
}
