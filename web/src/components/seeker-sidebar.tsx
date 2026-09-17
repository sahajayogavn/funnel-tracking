// code:web-component-009:seeker-sidebar
'use client';

import type { CSSProperties, ReactNode } from 'react';
import type { Seeker, SeekerDetail } from '@/lib/types';
import { sortFacebookMessages } from '@/lib/funnel-filters';
import { SeekerJourneyTimeline } from './seeker-journey-timeline';

const PAGE_ID = '1548373332058326';

// The preview runs through Turbopack in development. Its current stylesheet
// bundle can omit this component's base rules after hot reload, which turns the
// aside into an unbounded page-width block. Keep the structural rules with the
// component so the brief remains readable in every bundle state.
const SIDEBAR_CRITICAL_CSS = `
  .seeker-sidebar-header { display:flex; align-items:flex-start; gap:8px; margin-bottom:16px; }
  .seeker-sidebar-meta { margin-top:2px; color:var(--text-muted); font-size:11px; }
  .seeker-sidebar-close { width:28px; height:28px; display:inline-flex; align-items:center; justify-content:center; flex-shrink:0; padding:0; border:0; border-radius:6px; background:rgba(255,255,255,.06); color:var(--text-muted); font-size:14px; cursor:pointer; }
  .seeker-sidebar-close:hover, .seeker-sidebar-close:focus-visible { background:rgba(255,255,255,.12); color:var(--text-primary); outline:none; }
  .seeker-sidebar-journey { margin-bottom:16px; padding:12px; background:rgba(255,255,255,.02); border:1px solid var(--border-subtle); border-radius:10px; }
  .seeker-sidebar-loading { padding:20px; color:var(--text-muted); font-size:12px; text-align:center; }
  .seeker-sidebar-section { margin-bottom:12px; }
  .seeker-sidebar-section-title { margin-bottom:8px; color:var(--text-muted); font-size:11px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; }
  .sidebar-recent-messages { display:flex; flex-direction:column; gap:4px; max-height:260px; overflow-y:auto; padding-right:6px; }
  .seeker-sidebar-date-separator { display:flex; align-items:center; gap:8px; margin:6px 0 4px; }
  .seeker-sidebar-date-separator > div { flex:1; height:1px; background:rgba(255,255,255,.06); }
  .seeker-sidebar-date-separator > span { padding:2px 8px; border-radius:8px; background:rgba(255,255,255,.04); color:var(--text-muted); font-size:9px; font-weight:700; letter-spacing:.05em; white-space:nowrap; }
  .seeker-sidebar-message { padding:8px 10px; margin-bottom:4px; border-radius:8px 8px 8px 2px; background:rgba(255,255,255,.04); border-left:2px solid #f59e0b; }
  .seeker-sidebar-message.is-page { border-left:0; border-radius:8px 8px 2px 8px; background:rgba(99,102,241,.08); }
  .seeker-sidebar-message-sender { color:#f59e0b; font-size:9px; font-weight:700; }
  .seeker-sidebar-message.is-page .seeker-sidebar-message-sender { color:#818cf8; }
  .seeker-sidebar-message-content, .seeker-sidebar-comment-text { display:-webkit-box; overflow:hidden; margin-top:2px; color:var(--text-primary); font-size:12px; line-height:1.4; text-overflow:ellipsis; -webkit-box-orient:vertical; -webkit-line-clamp:2; }
  .seeker-sidebar-message-time { margin-top:2px; color:var(--text-muted); font-size:9px; }
  .seeker-sidebar-comment { padding:8px 10px; margin-bottom:4px; border:1px solid rgba(245,158,11,.1); border-radius:8px; background:rgba(245,158,11,.04); }
  .seeker-sidebar-comment-link { display:inline-block; margin-top:4px; color:#60a5fa; font-size:10px; text-decoration:none; }
  .seeker-sidebar-comment-link:hover { text-decoration:underline; }
`;

export interface SeekerSidebarProps {
  seeker: Seeker | null;
  detail: SeekerDetail | null;
  loading?: boolean;
  showQueue?: boolean;
  detailHref?: string;
  onClose?: () => void;
  onRefresh?: () => void;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
  headerBadge?: ReactNode;
  beforeJourney?: ReactNode;
  emptyState?: ReactNode;
  className?: string;
  style?: CSSProperties;
}

