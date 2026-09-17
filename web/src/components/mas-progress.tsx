'use client';

// code:web-mas-progress-001
// Backend-driven view of the persisted MAS recommendation job. Each stage is
// derived from the job phase reported by the API, never from a client timer.

export type MasRunType = 'all' | 'reply' | 'warmup' | 'event';
export type MasJob = {
  id: number;
  status: 'queued' | 'running' | 'completed' | 'failed';
  phase: string;
  type: MasRunType;
  threadIds: string[];
  createdAt: string;
  result?: { message?: string; count?: number; createdCount?: number } | null;
  error?: string | null;
};

type Stage = { key: string; icon: string; label: string; detail: string; agent?: boolean };

const AGENTS: Record<Exclude<MasRunType, 'all'>, Stage> = {
  reply: { key: 'responder', icon: '💬', label: 'BatchInboxAgent', detail: 'Đọc ngữ cảnh seeker + Knowledge Base → soạn reply', agent: true },
  warmup: { key: 'warmup', icon: '📣', label: 'WarmUpComposer', detail: 'Chọn chiến lược theo stage & thời gian im lặng → soạn tin', agent: true },
  event: { key: 'event', icon: '🗓️', label: 'EventAdvertiser', detail: 'Ghép sự kiện theo thành phố → soạn lời mời', agent: true },
};

function buildStages(type: MasRunType, seekerCount: number): Stage[] {
  const agents = type === 'all' ? [AGENTS.reply, AGENTS.warmup, AGENTS.event] : [AGENTS[type]];
  return [
    { key: 'spawn', icon: '🐍', label: 'Khởi động MAS', detail: 'Spawn tiến trình Python + nạp ADK agents' },
    { key: 'load', icon: '📚', label: 'Nạp dữ liệu', detail: `Lịch sử hội thoại & hồ sơ ${seekerCount} seeker, Knowledge Base` },
    ...agents,
    { key: 'sanitize', icon: '🧹', label: 'Sanitize', detail: 'Lọc reasoning leak, chặn [OUT_OF_SCOPE]' },
    { key: 'queue', icon: '📥', label: 'Hàng đợi HITL', detail: 'Ghi action_queue (pending) — chờ người duyệt' },
  ];
}

const PHASE_INDEX: Record<string, number> = {
  queued: 0,
  preparing_context: 1,
  waiting_for_llm: 2,
  saving_recommendations: 3,
  creating_safe_fallback: 3,
  completed: 4,
  failed: 4,
};

const PHASE_LABEL: Record<string, string> = {
  queued: 'Đang chờ trong hàng đợi…',
  preparing_context: 'Đang nạp dữ liệu…',
  waiting_for_llm: 'Đang chờ LLM soạn nội dung…',
  saving_recommendations: 'Đang ghi vào hàng đợi…',
  creating_safe_fallback: 'Đang ghi vào hàng đợi…',
};

// Single-line status used inline where multi-step MasProgress is too tall
// (e.g. replacing the approve/reject row of a queue item while its MAS
// job is in flight).
export function MasInlineStatus({ job }: { job: MasJob }) {
  const done = job.status === 'completed';
  const failed = job.status === 'failed';
  const label = failed
    ? (job.error || 'Không thể hoàn tất')
    : done
      ? 'Đã hoàn tất — đề xuất đã sẵn trong hàng đợi'
      : (PHASE_LABEL[job.phase] || 'Đang xử lý…');
  const color = failed ? '#fb7185' : done ? '#34d399' : '#a5b4fc';

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12px', fontWeight: 600, color, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
      {!done && !failed && <span className="mas-spinner" />}
      <span>{failed ? '⚠️' : done ? '✅' : '🤖'}</span>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>
    </span>
  );
}

