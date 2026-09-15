// code:web-component-006:seven-star-progress
'use client';

import { getStageNumber, CANONICAL_STAGES } from '@/lib/types';

interface SevenStarProgressProps {
  leadStage?: string | null;
  size?: 'sm' | 'md' | 'lg';
  showLabel?: boolean;
}

export function SevenStarProgress({
  leadStage,
  size = 'sm',
  showLabel = true,
}: SevenStarProgressProps) {
  const stageNum = getStageNumber(leadStage);
  const currentStage = CANONICAL_STAGES.find(s => s.number === stageNum) || CANONICAL_STAGES[0];
  const stageLabel = leadStage && leadStage.trim() !== '' ? leadStage : currentStage.label;

  const starSize = size === 'sm' ? 13 : size === 'md' ? 16 : 20;

  return (
    <div
      role="meter"
      aria-label={`Giai đoạn ${stageNum}/7: ${stageLabel}`}
      aria-valuenow={stageNum}
      aria-valuemin={1}
      aria-valuemax={7}
      aria-valuetext={`Giai đoạn ${stageNum} trên 7: ${stageLabel}`}
      tabIndex={0}
      title={`Giai đoạn ${stageNum}/7: ${stageLabel} - ${currentStage.description}`}
      style={{
        display: 'inline-flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: '2px',
        cursor: 'help',
        outline: 'none',
      }}
    >
      {/* 7 Stars Row */}
      <div
        style={{
          display: 'flex',
          gap: size === 'sm' ? '2px' : '3px',
          alignItems: 'center',
        }}
        aria-hidden="true"
      >
        {[1, 2, 3, 4, 5, 6, 7].map(num => {
          const isHighlighted = num <= stageNum;
          return (
            <span
              key={num}
              style={{
                fontSize: `${starSize}px`,
                lineHeight: 1,
                color: isHighlighted ? '#fbbf24' : 'rgba(255, 255, 255, 0.16)',
                textShadow: isHighlighted ? '0 0 6px rgba(251, 191, 36, 0.45)' : 'none',
                transition: 'color 0.15s ease',
              }}
            >
              ★
            </span>
          );
        })}
      </div>

      {/* Smaller stage label beneath */}
      {showLabel && (
        <span
          style={{
            fontSize: size === 'sm' ? '10px' : '11px',
            fontWeight: 600,
            color: currentStage.color || 'var(--text-secondary)',
            letterSpacing: '0.02em',
            whiteSpace: 'nowrap',
          }}
        >
          {stageLabel}
        </span>
      )}
    </div>
  );
}
