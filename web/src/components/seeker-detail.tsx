// code:web-component-002:seeker-detail
'use client';

import type { SeekerDetail } from '@/lib/types';

const PAGE_ID = '1548373332058326';

interface Props {
  detail: SeekerDetail;
}

// ── Color helpers ──
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

function fbInboxUrl(fbUserId: string) {
  return `https://business.facebook.com/latest/inbox/all?asset_id=${PAGE_ID}&selected_item_id=${fbUserId}&thread_type=FB_MESSAGE`;
}

function fbPostUrl(postUrl: string) {
  return `https://www.facebook.com/${PAGE_ID}/posts/${postUrl}`;
}

export function SeekerDetailView({ detail }: Props) {
  const { seeker, messages, comments, adSource } = detail;
  const cityStyle = CITY_COLORS[seeker.city] || CITY_COLORS['Unknown'];
  const stageColor = (seeker.leadStage === 'Seeker')
    ? { bg: 'rgba(99, 102, 241, 0.2)', text: '#818cf8' }
    : { bg: 'rgba(167, 139, 250, 0.15)', text: '#a78bfa' };

  // Extract FB user ID from fbProfileUrl or fb_url for inbox link
  const fbUserId = seeker.fbProfileUrl
    ? seeker.fbProfileUrl.split('/').pop()?.split('?')[0] || ''
    : '';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>

      {/* ── Profile Card ── */}
      <div className="card" style={{ padding: '24px' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
          <div>
            <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', marginBottom: '4px' }}>Phone</div>
            <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>{seeker.phone || '—'}</div>
          </div>
          <div>
            <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', marginBottom: '4px' }}>Email</div>
            <div style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>{seeker.email || '—'}</div>
          </div>
          <div>
            <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', marginBottom: '4px' }}>City</div>
            <span className="badge" style={{ background: cityStyle.bg, color: cityStyle.text }}>{seeker.city}</span>
          </div>
          <div>
            <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', marginBottom: '4px' }}>Stage</div>
            <span className="badge" style={{ background: stageColor.bg, color: stageColor.text }}>{seeker.leadStage}</span>
          </div>
          <div>
            <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', marginBottom: '4px' }}>Source</div>
            <span className="badge" style={{
              background: seeker.source === 'dm' ? 'rgba(99, 102, 241, 0.15)' : 'rgba(245, 158, 11, 0.15)',
              color: seeker.source === 'dm' ? '#818cf8' : '#fbbf24'
            }}>
              {seeker.source === 'dm' ? '💬 Direct Message' : '💬 Comment'}
            </span>
          </div>
          <div>
            <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', marginBottom: '4px' }}>First Seen</div>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>{seeker.firstSeen || '—'}</div>
          </div>
        </div>

        {/* FB Links */}
        <div style={{ marginTop: '16px', display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
          {seeker.fbProfileUrl && (
            <a href={seeker.fbProfileUrl} target="_blank" rel="noopener noreferrer"
              className="fb-link" style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '8px 14px', background: 'rgba(59, 130, 246, 0.1)', borderRadius: '8px', fontSize: '13px', fontWeight: 600, textDecoration: 'none', color: '#60a5fa' }}>
              👤 View FB Profile ↗
            </a>
          )}
          {seeker.source === 'dm' && fbUserId && (
            <a href={fbInboxUrl(fbUserId)} target="_blank" rel="noopener noreferrer"
              style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '8px 14px', background: 'rgba(99, 102, 241, 0.1)', borderRadius: '8px', fontSize: '13px', fontWeight: 600, textDecoration: 'none', color: '#818cf8' }}>
              💬 Open in FB Inbox ↗
            </a>
          )}
        </div>
      </div>

      {/* Stats row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' }}>
        <div className="card" style={{ padding: '16px', textAlign: 'center' }}>
          <div style={{ fontSize: '28px', fontWeight: 800, color: '#818cf8' }}>{detail.messageCount}</div>
          <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)' }}>Messages</div>
        </div>
        <div className="card" style={{ padding: '16px', textAlign: 'center' }}>
          <div style={{ fontSize: '28px', fontWeight: 800, color: '#f59e0b' }}>{detail.commentCount}</div>
          <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)' }}>Comments</div>
        </div>
        <div className="card" style={{ padding: '16px', textAlign: 'center' }}>
          <div style={{ fontSize: '28px', fontWeight: 800, color: adSource ? '#ec4899' : 'var(--text-muted)' }}>
            {adSource ? '✓' : '✗'}
          </div>
          <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)' }}>Ad Source</div>
        </div>
      </div>

      {/* ── Ad Source Card ── */}
      {adSource && (
        <div className="card" style={{ padding: '20px', borderLeft: '3px solid #ec4899' }}>
          <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: '#ec4899', fontWeight: 700, marginBottom: '8px' }}>
            📢 Ad Source — This seeker messaged from an ad
          </div>
          {adSource.matchedPostName && (
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '8px', lineHeight: 1.5 }}>
              Matched Post: <strong style={{ color: 'var(--text-primary)' }}>{adSource.matchedPostName.slice(0, 120)}...</strong>
            </div>
          )}
          {adSource.matchedPostId && (
            <a
              href={`https://www.facebook.com/${adSource.matchedPostId.split('_')[1] ? adSource.matchedPostId.split('_')[0] + '/posts/' + (() => {
                // Use post_url from the posts table (which we stored in matchedPostId's associated row)
                return adSource.matchedPostName?.slice(0, 20) || '';
              })() : ''}`}
              target="_blank" rel="noopener noreferrer"
              style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '6px 12px', background: 'rgba(236, 72, 153, 0.1)', borderRadius: '6px', fontSize: '12px', fontWeight: 600, textDecoration: 'none', color: '#f472b6' }}
            >
              📄 View Ad Post on Facebook ↗
            </a>
          )}
        </div>
      )}

      {/* ── DM Messages Timeline ── */}
      {messages.length > 0 && (
        <div className="card" style={{ padding: '20px' }}>
          <div style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            💬 Message History
            {seeker.source === 'dm' && fbUserId && (
              <a href={fbInboxUrl(fbUserId)} target="_blank" rel="noopener noreferrer"
                style={{ fontSize: '11px', color: '#818cf8', textDecoration: 'none', fontWeight: 600 }}>
                Open in FB Inbox ↗
              </a>
            )}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '600px', overflowY: 'auto' }}>
            {messages.map((msg, i) => {
              // Skip ad source messages in the bubble view
              if (msg.content?.includes('[AD SOURCE]')) return null;
              const isPage = msg.sender === 'Page';

              // ── Date separator logic ──
              // Parse date from messageTimestamp (formats: "Feb 27, 2026, 6:53 PM", "3/29/25, 9:50 PM", "Jan 22, 2026, 11:36 AM")
              let msgDate: string | null = null;
              const ts = msg.messageTimestamp || '';
              // Match "Mon DD, YYYY" (e.g., "Feb 27, 2026, 6:53 PM" or "Jan 22, 2026, 11:36 AM")
              const longMatch = ts.match(/([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})/);
              // Match "M/D/YY" (e.g., "3/29/25, 9:50 PM")
              const shortMatch = ts.match(/(\d{1,2})\/(\d{1,2})\/(\d{2,4})/);
              if (longMatch) {
                const months: Record<string, string> = { Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06', Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12' };
                const mm = months[longMatch[1]] || '01';
                const dd = longMatch[2].padStart(2, '0');
                msgDate = `${longMatch[3]}.${mm}.${dd}`;
              } else if (shortMatch) {
                const yr = shortMatch[3].length === 2 ? `20${shortMatch[3]}` : shortMatch[3];
                const mm = shortMatch[1].padStart(2, '0');
                const dd = shortMatch[2].padStart(2, '0');
                msgDate = `${yr}.${mm}.${dd}`;
              }

              // Find the previous non-ad message to compare dates
              let prevDate: string | null = null;
              for (let j = i - 1; j >= 0; j--) {
                if (messages[j].content?.includes('[AD SOURCE]')) continue;
                const pts = messages[j].messageTimestamp || '';
                const pLong = pts.match(/([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})/);
                const pShort = pts.match(/(\d{1,2})\/(\d{1,2})\/(\d{2,4})/);
                if (pLong) {
                  const months: Record<string, string> = { Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06', Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12' };
                  prevDate = `${pLong[3]}.${months[pLong[1]] || '01'}.${pLong[2].padStart(2, '0')}`;
                } else if (pShort) {
                  const yr = pShort[3].length === 2 ? `20${pShort[3]}` : pShort[3];
                  prevDate = `${yr}.${pShort[1].padStart(2, '0')}.${pShort[2].padStart(2, '0')}`;
                }
                break;
              }

              const showDateSep = msgDate && msgDate !== prevDate;

              return (
                <div key={i}>
                  {/* Date separator */}
                  {showDateSep && (
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: '12px',
                      margin: '12px 0 8px',
                    }}>
                      <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.08)' }} />
                      <div style={{
                        fontSize: '11px', fontWeight: 700, color: 'var(--text-muted)',
                        padding: '3px 12px', borderRadius: '10px',
                        background: 'rgba(255,255,255,0.05)',
                        letterSpacing: '0.05em', whiteSpace: 'nowrap',
                      }}>
                        {msgDate}
                      </div>
                      <div style={{ flex: 1, height: '1px', background: 'rgba(255,255,255,0.08)' }} />
                    </div>
                  )}
                  {/* Message bubble */}
                  <div style={{
                    display: 'flex',
                    justifyContent: isPage ? 'flex-end' : 'flex-start',
                  }}>
                    <div style={{
                      maxWidth: '75%',
                      padding: '10px 14px',
                      borderRadius: isPage ? '14px 14px 4px 14px' : '14px 14px 14px 4px',
                      background: isPage ? 'rgba(99, 102, 241, 0.15)' : 'rgba(255, 255, 255, 0.06)',
                      border: isPage ? 'none' : '1px solid rgba(255, 255, 255, 0.08)',
                    }}>
                      <div style={{ fontSize: '10px', fontWeight: 700, color: isPage ? '#818cf8' : '#f59e0b', marginBottom: '4px' }}>
                        {isPage ? 'Page' : seeker.name}
                      </div>
                      <div style={{ fontSize: '13px', color: 'var(--text-primary)', lineHeight: 1.5, wordBreak: 'break-word' }}>
                        {msg.content || '(empty)'}
                      </div>
                      <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginTop: '4px', textAlign: isPage ? 'right' : 'left' }}>
                        {msg.messageTimestamp || ''}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Comments Section ── */}
      {comments.length > 0 && (
        <div className="card" style={{ padding: '20px' }}>
          <div style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '16px' }}>
            💬 Comments on Posts
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {comments.map((cmt, i) => (
              <div key={i} style={{
                padding: '12px 16px',
                background: 'rgba(245, 158, 11, 0.06)',
                border: '1px solid rgba(245, 158, 11, 0.15)',
                borderRadius: '10px',
              }}>
                <div style={{ fontSize: '12px', color: '#fbbf24', fontWeight: 600, marginBottom: '6px' }}>
                  {cmt.isReply ? '↩ Reply' : '💬 Comment'}
                  {cmt.commentDate && <span style={{ color: 'var(--text-muted)', fontWeight: 400, marginLeft: '8px' }}>{cmt.commentDate}</span>}
                </div>
                <div style={{ fontSize: '13px', color: 'var(--text-primary)', lineHeight: 1.5, marginBottom: '8px' }}>
                  {cmt.commentText || '(empty)'}
                </div>
                {cmt.postName && (
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '6px' }}>
                    On post: <em>{cmt.postName.slice(0, 100)}...</em>
                  </div>
                )}
                {cmt.postUrl && (
                  <a href={fbPostUrl(cmt.postUrl)} target="_blank" rel="noopener noreferrer"
                    style={{ fontSize: '11px', color: '#60a5fa', textDecoration: 'none', fontWeight: 600 }}>
                    📄 View Post on Facebook ↗
                  </a>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Empty state */}
      {messages.length === 0 && comments.length === 0 && (
        <div className="card" style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
          No interaction history recorded yet.
        </div>
      )}
    </div>
  );
}
