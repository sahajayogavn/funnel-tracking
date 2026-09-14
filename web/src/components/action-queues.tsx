'use client';

import { useState } from 'react';
import type { ActionQueueItem } from '@/lib/queries';

const QUEUES = [
  ['reply_message', '1. Reply tin nhắn', '💬'],
  ['reply_comment', '2. Reply comments', '↩️'],
  ['proactive_comment', '3. Post comments chủ động', '📣'],
  ['proactive_message', '4. Send message chủ động', '✉️'],
] as const;

export default function ActionQueues({ initialItems }: { initialItems: ActionQueueItem[] }) {
  const [items, setItems] = useState(initialItems);
  const [error, setError] = useState('');
  const decide = async (id: number, decision: 'approve' | 'reject') => {
    setError('');
    const response = await fetch(`/api/action-queue/${id}`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ decision }),
    });
    if (!response.ok) { setError('Không thể cập nhật quyết định. Vui lòng tải lại trang.'); return; }
    const result = await response.json();
    setItems(current => current.map(item => item.id === id ? { ...item, status: result.status, approvalSource: 'webui' } : item));
  };

  return <>
    <div className="queue-notice">MAS chỉ tạo đề xuất. Mỗi mục chỉ được worker thực thi sau khi bạn bấm duyệt tại đây hoặc react 👍 trên Telegram. Thứ tự thực hiện là FIFO trong từng queue.</div>
    {error && <div className="queue-error">{error}</div>}
    <div className="queue-grid">
      {QUEUES.map(([key, title, icon]) => {
        const queueItems = items.filter(item => item.queueType === key);
        return <section className="queue-card" key={key}>
          <div className="queue-heading"><span>{icon}</span><div><h2>{title}</h2><p>{queueItems.length} đang chờ / đang xử lý</p></div></div>
          {queueItems.length === 0 ? <p className="queue-empty">Không có đề xuất chờ quyết định.</p> : queueItems.map((item, index) =>
            <article className="queue-item" key={item.id}>
              <div className="queue-item-meta"><span>#{item.id} · vị trí {index + 1}</span><span className={`queue-status ${item.status}`}>{item.status}</span></div>
              <strong>{item.targetName || item.targetType}</strong>
              <p>{item.reactionType ? `React: ${item.reactionType}` : item.actionText}</p>
              {item.errorText && <p className="queue-failure">Lỗi: {item.errorText}</p>}
              {item.status === 'pending' && <div className="queue-actions"><button onClick={() => decide(item.id, 'approve')}>Duyệt & xếp thực thi</button><button className="reject" onClick={() => decide(item.id, 'reject')}>Từ chối</button></div>}
              {item.status === 'approved' && <small>Đã duyệt qua {item.approvalSource}; chờ worker ở đầu queue.</small>}
            </article>
          )}
        </section>;
      })}
    </div>
  </>;
}