export function MasProgress({ job }: { job: MasJob }) {
  const type = job.type;
  const seekerCount = job.threadIds.length;
  const stages = buildStages(type, seekerCount);
  const done = job.status === 'completed';
  const failed = job.status === 'failed';
  const phaseIndex = PHASE_INDEX[job.phase] ?? 0;
  const current = done || failed ? stages.length - 1 : Math.min(phaseIndex, stages.length - 2);

  return (
    <div style={{ padding: '14px', borderRadius: '10px', background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(99,102,241,0.2)', marginBottom: '14px' }}>
      <style>{`
        @keyframes masPulse { 0%,100% { box-shadow: 0 0 0 0 rgba(129,140,248,0.55); } 50% { box-shadow: 0 0 0 8px rgba(129,140,248,0); } }
        @keyframes masFlow { 0% { background-position: 0 0; } 100% { background-position: 0 16px; } }
        @keyframes masDots { 0% { content: ''; } 33% { content: '.'; } 66% { content: '..'; } 100% { content: '...'; } }
        @keyframes masSpin { to { transform: rotate(360deg); } }
        .mas-stage-active .mas-node { animation: masPulse 1.4s ease-out infinite; }
        .mas-edge-flow { background: repeating-linear-gradient(180deg, rgba(129,140,248,0.9) 0 6px, transparent 6px 16px); animation: masFlow 0.8s linear infinite; }
        .mas-spinner { display:inline-block; width:12px; height:12px; border:2px solid rgba(129,140,248,0.3); border-top-color:#818cf8; border-radius:50%; animation: masSpin 0.8s linear infinite; }
      `}</style>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <div style={{ fontSize: '12px', fontWeight: 700, color: '#c7d2fe', display: 'flex', alignItems: 'center', gap: '8px' }}>
          {!done && <span className="mas-spinner" />}
          {failed ? '⚠️ MAS không thể hoàn tất' : done ? '✅ MAS đã hoàn tất' : job.status === 'queued' ? '📥 MAS đang chờ trong hàng đợi' : `🤖 MAS đang xử lý ${seekerCount} seeker`}
        </div>
        <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>Job #{job.id}</div>
      </div>

      <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {stages.map((stage, i) => {
          const state = i < current || (done && i === current) ? 'done' : i === current ? 'active' : 'pending';
          const color = state === 'done' ? '#34d399' : state === 'active' ? '#818cf8' : 'rgba(255,255,255,0.25)';
          return (
            <li key={stage.key} className={state === 'active' ? 'mas-stage-active' : undefined} style={{ display: 'flex', gap: '12px', alignItems: 'stretch' }}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: '28px' }}>
                <div
                  className="mas-node"
                  style={{
                    width: '28px', height: '28px', borderRadius: '50%', flexShrink: 0,
                    display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '14px',
                    background: state === 'pending' ? 'rgba(255,255,255,0.04)' : `${color}22`,
                    border: `2px solid ${color}`,
                    opacity: state === 'pending' ? 0.5 : 1,
                    transition: 'all 0.3s ease',
                  }}
                >
                  {state === 'done' ? '✓' : stage.icon}
                </div>
                {i < stages.length - 1 && (
                  <div
                    className={state === 'active' ? 'mas-edge-flow' : undefined}
                    style={{ width: '2px', flex: 1, minHeight: '14px', background: state === 'done' ? '#34d399' : 'rgba(255,255,255,0.1)', margin: '2px 0' }}
                  />
                )}
              </div>
              <div style={{ paddingBottom: i < stages.length - 1 ? '10px' : 0, opacity: state === 'pending' ? 0.45 : 1, transition: 'opacity 0.3s' }}>
                <div style={{ fontSize: '12px', fontWeight: 700, color: state === 'pending' ? 'var(--text-muted)' : 'var(--text-primary)', display: 'flex', gap: '6px', alignItems: 'center' }}>
                  {stage.label}
                  {stage.agent && (
                    <span style={{ fontSize: '9px', padding: '1px 6px', borderRadius: '999px', background: 'rgba(129,140,248,0.15)', color: '#a5b4fc', fontWeight: 600, letterSpacing: '0.04em' }}>
                      ADK AGENT
                    </span>
                  )}
                  {state === 'active' && !done && !failed && <span style={{ color: '#818cf8', fontSize: '11px' }}>{job.phase === 'waiting_for_llm' ? 'đang chờ LLM…' : 'đang chạy…'}</span>}
                </div>
                <div style={{ fontSize: '11px', color: 'var(--text-secondary)', lineHeight: 1.4 }}>{stage.detail}</div>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
