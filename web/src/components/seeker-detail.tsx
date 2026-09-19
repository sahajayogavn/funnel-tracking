// code:web-component-002:seeker-detail
'use client';

import type { SeekerDetail } from '@/lib/types';
import { sortFacebookMessages } from '@/lib/funnel-filters';
import { SevenStarProgress } from './seven-star-progress';
import { SeekerJourneyTimeline } from './seeker-journey-timeline';
import { MessengerMessageList } from './messenger-message-list';

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
  const { seeker, messages, comments, adSource, reactionEvents } = detail;
  const chronologicalMessages = sortFacebookMessages(
    messages.filter(message => !message.content?.includes('[AD SOURCE]'))
  );
  const cityStyle = CITY_COLORS[seeker.city] || CITY_COLORS['Unknown'];


  // Extract FB user ID from fbProfileUrl or fb_url for inbox link
  const fbUserId = seeker.fbProfileUrl
    ? seeker.fbProfileUrl.split('/').pop()?.split('?')[0] || ''
    : '';

  return (
    <div className="seeker-detail-layout">

      {/* ── Profile Card ── */}
      <section className="card seeker-info-card" aria-labelledby="seeker-info-heading">
        <div className="seeker-section-heading" id="seeker-info-heading">Seeker info</div>
        <div className="seeker-info-grid">
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
            <SevenStarProgress leadStage={seeker.leadStage} size="sm" />
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
          <div>
            <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', marginBottom: '4px' }}>Last Active</div>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
              {seeker.lastMessageDate || seeker.lastMessageTimestampText || seeker.lastInteraction || '—'}
            </div>
          </div>
        </div>

        {/* FB Links */}
        <div className="seeker-info-links">
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

        <div className="seeker-info-divider" />

        <section className="seeker-general-stats" aria-labelledby="general-stats-heading">
          <div className="seeker-section-heading" id="general-stats-heading">General stats</div>
          <div className="seeker-stats-grid">
            <div className="seeker-stat-card">
              <div className="seeker-stat-value seeker-stat-value--messages">{detail.messageCount}</div>
              <div className="seeker-stat-label">Messages</div>
            </div>
            <div className="seeker-stat-card">
              <div className="seeker-stat-value seeker-stat-value--comments">{detail.commentCount}</div>
              <div className="seeker-stat-label">Comments</div>
            </div>
            <div className="seeker-stat-card">
              <div className={`seeker-stat-value ${adSource ? 'seeker-stat-value--ad' : 'seeker-stat-value--muted'}`}>
                {adSource ? '✓' : '✗'}
              </div>
              <div className="seeker-stat-label">Ad Source</div>
            </div>
          </div>

          {adSource && (
            <div className="seeker-ad-source" aria-label="Ad Source details">
              <div className="seeker-ad-source-title">
                📢 Ad Source — This seeker messaged from an ad
              </div>
              {adSource.matchedPostName && (
                <div className="seeker-ad-source-post">
                  Matched Post: <strong>{adSource.matchedPostName.slice(0, 120)}...</strong>
                </div>
              )}
              {adSource.matchedPostId && (
                <a
                  href={`https://www.facebook.com/${adSource.matchedPostId.split('_')[1] ? adSource.matchedPostId.split('_')[0] + '/posts/' + (() => {
                    // Use post_url from the posts table (which we stored in matchedPostId's associated row)
                    return adSource.matchedPostName?.slice(0, 20) || '';
                  })() : ''}`}
                  target="_blank" rel="noopener noreferrer"
                  className="seeker-ad-source-link"
                >
                  📄 View Ad Post on Facebook ↗
                </a>
              )}
            </div>
          )}
        </section>
      </section>

      {/* ── Journey Timeline & Actionable Queued Recommendations ── */}
      <section className="card seeker-journey-card" aria-label="Seeker journey">
        <SeekerJourneyTimeline seeker={seeker} />
      </section>

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
          <MessengerMessageList
            maxHeight={600}
            messages={chronologicalMessages.map(message => ({
              id: message.id,
              sender: message.sender === 'Auto_Page' ? '@Auto_Page' : (message.sender === 'Page' ? 'Page' : (message.sender === 'Customer' ? seeker.name : (message.sender || 'Unknown'))),
              content: message.content,
              timestamp: message.messageTimestamp,
              eventAt: message.messageAt,
              quotedSender: message.quotedSender,
              quotedText: message.quotedText,
              replyToMessageId: message.replyToMessageId,
              reactions: message.reactions,
              outgoing: message.sender === 'Page' || message.sender === 'Auto_Page',
            }))}
          />
        </div>
      )}

      {reactionEvents.length > 0 && (
        <section className="card" aria-label="Reaction evidence" style={{ padding: '16px 20px' }}>
          <div style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '8px' }}>
            Reaction evidence
          </div>
          <div style={{ display: 'grid', gap: '6px', fontSize: '12px', color: 'var(--text-muted)' }}>
            {reactionEvents.map((event) => (
              <div key={event.id}>
                <strong style={{ color: 'var(--text-primary)' }}>{event.emoji || '•'}</strong>{' '}
                actor: {event.actor || 'unknown'}; scope: {event.targetScope || event.targetType || 'unknown'};
                target: {event.targetMessageId || 'unknown'}; observed: {event.observedAt || 'unknown'}
              </div>
            ))}
          </div>
        </section>
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
