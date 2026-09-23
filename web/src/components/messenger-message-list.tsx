// code:web-component-010:messenger-message-list
'use client';

import type { ReactNode } from 'react';

export interface MessengerMessage {
  id?: string | number;
  sender?: string | null;
  content?: string | null;
  /** The time text captured from Facebook, such as "3:30 PM". */
  timestamp?: string | null;
  /** Absolute Facebook event time, resolved while ingesting the Inbox row. */
  eventAt?: string | null;
  /** Deprecated scraper write time; never use it as a Facebook event time. */
  recordedAt?: string | null;
  /** True for Page/agent messages, which Messenger places on the right. */
  outgoing?: boolean;
  /** Confirmed MAS proposal origin, supplied by a caller's data query. */
  masProposed?: boolean;
  /** Evidence for a reply quote; it is never folded into the message body. */
  quotedSender?: string | null;
  quotedText?: string | null;
  replyToMessageId?: string | null;
  /** Reactions are annotations attached to this target message. */
  reactions?: {
    actor?: string | null; emoji?: string | null; count?: number | null;
  }[];
}

interface MessengerMessageListProps {
  messages: MessengerMessage[];
  className?: string;
  maxHeight?: number | string;
  compact?: boolean;
  showDateSeparators?: boolean;
  emptyState?: ReactNode;
  /** Origin metadata is opt-in so it can remain limited to Recent Messages. */
  showMasOrigin?: boolean;
}

const MONTHS: Record<string, string> = {
  Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06',
  Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12',
};

const REACTION_ICONS: Record<string, string> = {
  LIKE: '👍', LOVE: '❤', HAHA: '😆', WOW: '😮', SAD: '😢', ANGRY: '😡',
};

function splitReactionMarkers(content?: string | null) {
  const raw = content || '';
  const reactions = Array.from(raw.matchAll(/:::REACTION_([A-Z]+):::/g))
    .map((match) => REACTION_ICONS[match[1]] || '•');
  // Reactions are metadata on a bubble. The scraper's DOM-flattening marker
  // is not visible text in Messenger and must not appear as a quoted reply.
  const body = raw
    .replace(/\s*\n?\[Quoted Reply\/Link\]:\s*:::REACTION_[A-Z]+:::/g, '')
    .trim();
  return { body, reactions };
}

/** Extract the calendar label embedded in the timestamp text Facebook exports. */
export function messengerDateLabel(timestamp?: string | null): string | null {
  if (!timestamp) return null;
  const longMatch = timestamp.match(/([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})/);
  const shortMatch = timestamp.match(/(\d{1,2})\/(\d{1,2})\/(\d{2,4})/);
  if (longMatch) {
    return `${longMatch[3]}.${MONTHS[longMatch[1]] || '01'}.${longMatch[2].padStart(2, '0')}`;
  }
  if (shortMatch) {
    const year = shortMatch[3].length === 2 ? `20${shortMatch[3]}` : shortMatch[3];
    return `${year}.${shortMatch[1].padStart(2, '0')}.${shortMatch[2].padStart(2, '0')}`;
  }
  const storedMatch = timestamp.match(/(\d{4})-(\d{2})-(\d{2})/);
  if (storedMatch) return `${storedMatch[1]}.${storedMatch[2]}.${storedMatch[3]}`;
  return null;
}

function messengerBriefTime(timestamp?: string | null): string | null {
  const match = timestamp?.match(/\d{1,2}:\d{2}\s*[AP]M/i);
  return match ? match[0].replace(/\s+/g, ' ') : null;
}

/** A compact Messenger-style footer; dates are shown by the day separator. */
export function messengerMetaLabel(sender: string, timestamp?: string | null, eventAt?: string | null): string | null {
  const time = messengerBriefTime(timestamp) || messengerBriefTime(eventAt);
  return time ? `${sender} at ${time}` : null;
}

