// code:web-component-008:action-queues
'use client';

import { useState, useCallback, useRef } from 'react';
import type { ActionQueueItem } from '@/lib/queries';
import type { Seeker, SeekerDetail } from '@/lib/types';
import { SeekerJourneyTimeline } from './seeker-journey-timeline';
import { MasProgress, type MasJob } from './mas-progress';

const PAGE_ID = '1548373332058326';

function seekerDetailUrl(seeker?: Seeker | null, fallbackId?: string | null) {
  if (seeker?.id) {
    return seeker.source === 'dm' ? `/seekers/${seeker.id}` : `/seekers/comment-${seeker.id}`;
  }
  return `/seekers/${encodeURIComponent(fallbackId || '')}`;
}

function facebookProfileUrl(value?: string | null) {
  if (!value) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) {
    return `https://www.facebook.com/${trimmed}`;
  }
  const candidate = trimmed.startsWith('http') ? trimmed : `https://${trimmed}`;
  try {
    const url = new URL(candidate);
    return /(^|\.)facebook\.com$/i.test(url.hostname) && url.pathname.length > 1 ? url.toString() : null;
  } catch {
    return null;
  }
}

interface ParsedPayload {
  source?: string;
  trigger?: string;
  last_content?: string;
  classification?: string;
  type?: string;
  stage?: string;
  city?: string;
  eventTitle?: string;
  customer_message_timestamp?: string;
  post_id?: string;
  [key: string]: unknown;
}

function parsePayload(jsonStr?: string | null): ParsedPayload {
  if (!jsonStr) return {};
  try {
    return JSON.parse(jsonStr);
  } catch {
    return {};
  }
}

function getMasReasonDetails(item: ActionQueueItem, payload: ParsedPayload, seekerDetail?: SeekerDetail | null) {
  const source = payload.source === 'recommendation_engine'
    ? 'MAS Recommendation Engine'
    : payload.source === 'inbox_mas'
      ? 'Google ADK Inbox MAS'
      : (payload.source || 'MAS Multi-Agent System');

  let triggerLabel = '';
  let triggerDesc = '';

  if (payload.trigger === 'unreplied_message' || item.queueType === 'reply_message') {
    triggerLabel = '💬 Tin nhắn chưa trả lời từ Seeker';
    const lastCustomer = payload.last_content || seekerDetail?.messages?.filter(m => m.sender !== 'Page')?.slice(-1)[0]?.content;
    triggerDesc = lastCustomer
      ? `Seeker vừa nhắn: « ${lastCustomer.length > 110 ? lastCustomer.slice(0, 110) + '...' : lastCustomer} ». MAS phát hiện tin nhắn này chưa có câu trả lời từ Page.`
      : 'Phát hiện tin nhắn cuối cùng từ Seeker chưa có phản hồi từ Page.';
  } else if (payload.type === 'warmup') {
    triggerLabel = '🔥 Đề xuất Warm-up Seeker im lặng';
    const stage = payload.stage || seekerDetail?.seeker?.leadStage || 'Intake';
    triggerDesc = `Seeker đang ở giai đoạn ${stage} và chưa có tương tác gần đây. MAS chủ động đề xuất gửi tin nhắn warm-up để kích hoạt lại mối quan tâm.`;
  } else if (payload.type === 'event') {
    triggerLabel = '📅 Đề xuất mời tham gia sự kiện theo khu vực';
    const evTitle = payload.eventTitle || 'Chương trình Thiền & Âm nhạc';
    const loc = payload.city || seekerDetail?.seeker?.city || 'khu vực phù hợp';
    triggerDesc = `Sắp có sự kiện « ${evTitle} » tại ${loc}. MAS đối soát thấy Seeker thuộc khu vực này nên tạo đề xuất gửi thư mời.`;
  } else if (item.queueType === 'reply_comment') {
    triggerLabel = '↩️ Bình luận mới trên bài viết';
    triggerDesc = payload.post_id
      ? `Seeker đã bình luận trên bài viết (ID: ${payload.post_id}). MAS đề xuất câu trả lời và hướng dẫn Seeker kiểm tra hộp thư.`
      : 'Bình luận từ Seeker trên bài viết cần được phản hồi.';
  } else {
    triggerLabel = '⚡ Đề xuất chủ động từ MAS';
    triggerDesc = 'Hệ thống MAS tự động phân tích dữ liệu tương tác và đề xuất hành động phù hợp.';
  }

  return {
    source,
    triggerLabel,
    triggerDesc,
    classification: payload.classification,
    lastContent: payload.last_content,
    timestamp: payload.customer_message_timestamp,
  };
}

const QUEUES = [
  ['reply_message', '1. Reply tin nhắn', '💬'],
  ['reply_comment', '2. Reply comments', '↩️'],
  ['proactive_comment', '3. Post comments chủ động', '📣'],
  ['proactive_message', '4. Send message chủ động', '✉️'],
] as const;

