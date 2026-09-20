// code:web-component-002:seekers-table
'use client';

import { useState, useEffect, useCallback, useRef, useTransition } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { formatRelativeElapsed, getStageNumber, type Seeker } from '@/lib/types';
import { isDateInRange, parseRealDate } from '@/lib/funnel-filters';
import { InteractionHistogram } from './interaction-histogram';
import { FunnelFilterBar, type FilterState } from './funnel-filter-bar';
import { SevenStarProgress } from './seven-star-progress';
import { SeekerSidebar } from './seeker-sidebar';
import { MasProgress, type MasJob, type MasRunType } from './mas-progress';
import { PROGRAMS } from '@/lib/programs';

type SortField = keyof Seeker | 'lastMessageDate' | 'lastMessageTimestampText';

interface SeekersTableProps {
  initialSeekers: Seeker[];
}

// ── Distinct color per city ──
const CITY_COLORS: Record<string, { bg: string; text: string }> = {
  'Hà Nội':           { bg: 'rgba(99, 102, 241, 0.15)',  text: '#818cf8' },
  'Bắc Ninh':         { bg: 'rgba(6, 182, 212, 0.15)',   text: '#22d3ee' },
  'Hải Phòng':        { bg: 'rgba(16, 185, 129, 0.15)',  text: '#34d399' },
  'Hưng Yên':         { bg: 'rgba(245, 158, 11, 0.15)',  text: '#fbbf24' },
  'Nghệ An':          { bg: 'rgba(244, 63, 94, 0.15)',   text: '#fb7185' },
  'Đà Nẵng':          { bg: 'rgba(139, 92, 246, 0.15)',  text: '#a78bfa' },
  'Tp. Hồ Chí Minh':  { bg: 'rgba(236, 72, 153, 0.15)', text: '#f472b6' },
  'Unknown':          { bg: 'rgba(107, 114, 128, 0.12)', text: '#9ca3af' },
};

function getCityStyle(city: string) {
  return CITY_COLORS[city] || CITY_COLORS['Unknown'];
}


const PAGE_ID = '1548373332058326';

function seekerDetailUrl(seeker: Seeker) {
  return seeker.source === 'dm'
    ? `/seekers/${seeker.id}`
    : `/seekers/comment-${seeker.id}`;
}

function seekerSelectionKey(seeker: Seeker) {
  return `${seeker.source}:${seeker.id}`;
}

function facebookProfileUrl(value: string | null) {
  if (!value) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return `https://www.facebook.com/${trimmed}`;
  const candidate = trimmed.startsWith('http') ? trimmed : `https://${trimmed}`;
  try {
    const url = new URL(candidate);
    return /(^|\.)facebook\.com$/i.test(url.hostname) && url.pathname.length > 1 ? url.toString() : null;
  } catch {
    return null;
  }
}

function facebookInboxUrl(seeker: Seeker) {
  const profileUrl = facebookProfileUrl(seeker.fbProfileUrl);
  const profileId = profileUrl ? new URL(profileUrl).pathname.split('/').filter(Boolean).at(-1) : null;
  const userId = seeker.fbUserId?.trim() || profileId;
  return seeker.source === 'dm' && userId
    ? `https://business.facebook.com/latest/inbox/all?asset_id=${PAGE_ID}&selected_item_id=${encodeURIComponent(userId)}&thread_type=FB_MESSAGE`
    : null;
}

