// code:web-component-007:seeker-journey-timeline
'use client';

import { useState, useEffect, useCallback } from 'react';
import type { Seeker } from '@/lib/types';
import {
  CANONICAL_STAGES,
  getStageNumber,
  formatRelativeElapsed,
} from '@/lib/types';
import type { ActionQueueItem } from '@/lib/queries';

interface SeekerJourneyTimelineProps {
  seeker: Seeker;
  compact?: boolean;
  onRefreshSeeker?: () => void;
}

export function SeekerJourneyTimeline({ seeker, compact = false, onRefreshSeeker }: SeekerJourneyTimelineProps) {
  const currentStageNum = getStageNumber(seeker.leadStage);

  // Queued recommendations for this seeker
  const [queuedItems, setQueuedItems] = useState<ActionQueueItem[]>([]);
  const [loadingQueue, setLoadingQueue] = useState(false);
  const [runningRec, setRunningRec] = useState(false);
  const [actionError, setActionError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');

  const fetchQueuedItems = useCallback(async () => {
    setLoadingQueue(true);
    try {
      const params = new URLSearchParams();
      if (seeker.threadId) params.set('targetId', seeker.threadId);
      if (seeker.name) params.set('targetName', seeker.name);
      const res = await fetch(`/api/action-queue?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setQueuedItems(data || []);
      }
    } catch (err) {
      console.error('Failed to fetch queued recommendations:', err);
    } finally {
      setLoadingQueue(false);
    }
  }, [seeker.threadId, seeker.name]);

  useEffect(() => {
    fetchQueuedItems();
  }, [fetchQueuedItems]);

  const handleDecision = async (id: number, decision: 'approve' | 'reject') => {
    setActionError('');
    try {
      const res = await fetch(`/api/action-queue/${id}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ decision }),
      });
      if (!res.ok) {
        setActionError('Không thể cập nhật quyết định. Vui lòng thử lại.');
        return;
      }
      const updated = await res.json();
      setQueuedItems(prev =>
        prev.map(item => (item.id === id ? { ...item, status: updated.status, approvalSource: 'webui' } : item))
      );
      if (onRefreshSeeker) onRefreshSeeker();
    } catch {
      setActionError('Lỗi mạng khi cập nhật.');
    }
  };

  const handleRunRecommendations = async () => {
    setRunningRec(true);
    setActionError('');
    setSuccessMsg('');
    try {
      const res = await fetch('/api/action-queue/recommendations', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          type: 'all',
          threadId: seeker.threadId,
          seekerName: seeker.name,
          city: seeker.city,
          limit: 3,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setSuccessMsg(data.message || 'Đã tạo đề xuất an toàn vào hàng đợi.');
        await fetchQueuedItems();
        if (onRefreshSeeker) onRefreshSeeker();
      } else {
        setActionError(data.error || 'Không thể tạo đề xuất.');
      }
    } catch {
      setActionError('Lỗi khi gọi API tạo đề xuất.');
    } finally {
      setRunningRec(false);
    }
  };

  // Base timestamps & active stage resolution
  const firstSeenDate = seeker.firstSeen ? new Date(seeker.firstSeen) : null;
  const lastActiveTimestamp = seeker.lastMessageTimestampText || seeker.lastInteraction || seeker.firstSeen;
  const lastInteractionDate = lastActiveTimestamp ? new Date(lastActiveTimestamp) : null;
  const currentStage = CANONICAL_STAGES.find(s => s.number === currentStageNum) || CANONICAL_STAGES[0];
  const currentElapsed = lastInteractionDate
    ? formatRelativeElapsed(lastInteractionDate)
    : (firstSeenDate ? formatRelativeElapsed(firstSeenDate) : '—');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      {/* ── Compact 7-Stage Seeker Journey Timeline ── */}
      <div className="seeker-timeline-wrapper">
        <div className="seeker-timeline-header">
          <div className="seeker-timeline-title">
            <span aria-hidden="true">🛤️</span>
            <span>Hành trình 7 giai đoạn</span>
          </div>
          <div
            className="seeker-timeline-current-chip"
            aria-label={`Giai đoạn hiện tại: Giai đoạn ${currentStageNum} trên 7, ${currentStage.label}`}
          >
            <span className="seeker-timeline-pulse-dot" aria-hidden="true" />
            <span>GĐ {currentStageNum}/7: {currentStage.label}</span>
            {currentElapsed !== '—' && (
              <span style={{ opacity: 0.85, fontWeight: 500 }}>· {currentElapsed}</span>
            )}
          </div>
        </div>

        {/* Connecting Progress Stepper Rail */}
        <div
          className="seeker-timeline-rail"
          aria-hidden="true"
          title={`Tiến trình hành trình: Giai đoạn ${currentStageNum}/7 (${currentStage.label})`}
        >
          <div className="seeker-timeline-rail-track">
            <div
              className="seeker-timeline-rail-fill"
              style={{
                width: `${Math.max(0, Math.min(100, ((currentStageNum - 1) / 6) * 100))}%`,
              }}
            />
          </div>
          {CANONICAL_STAGES.map(s => {
            const isPrior = s.number < currentStageNum;
            const isCurrent = s.number === currentStageNum;
            return (
              <div
                key={s.number}
                className={`seeker-timeline-rail-node ${
                  isCurrent ? 'is-current' : isPrior ? 'is-prior' : 'is-future'
                }`}
                title={`Giai đoạn ${s.number}: ${s.label}`}
              >
                {isPrior ? '✓' : s.number}
              </div>
            );
          })}
        </div>

        {compact ? (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '4px 8px',
            fontSize: '11px',
            color: 'var(--text-secondary)',
            background: 'rgba(255, 255, 255, 0.02)',
            borderRadius: '6px',
            marginTop: '2px',
          }}>
            <span style={{ color: 'var(--text-muted)' }}>Mục tiêu giai đoạn:</span>
            <span style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{currentStage.description}</span>
          </div>
        ) : (
          /* Accessible 7-Stage Grid Cards */
          <ol
            aria-label="Chi tiết 7 giai đoạn hành trình seeker"
            className="seeker-timeline-grid"
            role="list"
          >
            {CANONICAL_STAGES.map(s => {
              const isPrior = s.number < currentStageNum;
              const isCurrent = s.number === currentStageNum;

              let elapsedStr = '—';
              let statusText = 'Chưa đến';
              if (isPrior) {
                statusText = 'Hoàn thành';
                elapsedStr = s.number === 1 && firstSeenDate ? formatRelativeElapsed(firstSeenDate) : '✓';
              } else if (isCurrent) {
                statusText = 'Hiện tại';
                elapsedStr = currentElapsed;
              }

              const accessibleStatus = isCurrent
                ? `Đang ở giai đoạn này.${elapsedStr !== '—' ? ` Hoạt động: ${elapsedStr} trước.` : ''}`
                : isPrior
                ? `Đã hoàn thành.${s.number === 1 && elapsedStr !== '✓' ? ` Bắt đầu: ${elapsedStr} trước.` : ''}`
                : 'Chưa đạt đến.';

              return (
                <li
                  key={s.number}
                  role="listitem"
                  aria-current={isCurrent ? 'step' : undefined}
                  tabIndex={0}
                  title={`${s.number}. ${s.label}: ${s.description} — ${statusText}${elapsedStr !== '—' && elapsedStr !== '✓' ? ` (${elapsedStr})` : ''}`}
                  className={`seeker-timeline-card ${
                    isCurrent ? 'is-current' : isPrior ? 'is-prior' : 'is-future'
                  }`}
                >
                  <span className="sr-only">
                    Giai đoạn {s.number}: {s.label}. {accessibleStatus} {s.description}
                  </span>
                  <div className="seeker-timeline-card-badge" aria-hidden="true">
                    {isPrior ? '✓' : s.number}
                  </div>
                  <div className="seeker-timeline-card-info">
                    <div className="seeker-timeline-card-name">
                      {s.label}
                    </div>
                    <div className="seeker-timeline-card-meta">
                      <span className="seeker-timeline-card-status">
                        {isCurrent ? '● Hiện tại' : isPrior ? '✓ Xong' : '○ Chưa đến'}
                      </span>
                      {elapsedStr !== '—' && elapsedStr !== '✓' && (
                        <span className="seeker-timeline-card-date">· {elapsedStr}</span>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </div>

      {/* ── Actionable Queued Recommendations ── */}
      <div style={{ marginTop: '6px', borderTop: '1px solid rgba(255, 255, 255, 0.08)', paddingTop: '14px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
          <div style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)' }}>
            ⚡ Đề xuất MAS chờ duyệt ({queuedItems.filter(i => i.status === 'pending').length})
          </div>
          <button
            type="button"
            onClick={handleRunRecommendations}
            disabled={runningRec}
            style={{
              padding: '4px 10px',
              borderRadius: '6px',
              background: 'rgba(99, 102, 241, 0.15)',
              border: '1px solid rgba(99, 102, 241, 0.3)',
              color: '#818cf8',
              fontSize: '11px',
              fontWeight: 600,
              cursor: runningRec ? 'wait' : 'pointer',
            }}
          >
            {runningRec ? 'Đang tạo...' : '⚡ Tạo đề xuất'}
          </button>
        </div>

        {actionError && (
          <div style={{ fontSize: '11px', color: '#fb7185', background: 'rgba(244,63,94,0.1)', padding: '6px 10px', borderRadius: '6px', marginBottom: '8px' }}>
            {actionError}
          </div>
        )}

        {successMsg && (
          <div style={{ fontSize: '11px', color: '#34d399', background: 'rgba(16,185,129,0.1)', padding: '6px 10px', borderRadius: '6px', marginBottom: '8px' }}>
            {successMsg}
          </div>
        )}

        {loadingQueue && (
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', textAlign: 'center', padding: '10px' }}>
            Đang tải đề xuất...
          </div>
        )}

        {!loadingQueue && queuedItems.length === 0 && (
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', padding: '10px 0', textAlign: 'center', background: 'rgba(255,255,255,0.02)', borderRadius: '8px' }}>
            Chưa có đề xuất nào trong hàng đợi. Bấm <strong>⚡ Tạo đề xuất</strong> để AI phân tích.
          </div>
        )}

        {!loadingQueue && queuedItems.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '220px', overflowY: 'auto' }}>
            {queuedItems.map(item => {
              const queueLabel =
                item.queueType === 'reply_message'
                  ? '💬 Reply tin nhắn'
                  : item.queueType === 'reply_comment'
                  ? '↩️ Reply comment'
                  : item.queueType === 'proactive_comment'
                  ? '📣 Comment chủ động'
                  : '✉️ Tin nhắn chủ động';

              return (
                <div
                  key={item.id}
                  style={{
                    padding: '10px 12px',
                    borderRadius: '8px',
                    background: 'rgba(255, 255, 255, 0.04)',
                    border: '1px solid rgba(255, 255, 255, 0.07)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '6px',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: '10px', fontWeight: 700, color: 'var(--accent-indigo)' }}>
                      {queueLabel}
                    </span>
                    <span
                      style={{
                        fontSize: '9px',
                        padding: '1px 6px',
                        borderRadius: '4px',
                        fontWeight: 700,
                        textTransform: 'uppercase',
                        background:
                          item.status === 'approved'
                            ? 'rgba(16, 185, 129, 0.2)'
                            : item.status === 'rejected'
                            ? 'rgba(244, 63, 94, 0.2)'
                            : 'rgba(245, 158, 11, 0.2)',
                        color:
                          item.status === 'approved'
                            ? '#34d399'
                            : item.status === 'rejected'
                            ? '#fb7185'
                            : '#fbbf24',
                      }}
                    >
                      {item.status}
                    </span>
                  </div>

                  <div
                    style={{
                      fontSize: '12px',
                      color: 'var(--text-primary)',
                      lineHeight: 1.4,
                      wordBreak: 'break-word',
                    }}
                  >
                    {item.reactionType ? `React: ${item.reactionType}` : item.actionText}
                  </div>

                  {item.status === 'pending' && (
                    <div style={{ display: 'flex', gap: '6px', marginTop: '4px' }}>
                      <button
                        type="button"
                        onClick={() => handleDecision(item.id, 'approve')}
                        style={{
                          flex: 1,
                          padding: '5px 8px',
                          borderRadius: '6px',
                          background: 'rgba(16, 185, 129, 0.15)',
                          border: '1px solid rgba(16, 185, 129, 0.3)',
                          color: '#34d399',
                          fontSize: '11px',
                          fontWeight: 700,
                          cursor: 'pointer',
                        }}
                      >
                        ✓ Duyệt đề xuất
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDecision(item.id, 'reject')}
                        style={{
                          padding: '5px 8px',
                          borderRadius: '6px',
                          background: 'rgba(244, 63, 94, 0.12)',
                          border: '1px solid rgba(244, 63, 94, 0.25)',
                          color: '#fb7185',
                          fontSize: '11px',
                          fontWeight: 700,
                          cursor: 'pointer',
                        }}
                      >
                        ✕ Từ chối
                      </button>
                    </div>
                  )}
                  {item.status === 'approved' && (
                    <div style={{ fontSize: '10px', color: '#34d399' }}>
                      Đã duyệt qua {item.approvalSource || 'webui'}; chờ worker xử lý.
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
