// code:web-page-004:journey
import { JourneyFlow } from '@/components/journey-flow';
import { getAllSeekers } from '@/lib/queries';
import { JourneyTransitionRules } from '@/components/journey-transition-rules';
import { normalizeJourneyStage } from '@/lib/journey-engine';

export const dynamic = 'force-dynamic';

export default function JourneyPage() {
  const seekers = getAllSeekers();

  // Count seekers by normalized canonical journey stage
  const seekerCountByStage: Record<string, number> = {};
  for (const s of seekers) {
    const stage = normalizeJourneyStage(s.leadStage);
    seekerCountByStage[stage] = (seekerCountByStage[stage] || 0) + 1;
  }

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">🛤️ Seeker Journey Workflow</h1>
        <p className="page-subtitle">
          AI-powered customer journey from first interaction to Sahaja Mahayogi. Each node is a stage, edges show touch-point triggers.
        </p>
      </div>

      <JourneyFlow seekerCountByStage={seekerCountByStage} initialSeekers={seekers} />

      <JourneyTransitionRules />
    </>
  );
}

