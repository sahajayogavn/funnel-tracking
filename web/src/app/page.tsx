// code:web-page-001:dashboard
import { getDashboardStats } from '@/lib/queries';

export const dynamic = 'force-dynamic';

export default function DashboardPage() {
  const stats = getDashboardStats();

  // Acquisition funnel: DM Users → Total Contacts → Messages
  const funnelSteps = [
    { label: 'DM Users', value: stats.totalDMUsers, color: '#818cf8', icon: '💬', desc: 'Raw inbox threads' },
    { label: 'Total Contacts', value: stats.totalSeekers, color: '#a78bfa', icon: '👥', desc: 'Deduplicated unique people' },
    { label: 'Messages', value: stats.totalMessages, color: '#6366f1', icon: '✉️', desc: 'Total messages exchanged' },
  ];

  // Detailed stat cards
  const detailStats = [
    { label: 'Posts Tracked', value: stats.totalPosts, color: '#f59e0b' },
    { label: 'DM Threads', value: stats.totalThreads, color: '#818cf8' },
    { label: 'Commenters', value: stats.totalCommentUsers, color: '#10b981' },
    { label: 'Comments', value: stats.totalComments, color: '#f59e0b' },
  ];

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">📊 Dashboard</h1>
        <p className="page-subtitle">Overview of Thiền Sahaja Yoga Việt Nam Funnel</p>
      </div>

      {/* Row 1: Acquisition Funnel */}
      <div className="card" style={{ padding: '28px 32px', marginBottom: '20px' }}>
        <h2 style={{ fontSize: '16px', fontWeight: 700, marginBottom: '20px', color: 'var(--text-secondary)' }}>
          📈 Acquisition Funnel
        </h2>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0', flexWrap: 'wrap' }}>
          {funnelSteps.map((step, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center' }}>
              {i > 0 && (
                <div style={{
                  width: '48px', height: '2px',
                  background: `linear-gradient(90deg, ${funnelSteps[i-1].color}, ${step.color})`,
                  margin: '0 4px', position: 'relative',
                }}>
                  <span style={{
                    position: 'absolute', top: '-12px', left: '50%', transform: 'translateX(-50%)',
                    fontSize: '14px', color: 'var(--text-muted)',
                  }}>→</span>
                </div>
              )}
              <div style={{
                textAlign: 'center', padding: '16px 24px',
                background: `${step.color}10`,
                border: `1px solid ${step.color}30`,
                borderRadius: '12px', minWidth: '160px',
              }}>
                <div style={{ fontSize: '11px', marginBottom: '4px' }}>{step.icon}</div>
                <div style={{ fontSize: '32px', fontWeight: 800, color: step.color, lineHeight: 1 }}>
                  {step.value.toLocaleString()}
                </div>
                <div style={{
                  fontSize: '12px', fontWeight: 700, color: step.color,
                  textTransform: 'uppercase', letterSpacing: '0.06em', marginTop: '4px',
                }}>{step.label}</div>
                <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginTop: '2px' }}>
                  {step.desc}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Row 2: Detailed Stat Cards */}
      <div className="stat-grid">
        {detailStats.map((s, i) => (
          <div key={i} className="stat-card">
            <div className="stat-value" style={{ color: s.color }}>{s.value}</div>
            <div className="stat-label">{s.label}</div>
          </div>
        ))}
      </div>

      {/* Row 3: Seeker Journey Funnel */}
      <div className="card" style={{ padding: '28px 32px' }}>
        <h2 style={{ fontSize: '16px', fontWeight: 700, marginBottom: '16px', color: 'var(--text-secondary)' }}>
          🛤️ Seeker Journey Funnel <span style={{ fontSize: '13px', fontWeight: 400, color: 'var(--text-muted)' }}>({stats.totalSeekers} total)</span>
        </h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          {[
            { label: 'User', color: '#a78bfa', count: stats.totalSeekers },
            { label: 'Seeker', color: '#6366f1', count: stats.seekerStageCount },
            { label: 'Public Program', color: '#8b5cf6', count: 0 },
            { label: '18 Weeks', color: '#06b6d4', count: 0 },
            { label: 'Seed', color: '#10b981', count: 0 },
            { label: 'Sahaja Yogi', color: '#f59e0b', count: 0 },
            { label: 'Dedicated', color: '#f43f5e', count: 0 },
            { label: 'Mahayogi', color: '#ec4899', count: 0 },
          ].map((stage, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
              {i > 0 && <span style={{ color: 'var(--text-muted)', margin: '0 4px' }}>→</span>}
              <span
                className="badge"
                style={{ background: `${stage.color}22`, color: stage.color, fontSize: '12px' }}
              >
                {stage.label} ({stage.count})
              </span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