/**
 * Shared conversation renderer for CRM surfaces.
 * The styles intentionally follow Facebook Messenger's dark theme: received
 * messages are neutral bubbles on the left; Page/agent messages are blue
 * bubbles on the right.
 */
export function MessengerMessageList({
  messages,
  className,
  maxHeight,
  compact = false,
  showDateSeparators = true,
  emptyState,
  showMasOrigin = false,
}: MessengerMessageListProps) {
  if (!messages.length) return emptyState ? <>{emptyState}</> : null;

  return (
    <div
      className={`messenger-message-list${compact ? ' messenger-message-list--compact' : ''}${className ? ` ${className}` : ''}`}
      style={maxHeight ? { maxHeight, overflowY: 'auto' } : undefined}
      aria-label="Message history"
    >
      {messages.map((message, index) => {
        // `eventAt` is the resolved Facebook event time. `timestamp` is only
        // a raw label and can contain just a clock, so it must not overwrite a
        // known day in the timeline.
        const dateLabel = messengerDateLabel(message.eventAt) || messengerDateLabel(message.timestamp);
        const previousDateLabel = index
          ? messengerDateLabel(messages[index - 1].eventAt) || messengerDateLabel(messages[index - 1].timestamp)
          : null;
        const showDate = showDateSeparators && dateLabel && dateLabel !== previousDateLabel;
        const sender = message.sender?.trim() || (message.outgoing ? 'Page' : 'Seeker');
        const metaLabel = messengerMetaLabel(sender, message.timestamp, message.eventAt);
        const { body, reactions: legacyReactions } = splitReactionMarkers(message.content);
        const reactions = message.reactions?.length
          ? message.reactions.map(reaction => ({
            emoji: reaction.emoji || '•',
            actor: reaction.actor || null,
            count: reaction.count || 1,
            // A missing actor is evidence of uncertainty, not a label we can
            // replace with the bubble sender.
            label: `${reaction.actor || 'người phản ứng chưa xác định'} thả ${reaction.emoji || '•'} ×${reaction.count || 1}`,
          }))
          : legacyReactions.map(emoji => ({
            emoji, actor: null, count: 1,
            label: `${emoji} — actor/target không có trong dữ liệu cũ`,
          }));

        return (
          <div className="messenger-message-group" key={message.id ?? `${message.timestamp || 'message'}-${sender}-${index}`}>
            {showDate && (
              <div className="messenger-date-separator" aria-label={`Messages from ${dateLabel}`}>
                <span />
                <time>{dateLabel}</time>
                <span />
              </div>
            )}
            <article className={`messenger-message ${message.outgoing ? 'messenger-message--outgoing' : 'messenger-message--incoming'}`}>
              <div className="messenger-message-content">{body || '(empty)'}</div>
              {message.quotedText && (
                <aside className="messenger-message-quote" aria-label="Quoted reply evidence">
                  <strong>Trích dẫn{message.quotedSender ? ` từ ${message.quotedSender}` : ' — người gửi chưa xác định'}:</strong>{' '}
                  {message.quotedText}
                </aside>
              )}
              {reactions.length > 0 && (
                <div className="messenger-message-reactions" aria-label={`Reactions: ${reactions.map(reaction => reaction.label).join('; ')}`}>
                  {reactions.map((reaction, reactionIndex) => (
                    <span key={`${reaction.label}-${reactionIndex}`} title={reaction.label}>
                      <span aria-hidden="true">{reaction.emoji}</span>{' '}
                      <small>{reaction.actor || 'unknown'} · ×{reaction.count}</small>
                    </span>
                  ))}
                </div>
              )}
              {metaLabel && <time className="messenger-message-time">
                {showMasOrigin && message.masProposed && (
                  <span className="messenger-message-mas-origin" title="Tin nhắn được MAS đề nghị" aria-label="Tin nhắn được MAS đề nghị">🤖</span>
                )}
                {metaLabel}
              </time>}
            </article>
          </div>
        );
      })}
    </div>
  );
}
