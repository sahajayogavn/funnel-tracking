import { NextRequest, NextResponse } from 'next/server';
import { execFile } from 'child_process';
import path from 'path';
import { getDb } from '@/lib/db';

const DEFAULT_PAGE_ID = '1548373332058326';
const PROJECT_ROOT = path.resolve(process.cwd(), '..');
const MAS_PYTHON = process.env.MAS_PYTHON || path.join(PROJECT_ROOT, '.venv', 'bin', 'python');
const MAS_SCRIPT = path.join(PROJECT_ROOT, 'tools', 'l5_mas_recommend.py');
const MAS_TIMEOUT_MS = Number(process.env.MAS_TIMEOUT_MS || 240_000);

type Proposal = { id: number; queueType: string; targetId: string; targetName: string; actionText: string; kind?: string };
type MasResult = {
  status: 'ok' | 'error';
  error?: string;
  count?: number;
  proposals?: Proposal[];
  skipped?: { threadId: string; reason: string }[];
};
type RecommendationJob = {
  id: number;
  status: 'queued' | 'running' | 'completed' | 'failed';
  phase: string;
  type: string;
  threadIds: string[];
  createdAt: string;
  startedAt?: string | null;
  completedAt?: string | null;
  result?: Record<string, unknown> | null;
  error?: string | null;
};

