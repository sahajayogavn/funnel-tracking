// code:web-page-003:graph
import { NetworkGraph } from '@/components/network-graph';

export default function GraphPage() {
  return (
    <>
      <div className="page-header">
        <h1 className="page-title">🕸️ Network Graph</h1>
        <p className="page-subtitle">
          Interactive visualization: Page → Cities → Posts → Users. Click users to open their FB profile.
        </p>
      </div>
      <NetworkGraph />
    </>
  );
}