export function SeekersTable({ initialSeekers }: SeekersTableProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const journeyStage = searchParams.get('journeyStage') || '';
  const seekers = initialSeekers;
  const [isRefreshing, startRefresh] = useTransition();
  const pendingClassifications = seekers.filter(
    seeker => seeker.classificationStatus === 'pending',
  ).length;

  useEffect(() => {
    if (!pendingClassifications) return;
    const refreshProgress = () => startRefresh(() => router.refresh());
    const interval = setInterval(() => {
      refreshProgress();
    }, 2500);
    return () => clearInterval(interval);
  }, [pendingClassifications, router, startRefresh]);
  const [sortField, setSortField] = useState<SortField>('lastMessageDate');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [search, setSearch] = useState('');
  const [activityData, setActivityData] = useState<Record<string, { date: string; count: number }[]>>({});

  // ── Right Sidebar state ──
  const [selectedSeeker, setSelectedSeeker] = useState<Seeker | null>(null);
  const [selectedSeekerKeys, setSelectedSeekerKeys] = useState<Set<string>>(new Set());
  const selectionAnchorRef = useRef<number | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [sidebarData, setSidebarData] = useState<any>(null);
  const [sidebarLoading, setSidebarLoading] = useState(false);

  // Fetch activity for visible seekers
  useEffect(() => {
    const fetchActivity = async () => {
      const newData: Record<string, { date: string; count: number }[]> = {};
      for (const s of seekers.slice(0, 20)) {
        try {
          const res = await fetch(`/api/seekers?action=activity&name=${encodeURIComponent(s.name)}`);
          const json = await res.json();
          newData[s.name] = json.activity || [];
        } catch {
          newData[s.name] = [];
        }
      }
      setActivityData(newData);
    };
    fetchActivity();
  }, [seekers]);

  // ── Filter State (City & Date Range) ──
  const [filterState, setFilterState] = useState<FilterState>({ city: 'all', programCode: 'all', dateRange: 'all' });
  const [batchModalOpen, setBatchModalOpen] = useState(false);
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchResult, setBatchResult] = useState<string | null>(null);
  const [batchJob, setBatchJob] = useState<MasJob | null>(null);
  const [replyInstruction, setReplyInstruction] = useState('Soạn tin nhắn phản hồi phù hợp với câu hỏi mới nhất của seeker; trả lời rõ ràng, thân thiện và không hỏi lại thông tin đã có.');
  const [reminderInstruction, setReminderInstruction] = useState('Soạn tin nhắc lịch học phù hợp cho seeker đã chọn. Chỉ đề xuất khi có lịch đã được xác thực và seeker còn phù hợp để nhận tin.');
  const [warmupInstruction, setWarmupInstruction] = useState('Soạn một tin nhắn warm-up chủ động, nhẹ nhàng và phù hợp với hành trình của seeker. Không đề xuất gửi nếu seeker đã từ chối nhận tin.');

  // Sort & filter
  const sorted = [...seekers]
    .filter(s => {
      // City filter
      if (filterState.city !== 'all') {
        const targetCity = filterState.city.toLowerCase();
        const seekerCity = (s.city || 'Unknown').toLowerCase();
        if (seekerCity !== targetCity) return false;
      }
      if (filterState.programCode !== 'all' && s.programCode !== filterState.programCode) return false;
      if (!isDateInRange(s.lastMessageDate || s.lastMessageTimestampText || s.lastInteraction || s.firstSeen, filterState.dateRange)) return false;
      if (journeyStage) {
        const targetStageNum = getStageNumber(journeyStage);
        const seekerStageNum = getStageNumber(s.leadStage);
        if (targetStageNum !== seekerStageNum && s.leadStage !== journeyStage) return false;
      }
      // Search
      if (!search) return true;
      const q = search.toLowerCase();
      return s.name?.toLowerCase().includes(q) ||
        s.realName?.toLowerCase().includes(q) ||
        s.city?.toLowerCase().includes(q) ||
        s.programCode?.toLowerCase().includes(q) ||
        s.phone?.toLowerCase().includes(q) ||
        s.email?.toLowerCase().includes(q);
    })
    .sort((a, b) => {
      if (sortField === 'lastMessageDate' || sortField === 'lastMessageTimestampText' || sortField === 'lastInteraction' || sortField === 'firstSeen') {
        if (sortField !== 'firstSeen' && sortField !== 'lastInteraction') {
          const rankA = a.source === 'dm' ? a.inboxSortIndex : null;
          const rankB = b.source === 'dm' ? b.inboxSortIndex : null;
          if (rankA != null || rankB != null) {
            if (rankA == null) return 1;
            if (rankB == null) return -1;
          }
        }
        const getSeekerTime = (s: Seeker) => {
          if (sortField === 'firstSeen') {
            return parseRealDate(s.firstSeen) || new Date(s.firstSeen || 0).getTime() || 0;
          }
          if (sortField === 'lastInteraction') {
            return parseRealDate(s.lastInteraction) || new Date(s.lastInteraction || 0).getTime() || 0;
          }
          if (s.lastMessageDate) {
            const ms = new Date(s.lastMessageDate).getTime();
            if (!isNaN(ms) && ms > 0) return ms;
          }
          return parseRealDate(s.lastMessageAt) || parseRealDate(s.lastInteraction) || parseRealDate(s.firstSeen) || parseRealDate(s.lastMessageTimestampText) || 0;
        };
        const timeA = getSeekerTime(a);
        const timeB = getSeekerTime(b);
        if (timeA !== timeB) return sortDir === 'asc' ? timeA - timeB : timeB - timeA;
        if (sortField !== 'firstSeen' && sortField !== 'lastInteraction') {
          const rankA = a.source === 'dm' ? a.inboxSortIndex : null;
          const rankB = b.source === 'dm' ? b.inboxSortIndex : null;
          if (rankA != null && rankB != null && rankA !== rankB) {
            return sortDir === 'desc' ? rankA - rankB : rankB - rankA;
          }
        }
        return 0;
      }
      if (sortField === 'leadStage') {
        const stageA = getStageNumber(a.leadStage);
        const stageB = getStageNumber(b.leadStage);
        return sortDir === 'asc' ? stageA - stageB : stageB - stageA;
      }
      const aVal = a[sortField as keyof Seeker] ?? '';
      const bVal = b[sortField as keyof Seeker] ?? '';
      const cmp = String(aVal).localeCompare(String(bVal));
      return sortDir === 'asc' ? cmp : -cmp;
    });

  const selectedDmThreadIds = sorted
    .filter(seeker => selectedSeekerKeys.has(seekerSelectionKey(seeker)) && seeker.source === 'dm' && seeker.threadId)
    .map(seeker => seeker.threadId as string);

  // Keep the modal open while MAS is running so the progress view stays visible.
  const closeBatchModal = () => {
    if (batchRunning) return;
    setBatchModalOpen(false);
    setBatchResult(null);
    setBatchJob(null);
  };

  const pollBatchJob = async (jobId: number) => {
    try {
      const res = await fetch(`/api/action-queue/recommendations?jobId=${jobId}`);
      const data = await res.json();
      if (!res.ok || !data.job) throw new Error(data.error || 'Không đọc được trạng thái job');
      const job = data.job as MasJob;
      setBatchJob(job);
      if (job.status === 'completed') {
        setBatchResult(job.result?.message || `Đã tạo ${job.result?.count ?? 0} đề xuất mới vào hàng đợi chờ duyệt.`);
        setBatchRunning(false);
        // The table receives its seeker rows from the server. Refresh it as
        // soon as the durable job completes so Pending message appears without
        // a manual browser reload.
        startRefresh(() => router.refresh());
        return;
      }
      if (job.status === 'failed') {
        setBatchResult(`Lỗi: ${job.error || 'MAS không thể hoàn tất.'}`);
        setBatchRunning(false);
        return;
      }
      window.setTimeout(() => { void pollBatchJob(jobId); }, 900);
    } catch (error) {
      setBatchResult(`Lỗi: ${error instanceof Error ? error.message : 'Không thể theo dõi MAS job.'}`);
      setBatchRunning(false);
    }
  };

  const handleRunBatchRec = async (type: MasRunType, instruction: string, carePurpose?: 'class_reminder' | 'warmup' | 'event') => {
    if (!selectedDmThreadIds.length) return;
    if (!instruction.trim()) {
      setBatchResult('⚠️ Hãy nhập nội dung chỉ dẫn cho MAS.');
      return;
    }
    setBatchRunning(true);
    setBatchResult(null);
    try {
      const res = await fetch('/api/action-queue/recommendations', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          type,
          threadIds: selectedDmThreadIds,
          city: filterState.city !== 'all' ? filterState.city : undefined,
          programCode: filterState.programCode !== 'all' ? filterState.programCode : undefined,
          instruction,
          carePurpose,
          limit: selectedDmThreadIds.length,
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.job) {
        setBatchResult(`Lỗi: ${data.error || 'Không thể tạo MAS job'}`);
        setBatchRunning(false);
        return;
      }
      setBatchJob(data.job);
      void pollBatchJob(data.job.id);
    } catch {
      setBatchResult('Lỗi kết nối khi gửi yêu cầu đề xuất.');
      setBatchRunning(false);
    }
  };

  const handleSort = (field: SortField) => {
    if (sortField === field || (field === 'lastMessageDate' && sortField === 'lastMessageTimestampText')) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDir('desc');
    }
  };

  // ── Row click → open sidebar ──
  const handleRowClick = useCallback(async (seeker: Seeker, index: number, shiftKey: boolean) => {
    if (shiftKey && selectionAnchorRef.current !== null) {
      const start = Math.min(selectionAnchorRef.current, index);
      const end = Math.max(selectionAnchorRef.current, index);
      setSelectedSeekerKeys(new Set(sorted.slice(start, end + 1).map(seekerSelectionKey)));
    } else {
      selectionAnchorRef.current = index;
      setSelectedSeekerKeys(new Set([seekerSelectionKey(seeker)]));
    }
    setSelectedSeeker(seeker);
    setSidebarData(null);
    setSidebarLoading(true);
    const seekerId = seeker.source === 'dm' ? String(seeker.id) : `comment-${seeker.id}`;
    try {
      const res = await fetch(`/api/seekers/${encodeURIComponent(seekerId)}`);
      const data = await res.json();
      setSidebarData(data);
    } catch { setSidebarData(null); }
    setSidebarLoading(false);
  }, [sorted]);

  return (
    <div className="seekers-workspace">
      {/* ── City & Date Range Filter Bar ── */}
      <FunnelFilterBar
        onFilterChange={setFilterState}
        availablePrograms={PROGRAMS}
        totalCount={seekers.length}
        filteredCount={sorted.length}
        unitLabel="contacts"
        extraControls={
          <>
          {journeyStage && (
            <button
              type="button"
              onClick={() => router.push('/seekers')}
              className="funnel-filter-reset"
              title="Bỏ lọc hành trình"
            >
              Journey: {journeyStage} ✕
            </button>
          )}
          <button
            type="button"
            onClick={() => setBatchModalOpen(true)}
            disabled={selectedDmThreadIds.length === 0}
            title={selectedDmThreadIds.length ? `Chạy MAS cho ${selectedDmThreadIds.length} seeker DM đã chọn` : 'Chọn ít nhất một seeker DM; giữ Shift để chọn một dải'}
            style={{
              padding: '7px 14px',
              borderRadius: '8px',
              background: 'linear-gradient(135deg, #6366f1, #818cf8)',
              color: '#fff',
              fontWeight: 700,
              fontSize: '12px',
              border: 'none',
              cursor: selectedDmThreadIds.length ? 'pointer' : 'not-allowed',
              opacity: selectedDmThreadIds.length ? 1 : 0.45,
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              boxShadow: '0 2px 10px rgba(99, 102, 241, 0.3)',
            }}
          >
            ⚡ Chạy đề xuất MAS{selectedDmThreadIds.length ? ` (${selectedDmThreadIds.length})` : ''}
          </button>
          </>
        }
      />

      {pendingClassifications > 0 && (
        <div className="seeker-classification-progress" role="status" aria-live="polite">
          <span className="seeker-classification-progress-icon" aria-hidden="true">
            <svg viewBox="0 0 16 16">
              <circle cx="8" cy="8" r="5.5" />
              <path d="M8 2.5a5.5 5.5 0 0 1 5.5 5.5" />
              <path d="M8 5.25v2.9l2 1.2" />
            </svg>
          </span>
          <div className="seeker-classification-progress-copy">
            <strong>LLM đang cập nhật City / Program</strong>
            <span>
              {pendingClassifications} seeker đang được suy luận
              {isRefreshing ? ' · đang lấy tiến trình mới…' : ' · tự cập nhật mỗi 2,5 giây'}
            </span>
          </div>
          <div
            className="seeker-classification-progress-track"
            role="progressbar"
            aria-label="Tiến trình phân loại seeker"
            aria-valuemin={0}
            aria-valuemax={seekers.length}
            aria-valuenow={seekers.length - pendingClassifications}
          >
            <span style={{ width: `${((seekers.length - pendingClassifications) / seekers.length) * 100}%` }} />
          </div>
          <button
            type="button"
            className="seeker-classification-refresh"
            onClick={() => startRefresh(() => router.refresh())}
            disabled={isRefreshing}
          >
            {isRefreshing ? 'Đang cập nhật…' : 'Cập nhật ngay'}
          </button>
        </div>
      )}

      <div className={`seekers-table-layout${selectedSeeker ? ' has-sidebar' : ''}`} style={{ display: 'flex', gap: '0px', position: 'relative' }}>
        {/* Main table area */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {/* Search */}
          <div style={{ marginBottom: '16px' }}>
            <input
              type="text"
              placeholder="Tìm kiếm theo tên, thành phố, SĐT, email..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{
                width: '100%', maxWidth: '400px', padding: '10px 16px',
                background: 'var(--bg-card)', border: '1px solid var(--border-subtle)',
                borderRadius: '10px', color: 'var(--text-primary)', fontSize: '13px', outline: 'none',
              }}
            />
          </div>

          {/* Table */}
          <div className="card seekers-table-scroll" tabIndex={0} role="region" aria-label="Danh sách seekers, cuộn ngang để xem thêm cột" style={{ overflow: 'auto', maxHeight: 'calc(100vh - 280px)' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: '36px' }}>#</th>
                  <th onClick={() => handleSort('name')}>Name {sortField === 'name' ? (sortDir === 'asc' ? '↑' : '↓') : ''}</th>
                  <th onClick={() => handleSort('phone')}>Phone</th>
                  <th onClick={() => handleSort('city')} style={{ minWidth: '230px' }}>City / Class</th>
                  <th onClick={() => handleSort('leadStage')} style={{ minWidth: '130px', textAlign: 'center' }}>
                    Stage {sortField === 'leadStage' ? (sortDir === 'asc' ? '↑' : '↓') : ''}
                  </th>
                  <th className="pending-message-heading" style={{ minWidth: selectedSeeker ? '130px' : '260px' }}>Pending message</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((seeker, idx) => {
                  const cityStyle = getCityStyle(seeker.city);
                  const isSelected = selectedSeekerKeys.has(seekerSelectionKey(seeker));
                  const profileUrl = facebookProfileUrl(seeker.fbProfileUrl);
                  const inboxUrl = facebookInboxUrl(seeker);
                  return (
                    <tr
                      key={`${seeker.source}-${seeker.id}-${idx}`}
                      onClick={(event) => handleRowClick(seeker, idx, event.shiftKey)}
                      onMouseDown={(event) => { if (event.shiftKey) event.preventDefault(); }}
                      style={{
                        cursor: 'pointer',
                        userSelect: 'none',
                        background: isSelected ? 'rgba(99, 102, 241, 0.1)' : undefined,
                        borderLeft: isSelected ? '3px solid #818cf8' : '3px solid transparent',
                      }}
                      >
                      <td style={{ color: 'var(--text-muted)', fontSize: '11px', fontWeight: 600 }}>{idx + 1}</td>
                      <td style={{ fontWeight: 600 }}>
                        <div className="seeker-name-cell">
                          {profileUrl && (
                            <a
                              href={profileUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="seeker-profile-icon"
                              aria-label={`Mở Facebook profile của ${seeker.name || 'seeker'} trong tab mới`}
                              title={`Mở trang cá nhân Facebook: ${seeker.name || ''}`}
                              onClick={e => e.stopPropagation()}
                            >
                              <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                                <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/>
                              </svg>
                            </a>
                          )}
                          {inboxUrl && (
                            <a
                              href={inboxUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="seeker-profile-icon seeker-inbox-icon"
                              aria-label={`Mở Facebook Message Inbox của ${seeker.name || 'seeker'} trong tab mới`}
                              title={`Mở Facebook Message Inbox: ${seeker.name || ''}`}
                              onClick={e => e.stopPropagation()}
                            >
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                <path d="M21 11.5a8.38 8.38 0 0 1-1.88 5.32A8.5 8.5 0 0 1 12.5 20a8.38 8.38 0 0 1-4.3-1.18L3 20l1.18-4.3A8.38 8.38 0 0 1 3 11.5 8.5 8.5 0 0 1 11.5 3 8.5 8.5 0 0 1 21 11.5Z" />
                                <path d="m8.5 12 2.2 2 4.8-5" />
                              </svg>
                            </a>
                          )}
                          <div className="seeker-name-copy">
                            <button
                              type="button"
                              className="seeker-name-link"
                              onClick={(event) => {
                                event.stopPropagation();
                                router.push(seekerDetailUrl(seeker));
                              }}
                              title={`Mở chi tiết của ${seeker.name || 'seeker'}`}
                            >
                              {seeker.name || '—'}
                            </button>
                            {seeker.realName && seeker.realName !== seeker.name && (
                              <span className="seeker-real-name" title="Tên người đăng ký cung cấp trong cuộc trò chuyện">
                                {seeker.realName}
                              </span>
                            )}
                          </div>
                        </div>
                      </td>

                      <td>{seeker.phone || '—'}</td>
                      <td>
                        {seeker.classificationStatus === 'unknown' ? (
                          <span className="badge" style={{ background: 'rgba(107, 114, 128, 0.12)', color: '#9ca3af' }}>—</span>
                        ) : (
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                            {(seeker.classificationStatus !== 'pending' || seeker.city !== 'Unknown') && (
                              <span className="badge" style={{ background: cityStyle.bg, color: cityStyle.text }}>
                                {seeker.city}
                              </span>
                            )}
                            {seeker.classificationStatus === 'pending' && (
                              <span className="spinner" style={{ width: '14px', height: '14px', borderWidth: '2px', flexShrink: 0 }} aria-label="Đang suy luận" title="Đang bóc tách & suy luận city/program…"></span>
                            )}
                          </span>
                        )}
                        {seeker.programCode && (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', marginTop: '6px', fontSize: '10px', lineHeight: 1.3 }}>
                          {PROGRAMS.filter(program => program.code === seeker.programCode).map(program => (
                            <span key={program.code} title={`${program.day} · ${program.time} · ${program.location}`} style={{ color: seeker.programCode ? '#c4b5fd' : 'var(--text-muted)' }}>
                              {program.code}
                            </span>
                          ))}
                          </div>
                        )}
                      </td>
                      <td className="seeker-stage-cell">
                        <SevenStarProgress leadStage={seeker.leadStage} />
                        <div className="last-message-cell">
                          <time
                            dateTime={seeker.lastMessageDate || seeker.lastInteraction || undefined}
                            title={seeker.lastMessageDate || seeker.lastMessageTimestampText || seeker.lastInteraction || undefined}
                          >
                            {formatRelativeElapsed(seeker.lastMessageDate || seeker.lastInteraction || seeker.lastMessageTimestampText)}
                          </time>
                          <div className="last-message-activity" aria-label="Hoạt động tương tác trong 12 tháng qua">
                            <InteractionHistogram data={activityData[seeker.name] || []} />
                          </div>
                        </div>
                      </td>
                      <td className="pending-message-cell">
                        {seeker.pendingMessage ? (
                          <div title={seeker.pendingMessage} style={{ color: '#fcd34d', fontSize: '11px', lineHeight: 1.4 }}>
                            <div style={{ fontSize: '10px', color: '#a5b4fc', fontWeight: 700, marginBottom: '3px' }}>
                              ⏳ {seeker.pendingMessageKind || 'MAS'}
                            </div>
                            {seeker.pendingMessage.length > 150 ? `${seeker.pendingMessage.slice(0, 150)}…` : seeker.pendingMessage}
                          </div>
                        ) : <span style={{ color: 'var(--text-muted)' }}>—</span>}
                      </td>
                    </tr>
                  );
                })}
                {sorted.length === 0 && (
                  <tr><td colSpan={6} style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }}>No seekers found</td></tr>
                )}
              </tbody>
          </table>
        </div>
      </div>

      {/* ── Right Sidebar ── */}
      {selectedSeeker && (
        <SeekerSidebar
          seeker={selectedSeeker}
          detail={sidebarData}
          loading={sidebarLoading}
          showQueue={true}
          detailHref={seekerDetailUrl(selectedSeeker)}
          onClose={() => { setSelectedSeeker(null); setSidebarData(null); }}
          onRefresh={() => {
            const seekerId = selectedSeeker.source === 'dm' ? String(selectedSeeker.id) : `comment-${selectedSeeker.id}`;
            fetch(`/api/seekers/${encodeURIComponent(seekerId)}`)
              .then(res => res.json())
              .then(data => setSidebarData(data))
              .catch(() => {});
          }}
          style={{ marginLeft: '16px' }}
        />
      )}
      </div>
      {/* ── Batch Recommendations Modal ── */}
      {batchModalOpen && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.7)',
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1100,
          }}
          onClick={closeBatchModal}
        >
          <div
            className="card seekers-batch-modal"
            style={{
              width: '460px',
              maxWidth: '90vw',
              padding: '24px',
              background: 'var(--bg-secondary)',
              border: '1px solid var(--border-glow)',
              borderRadius: '16px',
              boxShadow: '0 12px 40px rgba(0,0,0,0.6)',
            }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
              <h3 style={{ fontSize: '16px', fontWeight: 700, color: 'var(--text-primary)' }}>
                ⚡ Chạy đề xuất MAS cho {selectedDmThreadIds.length} seeker
              </h3>
              <button
                type="button"
                onClick={closeBatchModal}
                style={{
                  background: 'rgba(255,255,255,0.06)',
                  border: 'none',
                  borderRadius: '6px',
                  color: 'var(--text-muted)',
                  fontSize: '14px',
                  cursor: 'pointer',
                  width: '28px',
                  height: '28px',
                }}
              >
                ✕
              </button>
            </div>

            <p style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5, marginBottom: '16px' }}>
              MAS (ADK agents) sẽ đọc lịch sử hội thoại của {selectedDmThreadIds.length} seeker DM đã chọn (giữ Shift khi click để chọn một dải) và tạo đề xuất hành động vào hàng đợi (action_queue)
              với trạng thái <strong>pending</strong>. Mỗi lượt chạy mất khoảng 10–60 giây tùy số seeker. Tuyệt đối <strong>không</strong> tự động gửi tin nhắn đến người dùng.
            </p>

            {batchJob && (
              <MasProgress job={batchJob} />
            )}

            {batchResult && (
              <div
                style={{
                  padding: '10px 14px',
                  borderRadius: '8px',
                  background: batchResult.startsWith('Lỗi')
                    ? 'rgba(244,63,94,0.1)'
                    : batchResult.startsWith('⚠️') ? 'rgba(245,158,11,0.12)' : 'rgba(16,185,129,0.1)',
                  color: batchResult.startsWith('Lỗi') ? '#fb7185' : batchResult.startsWith('⚠️') ? '#fbbf24' : '#34d399',
                  fontSize: '12px',
                  lineHeight: 1.4,
                  marginBottom: '14px',
                }}
              >
                {batchResult}
              </div>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ color: 'var(--text-secondary)', fontSize: '11px', lineHeight: 1.45 }}>
                Chọn mục đích, chỉnh nội dung chỉ dẫn nếu cần, rồi để MAS tạo bản nháp. Mọi đề xuất vẫn ở trạng thái <strong>pending</strong>, không tự gửi Facebook.
              </div>

              <section style={{ padding: '12px', borderRadius: '10px', background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(129,140,248,0.24)' }}>
                <label htmlFor="mas-reply-instruction" style={{ display: 'block', color: 'var(--text-primary)', fontSize: '13px', fontWeight: 700, marginBottom: '7px' }}>
                  1. 💬 Soạn tin nhắn phản hồi
                </label>
                <textarea
                  id="mas-reply-instruction"
                  value={replyInstruction}
                  onChange={event => setReplyInstruction(event.target.value)}
                  disabled={batchRunning}
                  rows={3}
                  style={{ width: '100%', resize: 'vertical', padding: '9px 10px', borderRadius: '7px', background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', color: 'var(--text-primary)', fontSize: '12px', lineHeight: 1.45, outline: 'none' }}
                />
                <button type="button" disabled={batchRunning || selectedDmThreadIds.length === 0} onClick={() => handleRunBatchRec('reply', replyInstruction)} style={{ marginTop: '8px', width: '100%', padding: '9px 12px', borderRadius: '7px', background: '#4f46e5', color: '#fff', fontWeight: 700, fontSize: '12px', border: 'none', cursor: batchRunning ? 'wait' : 'pointer', opacity: batchRunning || !selectedDmThreadIds.length ? 0.55 : 1 }}>
                  ⚡ Soạn phản hồi cho {selectedDmThreadIds.length} seeker
                </button>
              </section>

              <section style={{ padding: '12px', borderRadius: '10px', background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(52,211,153,0.24)' }}>
                <label htmlFor="mas-reminder-instruction" style={{ display: 'block', color: 'var(--text-primary)', fontSize: '13px', fontWeight: 700, marginBottom: '7px' }}>
                  2. 🗓️ Soạn tin nhắc lịch học / sự kiện
                </label>
                <textarea
                  id="mas-reminder-instruction"
                  value={reminderInstruction}
                  onChange={event => setReminderInstruction(event.target.value)}
                  disabled={batchRunning}
                  rows={3}
                  style={{ width: '100%', resize: 'vertical', padding: '9px 10px', borderRadius: '7px', background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', color: 'var(--text-primary)', fontSize: '12px', lineHeight: 1.45, outline: 'none' }}
                />
                <button type="button" disabled={batchRunning || selectedDmThreadIds.length === 0} onClick={() => handleRunBatchRec('care', reminderInstruction, 'class_reminder')} style={{ marginTop: '8px', width: '100%', padding: '9px 12px', borderRadius: '7px', background: '#059669', color: '#fff', fontWeight: 700, fontSize: '12px', border: 'none', cursor: batchRunning ? 'wait' : 'pointer', opacity: batchRunning || !selectedDmThreadIds.length ? 0.55 : 1 }}>
                  ⚡ Soạn tin nhắc cho {selectedDmThreadIds.length} seeker
                </button>
              </section>

              <section style={{ padding: '12px', borderRadius: '10px', background: 'rgba(245,158,11,0.06)', border: '1px solid rgba(251,191,36,0.24)' }}>
                <label htmlFor="mas-warmup-instruction" style={{ display: 'block', color: 'var(--text-primary)', fontSize: '13px', fontWeight: 700, marginBottom: '7px' }}>
                  3. 🌱 Warm-up chủ động
                </label>
                <textarea
                  id="mas-warmup-instruction"
                  value={warmupInstruction}
                  onChange={event => setWarmupInstruction(event.target.value)}
                  disabled={batchRunning}
                  rows={3}
                  style={{ width: '100%', resize: 'vertical', padding: '9px 10px', borderRadius: '7px', background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', color: 'var(--text-primary)', fontSize: '12px', lineHeight: 1.45, outline: 'none' }}
                />
                <button type="button" disabled={batchRunning || selectedDmThreadIds.length === 0} onClick={() => handleRunBatchRec('care', warmupInstruction, 'warmup')} style={{ marginTop: '8px', width: '100%', padding: '9px 12px', borderRadius: '7px', background: '#d97706', color: '#fff', fontWeight: 700, fontSize: '12px', border: 'none', cursor: batchRunning ? 'wait' : 'pointer', opacity: batchRunning || !selectedDmThreadIds.length ? 0.55 : 1 }}>
                  ⚡ Tạo warm-up cho {selectedDmThreadIds.length} seeker
                </button>
              </section>
            </div>

            <div style={{ marginTop: '16px', display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
              <button
                type="button"
                onClick={() => router.push('/queues')}
                style={{
                  padding: '8px 14px',
                  borderRadius: '8px',
                  background: 'rgba(255,255,255,0.06)',
                  border: '1px solid var(--border-subtle)',
                  color: 'var(--text-secondary)',
                  fontSize: '12px',
                  cursor: 'pointer',
                }}
              >
                Xem Hàng đợi (Queues) →
              </button>
              <button
                type="button"
                onClick={closeBatchModal}
                style={{
                  padding: '8px 14px',
                  borderRadius: '8px',
                  background: 'rgba(255,255,255,0.06)',
                  border: '1px solid var(--border-subtle)',
                  color: 'var(--text-primary)',
                  fontSize: '12px',
                  cursor: 'pointer',
                }}
              >
                Đóng
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Sidebar animation */}
      <style>{`
        @keyframes slideIn {
          from { opacity: 0; transform: translateX(20px); }
          to { opacity: 1; transform: translateX(0); }
        }
      `}</style>
    </div>
  );
}