function ensureRecommendationJobsTable(db: ReturnType<typeof getDb>) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS mas_recommendation_jobs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'failed')),
      phase TEXT NOT NULL DEFAULT 'queued',
      request_json TEXT NOT NULL,
      result_json TEXT,
      error_text TEXT,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      started_at DATETIME,
      completed_at DATETIME,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_mas_recommendation_jobs_status ON mas_recommendation_jobs(status, id);
  `);
}

function formatJob(row: Record<string, unknown>): RecommendationJob {
  const request = JSON.parse(String(row.request_json || '{}')) as { type?: string; threadIds?: string[] };
  return {
    id: Number(row.id),
    status: row.status as RecommendationJob['status'],
    phase: String(row.phase || 'queued'),
    type: request.type || 'all',
    threadIds: request.threadIds || [],
    createdAt: String(row.created_at),
    startedAt: row.started_at as string | null,
    completedAt: row.completed_at as string | null,
    result: row.result_json ? JSON.parse(String(row.result_json)) : null,
    error: row.error_text as string | null,
  };
}

// code:api-recommendations-001:mas-engine
// Spawns the Python MAS entrypoint (ADK BatchInboxAgent / WarmUpComposer / EventAdvertiser).
// The script only enqueues pending proposals; it never opens a browser or sends messages.
function runMasEngine(opts: { type: string; threadIds: string[]; city?: string }): Promise<MasResult> {
  const args = [MAS_SCRIPT, '--thread-ids', opts.threadIds.join(','), '--type', opts.type, '--page-id', DEFAULT_PAGE_ID];
  if (opts.city && opts.city !== 'all') args.push('--city', opts.city);
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

// code:api-recommendations-001:template-engine
// Legacy rule-based engine (hard-coded templates). Used only as a fallback when
// the MAS process cannot run, or for non-selected bulk/comment requests.
function runTemplateEngine(
  db: ReturnType<typeof getDb>,
  { type, threadId, targetThreadIds, selectedOnly, seekerName, city, limit }: {
    type: string; threadId?: string; targetThreadIds: string[]; selectedOnly: boolean;
    seekerName?: string; city?: string; limit: number;
  }
): { created: Proposal[]; existing?: Proposal } {
    const created: Proposal[] = [];

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
      } else if (selectedOnly) {
        query += ` AND t.id IN (${targetThreadIds.map(() => '?').join(', ')})`;
        params.push(...targetThreadIds);
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
    if ((type === 'all' || type === 'comment') && !threadId && !selectedOnly) {
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
      } else if (selectedOnly) {
        query += ` AND u.thread_id IN (${targetThreadIds.map(() => '?').join(', ')})`;
        params.push(...targetThreadIds);
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
      } else if (selectedOnly) {
        query += ` AND u.thread_id IN (${targetThreadIds.map(() => '?').join(', ')})`;
        params.push(...targetThreadIds);
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
    if (!selectedOnly && (threadId || seekerName) && created.length === 0) {
      const existing = db.prepare(`
        SELECT id, queue_type AS queueType, target_id AS targetId, target_name AS targetName, action_text AS actionText
        FROM action_queue
        WHERE (target_id = ? OR (target_name IS NOT NULL AND target_name = ?)) AND status = 'pending'
        LIMIT 1
      `).get(threadId || '', seekerName || '') as { id: number; queueType: string; targetId: string; targetName: string; actionText: string } | undefined;

      if (existing) {
        return { created, existing };
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

    return { created };
}

function summarizeSkipped(skipped: { threadId: string; reason: string }[]): string {
  const counts = new Map<string, number>();
  for (const s of skipped) counts.set(s.reason, (counts.get(s.reason) || 0) + 1);
  return [...counts.entries()].map(([reason, n]) => `${reason}×${n}`).join(', ');
}

async function runRecommendationJob(jobId: number) {
  const db = getDb();
  ensureRecommendationJobsTable(db);
  // Claim once. A poll after a dev-server restart can safely resume a queued job.
  const claimed = db.prepare(`
    UPDATE mas_recommendation_jobs
    SET status = 'running', phase = 'preparing_context', started_at = COALESCE(started_at, datetime('now')), updated_at = datetime('now')
    WHERE id = ? AND status = 'queued'
  `).run(jobId);
  if (!claimed.changes) return;

  const row = db.prepare('SELECT request_json FROM mas_recommendation_jobs WHERE id = ?').get(jobId) as { request_json: string } | undefined;
  if (!row) return;
  const request = JSON.parse(row.request_json) as { type: string; threadId?: string; threadIds: string[]; seekerName?: string; city?: string; limit: number };
  const setPhase = (phase: string) => db.prepare("UPDATE mas_recommendation_jobs SET phase = ?, updated_at = datetime('now') WHERE id = ?").run(phase, jobId);

  try {
    const selectedOnly = request.threadIds.length > 0;
    let result: Record<string, unknown>;
    if (selectedOnly && ['all', 'reply', 'warmup', 'event'].includes(request.type)) {
      setPhase('waiting_for_llm');
      const mas = await runMasEngine({ type: request.type, threadIds: request.threadIds, city: request.city });
      if (mas.status === 'ok') {
        setPhase('saving_recommendations');
        const proposals = mas.proposals || [];
        const skipped = mas.skipped || [];
        const skippedNote = skipped.length ? ` Bỏ qua ${skipped.length}: ${summarizeSkipped(skipped)}.` : '';
        result = {
          success: true, engine: 'inbox_mas', count: proposals.length, createdCount: proposals.length, proposals, skipped,
          message: `MAS đã tạo ${proposals.length} đề xuất mới vào hàng đợi chờ duyệt (status: pending).${skippedNote}`,
        };
      } else {
        setPhase('creating_safe_fallback');
        const fallback = runTemplateEngine(db, { type: request.type, threadId: request.threadId, targetThreadIds: request.threadIds, selectedOnly, seekerName: request.seekerName, city: request.city, limit: request.limit }).created;
        result = {
          success: true, engine: 'template_fallback', masError: mas.error, count: fallback.length, createdCount: fallback.length, proposals: fallback,
          message: `⚠️ MAS không chạy được (${mas.error}). Đã tạo ${fallback.length} đề xuất tạm bằng template vào hàng đợi chờ duyệt.`,
        };
      }
    } else {
      setPhase('creating_safe_fallback');
      const { created, existing } = runTemplateEngine(db, { type: request.type, threadId: request.threadId, targetThreadIds: request.threadIds, selectedOnly, seekerName: request.seekerName, city: request.city, limit: request.limit });
      result = existing
        ? { success: true, engine: 'template', count: 0, proposals: [existing], message: 'Đã có đề xuất đang chờ duyệt trong hàng đợi.' }
        : { success: true, engine: 'template', count: created.length, createdCount: created.length, proposals: created, message: `Đã tạo an toàn ${created.length} đề xuất mới vào hàng đợi chờ duyệt (status: pending).` };
    }
    db.prepare("UPDATE mas_recommendation_jobs SET status = 'completed', phase = 'completed', result_json = ?, completed_at = datetime('now'), updated_at = datetime('now') WHERE id = ?")
      .run(JSON.stringify(result), jobId);
  } catch (error) {
    console.error('Recommendation job failed:', error);
    db.prepare("UPDATE mas_recommendation_jobs SET status = 'failed', phase = 'failed', error_text = ?, completed_at = datetime('now'), updated_at = datetime('now') WHERE id = ?")
      .run(error instanceof Error ? error.message : 'Lỗi không xác định khi chạy MAS', jobId);
  }
}

export async function GET(request: NextRequest) {
  const jobId = Number(new URL(request.url).searchParams.get('jobId'));
  if (!Number.isInteger(jobId) || jobId < 1) return NextResponse.json({ error: 'jobId không hợp lệ' }, { status: 400 });
  const db = getDb();
  ensureRecommendationJobsTable(db);
  const row = db.prepare('SELECT * FROM mas_recommendation_jobs WHERE id = ?').get(jobId) as Record<string, unknown> | undefined;
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
      limit = 5,
    } = body;

    const targetThreadIds = Array.isArray(threadIds)
      ? [...new Set(threadIds.filter((id): id is string => typeof id === 'string' && id.length > 0))]
      : [];
    const selectedOnly = targetThreadIds.length > 0;

    const db = getDb();
    ensureRecommendationJobsTable(db);
    const jobRequest = { type, threadId, threadIds: targetThreadIds, seekerName, city, limit, selectedOnly };
    const result = db.prepare("INSERT INTO mas_recommendation_jobs (status, phase, request_json) VALUES ('queued', 'queued', ?)")
      .run(JSON.stringify(jobRequest));
    const jobId = Number(result.lastInsertRowid);
    // Deliberately do not await: the request is now durable and the UI follows it via GET.
    void runRecommendationJob(jobId);
    const job = db.prepare('SELECT * FROM mas_recommendation_jobs WHERE id = ?').get(jobId) as Record<string, unknown>;
    return NextResponse.json({ job: formatJob(job) }, { status: 202 });
  } catch (error) {
    console.error('Recommendations error:', error);
    return NextResponse.json({ error: 'Lỗi khi tạo đề xuất MAS' }, { status: 500 });
  }
}
