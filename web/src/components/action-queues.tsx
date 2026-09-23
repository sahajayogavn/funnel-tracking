// code:web-component-008:action-queues
'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import Link from 'next/link';
import type { ActionQueueItem } from '@/lib/queries';
import type { Seeker, SeekerDetail } from '@/lib/types';
import { SeekerSidebar, SeekerSidebarEmptyState } from './seeker-sidebar';
import { MasProgress, type MasJob } from './mas-progress';

// SeekerSidebar composes SeekerJourneyTimeline for this queue view.

function queueSeekerDetailUrl(item: ActionQueueItem) {
  // Meta thread IDs are commonly larger than Number.MAX_SAFE_INTEGER. Keep the
  // database value as a string so the route lookup receives the exact ID rather
  // than a rounded value rendered in scientific notation.
  const targetId = item.targetId || item.targetName || '';
  return `/seekers/${encodeURIComponent(targetId)}`;
}

function seekerDetailUrl(seeker?: Seeker | null, fallbackId?: string | null) {
  if (seeker?.id) {
    return seeker.source === 'dm' ? `/seekers/${seeker.id}` : `/seekers/comment-${seeker.id}`;
  }
  return `/seekers/${encodeURIComponent(fallbackId || '')}`;
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
  count?: number;
  customer_message_timestamp?: string;
  post_id?: string;
  session?: { program_code?: string; class_key?: string };
  event_id?: number | string;
  instruction?: string;
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

function seekerFromQueueItem(item: ActionQueueItem): Seeker {
  return {
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
  };
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
  } else if (payload.type === 'class_reminder' && item.queueType === 'session_proposal') {
    triggerLabel = '📅 Phiên nhắc lịch lớp (08:00–09:00 hằng ngày)';
    triggerDesc = `Có ${payload.count ?? '?'} seeker đã ghi danh lớp này và còn tương tác trong 21 ngày. Mở phiên để MAS soạn nháp nhắc từng người vào queue 4; bỏ qua nếu buổi này không diễn ra.`;
  } else if (payload.type === 'class_reminder') {
    triggerLabel = '📅 Nhắc lịch lớp cho seeker đã ghi danh';
    triggerDesc = 'Nháp được soạn sau khi quản trị viên mở phiên nhắc. Gửi tay trên Facebook như mọi tin khác.';
  } else if (payload.type === 'attendance') {
    triggerLabel = '📋 Điểm danh sau buổi học';
    triggerDesc = 'Seeker này đã được nhắc lịch cho buổi hôm qua. "Có mặt" là bằng chứng để chuyển stage; "Vắng" đưa seeker vào chuỗi warm-up nhẹ.';
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
  // prd:mas-time-aware-001 — internal decision queues; approval never reaches Facebook.
  ['session_proposal', '5. Phiên nhắc lịch lớp', '📅'],
  ['attendance_check', '6. Điểm danh sau buổi học', '📋'],
] as const;

const QUEUE_META = Object.fromEntries(QUEUES.map(([key, title, icon]) => [key, { title, icon }])) as Record<string, { title: string; icon: string }>;
const STATUS_META: Record<string, { label: string; tone: string }> = {
  pending: { label: 'Chờ duyệt', tone: 'pending' },
  approved: { label: 'Approved', tone: 'approved' },
  executing: { label: 'Approved', tone: 'approved' },
  executed: { label: 'Processed', tone: 'processed' },
  failed: { label: 'Processed', tone: 'processed' },
  rejected: { label: 'Rejected', tone: 'rejected' },
  deleted: { label: 'Deleted', tone: 'deleted' },
};

function queueMeta(queueType: unknown): { title: string; icon: string } {
  const key = typeof queueType === 'string' ? queueType : '';
  // `hasOwn` avoids inherited object properties such as `constructor` being
  // treated as queue metadata when a legacy or malformed row reaches the UI.
  if (Object.hasOwn(QUEUE_META, key)) return QUEUE_META[key];
  return { title: key || 'Không xác định', icon: '⚡' };
}

function displayStatus(status: string, payloadJson?: string | null) {
  const delivery = parsePayload(payloadJson).delivery_status;
  if (status === 'rejected' && delivery === 'outdated') return { label: 'Out-date', tone: 'rejected' };
  if (status === 'executing' && delivery === 'drafted') return { label: 'Processed', tone: 'processed' };
  return STATUS_META[status] || { label: status, tone: status };
}

function filterStatus(status: string, payloadJson?: string | null) {
  const delivery = parsePayload(payloadJson).delivery_status;
  if (status === 'executed' || status === 'failed' || (status === 'executing' && delivery === 'drafted')) return 'processed';
  if (status === 'executing') return 'approved';
  return status;
}

function formatQueueTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('vi-VN', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date);
}

