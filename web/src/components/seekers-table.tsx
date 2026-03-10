// code:web-component-002:seekers-table
'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import type { Seeker, TouchPoint } from '@/lib/types';
import { InteractionHistogram } from './interaction-histogram';

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

// ── Distinct color per stage ──
const STAGE_COLORS: Record<string, { bg: string; text: string }> = {
  'User':   { bg: 'rgba(167, 139, 250, 0.15)', text: '#a78bfa' },
  'Seeker': { bg: 'rgba(99, 102, 241, 0.2)',   text: '#818cf8' },
};

function getCityStyle(city: string) {
  return CITY_COLORS[city] || CITY_COLORS['Unknown'];
}

function getStageStyle(stage: string) {
  return STAGE_COLORS[stage] || STAGE_COLORS['User'];
}

const touchPointColor = (type: string) => {
  const colors: Record<string, string> = {
    comment: '#f59e0b',
    message: '#6366f1',
    reply: '#10b981',
    ad_click: '#ec4899',
    ad_message: '#ec4899',
  };
  return colors[type] || '#6b7280';
};

const PAGE_ID = '1548373332058326';

function seekerDetailUrl(seeker: Seeker) {
  return seeker.source === 'dm'
    ? `/seekers/${seeker.id}`
    : `/seekers/comment-${seeker.id}`;
}

function fbProfileLink(url: string) {
  return url.startsWith('http') ? url : `https://facebook.com/${url}`;
}

