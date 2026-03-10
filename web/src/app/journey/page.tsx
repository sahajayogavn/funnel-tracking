// code:web-page-004:journey
import { JourneyFlow } from '@/components/journey-flow';
import { getAllSeekers } from '@/lib/queries';
import { JOURNEY_TRANSITIONS } from '@/lib/journey-engine';

export const dynamic = 'force-dynamic';

export default function JourneyPage() {
  const seekers = getAllSeekers();

  // Count seekers by stage
  // Dynamic: has phone → 'Seeker', else → 'User' (already computed in getAllSeekers)
  const seekerCountByStage: Record<string, number> = {};
  for (const s of seekers) {
    seekerCountByStage[s.leadStage] = (seekerCountByStage[s.leadStage] || 0) + 1;
  }

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">🛤️ Seeker Journey Workflow</h1>
        <p className="page-subtitle">
          AI-powered customer journey from first interaction to Sahaja Mahayogi. Each node is a stage, edges show touch-point triggers.
        </p>
      </div>

      <JourneyFlow seekerCountByStage={seekerCountByStage} />

      {/* Transition Rules Table */}
      <div className="card" style={{ marginTop: '24px' }}>
        <h2 style={{ fontSize: '16px', fontWeight: 700, marginBottom: '16px' }}>
          ⚡ Journey Transition Rules
        </h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>From Stage</th>
              <th>To Stage</th>
              <th>Trigger</th>
              <th>Condition</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {JOURNEY_TRANSITIONS.map((t, i) => (
              <tr key={i}>
                <td><span className="badge badge-indigo">{t.fromStage}</span></td>
                <td><span className="badge badge-emerald">{t.toStage}</span></td>
                <td><span className="badge badge-amber">{t.triggerType}</span></td>
                <td>{t.condition}</td>
                <td style={{ fontSize: '12px' }}>{t.action}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