export function SeekerSidebarEmptyState() {
  return (
    <div className="seeker-sidebar-empty">
      <div className="seeker-sidebar-empty-icon">👤</div>
      <div className="seeker-sidebar-empty-title">Thông tin Seeker &amp; Lý do MAS</div>
      <p>Bấm hoặc di chuột vào tên Seeker trong hàng đợi bên trái để xem hồ sơ, hội thoại và bằng chứng phân tích từ MAS.</p>
      <div className="seeker-sidebar-empty-hints">
        <div>
          <span>🤖</span>
          <div><strong>Bằng chứng MAS Proof</strong><span>Hiển thị nguyên nhân kích hoạt và nội dung đề xuất của MAS.</span></div>
        </div>
        <div>
          <span>💬</span>
          <div><strong>Hội thoại thực tế</strong><span>Xem các tin nhắn gần đây để nắm bắt ngữ cảnh trước khi duyệt.</span></div>
        </div>
        <div>
          <span>🛤️</span>
          <div><strong>Hành trình &amp; Liên kết</strong><span>Giai đoạn phễu Seeker, hồ sơ Facebook và hộp thư Meta Inbox.</span></div>
        </div>
      </div>
      <div className="seeker-sidebar-empty-tip">💡 Khung này cố định sẵn để bố cục không bị co giãn khi bạn bấm xem chi tiết.</div>
    </div>
  );
}