// Labels for queues whose "approve" is a human decision, not a Facebook send.
const DECISION_LABELS: Record<string, { approve: string; reject: string; approvedNote: string }> = {
  session_proposal: { approve: 'Mở phiên nhắc', reject: 'Bỏ qua buổi này', approvedNote: 'Đã mở — MAS đang soạn nháp từng seeker' },
  attendance_check: { approve: '✅ Có mặt', reject: '❌ Vắng', approvedNote: 'Đã ghi nhận có mặt' },
};

export default function ActionQueues({ initialItems }: { initialItems: ActionQueueItem[] }) {
  const [items, setItems] = useState(initialItems);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [loadingContext, setLoadingContext] = useState<string | null>(null);
  const [recommendationJob, setRecommendationJob] = useState<MasJob | null>(null);
  // Keeps the per-item inline MAS status visible for a short flash after the
  // job reaches a terminal state, before the item's row reverts to its
  // normal approve/reject buttons.
  const [flashJobId, setFlashJobId] = useState<number | null>(null);
  const [selectedQueueItemIds, setSelectedQueueItemIds] = useState<Set<number>>(new Set());
  const selectionAnchorByQueueRef = useRef<Record<string, number>>({});
  const [masTypeFilter, setMasTypeFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState<string[]>(['pending', 'approved', 'processed']);
  const [sortBy, setSortBy] = useState<'createdAt' | 'targetName' | 'queueType' | 'status'>('createdAt');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');

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

  // Approved/executing items move forward only when the worker daemon
  // (tools/l5_hitl_execution.py hitl_execution_job) claims them; poll so the
  // inline status reflects that without a manual page refresh.
  const hasInFlightItems = items.some(item => item.status === 'approved' || item.status === 'executing');
  useEffect(() => {
    if (!hasInFlightItems) return;
    const interval = window.setInterval(() => { void fetchItems(); }, 6000);
    return () => window.clearInterval(interval);
  }, [hasInFlightItems]);

  const handleRunRecommendations = async (
    context: string,
    targetIds: string[],
    regenerate = false,
    carePurpose?: 'class_reminder' | 'warmup' | 'event',
    programCode?: string,
    eventId?: string,
    instruction?: string,
  ) => {
    if (!targetIds.length) return;
    setError('');
    setSuccess('');
    setLoadingContext(context);
    let queuedJob = false;
    try {
      const res = await fetch('/api/action-queue/recommendations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: context, threadIds: targetIds, limit: targetIds.length, regenerate, carePurpose, programCode, eventId, instruction }),
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
          if (job.status === 'completed' || job.status === 'failed') {
            setFlashJobId(job.id);
            window.setTimeout(() => setFlashJobId(current => current === job.id ? null : current), 4000);
          }
          if (job.status === 'completed') {
            setSuccess(job.result?.message || `Đã tạo ${job.result?.count ?? 0} đề xuất mới (pending).`);
            // Superseded drafts leave the list on refresh; drop their stale selection.
            if (regenerate) setSelectedQueueItemIds(new Set());
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

  // Items selected inside a queue card already have a live draft, so the
  // queue-card button runs MAS in regenerate mode: the new draft replaces the
  // selected pending/approved one (executing items are left to the worker).
  const runSelectedQueue = (queueType: string, queueItems: ActionQueueItem[]) => {
    const selected = queueItems.filter(item => selectedQueueItemIds.has(item.id) && item.targetType === 'thread' && item.targetId);
    const targetIds = selected.map(item => item.targetId as string);
    if (queueType !== 'proactive_message') {
      const context = queueType === 'reply_message' ? 'reply' : 'comment';
      void handleRunRecommendations(context, [...new Set(targetIds)], true);
      return;
    }
    const payloads = selected.map(item => parsePayload(item.payloadJson));
    const purposes = [...new Set(payloads.map(payload => payload.type).filter(
      (type): type is 'class_reminder' | 'warmup' | 'event' => type === 'class_reminder' || type === 'warmup' || type === 'event',
    ))];
    if (purposes.length !== 1) {
      setError('Chỉ regenerate cùng một mục đích Care trong một lần; hãy chọn riêng nhắc lớp, warm-up hoặc sự kiện.');
      return;
    }
    const purpose = purposes[0];
    const programCodes = [...new Set(payloads.map(payload => payload.session?.program_code || payload.session?.class_key).filter(Boolean))];
    const eventIds = [...new Set(payloads.map(payload => payload.event_id).filter((id): id is number | string => id !== undefined && id !== null).map(String))];
    if ((purpose === 'class_reminder' && programCodes.length !== 1) || (purpose === 'event' && eventIds.length !== 1)) {
      setError('Chỉ regenerate các draft có cùng lớp hoặc cùng sự kiện trong một lần.');
      return;
    }
    const instructions = [...new Set(payloads.map(payload => payload.instruction).filter((value): value is string => Boolean(value?.trim())))];
    if (instructions.length > 1) {
      setError('Chỉ regenerate các draft có cùng chỉ dẫn vận hành trong một lần.');
      return;
    }
    void handleRunRecommendations('care', [...new Set(targetIds)], true, purpose, programCodes[0], eventIds[0], instructions[0]);
  };

  const selectedThreadIds = items
    .filter(item => selectedQueueItemIds.has(item.id) && item.targetType === 'thread' && item.targetId)
    .map(item => item.targetId as string);

  const decide = async (id: number, decision: 'approve' | 'reject' | 'reprocess') => {
    setError('');
    const response = await fetch(`/api/action-queue/${id}`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ decision }),
    });
    if (!response.ok) { setError('Không thể cập nhật quyết định. Vui lòng tải lại trang.'); return; }
    const result = await response.json();
    setItems(current => current.map(item => item.id === id ? { ...item, status: result.status, approvalSource: result.approvalSource || 'webui' } : item));
    if (selectedItem?.id === id) {
      setSelectedItem(current => current ? { ...current, status: result.status, approvalSource: result.approvalSource || 'webui' } : null);
    }
  };

  const deleteQueueItems = async (ids: number[]) => {
    const uniqueIds = [...new Set(ids)];
    if (!uniqueIds.length) return;
    setError('');
    setSuccess('');
    const results = await Promise.all(uniqueIds.map(async id => {
      const response = await fetch(`/api/action-queue/${id}`, { method: 'DELETE' });
      return { id, ok: response.ok };
    }));
    const deletedIds = new Set(results.filter(result => result.ok).map(result => result.id));
    if (deletedIds.size) {
      setItems(current => current.map(item => deletedIds.has(item.id) ? { ...item, status: 'deleted' } : item));
      setSelectedQueueItemIds(current => new Set([...current].filter(id => !deletedIds.has(id))));
      if (selectedItem && deletedIds.has(selectedItem.id)) handleCloseSidebar();
      setSuccess(`Đã xóa ${deletedIds.size} đề xuất khỏi hàng đợi.`);
    }
    if (deletedIds.size !== uniqueIds.length) setError('Một số mục không thể xóa vì đã được worker xử lý.');
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
          seeker: seekerFromQueueItem(item),
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

  const filteredItems = items
    .filter(item => masTypeFilter === 'all' || item.queueType === masTypeFilter)
    .filter(item => statusFilter.includes(filterStatus(item.status, item.payloadJson)))
    .sort((a, b) => {
      const direction = sortDirection === 'asc' ? 1 : -1;
      if (sortBy === 'targetName') return direction * (a.targetName || '').localeCompare(b.targetName || '', 'vi');
      if (sortBy === 'queueType') return direction * (QUEUE_META[a.queueType]?.title || '').localeCompare(QUEUE_META[b.queueType]?.title || '', 'vi');
      if (sortBy === 'status') return direction * displayStatus(a.status).label.localeCompare(displayStatus(b.status).label, 'vi');
      return direction * (new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());
    });

  const toggleStatusFilter = (status: string) => {
    setStatusFilter(current => current.includes(status) ? current.filter(value => value !== status) : [...current, status]);
  };

  const toggleSort = (column: typeof sortBy) => {
    if (sortBy === column) setSortDirection(direction => direction === 'asc' ? 'desc' : 'asc');
    else { setSortBy(column); setSortDirection(column === 'createdAt' ? 'desc' : 'asc'); }
  };

  return <>
    {/* Spinner keyframes are needed by inline status badges (approved/executing
        rows) even when no MasProgress panel is mounted to define them. */}
    <style>{`
      @keyframes masSpin { to { transform: rotate(360deg); } }
      .mas-spinner { display:inline-block; width:12px; height:12px; border:2px solid rgba(129,140,248,0.3); border-top-color:#818cf8; border-radius:50%; animation: masSpin 0.8s linear infinite; }
    `}</style>
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
    <div className="queue-two-column">
    <section className="queue-table-shell" aria-label="Danh sách action queue hợp nhất">
      <div className="queue-table-toolbar">
        <div className="queue-filter-group">
          <span className="queue-filter-label">Loại MAS</span>
          <select className="funnel-filter-select" value={masTypeFilter} onChange={event => setMasTypeFilter(event.target.value)} aria-label="Lọc theo loại MAS">
            <option value="all">Tất cả loại</option>
            {QUEUES.map(([key, title]) => <option key={key} value={key}>{title}</option>)}
          </select>
        </div>
        <div className="queue-filter-group queue-status-filters">
          <span className="queue-filter-label">Trạng thái</span>
          {(['pending', 'approved', 'processed', 'rejected', 'deleted'] as const).map(status => (
            <label key={status} className={`queue-filter-chip ${statusFilter.includes(status) ? 'is-active' : ''}`}>
              <input type="checkbox" checked={statusFilter.includes(status)} onChange={() => toggleStatusFilter(status)} />
              {status === 'pending' ? 'Chờ duyệt' : status[0].toUpperCase() + status.slice(1)}
            </label>
          ))}
        </div>
        <div className="queue-filter-summary"><strong>{filteredItems.length}</strong> / {items.length} mục</div>
      </div>
      <div className="queue-table-scroll" tabIndex={0} role="region" aria-label="Danh sách queue, cuộn ngang để xem thêm cột">
        <table className="queue-table">
          <colgroup><col className="queue-col-check" /><col className="queue-col-number" /><col className="queue-col-seeker" /><col className="queue-col-content" /><col className="queue-col-time" /><col className="queue-col-type" /><col className="queue-col-status" /></colgroup>
          <thead><tr>
            <th aria-label="Chọn" />
            <th>#</th>
            <th><button className="queue-sort-button" onClick={() => toggleSort('targetName')}>Seeker Name <span>{sortBy === 'targetName' ? (sortDirection === 'asc' ? '↑' : '↓') : '↕'}</span></button></th>
            <th>Nội dung</th>
            <th><button className="queue-sort-button" onClick={() => toggleSort('createdAt')}>Time <span>{sortBy === 'createdAt' ? (sortDirection === 'asc' ? '↑' : '↓') : '↕'}</span></button></th>
            <th><button className="queue-sort-button" onClick={() => toggleSort('queueType')}>Loại MAS <span>{sortBy === 'queueType' ? (sortDirection === 'asc' ? '↑' : '↓') : '↕'}</span></button></th>
            <th><button className="queue-sort-button" onClick={() => toggleSort('status')}>Trạng thái <span>{sortBy === 'status' ? (sortDirection === 'asc' ? '↑' : '↓') : '↕'}</span></button></th>
          </tr></thead>
          <tbody>
            {filteredItems.length === 0 ? <tr><td colSpan={7} className="queue-table-empty">Không có mục phù hợp với bộ lọc.</td></tr> : filteredItems.map((item, index) => {
              const selected = selectedQueueItemIds.has(item.id);
              const selectedRow = selectedItem?.id === item.id;
              const type = queueMeta(item.queueType);
              const status = displayStatus(item.status, item.payloadJson);
              const canApprove = item.status === 'pending' || item.status === 'rejected' || item.status === 'deleted';
              const canReprocess = filterStatus(item.status, item.payloadJson) === 'processed';
              const decisionLabels = DECISION_LABELS[item.queueType];
              return <tr key={item.id} className={`${selectedRow ? 'is-focused' : ''} ${selected ? 'is-checked' : ''}`} onClick={() => selectItem(item, false)}>
                <td><input type="checkbox" checked={selected} onChange={() => setSelectedQueueItemIds(current => { const next = new Set(current); if (next.has(item.id)) next.delete(item.id); else next.add(item.id); return next; })} onClick={event => event.stopPropagation()} aria-label={`Chọn mục #${item.id}`} /></td>
                <td className="queue-table-id">{index + 1}</td>
                <td><div className="queue-seeker-cell"><Link href={queueSeekerDetailUrl(item)} onClick={event => event.stopPropagation()}>{item.targetName || item.targetType}</Link><button type="button" className="queue-icon-button" onClick={event => { event.stopPropagation(); handleTogglePin(item); }} title="Xem nhanh seeker" aria-label="Xem nhanh seeker">ⓘ</button></div></td>
                <td className="queue-content-cell"><div>{item.reactionType ? `React: ${item.reactionType}` : item.actionText || '—'}</div>{item.errorText && <small className="queue-failure">{item.errorText}</small>}</td>
                <td className="queue-time-cell">{formatQueueTime(item.createdAt)}</td>
                <td><span className={`queue-type-icon queue-type-icon--${item.queueType}`} title={type.title.replace(/^\d+\.\s*/, '')} aria-label={type.title.replace(/^\d+\.\s*/, '')}>{type.icon}</span></td>
                <td><div className="queue-status-cell"><span className={`queue-status-badge ${status.tone}`} title={item.errorText || undefined}>{status.label}</span><div className="queue-row-actions">
                  {canApprove && <button type="button" className="queue-icon-action queue-icon-action--approve" onClick={event => { event.stopPropagation(); void decide(item.id, 'approve'); }} title={item.status === 'pending' ? (decisionLabels?.approve || 'Approve') : 'Approve lại'} aria-label={item.status === 'pending' ? (decisionLabels?.approve || 'Approve') : 'Approve lại'}>✓</button>}
                  {canReprocess && <button type="button" className="queue-icon-action queue-icon-action--reprocess" onClick={event => { event.stopPropagation(); void decide(item.id, 'reprocess'); }} title="Re-process again" aria-label="Re-process again">↻</button>}
                  {item.status === 'pending' && <button type="button" className="queue-icon-action queue-icon-action--reject" onClick={event => { event.stopPropagation(); void decide(item.id, 'reject'); }} title={decisionLabels?.reject || 'Từ chối'} aria-label={decisionLabels?.reject || 'Từ chối'}>×</button>}
                  {(item.status === 'pending' || item.status === 'approved') && <button type="button" className="queue-icon-action queue-icon-action--delete" onClick={event => { event.stopPropagation(); void deleteQueueItems([item.id]); }} title="Xóa" aria-label="Xóa">⌫</button>}
                </div></div></td>
              </tr>;
            })}
          </tbody>
        </table>
      </div>
    </section>
    <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start', position: 'relative' }}>
      <div style={{ flex: 1, minWidth: 0, display: 'none' }}>
        <div className="queue-grid">
          {QUEUES.map(([key, title, icon]) => {
            const queueItems = items.filter(item => item.queueType === key);
            const selectedInQueue = queueItems.filter(item => selectedQueueItemIds.has(item.id));
            const selectableInQueue = selectedInQueue.filter(item => item.targetType === 'thread' && item.targetId);
            const deletableInQueue = selectedInQueue.filter(item => item.status === 'pending' || item.status === 'approved');
            return <section className="queue-card" key={key}>
              <div className="queue-heading">
                <div className="queue-heading__label">
                  <span>{icon}</span>
                  <div><h2>{title}</h2><p>{queueItems.length} đang chờ / đang xử lý</p></div>
                </div>
                <div className="queue-heading__actions">
                  <button
                    type="button"
                    className="recommendation-action recommendation-action--reply queue-heading__action"
                    disabled={loadingContext !== null || selectableInQueue.length === 0}
                    onClick={() => runSelectedQueue(key, queueItems)}
                    title={selectableInQueue.length ? `Chạy lại MAS cho ${selectableInQueue.length} seeker đã chọn — đề xuất mới sẽ thay thế đề xuất hiện tại` : 'Chọn ít nhất một seeker DM trong queue này'}
                  >
                    {loadingContext === key ? '⏳ Đang chạy MAS...' : `⚡ Chạy đề xuất MAS${selectableInQueue.length ? ` (${selectableInQueue.length})` : ''}`}
                  </button>
                  <button
                    type="button"
                    className="queue-delete-button queue-delete-button--selected"
                    disabled={deletableInQueue.length === 0}
                    onClick={() => void deleteQueueItems(deletableInQueue.map(item => item.id))}
                    aria-label={`Xóa ${deletableInQueue.length} mục đã chọn`}
                    title={deletableInQueue.length ? `Xóa ${deletableInQueue.length} mục đã chọn` : 'Chọn mục đang chờ hoặc đã duyệt để xóa'}
                  >
                    <span aria-hidden="true">🗑️</span>
                    <span>Xóa đã chọn{deletableInQueue.length ? ` (${deletableInQueue.length})` : ''}</span>
                  </button>
                </div>
              </div>
              {queueItems.length === 0 ? <p className="queue-empty">Không có đề xuất chờ quyết định.</p> : queueItems.map((item, index) => {
                const isSelected = selectedItem?.id === item.id;
                const isRangeSelected = selectedQueueItemIds.has(item.id);
                const isPinned = pinnedItemId === item.id;
                const jobTargetsItem = !!recommendationJob && !!item.targetId && recommendationJob.threadIds.includes(item.targetId);
                const showJobInline = jobTargetsItem && recommendationJob && (
                  recommendationJob.status === 'queued'
                  || recommendationJob.status === 'running'
                  || flashJobId === recommendationJob.id
                );
                // A batch job has one shared step state. Mount the full trace
                // once, inside the first matching queue item.
                const showJobSteps = showJobInline && recommendationJob && recommendationJob.threadIds[0] === item.targetId;
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
                      <span className={`queue-status ${item.status}`}>{displayStatus(item.status, item.payloadJson).label}</span>
                    </div>
                    <div className="queue-item-title-row">
                      <strong
                        className="queue-item-username"
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
                        title="Di chuột để xem thông tin Seeker & lý do đề xuất MAS"
                      >
                        <Link
                          className="queue-item-username-link"
                          href={queueSeekerDetailUrl(item)}
                          onClick={(event) => event.stopPropagation()}
                          title="Mở trang chi tiết Seeker"
                        >
                          {item.targetName || item.targetType}
                        </Link>
                        <button
                          type="button"
                          onClick={(event) => { event.stopPropagation(); handleTogglePin(item); }}
                          aria-label={`Xem thông tin ${item.targetName || item.targetType}`}
                          title="Ghim hoặc bỏ ghim thông tin Seeker"
                          style={{
                            border: 0,
                            background: 'transparent',
                            color: 'inherit',
                            cursor: 'pointer',
                            fontSize: '11px',
                            lineHeight: 1,
                            padding: 0,
                          }}
                        >
                          ℹ️
                        </button>
                        {isPinned && <span style={{ fontSize: '11px', color: '#818cf8' }} title="Đã ghim">📌</span>}
                      </strong>
                      {(() => {
                        if (item.status !== 'pending' && item.status !== 'approved' && item.status !== 'executing') {
                          return null;
                        }
                        const decisionLabels = DECISION_LABELS[item.queueType];
                        if (item.status === 'approved') {
                          return (
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12px', fontWeight: 600, color: '#fbbf24', whiteSpace: 'nowrap' }}>
                              <span>⏳</span><span>{decisionLabels ? decisionLabels.approvedNote : 'Đã duyệt — chờ worker thực thi'}</span>
                            </span>
                          );
                        }
                        if (item.status === 'executing') {
                          if (parsePayload(item.payloadJson).delivery_status === 'drafted') {
                            return <span>Đã soạn trên Facebook — chờ bạn nhấn Enter</span>;
                          }
                          return (
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12px', fontWeight: 600, color: '#818cf8', whiteSpace: 'nowrap' }}>
                              <span className="mas-spinner" />
                              <span>⚙️ Đang thực thi…</span>
                            </span>
                          );
                        }
                        return (
                          <div className="queue-actions queue-actions--inline">
                            <button
                              onClick={(event) => { event.stopPropagation(); void decide(item.id, 'approve'); }}
                            >
                              {decisionLabels ? decisionLabels.approve : 'Duyệt & xếp thực thi'}
                            </button>
                            <button
                              className="reject"
                              onClick={(event) => { event.stopPropagation(); void decide(item.id, 'reject'); }}
                            >
                              {decisionLabels ? decisionLabels.reject : 'Từ chối'}
                            </button>
                            <button
                              type="button"
                              className="queue-delete-button"
                              onClick={(event) => { event.stopPropagation(); void deleteQueueItems([item.id]); }}
                            aria-label={`Xóa đề xuất của ${item.targetName || item.targetType}`}
                            title="Xóa đề xuất"
                          >
                            <span aria-hidden="true">🗑️</span>
                            <span>Xóa</span>
                            </button>
                          </div>
                        );
                      })()}
                    </div>
                    {showJobSteps && recommendationJob && (
                      <div
                        className="queue-item-mas-progress"
                        onClick={(event) => event.stopPropagation()}
                        aria-label={`Tiến trình MAS cho job ${recommendationJob.id}`}
                      >
                        <MasProgress job={recommendationJob} />
                      </div>
                    )}
                    <div style={{ marginTop: '8px', padding: '10px 12px', borderRadius: '8px', background: 'rgba(15, 23, 42, 0.62)', border: '1px solid rgba(129, 140, 248, 0.18)' }}>
                      <div style={{ fontSize: '9px', color: '#a5b4fc', fontWeight: 800, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '5px' }}>Nội dung đề xuất</div>
                      <div style={{ whiteSpace: 'pre-wrap', fontSize: '13px', lineHeight: 1.55, color: 'var(--text-primary)' }}>{item.reactionType ? `React: ${item.reactionType}` : item.actionText}</div>
                    </div>
                    {item.errorText && <p className="queue-failure">Lỗi: {item.errorText}</p>}
                    {item.status === 'approved' && <small>Đã duyệt qua {item.approvalSource}; chờ worker ở đầu queue.</small>}
                  </article>
                );
              })}
            </section>;
          })}
        </div>
      </div>

      {/* ── Right Sidebar (shared with /seekers) ── */}
      <SeekerSidebar
        seeker={sidebarData?.seeker || (selectedItem ? seekerFromQueueItem(selectedItem) : null)}
        detail={sidebarData}
        loading={sidebarLoading}
        detailHref={seekerDetailUrl(sidebarData?.seeker, selectedItem?.targetId || selectedItem?.targetName)}
        onClose={handleCloseSidebar}
        onRefresh={() => {
          if (selectedItem) void loadSeekerData(selectedItem);
        }}
        onMouseEnter={handleSidebarMouseEnter}
        onMouseLeave={handleSidebarMouseLeave}
        headerBadge={selectedItem ? (
          <span style={{
            display: 'inline-block',
            marginTop: '5px',
            padding: '1px 6px',
            border: pinnedItemId === selectedItem.id ? '1px solid rgba(99, 102, 241, 0.4)' : '1px solid transparent',
            borderRadius: '10px',
            background: pinnedItemId === selectedItem.id ? 'rgba(99, 102, 241, 0.2)' : 'transparent',
            color: pinnedItemId === selectedItem.id ? '#a5b4fc' : 'var(--text-muted)',
            fontSize: '10px',
            fontWeight: pinnedItemId === selectedItem.id ? 600 : 400,
          }}>
            {pinnedItemId === selectedItem.id ? '📌 Đã ghim' : 'Xem nhanh (hover)'}
          </span>
        ) : null}
        beforeJourney={selectedItem ? (() => {
          const payload = parsePayload(selectedItem.payloadJson);
          const masReason = getMasReasonDetails(selectedItem, payload, sidebarData);
          return (
            <div style={{
              padding: '12px 14px',
              marginBottom: '14px',
              border: '1px solid rgba(99, 102, 241, 0.35)',
              borderRadius: '10px',
              background: 'linear-gradient(135deg, rgba(99, 102, 241, 0.14), rgba(168, 85, 247, 0.09))',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', marginBottom: '8px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#a5b4fc', fontSize: '11px', fontWeight: 700, letterSpacing: '0.05em', textTransform: 'uppercase' }}>
                  <span>🤖</span>
                  <span>Lý do trong hàng đợi (MAS Proof)</span>
                </div>
                <span style={{ flexShrink: 0, padding: '2px 7px', borderRadius: '8px', background: 'rgba(99, 102, 241, 0.25)', color: '#c7d2fe', fontSize: '10px', fontWeight: 600 }}>
                  {masReason.source}
                </span>
              </div>
              <div style={{ marginBottom: '4px', color: '#38bdf8', fontSize: '12px', fontWeight: 600 }}>
                {masReason.triggerLabel}
              </div>
              <div style={{ marginBottom: '6px', color: 'var(--text-secondary)', fontSize: '12px', lineHeight: 1.45 }}>
                {masReason.triggerDesc}
              </div>
              {masReason.classification && (
                <div style={{ marginBottom: '6px', color: '#fbbf24', fontSize: '11px' }}>
                  🏷️ Phân loại ý định: <strong>{masReason.classification}</strong>
                </div>
              )}
              <div style={{
                padding: '8px 10px',
                marginTop: '8px',
                borderLeft: '3px solid #818cf8',
                borderRadius: '6px',
                background: 'rgba(0,0,0,0.25)',
                fontSize: '11px',
              }}>
                <div style={{ marginBottom: '2px', color: '#818cf8', fontWeight: 700 }}>Nội dung đề xuất do MAS soạn thảo:</div>
                <div style={{ color: 'var(--text-primary)', fontStyle: 'italic', lineHeight: 1.35 }}>
                  &ldquo;{selectedItem.actionText || (selectedItem.reactionType ? `Reaction: ${selectedItem.reactionType}` : '')}&rdquo;
                </div>
              </div>
              {selectedItem.status === 'pending' && (
                <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                  <button
                    onClick={() => void decide(selectedItem.id, 'approve')}
                    style={{
                      flex: 1,
                      padding: '6px 10px',
                      border: 'none',
                      borderRadius: '6px',
                      background: 'var(--accent-emerald)',
                      color: '#052e24',
                      fontSize: '11px',
                      fontWeight: 700,
                      cursor: 'pointer',
                    }}
                  >
                    ✓ Duyệt đề xuất này
                  </button>
                  <button
                    onClick={() => void decide(selectedItem.id, 'reject')}
                    className="reject"
                    style={{
                      padding: '6px 10px',
                      border: '1px solid rgba(244,63,94,.45)',
                      borderRadius: '6px',
                      background: 'transparent',
                      color: 'var(--accent-rose)',
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
        })() : null}
        emptyState={<SeekerSidebarEmptyState />}
        className={`queue-seeker-sidebar${selectedItem ? ' is-open' : ''}`}
        style={{
          maxHeight: 'calc(100vh - 140px)',
          position: 'sticky',
          top: '20px',
          border: selectedItem ? '1px solid var(--border-glow)' : '1px dashed var(--border-subtle)',
          boxShadow: selectedItem ? '0 8px 32px rgba(0,0,0,0.4)' : 'none',
          transition: 'border-color 0.2s ease, box-shadow 0.2s ease',
          zIndex: 40,
        }}
      />
    </div>
    </div>
  </>;
}
