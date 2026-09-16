// code:web-component-002:seekers-table
'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { formatRelativeElapsed, getStageNumber, type Seeker } from '@/lib/types';
import { isDateInRange, parseRealDate } from '@/lib/funnel-filters';
import { InteractionHistogram } from './interaction-histogram';
import { FunnelFilterBar, type FilterState } from './funnel-filter-bar';
import { SevenStarProgress } from './seven-star-progress';
import { SeekerJourneyTimeline } from './seeker-journey-timeline';
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
  const [seekers] = useState(initialSeekers);
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

  const handleRunBatchRec = async (type: MasRunType) => {
    if (!selectedDmThreadIds.length) return;
    setBatchRunning(true);
    setBatchResult(null);
    try {
      const res = await fetch('/api/action-queue/recommendations', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ type, threadIds: selectedDmThreadIds, city: filterState.city !== 'all' ? filterState.city : undefined, limit: selectedDmThreadIds.length }),
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
    <div>
      {/* ── City & Date Range Filter Bar ── */}
      <FunnelFilterBar
        onFilterChange={setFilterState}
        availablePrograms={PROGRAMS}
        totalCount={seekers.length}
        filteredCount={sorted.length}
        unitLabel="seekers"
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

      <div style={{ display: 'flex', gap: '0px', position: 'relative' }}>
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
          <div className="card" style={{ overflow: 'auto', maxHeight: 'calc(100vh - 280px)' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: '36px' }}>#</th>
                  <th onClick={() => handleSort('name')}>Name {sortField === 'name' ? (sortDir === 'asc' ? '↑' : '↓') : ''}</th>
                  <th onClick={() => handleSort('phone')}>Phone</th>
                  <th onClick={() => handleSort('lastMessageDate')} style={{ cursor: 'pointer' }}>
                    Last Message {(sortField === 'lastMessageDate' || sortField === 'lastMessageTimestampText') ? (sortDir === 'asc' ? '↑' : '↓') : ''}
                  </th>
                  <th onClick={() => handleSort('city')} style={{ minWidth: '230px' }}>City / Class</th>
                  <th onClick={() => handleSort('leadStage')} style={{ minWidth: '130px', textAlign: 'center' }}>
                    Stage {sortField === 'leadStage' ? (sortDir === 'asc' ? '↑' : '↓') : ''}
                  </th>
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
                        <button
                          type="button"
                          className="seeker-name-link"
                          onClick={(e) => { e.stopPropagation(); router.push(seekerDetailUrl(seeker)); }}
                        >
                          {seeker.name || '—'}
                        </button>
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
                      </td>

                      <td>{seeker.phone || '—'}</td>
                      <td>
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
                      <td>
                        <span className="badge" style={{ background: cityStyle.bg, color: cityStyle.text }}>{seeker.city}</span>
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
                      <td style={{ textAlign: 'center' }}>
                        <SevenStarProgress leadStage={seeker.leadStage} />
                      </td>
                    </tr>
                  );
                })}
                {sorted.length === 0 && (
                  <tr><td colSpan={7} style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }}>No seekers found</td></tr>
                )}
              </tbody>
          </table>
        </div>
      </div>

      {/* ── Right Sidebar ── */}
      {selectedSeeker && (
        <div style={{
          width: '380px', minWidth: '380px', maxHeight: 'calc(100vh - 160px)', overflowY: 'auto',
          background: 'var(--bg-secondary)', border: '1px solid var(--border-glow)', borderRadius: '14px',
          padding: '20px', marginLeft: '16px', boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
          animation: 'slideIn 0.2s ease-out',
        }}>
          {/* Header + icon-only quick links */}
          <div className="seeker-sidebar-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '16px' }}>
            <div className="seeker-sidebar-heading">
              <div className="seeker-sidebar-name" style={{ fontSize: '16px', fontWeight: 700, color: 'var(--text-primary)' }}>{selectedSeeker.name}</div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>
                {selectedSeeker.source === 'dm' ? '💬 DM' : '💬 Comment'} · {selectedSeeker.city}
              </div>
            </div>
            <div className="seeker-sidebar-quick-links" aria-label="Seeker links">
              <a href={seekerDetailUrl(selectedSeeker)} className="seeker-sidebar-link seeker-sidebar-link--details"
                aria-label="Mở Full Details" title="Full Details" onClick={e => e.stopPropagation()}>
                <span aria-hidden="true">📋</span>
              </a>
              {facebookProfileUrl(selectedSeeker.fbProfileUrl) && (
                <a href={facebookProfileUrl(selectedSeeker.fbProfileUrl)!} target="_blank" rel="noopener noreferrer"
                  className="seeker-sidebar-link seeker-sidebar-link--profile"
                  aria-label={`Mở Facebook profile của ${selectedSeeker.name || 'seeker'} trong tab mới`}
                  title="Facebook Profile" onClick={e => e.stopPropagation()}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z" />
                  </svg>
                </a>
              )}
              {selectedSeeker.source === 'dm' && facebookProfileUrl(selectedSeeker.fbProfileUrl) && (
                <a href={`https://business.facebook.com/latest/inbox/all?asset_id=${PAGE_ID}&selected_item_id=${facebookProfileUrl(selectedSeeker.fbProfileUrl)!.split('/').pop()?.split('?')[0]}&thread_type=FB_MESSAGE`}
                  target="_blank" rel="noopener noreferrer" className="seeker-sidebar-link seeker-sidebar-link--inbox"
                  aria-label={`Mở Facebook Message Inbox của ${selectedSeeker.name || 'seeker'} trong tab mới`}
                  title="Facebook Inbox" onClick={e => e.stopPropagation()}>
                  <span aria-hidden="true">💬</span>
                </a>
              )}
            </div>
            <button onClick={() => { setSelectedSeeker(null); setSidebarData(null); }}
              style={{ background: 'rgba(255,255,255,0.06)', border: 'none', borderRadius: '6px', color: 'var(--text-muted)', fontSize: '14px', cursor: 'pointer', width: '28px', height: '28px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              ✕
            </button>
          </div>

          {/* ── Compact Journey Timeline ── */}
          <div style={{ marginBottom: '16px', padding: '12px', background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border-subtle)', borderRadius: '10px' }}>
            <SeekerJourneyTimeline
              seeker={selectedSeeker}
              compact={true}
              showQueue={false}
              onRefreshSeeker={() => {
                const seekerId = selectedSeeker.source === 'dm' ? String(selectedSeeker.id) : `comment-${selectedSeeker.id}`;
                fetch(`/api/seekers/${encodeURIComponent(seekerId)}`)
                  .then(res => res.json())
                  .then(data => setSidebarData(data))
                  .catch(() => {});
              }}
            />
          </div>

          {/* Loading */}
          {sidebarLoading && (
            <div style={{ textAlign: 'center', padding: '20px', color: 'var(--text-muted)', fontSize: '12px' }}>Loading...</div>
          )}

          {/* Sidebar content */}
          {sidebarData && !sidebarLoading && (
            <>
              {/* Compact general stats */}
              <div className="sidebar-general-stats" aria-label="General stats">
                <span className="sidebar-general-stats-label">General stats</span>
                <span className="sidebar-stat-badge sidebar-stat-badge--messages" title={`${sidebarData.messageCount ?? 0} messages`}>
                  <span aria-hidden="true">💬</span>
                  <strong>{sidebarData.messageCount ?? 0}</strong>
                  <span>Msgs</span>
                </span>
                <span className="sidebar-stat-badge sidebar-stat-badge--comments" title={`${sidebarData.commentCount ?? 0} comments`}>
                  <span aria-hidden="true">💭</span>
                  <strong>{sidebarData.commentCount ?? 0}</strong>
                  <span>Cmts</span>
                </span>
                {sidebarData.adSource && (
                  <span
                    className="sidebar-ad-badge"
                    title={sidebarData.adSource.matchedPostId
                      ? `Facebook Ad post · ad_id: ${sidebarData.adSource.matchedPostId}`
                      : sidebarData.adSource.matchedPostName || 'Facebook Ad post'}
                    aria-label={sidebarData.adSource.matchedPostId
                      ? `Facebook Ad post, ad_id ${sidebarData.adSource.matchedPostId}`
                      : 'Facebook Ad post'}
                  >
                    <span aria-hidden="true">📢</span>
                    <span className="sr-only">Facebook Ad post</span>
                  </span>
                )}
              </div>

              {/* Recent messages */}
              {sidebarData.messages?.length > 0 && (
                <div style={{ marginBottom: '12px' }}>
                  <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>Recent Messages</div>
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
                    {(() => {
                      const filtered = sidebarData.messages
                        .filter((m: { content?: string }) => !m.content?.includes('[AD SOURCE]'))
                        .slice(-20);

                      // Date parsing helper
                      const parseDate = (ts: string): string | null => {
                        const longMatch = ts.match(/([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})/);
                        const shortMatch = ts.match(/(\d{1,2})\/(\d{1,2})\/(\d{2,4})/);
                        if (longMatch) {
                          const months: Record<string, string> = { Jan:'01',Feb:'02',Mar:'03',Apr:'04',May:'05',Jun:'06',Jul:'07',Aug:'08',Sep:'09',Oct:'10',Nov:'11',Dec:'12' };
                          return `${longMatch[3]}.${months[longMatch[1]]||'01'}.${longMatch[2].padStart(2,'0')}`;
                        } else if (shortMatch) {
                          const yr = shortMatch[3].length === 2 ? `20${shortMatch[3]}` : shortMatch[3];
                          return `${yr}.${shortMatch[1].padStart(2,'0')}.${shortMatch[2].padStart(2,'0')}`;
                        }
                        return null;
                      };

                      return filtered.map((msg: { sender: string; content: string; messageTimestamp?: string }, i: number) => {
                        const msgDate = parseDate(msg.messageTimestamp || '');
                        const prevDate = i > 0 ? parseDate(filtered[i - 1].messageTimestamp || '') : null;
                        const showDateSep = msgDate && msgDate !== prevDate;
                        return (
                          <div key={i}>
                            {showDateSep && (
                              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', margin: '6px 0 4px' }}>
                                <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.06)' }} />
                                <div style={{ fontSize: '9px', fontWeight: 700, color: 'var(--text-muted)', padding: '2px 8px', borderRadius: '8px', background: 'rgba(255,255,255,0.04)', letterSpacing: '0.05em', whiteSpace: 'nowrap' }}>
                                  {msgDate}
                                </div>
                                <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.06)' }} />
                              </div>
                            )}
                            <div style={{
                              padding: '8px 10px', marginBottom: '4px',
                              borderRadius: msg.sender === 'Page' ? '8px 8px 2px 8px' : '8px 8px 8px 2px',
                              background: msg.sender === 'Page' ? 'rgba(99,102,241,0.08)' : 'rgba(255,255,255,0.04)',
                              borderLeft: msg.sender !== 'Page' ? '2px solid #f59e0b' : 'none',
                            }}>
                              <div style={{ fontSize: '9px', fontWeight: 700, color: msg.sender === 'Page' ? '#818cf8' : '#f59e0b' }}>
                                {msg.sender === 'Page' ? 'Page' : selectedSeeker.name}
                              </div>
                              <div style={{ fontSize: '12px', color: 'var(--text-primary)', lineHeight: 1.4, marginTop: '2px',
                                overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' as const }}>
                                {msg.content || '(empty)'}
                              </div>
                              {msg.messageTimestamp && <div style={{ fontSize: '9px', color: 'var(--text-muted)', marginTop: '2px' }}>{msg.messageTimestamp}</div>}
                            </div>
                          </div>
                        );
                      });
                    })()}
                  </div>
                </div>
              )}

              {/* Comments */}
              {sidebarData.comments?.length > 0 && (
                <div>
                  <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>Comments</div>
                  {sidebarData.comments.slice(0, 3).map((cmt: { commentText?: string; postUrl?: string }, i: number) => (
                    <div key={i} style={{ padding: '8px 10px', marginBottom: '4px', background: 'rgba(245,158,11,0.04)', border: '1px solid rgba(245,158,11,0.1)', borderRadius: '8px' }}>
                      <div style={{ fontSize: '12px', color: 'var(--text-primary)', lineHeight: 1.4,
                        overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' as const }}>
                        {cmt.commentText || '(empty)'}
                      </div>
                      {cmt.postUrl && (
                        <a href={`https://www.facebook.com/${PAGE_ID}/posts/${cmt.postUrl}`} target="_blank" rel="noopener noreferrer"
                          style={{ fontSize: '10px', color: '#60a5fa', textDecoration: 'none', marginTop: '4px', display: 'inline-block' }}>
                          View Post ↗
                        </a>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {/* MAS approvals stay below the conversation context. */}
          <SeekerJourneyTimeline
            seeker={selectedSeeker}
            compact={true}
            showTimeline={false}
            onRefreshSeeker={() => {
              const seekerId = selectedSeeker.source === 'dm' ? String(selectedSeeker.id) : `comment-${selectedSeeker.id}`;
              fetch(`/api/seekers/${encodeURIComponent(seekerId)}`)
                .then(res => res.json())
                .then(data => setSidebarData(data))
                .catch(() => {});
            }}
          />
        </div>
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
            className="card"
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

            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <button
                type="button"
                disabled={batchRunning || selectedDmThreadIds.length === 0}
                onClick={() => handleRunBatchRec('all')}
                style={{
                  padding: '10px 14px',
                  borderRadius: '8px',
                  background: 'linear-gradient(135deg, #6366f1, #818cf8)',
                  color: '#fff',
                  fontWeight: 700,
                  fontSize: '13px',
                  border: 'none',
                  cursor: batchRunning ? 'wait' : 'pointer',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <span>⚡ Chạy đề xuất cho {selectedDmThreadIds.length} seeker</span>
                <span style={{ fontSize: '11px', opacity: 0.8 }}>Reply + Warmup + Event</span>
              </button>

              <button
                type="button"
                disabled={batchRunning}
                onClick={() => handleRunBatchRec('reply')}
                style={{
                  padding: '10px 14px',
                  borderRadius: '8px',
                  background: 'rgba(99, 102, 241, 0.12)',
                  border: '1px solid rgba(99, 102, 241, 0.25)',
                  color: '#818cf8',
                  fontWeight: 600,
                  fontSize: '13px',
                  cursor: batchRunning ? 'wait' : 'pointer',
                  textAlign: 'left',
                }}
              >
                💬 Đề xuất Reply tin nhắn chưa trả lời
              </button>

              <button
                type="button"
                disabled={batchRunning}
                onClick={() => handleRunBatchRec('warmup')}
                style={{
                  padding: '10px 14px',
                  borderRadius: '8px',
                  background: 'rgba(245, 158, 11, 0.12)',
                  border: '1px solid rgba(245, 158, 11, 0.25)',
                  color: '#fbbf24',
                  fontWeight: 600,
                  fontSize: '13px',
                  cursor: batchRunning ? 'wait' : 'pointer',
                  textAlign: 'left',
                }}
              >
                📣 Đề xuất Warm-up cho Seeker im lặng
              </button>

              <button
                type="button"
                disabled={batchRunning}
                onClick={() => handleRunBatchRec('event')}
                style={{
                  padding: '10px 14px',
                  borderRadius: '8px',
                  background: 'rgba(16, 185, 129, 0.12)',
                  border: '1px solid rgba(16, 185, 129, 0.25)',
                  color: '#34d399',
                  fontWeight: 600,
                  fontSize: '13px',
                  cursor: batchRunning ? 'wait' : 'pointer',
                  textAlign: 'left',
                }}
              >
                🗓️ Đề xuất Sự kiện theo thành phố
              </button>
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
