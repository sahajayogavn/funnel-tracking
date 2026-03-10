// code:web-component-001:interaction-histogram
// Compact 1-row × 12 cells (months) activity histogram
'use client';

interface HistogramProps {
  data: { date: string; count: number }[];
}

export function InteractionHistogram({ data }: HistogramProps) {
  // Group by month (last 12 months)
  const now = new Date();
  const monthCounts: number[] = new Array(12).fill(0);

  for (const d of data) {
    const date = new Date(d.date);
    const monthsAgo = (now.getFullYear() - date.getFullYear()) * 12 + (now.getMonth() - date.getMonth());
    if (monthsAgo >= 0 && monthsAgo < 12) {
      monthCounts[11 - monthsAgo] += d.count;
    }
  }

  const maxCount = Math.max(...monthCounts, 1);

  return (
    <div style={{ display: 'flex', gap: '2px', alignItems: 'center' }}>
      {monthCounts.map((count, i) => {
        let alpha = 0.06;
        if (count > 0) {
          const ratio = count / maxCount;
          if (ratio <= 0.25) alpha = 0.3;
          else if (ratio <= 0.5) alpha = 0.5;
          else if (ratio <= 0.75) alpha = 0.7;
          else alpha = 1.0;
        }

        return (
          <div
            key={i}
            title={`${count} interaction${count !== 1 ? 's' : ''}`}
            style={{
              width: '8px',
              height: '8px',
              borderRadius: '2px',
              background: count > 0
                ? `rgba(99, 102, 241, ${alpha})`
                : 'rgba(255, 255, 255, 0.06)',
            }}
          />
        );
      })}
    </div>
  );
}