export function SeekersTable({ initialSeekers }: SeekersTableProps) {
  const router = useRouter();
  const [seekers] = useState(initialSeekers);
  const [sortField, setSortField] = useState<keyof Seeker>('lastInteraction');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [search, setSearch] = useState('');
  const [hoveredSeeker, setHoveredSeeker] = useState<string | null>(null);
  const [touchPoints, setTouchPoints] = useState<TouchPoint[]>([]);
  const [loadingTp, setLoadingTp] = useState(false);
  const [activityData, setActivityData] = useState<Record<string, { date: string; count: number }[]>>({});
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });
  const abortRef = useRef<AbortController | null>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);

  // ── Right Sidebar state ──
  const [selectedSeeker, setSelectedSeeker] = useState<Seeker | null>(null);
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

  // Sort & filter
  const sorted = [...seekers]
    .filter(s => {
      if (!search) return true;
      const q = search.toLowerCase();
      return s.name?.toLowerCase().includes(q) ||
        s.city?.toLowerCase().includes(q) ||
        s.phone?.toLowerCase().includes(q) ||
        s.email?.toLowerCase().includes(q);
    })
    .sort((a, b) => {
      const aVal = a[sortField] ?? '';
      const bVal = b[sortField] ?? '';
      const cmp = String(aVal).localeCompare(String(bVal));
      return sortDir === 'asc' ? cmp : -cmp;
    });

  const handleSort = (field: keyof Seeker) => {
    if (sortField === field) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDir('desc');
    }
  };

  const handleJourneyHover = useCallback(async (name: string, e: React.MouseEvent) => {
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setHoveredSeeker(name);
    setTouchPoints([]);
    setLoadingTp(true);
    const x = Math.min(e.clientX + 20, window.innerWidth - 420);
    const y = Math.min(e.clientY - 20, window.innerHeight - 300);
    setTooltipPos({ x, y });
    try {
      const res = await fetch(`/api/seekers?action=touchpoints&name=${encodeURIComponent(name)}`, { signal: controller.signal });
      const json = await res.json();
      if (!controller.signal.aborted) { setTouchPoints(json.touchPoints || []); setLoadingTp(false); }
    } catch {
      if (!controller.signal.aborted) { setTouchPoints([]); setLoadingTp(false); }
    }
  }, []);

  const handleJourneyLeave = useCallback(() => {
    if (abortRef.current) abortRef.current.abort();
    setHoveredSeeker(null);
    setTouchPoints([]);
    setLoadingTp(false);
  }, []);

  // ── Row click → open sidebar ──
  const handleRowClick = useCallback(async (seeker: Seeker) => {
    if (selectedSeeker?.id === seeker.id && selectedSeeker?.source === seeker.source) {
      setSelectedSeeker(null); setSidebarData(null); return;
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
  }, [selectedSeeker]);

  return (
    <div style={{ display: 'flex', gap: '0px', position: 'relative' }}>
      {/* Main table area */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {/* Search */}
        <div style={{ marginBottom: '16px' }}>
          <input
            type="text"
            placeholder="Search seekers by name, city, phone, email..."
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
        <div className="card" style={{ overflow: 'auto', maxHeight: 'calc(100vh - 240px)' }}>
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: '36px' }}>#</th>
                <th style={{ width: '42px' }}>Journey</th>
                <th onClick={() => handleSort('name')}>Name {sortField === 'name' ? (sortDir === 'asc' ? '↑' : '↓') : ''}</th>
                <th>FB Profile</th>
                <th onClick={() => handleSort('phone')}>Phone</th>
                <th onClick={() => handleSort('email')}>Email</th>
                <th>Activity</th>
                <th onClick={() => handleSort('city')}>City</th>
                <th onClick={() => handleSort('leadStage')}>Stage</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((seeker, idx) => {
                const cityStyle = getCityStyle(seeker.city);
                const stageStyle = getStageStyle(seeker.leadStage);
                const isSelected = selectedSeeker?.id === seeker.id && selectedSeeker?.source === seeker.source;
                return (
                  <tr
                    key={`${seeker.source}-${seeker.id}-${idx}`}
                    onClick={() => handleRowClick(seeker)}
                    style={{
                      cursor: 'pointer',
                      background: isSelected ? 'rgba(99, 102, 241, 0.1)' : undefined,
                      borderLeft: isSelected ? '3px solid #818cf8' : '3px solid transparent',
                    }}
                  >
                    <td style={{ color: 'var(--text-muted)', fontSize: '11px', fontWeight: 600 }}>{idx + 1}</td>
                    <td>
                      <span
                        style={{ cursor: 'pointer', fontSize: '16px', display: 'inline-block' }}
                        onMouseEnter={(e) => { e.stopPropagation(); handleJourneyHover(seeker.name, e); }}
                        onMouseLeave={handleJourneyLeave}
                      >🛤️</span>
                    </td>
                    <td
                      style={{ fontWeight: 600, color: 'var(--accent-indigo)', cursor: 'pointer' }}
                      onClick={(e) => { e.stopPropagation(); router.push(seekerDetailUrl(seeker)); }}
                    >
                      {seeker.name || '—'} ↗
                    </td>
                    <td>
                      {seeker.fbProfileUrl ? (
                        <a href={fbProfileLink(seeker.fbProfileUrl)} target="_blank" rel="noopener noreferrer" className="fb-link" onClick={e => e.stopPropagation()}>
                          Profile ↗
                        </a>
                      ) : '—'}
                    </td>
                    <td>{seeker.phone || '—'}</td>
                    <td>{seeker.email || '—'}</td>
                    <td><InteractionHistogram data={activityData[seeker.name] || []} /></td>
                    <td><span className="badge" style={{ background: cityStyle.bg, color: cityStyle.text }}>{seeker.city}</span></td>
                    <td><span className="badge" style={{ background: stageStyle.bg, color: stageStyle.text }}>{seeker.leadStage}</span></td>
                  </tr>
                );
              })}
              {sorted.length === 0 && (
                <tr><td colSpan={9} style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }}>No seekers found</td></tr>
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
          {/* Header */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '16px' }}>
            <div>
              <div style={{ fontSize: '16px', fontWeight: 700, color: 'var(--text-primary)' }}>{selectedSeeker.name}</div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>
                {selectedSeeker.source === 'dm' ? '💬 DM' : '💬 Comment'} · {selectedSeeker.city}
              </div>
            </div>
            <button onClick={() => { setSelectedSeeker(null); setSidebarData(null); }}
              style={{ background: 'rgba(255,255,255,0.06)', border: 'none', borderRadius: '6px', color: 'var(--text-muted)', fontSize: '14px', cursor: 'pointer', width: '28px', height: '28px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              ✕
            </button>
          </div>

          {/* Profile info */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', marginBottom: '14px' }}>
            <div>
              <div style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)' }}>Phone</div>
              <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>{selectedSeeker.phone || '—'}</div>
            </div>
            <div>
              <div style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)' }}>Email</div>
              <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>{selectedSeeker.email || '—'}</div>
            </div>
            <div>
              <div style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)' }}>Stage</div>
              <span className="badge" style={{
                background: selectedSeeker.leadStage === 'Seeker' ? 'rgba(99,102,241,0.2)' : 'rgba(167,139,250,0.15)',
                color: selectedSeeker.leadStage === 'Seeker' ? '#818cf8' : '#a78bfa',
              }}>{selectedSeeker.leadStage}</span>
            </div>
            <div>
              <div style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)' }}>First Seen</div>
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{selectedSeeker.firstSeen || '—'}</div>
            </div>
          </div>

          {/* Quick links */}
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '14px' }}>
            <a href={seekerDetailUrl(selectedSeeker)}
              style={{ padding: '6px 12px', background: 'rgba(99,102,241,0.12)', borderRadius: '6px', fontSize: '11px', fontWeight: 600, color: '#818cf8', textDecoration: 'none' }}>
              📋 Full Details →
            </a>
            {selectedSeeker.fbProfileUrl && (
              <a href={fbProfileLink(selectedSeeker.fbProfileUrl)} target="_blank" rel="noopener noreferrer"
                style={{ padding: '6px 12px', background: 'rgba(59,130,246,0.12)', borderRadius: '6px', fontSize: '11px', fontWeight: 600, color: '#60a5fa', textDecoration: 'none' }}>
                👤 FB Profile ↗
              </a>
            )}
            {selectedSeeker.source === 'dm' && selectedSeeker.fbProfileUrl && (
              <a href={`https://business.facebook.com/latest/inbox/all?asset_id=${PAGE_ID}&selected_item_id=${selectedSeeker.fbProfileUrl.startsWith('http') ? selectedSeeker.fbProfileUrl.split('/').pop()?.split('?')[0] : selectedSeeker.fbProfileUrl}&thread_type=FB_MESSAGE`}
                target="_blank" rel="noopener noreferrer"
                style={{ padding: '6px 12px', background: 'rgba(99,102,241,0.12)', borderRadius: '6px', fontSize: '11px', fontWeight: 600, color: '#818cf8', textDecoration: 'none' }}>
                💬 FB Inbox ↗
              </a>
            )}
          </div>

          {/* Loading */}
          {sidebarLoading && (
            <div style={{ textAlign: 'center', padding: '20px', color: 'var(--text-muted)', fontSize: '12px' }}>Loading...</div>
          )}

          {/* Sidebar content */}
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

              {/* Ad Source */}
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
                  <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>Recent Messages</div>
                  {(() => {
                    const filtered = sidebarData.messages
                      .filter((m: { content?: string }) => !m.content?.includes('[AD SOURCE]'))
                      .slice(-5);

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
        </div>
      )}

      {/* Journey Tooltip */}
      {hoveredSeeker && (
        <div ref={tooltipRef} style={{
          position: 'fixed', top: tooltipPos.y, left: tooltipPos.x, zIndex: 1000,
          background: 'var(--bg-secondary)', border: '1px solid var(--border-glow)', borderRadius: '12px',
          padding: '16px', minWidth: '320px', maxWidth: '400px', maxHeight: '280px', overflowY: 'auto',
          boxShadow: '0 8px 32px rgba(0,0,0,0.5)', pointerEvents: 'none',
        }}>
          <div style={{ fontSize: '13px', fontWeight: 700, marginBottom: '12px', color: 'var(--accent-indigo)' }}>Journey: {hoveredSeeker}</div>
          {loadingTp ? (
            <div style={{ color: 'var(--text-muted)', fontSize: '12px' }}>Loading touch-points...</div>
          ) : touchPoints.length > 0 ? (
            touchPoints.slice(0, 8).map((tp, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <div style={{ width: '7px', height: '7px', borderRadius: '50%', marginTop: '5px', flexShrink: 0, background: touchPointColor(tp.type) }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: touchPointColor(tp.type) }}>{tp.type.replace('_', ' ')}</div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '1px', lineHeight: 1.3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{tp.detail || '(no content)'}</div>
                  <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginTop: '1px' }}>{tp.date}</div>
                </div>
              </div>
            ))
          ) : (
            <div style={{ color: 'var(--text-muted)', fontSize: '12px' }}>No touch-points recorded</div>
          )}
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