function facebookProfileUrl(value?: string | null) {
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

function defaultDetailHref(seeker: Seeker) {
  return seeker.source === 'dm' ? `/seekers/${seeker.id}` : `/seekers/comment-${seeker.id}`;
}

function parseDate(ts: string): string | null {
  const longMatch = ts.match(/([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})/);
  const shortMatch = ts.match(/(\d{1,2})\/(\d{1,2})\/(\d{2,4})/);
  if (longMatch) {
    const months: Record<string, string> = { Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06', Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12' };
    return `${longMatch[3]}.${months[longMatch[1]] || '01'}.${longMatch[2].padStart(2, '0')}`;
  }
  if (shortMatch) {
    const year = shortMatch[3].length === 2 ? `20${shortMatch[3]}` : shortMatch[3];
    return `${year}.${shortMatch[1].padStart(2, '0')}.${shortMatch[2].padStart(2, '0')}`;
  }
  return null;
}

export function SeekerSidebar({
  seeker,
  detail,
  loading = false,
  showQueue = false,
  detailHref,
  onClose,
  onRefresh,
  onMouseEnter,
  onMouseLeave,
  headerBadge,
  beforeJourney,
  emptyState,
  className,
  style,
}: SeekerSidebarProps) {
  const profileUrl = seeker ? facebookProfileUrl(seeker.fbProfileUrl) : null;
  const inboxUrl = seeker ? facebookInboxUrl(seeker) : null;

  return (
    <aside
      className={`seeker-sidebar${className ? ` ${className}` : ''}`}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      style={{
        // Keep the preview a true side pane.  The explicit flex basis prevents
        // a long message or timeline from allowing the aside to consume the
        // full row when it is rendered beside a table or queue grid.
        boxSizing: 'border-box',
        flex: '0 0 380px',
        width: '380px',
        minWidth: '380px',
        maxWidth: '380px',
        ...style,
      }}
    >
      <style>{SIDEBAR_CRITICAL_CSS}</style>
      {seeker ? (
        <>
          <div className="seeker-sidebar-header">
            <div className="seeker-sidebar-heading">
              <div className="seeker-sidebar-name">{seeker.name || '—'}</div>
              <div className="seeker-sidebar-meta">
                {seeker.source === 'dm' ? '💬 DM' : '💬 Comment'} · {seeker.city || 'Unknown'}
                {seeker.leadStage ? ` · ${seeker.leadStage}` : ''}
              </div>
              {headerBadge}
            </div>
            <div className="seeker-sidebar-quick-links" aria-label="Seeker links">
              <a
                href={detailHref || defaultDetailHref(seeker)}
                className="seeker-sidebar-link seeker-sidebar-link--details"
                aria-label="Mở Full Details"
                title="Full Details"
                onClick={event => event.stopPropagation()}
              >
                <span aria-hidden="true">📋</span>
              </a>
              {profileUrl && (
                <a
                  href={profileUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="seeker-sidebar-link seeker-sidebar-link--profile"
                  aria-label={`Mở Facebook profile của ${seeker.name || 'seeker'} trong tab mới`}
                  title="Facebook Profile"
                  onClick={event => event.stopPropagation()}
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z" />
                  </svg>
                </a>
              )}
              {inboxUrl && (
                <a
                  href={inboxUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="seeker-sidebar-link seeker-sidebar-link--inbox"
                  aria-label={`Mở Facebook Message Inbox của ${seeker.name || 'seeker'} trong tab mới`}
                  title="Facebook Inbox"
                  onClick={event => event.stopPropagation()}
                >
                  <span aria-hidden="true">💬</span>
                </a>
              )}
            </div>
            {onClose && (
              <button className="seeker-sidebar-close" onClick={onClose} title="Đóng sidebar" aria-label="Đóng sidebar">
                ✕
              </button>
            )}
          </div>

          {beforeJourney}

          <div className="seeker-sidebar-journey">
            <SeekerJourneyTimeline
              seeker={seeker}
              compact={true}
              showQueue={false}
              onRefreshSeeker={onRefresh}
            />
          </div>

          {loading && (
            <div className="seeker-sidebar-loading">⏳ Đang tải thông tin Seeker...</div>
          )}

          {detail && !loading && (
            <>
              <div className="sidebar-general-stats" aria-label="General stats">
                <span className="sidebar-general-stats-label">General stats</span>
                <span className="sidebar-stat-badge sidebar-stat-badge--messages" title={`${detail.messageCount ?? 0} messages`}>
                  <span aria-hidden="true">💬</span>
                  <strong>{detail.messageCount ?? 0}</strong>
                  <span>Msgs</span>
                </span>
                <span className="sidebar-stat-badge sidebar-stat-badge--comments" title={`${detail.commentCount ?? 0} comments`}>
                  <span aria-hidden="true">💭</span>
                  <strong>{detail.commentCount ?? 0}</strong>
                  <span>Cmts</span>
                </span>
                {detail.adSource && (
                  <span
                    className="sidebar-ad-badge"
                    title={detail.adSource.matchedPostId
                      ? `Facebook Ad post · ad_id: ${detail.adSource.matchedPostId}`
                      : detail.adSource.matchedPostName || 'Facebook Ad post'}
                    aria-label={detail.adSource.matchedPostId
                      ? `Facebook Ad post, ad_id ${detail.adSource.matchedPostId}`
                      : 'Facebook Ad post'}
                  >
                    <span aria-hidden="true">📢</span>
                    <span className="sr-only">Facebook Ad post</span>
                  </span>
                )}
              </div>

              {detail.messages?.length > 0 && (
                <div className="seeker-sidebar-section">
                  <div className="seeker-sidebar-section-title">Recent Messages</div>
                  <div className="sidebar-recent-messages">
                    {sortFacebookMessages(
                      detail.messages.filter(message => !message.content?.includes('[AD SOURCE]'))
                    )
                      .slice(-20)
                      .map((message, index, visibleMessages) => {
                        const messageDate = parseDate(message.messageTimestamp || '');
                        const previousDate = index > 0 ? parseDate(visibleMessages[index - 1].messageTimestamp || '') : null;
                        return (
                          <div key={index}>
                            {messageDate && messageDate !== previousDate && (
                              <div className="seeker-sidebar-date-separator">
                                <div />
                                <span>{messageDate}</span>
                                <div />
                              </div>
                            )}
                            <div className={`seeker-sidebar-message ${message.sender === 'Page' ? 'is-page' : 'is-seeker'}`}>
                              <div className="seeker-sidebar-message-sender">
                                {message.sender === 'Page' ? 'Page' : seeker.name}
                              </div>
                              <div className="seeker-sidebar-message-content">{message.content || '(empty)'}</div>
                              {message.messageTimestamp && <div className="seeker-sidebar-message-time">{message.messageTimestamp}</div>}
                            </div>
                          </div>
                        );
                      })}
                  </div>
                </div>
              )}

              {detail.comments?.length > 0 && (
                <div className="seeker-sidebar-section">
                  <div className="seeker-sidebar-section-title">Comments ({detail.comments.length})</div>
                  {detail.comments.slice(0, 3).map((comment, index) => (
                    <div className="seeker-sidebar-comment" key={index}>
                      <div className="seeker-sidebar-comment-text">{comment.commentText || '(empty)'}</div>
                      {comment.postUrl && (
                        <a
                          href={`https://www.facebook.com/${PAGE_ID}/posts/${comment.postUrl}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="seeker-sidebar-comment-link"
                        >
                          View Post ↗
                        </a>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {showQueue && (
            <SeekerJourneyTimeline
              seeker={seeker}
              compact={true}
              showTimeline={false}
              onRefreshSeeker={onRefresh}
            />
          )}
        </>
      ) : emptyState}
    </aside>
  );
}
