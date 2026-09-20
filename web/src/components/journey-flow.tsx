// code:web-component-004:journey-flow
'use client';

import { useState, useCallback, useMemo } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  type NodeProps,
  type Node,
  type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { JOURNEY_STAGES, type JourneyStage, type Seeker } from '@/lib/types';
import { JOURNEY_TRANSITIONS, normalizeJourneyStage, cumulativeJourneyCounts } from '@/lib/journey-engine';
import { FunnelFilterBar, type FilterState } from './funnel-filter-bar';
import { isDateInRange } from '@/lib/funnel-filters';
import { PROGRAMS } from '@/lib/programs';

// ── Custom Journey Node ──
interface JourneyNodeData extends Record<string, unknown> {
  label: string;
  description: string;
  stage: JourneyStage;
  seekerCount: number;
  isActive: boolean;
}

function JourneyNodeComponent({ data }: NodeProps<Node<JourneyNodeData>>) {
  const stageColors: Record<string, string> = {
    User: '#a78bfa',
    Seeker: '#6366f1',
    Seeker_Public_Program: '#8b5cf6',
    Seeker_18_Weeks: '#06b6d4',
    Seed: '#10b981',
    Sahaja_Yogi: '#f59e0b',
    Sahaja_Yogi_Dedicated: '#f43f5e',
    Sahaja_Mahayogi: '#ec4899',
  };

  const color = stageColors[data.stage] || '#6b7280';
  const isActive = data.seekerCount > 0;

  return (
    <div
      className={`journey-node ${isActive ? 'active' : ''}`}
      style={{ borderColor: isActive ? color : undefined }}
    >
      <Handle type="target" position={Position.Left} style={{ background: color, width: 8, height: 8 }} />
      <div className="journey-node-label">{data.label}</div>
      <div className="journey-node-count" style={{ 
        background: `linear-gradient(135deg, ${color}, ${color}cc)`,
        WebkitBackgroundClip: 'text',
        WebkitTextFillColor: 'transparent',
      }}>
        {data.seekerCount}
      </div>
      <div className="journey-node-desc">{data.description}</div>
      <Handle type="source" position={Position.Right} style={{ background: color, width: 8, height: 8 }} />
    </div>
  );
}

// ── Main Component ──
interface JourneyFlowProps {
  seekerCountByStage?: Record<string, number>;
  initialSeekers?: Seeker[];
}

