import type { PendingHistory } from '@/lib/types';

const LABELS: Record<string, string> = {
  missing_message_identity: 'Thiếu message ID', unverified_actor: 'Chưa xác minh người gửi',
  unresolved_calendar_day: 'Chưa xác minh ngày', unresolved_clock: 'Chưa xác minh giờ',
  missing_source_model: 'Chưa đọc được nguồn Facebook',
  missing_source_timestamp: 'Chưa xác minh timestamp nguồn',
  unsupported_source_payload: 'Định dạng nguồn cần kiểm tra thêm',
  observation_decode_failed: 'Không đọc được bản ghi quan sát; cần kiểm tra dữ liệu',
};

export function PendingMessageHistory({ history }: { history?: PendingHistory | null }) {
  if (!history) return null;
  return <section aria-label="Dữ liệu chờ xác minh" style={{ padding: 16, marginTop: 12,
    border: '1px solid var(--border-color, #71717a)', borderRadius: 8 }}>
    <h3 style={{ fontSize: 14, margin: '0 0 8px' }}>Chờ xác minh · {history.messages.length} bản ghi</h3>
    <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
      Đã quan sát nội dung trên Facebook nhưng chưa đủ bằng chứng để đưa vào lịch sử hội thoại.
      Chưa xác nhận người gửi/ngày giờ; không dùng các bản ghi này cho MAS.
    </p>
    <p style={{ fontSize: 12 }}>Thời điểm quét: {history.observedAt} — không phải thời gian gửi tin.</p>
    {history.reasons.length > 0 && <p style={{ fontSize: 12 }}>
      Lý do: {history.reasons.map(r => LABELS[r] || r).join(' · ')}
    </p>}
    <div style={{ maxHeight: 360, overflowY: 'auto' }}>
      {history.messages.map((m, index) => <article key={`${m.sourceId || 'unknown'}-${index}`}
        style={{ padding: '12px 0', borderTop: '1px solid var(--border-color, #71717a)' }}>
        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Người gửi và thời gian chưa xác minh</div>
        <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 13 }}>{m.content}</div>
        {m.reasons.length > 0 && <small>{m.reasons.map(r => LABELS[r] || r).join(' · ')}</small>}
      </article>)}
    </div>
  </section>;
}