export default function ActionQueues({ initialItems }: { initialItems: ActionQueueItem[] }) {
  const [items, setItems] = useState(initialItems);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [loadingContext, setLoadingContext] = useState<string | null>(null);
  const [recommendationJob, setRecommendationJob] = useState<MasJob | null>(null);
  const [selectedQueueItemIds, setSelectedQueueItemIds] = useState<Set<number>>(new Set());
  const selectionAnchorByQueueRef = useRef<Record<string, number>>({});

  // Seeker info & MAS reason preview / pinned state
  const [selectedItem, setSelectedItem] = useState<ActionQueueItem | null>(null);
  const [pinnedItemId, setPinnedItemId] = useState<number | null>(null);
  const [sidebarData, setSidebarData] = useState<SeekerDetail | null>(null);
  const [sidebarLoading, setSidebarLoading] = useState(false);
  const hoverTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const fetchItems = async () => {
    try {
      const res = await fetch('/api/action-queue');
      if (res.ok) {
        const data = await res.json();
        setItems(data);
      }
    } catch {
      // ignore
    }
  };

  const handleRunRecommendations = async (context: string, targetIds: string[]) => {
    if (!targetIds.length) return;
    setError('');
    setSuccess('');
    setLoadingContext(context);
    let queuedJob = false;
    try {
      const res = await fetch('/api/action-queue/recommendations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: context, threadIds: targetIds, limit: targetIds.length }),
      });
      const data = await res.json();
      if (!res.ok || !data.job) {
        setError(data.error || 'Có lỗi xảy ra khi tạo đề xuất.');
      } else {
        queuedJob = true;
        const pollJob = async (jobId: number): Promise<void> => {
          const statusRes = await fetch(`/api/action-queue/recommendations?jobId=${jobId}`);
          const statusData = await statusRes.json();
          if (!statusRes.ok || !statusData.job) throw new Error(statusData.error || 'Không đọc được trạng thái MAS job');
          const job = statusData.job as MasJob;
          setRecommendationJob(job);
          if (job.status === 'completed') {
            setSuccess(job.result?.message || `Đã tạo ${job.result?.count ?? 0} đề xuất mới (pending).`);
            await fetchItems();
            setLoadingContext(null);
            return;
          }
          if (job.status === 'failed') {
            setError(job.error || 'MAS job không thể hoàn tất.');
            setLoadingContext(null);
            return;
          }
          window.setTimeout(() => { void pollJob(jobId).catch(err => { setError(err instanceof Error ? err.message : 'Không thể theo dõi MAS job.'); setLoadingContext(null); }); }, 900);
        };
        setRecommendationJob(data.job as MasJob);
        void pollJob(data.job.id).catch(err => { setError(err instanceof Error ? err.message : 'Không thể theo dõi MAS job.'); setLoadingContext(null); });
      }
    } catch {
      setError('Lỗi kết nối tới máy chủ khi tạo đề xuất.');
      setLoadingContext(null);
    } finally {
      // A queued/running job owns this state until polling reaches a terminal result.
      if (!queuedJob) {
        setLoadingContext(null);
      }
    }
  };

  const handleQueueRangeSelection = (queueType: string, queueItems: ActionQueueItem[], index: number, shiftKey: boolean) => {
    const anchor = selectionAnchorByQueueRef.current[queueType];
    const ids = shiftKey && anchor !== undefined
      ? queueItems.slice(Math.min(anchor, index), Math.max(anchor, index) + 1).map(item => item.id)
      : [queueItems[index].id];
    if (!shiftKey) selectionAnchorByQueueRef.current[queueType] = index;
    setSelectedQueueItemIds(new Set(ids));
  };

  const runSelectedQueue = (queueType: string, queueItems: ActionQueueItem[]) => {
    const targetIds = queueItems
      .filter(item => selectedQueueItemIds.has(item.id) && item.targetType === 'thread' && item.targetId)
      .map(item => item.targetId as string);
    const context = queueType === 'reply_message' ? 'reply' : queueType === 'reply_comment' ? 'comment' : queueType === 'proactive_message' ? 'warmup' : 'comment';
    handleRunRecommendations(context, [...new Set(targetIds)]);
  };

  const selectedThreadIds = items
    .filter(item => selectedQueueItemIds.has(item.id) && item.targetType === 'thread' && item.targetId)
    .map(item => item.targetId as string);

  const decide = async (id: number, decision: 'approve' | 'reject') => {
    setError('');
    const response = await fetch(`/api/action-queue/${id}`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ decision }),
    });
    if (!response.ok) { setError('Không thể cập nhật quyết định. Vui lòng tải lại trang.'); return; }
    const result = await response.json();
    setItems(current => current.map(item => item.id === id ? { ...item, status: result.status, approvalSource: 'webui' } : item));
    if (selectedItem?.id === id) {
      setSelectedItem(current => current ? { ...current, status: result.status, approvalSource: 'webui' } : null);
    }
  };

  const loadSeekerData = useCallback(async (item: ActionQueueItem) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setSidebarLoading(true);
    setSidebarData(null);

    const lookupKey = item.targetId || item.targetName || '';
    try {
      const res = await fetch(`/api/seekers/${encodeURIComponent(lookupKey)}`, { signal: controller.signal });
      if (res.ok) {
        const data = await res.json();
        setSidebarData(data);
      } else {
        // Fallback seeker info
        setSidebarData({
          seeker: {
            id: item.targetType === 'thread' ? Number(item.targetId?.replace(/\D/g, '') || 0) : 0,
            threadId: item.targetId || undefined,
            name: item.targetName || item.targetType,
            city: 'Unknown',
            phone: null,
            email: null,
            fbProfileUrl: null,
            fbUserId: null,
            leadStage: 'Intake',
            firstSeen: item.createdAt,
            lastInteraction: item.createdAt,
            source: item.targetType === 'comment' ? 'comment' : 'dm',
          },
          messages: [],
          comments: [],
          adSource: null,
          messageCount: 0,
          commentCount: 0,
        });
      }
    } catch (err: unknown) {
      if (!(err instanceof DOMException && err.name === 'AbortError')) {
        setSidebarData(null);
      }
    } finally {
      setSidebarLoading(false);
    }
  }, []);

  const selectItem = useCallback((item: ActionQueueItem, pin = false) => {
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
      hoverTimeoutRef.current = null;
    }
    setSelectedItem(item);
    if (pin) {
      setPinnedItemId(item.id);
    }
    loadSeekerData(item);
  }, [loadSeekerData]);

  const handleTogglePin = useCallback((item: ActionQueueItem) => {
    if (pinnedItemId === item.id) {
      setPinnedItemId(null);
      setSelectedItem(null);
      setSidebarData(null);
    } else {
      selectItem(item, true);
    }
  }, [pinnedItemId, selectItem]);

  const handleMouseEnter = useCallback((item: ActionQueueItem) => {
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
      hoverTimeoutRef.current = null;
    }
    if (pinnedItemId === null) {
      selectItem(item, false);
    }
  }, [pinnedItemId, selectItem]);

  const handleMouseLeave = useCallback(() => {
    if (pinnedItemId === null) {
      hoverTimeoutRef.current = setTimeout(() => {
        setSelectedItem(null);
        setSidebarData(null);
      }, 300);
    }
  }, [pinnedItemId]);

  const handleSidebarMouseEnter = useCallback(() => {
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
      hoverTimeoutRef.current = null;
    }
  }, []);

  const handleSidebarMouseLeave = useCallback(() => {
    if (pinnedItemId === null) {
      hoverTimeoutRef.current = setTimeout(() => {
        setSelectedItem(null);
        setSidebarData(null);
      }, 300);
    }
  }, [pinnedItemId]);

  const handleCloseSidebar = useCallback(() => {
    setPinnedItemId(null);
    setSelectedItem(null);
    setSidebarData(null);
  }, []);

  return <>
    {/* Safe Run Recommendations Control Panel */}
    <div style={{
      background: 'rgba(30, 27, 75, 0.4)',
      border: '1px solid rgba(99, 102, 241, 0.3)',
      borderRadius: '16px',
      padding: '20px',
      marginBottom: '24px',
      boxShadow: '0 4px 20px rgba(0,0,0,0.2)'
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '12px', marginBottom: '14px' }}>
        <div>
          <h2 style={{ fontSize: '16px', fontWeight: 700, color: '#f1f5f9', display: 'flex', alignItems: 'center', gap: '8px', margin: 0 }}>
            <span>🤖</span>
            <span>Tạo đề xuất MAS (Run Recommendations)</span>
            <span style={{ fontSize: '11px', background: 'rgba(16, 185, 129, 0.2)', color: '#34d399', border: '1px solid rgba(16, 185, 129, 0.4)', padding: '2px 8px', borderRadius: '12px', fontWeight: 600 }}>
              Human-in-the-Loop Safe
            </span>
          </h2>
          <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '4px', marginBottom: 0 }}>
            Quét dữ liệu hội thoại & tương tác để tự động đề xuất hành động. Tất cả đề xuất được đưa vào hàng đợi với trạng thái <strong style={{ color: '#fbbf24' }}>pending</strong>, tuyệt đối không tự ý gửi tin nhắn ra ngoài mà chờ duyệt.
          </p>
        </div>
        <button
          onClick={fetchItems}
          style={{
            background: 'rgba(255,255,255,0.06)',
            border: '1px solid var(--border-subtle)',
            borderRadius: '8px',
            color: 'var(--text-secondary)',
            fontSize: '12px',
            padding: '6px 12px',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: '6px'
          }}
          title="Tải lại hàng đợi"
        >
          🔄 Làm mới danh sách
        </button>
      </div>

      <p style={{ margin: '0 0 10px', fontSize: '12px', color: 'var(--text-muted)' }}>Click một mục để chọn; giữ Shift rồi click mục khác để chọn cả dải. Nút chạy MAS chỉ mở khi đã chọn seeker.</p>
      <div className="recommendation-actions" role="toolbar" aria-label="Chạy MAS cho các seeker đã chọn">
        {([
          ['all', 'recommendation-action--all', '⚡ Tất cả'],
          ['reply', 'recommendation-action--reply', '💬 Tin nhắn'],
          ['comment', 'recommendation-action--comment', '↩️ Bình luận'],
          ['warmup', 'recommendation-action--warmup', '🔥 Warm-up'],
          ['event', 'recommendation-action--event', '📅 Sự kiện'],
        ] as const).map(([context, className, label]) => (
          <button
            key={context}
            className={`recommendation-action ${className}`}
            disabled={loadingContext !== null || selectedThreadIds.length === 0}
            onClick={() => handleRunRecommendations(context, [...new Set(selectedThreadIds)])}
            title={selectedThreadIds.length ? `Chạy ${label} cho ${selectedThreadIds.length} seeker đã chọn` : 'Chọn ít nhất một seeker DM trong queue'}
          >
            {loadingContext === context ? '⏳ Đang chạy...' : `${label}${selectedThreadIds.length ? ` (${selectedThreadIds.length})` : ''}`}
          </button>
        ))}
      </div>

      {recommendationJob && <div style={{ marginTop: '14px' }}><MasProgress job={recommendationJob} /></div>}


      {success && (
        <div style={{
          marginTop: '14px',
          padding: '10px 14px',
          borderRadius: '8px',
          background: 'rgba(16, 185, 129, 0.12)',
          border: '1px solid rgba(16, 185, 129, 0.3)',
          color: '#6ee7b7',
          fontSize: '13px',
          display: 'flex',
          alignItems: 'center',
          gap: '8px'
        }}>
          <span>✅</span>
          <span>{success}</span>
        </div>
      )}
    </div>

    <div className="queue-notice">MAS chỉ tạo đề xuất. Mỗi mục chỉ được worker thực thi sau khi bạn bấm duyệt tại đây hoặc react 👍 trên Telegram. Thứ tự thực hiện là FIFO trong từng queue.</div>
    {error && <div className="queue-error">{error}</div>}
    <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start', position: 'relative' }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className={`queue-grid ${selectedItem ? 'queue-grid--with-sidebar' : ''}`}>
          {QUEUES.map(([key, title, icon]) => {
            const queueItems = items.filter(item => item.queueType === key);
            const selectedInQueue = queueItems.filter(item => selectedQueueItemIds.has(item.id));
            const selectableInQueue = selectedInQueue.filter(item => item.targetType === 'thread' && item.targetId);
            return <section className="queue-card" key={key}>
              <div className="queue-heading">
                <div className="queue-heading__label">
                  <span>{icon}</span>
                  <div><h2>{title}</h2><p>{queueItems.length} đang chờ / đang xử lý</p></div>
                </div>
                <button
                  type="button"
                  className="recommendation-action recommendation-action--reply queue-heading__action"
                  disabled={loadingContext !== null || selectableInQueue.length === 0}
                  onClick={() => runSelectedQueue(key, queueItems)}
                  title={selectableInQueue.length ? `Chạy MAS cho ${selectableInQueue.length} seeker đã chọn` : 'Chọn ít nhất một seeker DM trong queue này'}
                >
                  {loadingContext === key ? '⏳ Đang chạy MAS...' : `⚡ Chạy đề xuất MAS${selectableInQueue.length ? ` (${selectableInQueue.length})` : ''}`}
                </button>
              </div>
              {queueItems.length === 0 ? <p className="queue-empty">Không có đề xuất chờ quyết định.</p> : queueItems.map((item, index) => {
                const isSelected = selectedItem?.id === item.id;
                const isRangeSelected = selectedQueueItemIds.has(item.id);
                const isPinned = pinnedItemId === item.id;
                return (
                  <article
                    className={`queue-item ${isSelected ? 'queue-item--selected' : ''}`}
                    key={item.id}
                    onClick={(event) => {
                      handleQueueRangeSelection(key, queueItems, index, event.shiftKey);
                      selectItem(item, false);
                    }}
                    onMouseDown={(event) => { if (event.shiftKey) event.preventDefault(); }}
                    style={{
                      cursor: 'pointer',
                      userSelect: 'none',
                      borderColor: isRangeSelected || isSelected ? 'var(--border-glow)' : undefined,
                      background: isRangeSelected ? 'rgba(99, 102, 241, 0.14)' : isSelected ? 'rgba(99, 102, 241, 0.07)' : undefined,
                      transition: 'all 0.15s ease',
                    }}
                  >
                    <div className="queue-item-meta">
                      <span>#{item.id} · vị trí {index + 1}</span>
                      <span className={`queue-status ${item.status}`}>{item.status}</span>
                    </div>
                    <div>
                      <strong
                        className="queue-item-username"
                        onClick={(e) => { e.stopPropagation(); handleTogglePin(item); }}
                        onMouseEnter={() => handleMouseEnter(item)}
                        onMouseLeave={handleMouseLeave}
                        style={{
                          cursor: 'pointer',
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '6px',
                          color: isSelected ? '#a5b4fc' : 'inherit',
                          borderBottom: '1px dashed rgba(129, 140, 248, 0.6)',
                          paddingBottom: '1px',
                          transition: 'color 0.15s ease',
                        }}
                        title="Bấm hoặc di chuột để xem thông tin Seeker & lý do đề xuất MAS"
                      >
                        <span>{item.targetName || item.targetType}</span>
                        <span style={{ fontSize: '11px', opacity: 0.75 }}>ℹ️</span>
                        {isPinned && <span style={{ fontSize: '11px', color: '#818cf8' }} title="Đã ghim">📌</span>}
                      </strong>
                    </div>
                    <div style={{ marginTop: '8px', padding: '10px 12px', borderRadius: '8px', background: 'rgba(15, 23, 42, 0.62)', border: '1px solid rgba(129, 140, 248, 0.18)' }}>
                      <div style={{ fontSize: '9px', color: '#a5b4fc', fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '5px' }}>Nội dung đề xuất</div>
                      <div style={{ whiteSpace: 'pre-wrap', fontSize: '13px', lineHeight: 1.55, color: 'var(--text-primary)' }}>{item.reactionType ? `React: ${item.reactionType}` : item.actionText}</div>
                    </div>
                    {item.errorText && <p className="queue-failure">Lỗi: {item.errorText}</p>}
                    {item.status === 'pending' && <div className="queue-actions"><button onClick={() => decide(item.id, 'approve')}>Duyệt & xếp thực thi</button><button className="reject" onClick={() => decide(item.id, 'reject')}>Từ chối</button></div>}
                    {item.status === 'approved' && <small>Đã duyệt qua {item.approvalSource}; chờ worker ở đầu queue.</small>}
                  </article>
                );
              })}
            </section>;
          })}
        </div>
      </div>

      {/* ── Right Sidebar (Reserved space by default to avoid layout shift) ── */}
      <aside
        className="queue-seeker-sidebar"
        onMouseEnter={handleSidebarMouseEnter}
        onMouseLeave={handleSidebarMouseLeave}
        style={{
          width: '380px',
          minWidth: '380px',
          maxHeight: 'calc(100vh - 140px)',
          overflowY: 'auto',
          position: 'sticky',
          top: '20px',
          background: 'var(--bg-secondary)',
          border: selectedItem ? '1px solid var(--border-glow)' : '1px dashed var(--border-subtle)',
          borderRadius: '14px',
          padding: '20px',
          boxShadow: selectedItem ? '0 8px 32px rgba(0,0,0,0.4)' : 'none',
          transition: 'border-color 0.2s ease, box-shadow 0.2s ease',
          zIndex: 40,
        }}
      >
        {selectedItem ? (
          <>
            {/* Header */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '14px' }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ fontSize: '16px', fontWeight: 700, color: 'var(--text-primary)' }}>
                  {sidebarData?.seeker?.name || selectedItem.targetName || selectedItem.targetType}
                </span>
                {pinnedItemId === selectedItem.id ? (
                  <span style={{ fontSize: '10px', background: 'rgba(99, 102, 241, 0.2)', color: '#a5b4fc', border: '1px solid rgba(99, 102, 241, 0.4)', padding: '1px 6px', borderRadius: '10px', fontWeight: 600 }}>
                    📌 Đã ghim
                  </span>
                ) : (
                  <span style={{ fontSize: '10px', background: 'rgba(255, 255, 255, 0.06)', color: 'var(--text-muted)', padding: '1px 6px', borderRadius: '10px' }}>
                    Xem nhanh (hover)
                  </span>
                )}
              </div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '3px' }}>
                {sidebarData?.seeker?.source === 'dm' || selectedItem.targetType === 'thread' ? '💬 DM' : '💬 Comment'} · {sidebarData?.seeker?.city || 'Unknown'} · {sidebarData?.seeker?.leadStage || 'Intake'}
              </div>
            </div>
            <button
              onClick={handleCloseSidebar}
              style={{
                background: 'rgba(255,255,255,0.06)',
                border: 'none',
                borderRadius: '6px',
                color: 'var(--text-muted)',
                fontSize: '14px',
                cursor: 'pointer',
                width: '28px',
                height: '28px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center'
              }}
              title="Đóng sidebar"
            >
              ✕
            </button>
          </div>

          {/* Quick links */}
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '14px' }}>
            <a
              href={seekerDetailUrl(sidebarData?.seeker, selectedItem.targetId || selectedItem.targetName)}
              style={{ padding: '6px 12px', background: 'rgba(99,102,241,0.12)', borderRadius: '6px', fontSize: '11px', fontWeight: 600, color: '#818cf8', textDecoration: 'none' }}
            >
              📋 Full Details →
            </a>
            {facebookProfileUrl(sidebarData?.seeker?.fbProfileUrl) && (
              <a
                href={facebookProfileUrl(sidebarData?.seeker?.fbProfileUrl)!}
                target="_blank"
                rel="noopener noreferrer"
                style={{ padding: '6px 12px', background: 'rgba(59,130,246,0.12)', borderRadius: '6px', fontSize: '11px', fontWeight: 600, color: '#60a5fa', textDecoration: 'none' }}
              >
                👤 FB Profile ↗
              </a>
            )}
            {(sidebarData?.seeker?.source === 'dm' || selectedItem.targetType === 'thread') && facebookProfileUrl(sidebarData?.seeker?.fbProfileUrl) && (
              <a
                href={`https://business.facebook.com/latest/inbox/all?asset_id=${PAGE_ID}&selected_item_id=${facebookProfileUrl(sidebarData?.seeker?.fbProfileUrl)!.split('/').pop()?.split('?')[0]}&thread_type=FB_MESSAGE`}
                target="_blank"
                rel="noopener noreferrer"
                style={{ padding: '6px 12px', background: 'rgba(99,102,241,0.12)', borderRadius: '6px', fontSize: '11px', fontWeight: 600, color: '#818cf8', textDecoration: 'none' }}
              >
                💬 FB Inbox ↗
              </a>
            )}
          </div>

          {/* ── MAS Queue Reason & Proof Box ── */}
          {(() => {
            const payload = parsePayload(selectedItem.payloadJson);
            const masReason = getMasReasonDetails(selectedItem, payload, sidebarData);
            return (
              <div style={{
                padding: '12px 14px',
                background: 'linear-gradient(135deg, rgba(99, 102, 241, 0.14), rgba(168, 85, 247, 0.09))',
                border: '1px solid rgba(99, 102, 241, 0.35)',
                borderRadius: '10px',
                marginBottom: '14px',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                  <div style={{ fontSize: '11px', fontWeight: 700, color: '#a5b4fc', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span>🤖</span>
                    <span>Lý do trong hàng đợi (MAS Proof)</span>
                  </div>
                  <span style={{ fontSize: '10px', padding: '2px 7px', borderRadius: '8px', background: 'rgba(99, 102, 241, 0.25)', color: '#c7d2fe', fontWeight: 600 }}>
                    {masReason.source}
                  </span>
                </div>

                <div style={{ fontSize: '12px', fontWeight: 600, color: '#38bdf8', marginBottom: '4px' }}>
                  {masReason.triggerLabel}
                </div>
                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.45, marginBottom: '6px' }}>
                  {masReason.triggerDesc}
                </div>

                {masReason.classification && (
                  <div style={{ fontSize: '11px', color: '#fbbf24', marginBottom: '6px' }}>
                    🏷️ Phân loại ý định: <strong>{masReason.classification}</strong>
                  </div>
                )}

                <div style={{
                  padding: '8px 10px',
                  borderRadius: '6px',
                  background: 'rgba(0,0,0,0.25)',
                  borderLeft: '3px solid #818cf8',
                  fontSize: '11px',
                  marginTop: '8px',
                }}>
                  <div style={{ fontWeight: 700, color: '#818cf8', marginBottom: '2px' }}>Nội dung đề xuất do MAS soạn thảo:</div>
                  <div style={{ color: 'var(--text-primary)', fontStyle: 'italic', lineHeight: 1.35 }}>
                    &ldquo;{selectedItem.actionText || (selectedItem.reactionType ? `Reaction: ${selectedItem.reactionType}` : '')}&rdquo;
                  </div>
                </div>

                {selectedItem.status === 'pending' && (
                  <div style={{ marginTop: '10px', display: 'flex', gap: '8px' }}>
                    <button
                      onClick={() => decide(selectedItem.id, 'approve')}
                      style={{
                        flex: 1,
                        padding: '6px 10px',
                        background: 'var(--accent-emerald)',
                        color: '#052e24',
                        border: 'none',
                        borderRadius: '6px',
                        fontSize: '11px',
                        fontWeight: 700,
                        cursor: 'pointer',
                      }}
                    >
                      ✓ Duyệt đề xuất này
                    </button>
                    <button
                      onClick={() => decide(selectedItem.id, 'reject')}
                      className="reject"
                      style={{
                        padding: '6px 10px',
                        background: 'transparent',
                        color: 'var(--accent-rose)',
                        border: '1px solid rgba(244,63,94,.45)',
                        borderRadius: '6px',
                        fontSize: '11px',
                        cursor: 'pointer',
                      }}
                    >
                      Từ chối
                    </button>
                  </div>
                )}
              </div>
            );
          })()}

          {/* Compact Journey Timeline */}
          {sidebarData?.seeker && (
            <div style={{ marginBottom: '14px', padding: '12px', background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border-subtle)', borderRadius: '10px' }}>
              <SeekerJourneyTimeline
                seeker={sidebarData.seeker}
                compact={true}
                onRefreshSeeker={() => loadSeekerData(selectedItem)}
              />
            </div>
          )}

          {sidebarLoading && (
            <div style={{ textAlign: 'center', padding: '20px', color: 'var(--text-muted)', fontSize: '12px' }}>
              ⏳ Đang tải thông tin Seeker...
            </div>
          )}

          {/* Stats, Recent messages, Comments */}
          {sidebarData && !sidebarLoading && (
            <>
              {/* Stats */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px', marginBottom: '14px' }}>
                <div style={{ textAlign: 'center', padding: '10px 4px', background: 'rgba(99,102,241,0.06)', borderRadius: '8px' }}>
                  <div style={{ fontSize: '20px', fontWeight: 800, color: '#818cf8' }}>{sidebarData.messageCount ?? 0}</div>
                  <div style={{ fontSize: '9px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)' }}>Msgs</div>
                </div>
                <div style={{ textAlign: 'center', padding: '10px 4px', background: 'rgba(245,158,11,0.06)', borderRadius: '8px' }}>
                  <div style={{ fontSize: '20px', fontWeight: 800, color: '#f59e0b' }}>{sidebarData.commentCount ?? 0}</div>
                  <div style={{ fontSize: '9px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)' }}>Cmts</div>
                </div>
                <div style={{ textAlign: 'center', padding: '10px 4px', background: sidebarData.adSource ? 'rgba(236,72,153,0.06)' : 'rgba(107,114,128,0.06)', borderRadius: '8px' }}>
                  <div style={{ fontSize: '20px', fontWeight: 800, color: sidebarData.adSource ? '#ec4899' : 'var(--text-muted)' }}>{sidebarData.adSource ? '✓' : '✗'}</div>
                  <div style={{ fontSize: '9px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)' }}>Ad</div>
                </div>
              </div>

              {/* Ad source */}
              {sidebarData.adSource && (
                <div style={{ padding: '10px 12px', background: 'rgba(236,72,153,0.06)', border: '1px solid rgba(236,72,153,0.15)', borderRadius: '8px', marginBottom: '12px' }}>
                  <div style={{ fontSize: '10px', fontWeight: 700, color: '#ec4899', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '4px' }}>📢 Ad Source</div>
                  <div style={{ fontSize: '11px', color: 'var(--text-secondary)', lineHeight: 1.4 }}>
                    {sidebarData.adSource.matchedPostName?.slice(0, 100) || 'Replied to ad post'}
                  </div>
                </div>
              )}

              {/* Recent messages */}
              {sidebarData.messages?.length > 0 && (
                <div style={{ marginBottom: '12px' }}>
                  <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
                    Recent Messages ({sidebarData.messages.length})
                  </div>
                  <div
                    className="sidebar-recent-messages"
                    style={{
                      maxHeight: '260px',
                      overflowY: 'auto',
                      paddingRight: '6px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '4px',
                    }}
                  >
                    {sidebarData.messages
                      .filter(m => !m.content?.includes('[AD SOURCE]'))
                      .slice(-12)
                      .map((msg, i) => (
                        <div
                          key={i}
                          style={{
                            padding: '8px 10px',
                            marginBottom: '4px',
                            borderRadius: msg.sender === 'Page' ? '8px 8px 2px 8px' : '8px 8px 8px 2px',
                            background: msg.sender === 'Page' ? 'rgba(99,102,241,0.08)' : 'rgba(255,255,255,0.04)',
                            borderLeft: msg.sender !== 'Page' ? '2px solid #f59e0b' : 'none',
                          }}
                        >
                          <div style={{ fontSize: '9px', fontWeight: 700, color: msg.sender === 'Page' ? '#818cf8' : '#f59e0b' }}>
                            {msg.sender === 'Page' ? 'Page' : (sidebarData.seeker?.name || selectedItem.targetName || 'Seeker')}
                          </div>
                          <div style={{
                            fontSize: '12px',
                            color: 'var(--text-primary)',
                            lineHeight: 1.4,
                            marginTop: '2px',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            display: '-webkit-box',
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: 'vertical' as const,
                          }}>
                            {msg.content || '(empty)'}
                          </div>
                          {msg.messageTimestamp && (
                            <div style={{ fontSize: '9px', color: 'var(--text-muted)', marginTop: '2px' }}>
                              {msg.messageTimestamp}
                            </div>
                          )}
                        </div>
                      ))}
                  </div>
                </div>
              )}

              {/* Comments */}
              {sidebarData.comments?.length > 0 && (
                <div>
                  <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
                    Comments ({sidebarData.comments.length})
                  </div>
                  {sidebarData.comments.slice(0, 3).map((cmt, i) => (
                    <div key={i} style={{ padding: '8px 10px', marginBottom: '4px', background: 'rgba(245,158,11,0.04)', border: '1px solid rgba(245,158,11,0.1)', borderRadius: '8px' }}>
                      <div style={{
                        fontSize: '12px',
                        color: 'var(--text-primary)',
                        lineHeight: 1.4,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        display: '-webkit-box',
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: 'vertical' as const,
                      }}>
                        {cmt.commentText || '(empty)'}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </>
      ) : (
        /* Empty State Placeholder (spares space by default to avoid layout resize) */
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          textAlign: 'center',
          minHeight: '340px',
          padding: '28px 16px',
        }}>
          <div style={{
            width: '56px',
            height: '56px',
            borderRadius: '50%',
            background: 'rgba(99, 102, 241, 0.08)',
            border: '1px solid rgba(99, 102, 241, 0.25)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '26px',
            marginBottom: '16px',
          }}>
            👤
          </div>
          <div style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '6px' }}>
            Thông tin Seeker & Lý do MAS
          </div>
          <p style={{ fontSize: '12px', lineHeight: 1.5, color: 'var(--text-secondary)', maxWidth: '290px', margin: '0 0 22px' }}>
            Bấm hoặc di chuột vào tên Seeker trong hàng đợi bên trái để xem hồ sơ, hội thoại và bằng chứng phân tích từ MAS.
          </p>

          <div style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '10px',
            width: '100%',
            textAlign: 'left',
          }}>
            <div style={{
              padding: '10px 12px',
              borderRadius: '8px',
              background: 'rgba(255, 255, 255, 0.02)',
              border: '1px solid rgba(255, 255, 255, 0.05)',
              fontSize: '11px',
              display: 'flex',
              alignItems: 'flex-start',
              gap: '10px',
            }}>
              <span style={{ fontSize: '15px' }}>🤖</span>
              <div>
                <strong style={{ color: '#818cf8', display: 'block' }}>Bằng chứng MAS Proof</strong>
                <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>Hiển thị nguyên nhân kích hoạt và nội dung đề xuất của MAS.</span>
              </div>
            </div>

            <div style={{
              padding: '10px 12px',
              borderRadius: '8px',
              background: 'rgba(255, 255, 255, 0.02)',
              border: '1px solid rgba(255, 255, 255, 0.05)',
              fontSize: '11px',
              display: 'flex',
              alignItems: 'flex-start',
              gap: '10px',
            }}>
              <span style={{ fontSize: '15px' }}>💬</span>
              <div>
                <strong style={{ color: '#fbbf24', display: 'block' }}>Hội thoại thực tế</strong>
                <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>Xem các tin nhắn gần đây để nắm bắt ngữ cảnh trước khi duyệt.</span>
              </div>
            </div>

            <div style={{
              padding: '10px 12px',
              borderRadius: '8px',
              background: 'rgba(255, 255, 255, 0.02)',
              border: '1px solid rgba(255, 255, 255, 0.05)',
              fontSize: '11px',
              display: 'flex',
              alignItems: 'flex-start',
              gap: '10px',
            }}>
              <span style={{ fontSize: '15px' }}>🛤️</span>
              <div>
                <strong style={{ color: '#34d399', display: 'block' }}>Hành trình & Liên kết</strong>
                <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>Giai đoạn phễu Seeker, hồ sơ Facebook và hộp thư Meta Inbox.</span>
              </div>
            </div>
          </div>

          <div style={{
            marginTop: '22px',
            fontSize: '10px',
            color: 'var(--text-muted)',
            fontStyle: 'italic',
            background: 'rgba(255, 255, 255, 0.02)',
            padding: '6px 12px',
            borderRadius: '6px',
          }}>
            💡 Khung này cố định sẵn để bố cục không bị co giãn khi bạn bấm xem chi tiết.
          </div>
        </div>
      )}
      </aside>
    </div>
  </>;
}