export function JourneyFlow({ seekerCountByStage, initialSeekers }: JourneyFlowProps) {
  const [filterState, setFilterState] = useState<FilterState>({ city: 'all', programCode: 'all', dateRange: 'all' });

  const filteredSeekers = useMemo(() => {
    if (!initialSeekers) return [];
    return initialSeekers.filter(s => {
      if (filterState.city !== 'all') {
        if ((s.city || 'Unknown').toLowerCase() !== filterState.city.toLowerCase()) return false;
      }
      if (filterState.programCode !== 'all' && s.programCode !== filterState.programCode) return false;
      if (!isDateInRange(s.lastMessageTimestampText || s.lastInteraction || s.firstSeen, filterState.dateRange)) return false;
      return true;
    });
  }, [initialSeekers, filterState]);

  const counts: Record<string, number> = useMemo(() => {
    if (initialSeekers) {
      const c: Record<string, number> = {};
      for (const s of filteredSeekers) {
        const stage = normalizeJourneyStage(s.leadStage);
        c[stage] = (c[stage] || 0) + 1;
      }
      return c;
    }
    return seekerCountByStage || {};
  }, [initialSeekers, filteredSeekers, seekerCountByStage]);

  const nodeTypes = useMemo(() => ({ journeyNode: JourneyNodeComponent }), []);
  const funnelCounts = useMemo(() => cumulativeJourneyCounts(counts), [counts]);

  const nodes: Node<JourneyNodeData>[] = useMemo(() => 
    JOURNEY_STAGES.map((stage, i) => ({
      id: stage.key,
      type: 'journeyNode',
      position: { x: i * 240, y: Math.sin(i * 0.8) * 80 + 150 },
      data: {
        label: stage.label,
        description: 'Contacts ở giai đoạn này hoặc các giai đoạn sau',
        stage: stage.key,
        seekerCount: funnelCounts[stage.key] || 0,
        isActive: (funnelCounts[stage.key] || 0) > 0,
      },
    })),
  [funnelCounts]);

  // Deduplicate edges
  const edges: Edge[] = useMemo(() => {
    const seen = new Set<string>();
    return JOURNEY_TRANSITIONS
      .filter(t => {
        const key = `${t.fromStage}-${t.toStage}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .map((t, i) => ({
        id: `edge-${i}`,
        source: t.fromStage,
        target: t.toStage,
        label: t.triggerType.replace(/_/g, ' '),
        animated: true,
        style: { stroke: '#6366f1', strokeWidth: 2 },
        labelStyle: { fill: '#a0a0b8', fontSize: 10, fontFamily: 'Inter' },
        labelBgStyle: { fill: '#1a1a2e', fillOpacity: 0.8 },
      }));
  }, []);

  const onInit = useCallback((reactFlowInstance: { fitView: () => void }) => {
    setTimeout(() => reactFlowInstance.fitView(), 100);
  }, []);

  return (
    <div>
      {initialSeekers && (
        <FunnelFilterBar
          onFilterChange={setFilterState}
          availablePrograms={PROGRAMS}
          totalCount={initialSeekers.length}
          filteredCount={filteredSeekers.length}
          unitLabel="contacts"
        />
      )}
      <div className="card" style={{ marginBottom: 16 }} aria-live="polite">
        <h3 style={{ fontSize: 18, marginBottom: 8 }}>
          Tổng contacts trong hành trình: {Object.values(counts).reduce((total, count) => total + count, 0).toLocaleString('vi-VN')}
        </h3>
        <p style={{ color: 'var(--text-secondary)', fontSize: 13, lineHeight: 1.7 }}>
          {JOURNEY_STAGES.filter(stage => (funnelCounts[stage.key] || 0) > 0)
            .map(stage => `${stage.label}: ${funnelCounts[stage.key].toLocaleString('vi-VN')}`)
            .join(' → ') || 'Không có contacts phù hợp với bộ lọc.'}
        </p>
        <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 8 }}>
          Số cộng dồn theo giai đoạn hiện tại: mỗi bước bao gồm contacts ở bước đó và tất cả bước sau. Các bước có trùng contacts nên không cộng các số này thành tổng.
        </p>
      </div>
      <div style={{ height: 'calc(100vh - 360px)', minHeight: '500px' }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
        nodeTypes={nodeTypes}
        onInit={onInit}
        fitView
        proOptions={{ hideAttribution: true }}
        style={{ background: '#1a1a2e', borderRadius: '16px' }}
      >
        <Background color="#2a2a40" gap={20} />
        <Controls
          style={{
            background: '#1e1e30',
            borderRadius: '8px',
            border: '1px solid rgba(99, 102, 241, 0.3)',
            boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          }}
          className="react-flow-controls-dark"
        />
        <MiniMap
          style={{ background: '#12121a', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.06)' }}
          nodeColor={(node) => {
            const stageColors: Record<string, string> = {
              Unknown: '#6b7280', Seeker: '#6366f1', Seeker_Public_Program: '#8b5cf6',
              Seeker_18_Weeks: '#06b6d4', Seed: '#10b981', Sahaja_Yogi: '#f59e0b',
              Sahaja_Yogi_Dedicated: '#f43f5e', Sahaja_Mahayogi: '#ec4899',
            };
            return stageColors[(node.data as JourneyNodeData)?.stage] || '#a78bfa';
          }}
        />
      </ReactFlow>
      </div>
    </div>
  );
}
