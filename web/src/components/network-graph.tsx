// code:web-component-003:network-graph
'use client';

import { useRef, useState, useEffect, useCallback } from 'react';
import dynamic from 'next/dynamic';

// Use 2D ForceGraph which uses Canvas2D (WebGL-accelerated) for better performance
const ForceGraph2D = dynamic(() => import('react-force-graph-2d'), {
  ssr: false,
  loading: () => <div className="loading-spinner"><div className="spinner" /></div>,
});

interface GraphNode {
  id: string;
  name: string;
  type: 'page' | 'city' | 'post' | 'user';
  val: number;
  color: string;
  fbUrl?: string;
  phone?: string;
  dbId?: number;    // users.id for /seekers/[id] routing
  x?: number;
  y?: number;
}

interface GraphLink {
  source: string | GraphNode;
  target: string | GraphNode;
}

interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}

export function NetworkGraph() {
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null);
  const fgRef = useRef<{ d3Force: (name: string) => { strength: (s: number) => void } }>(null);

  useEffect(() => {
    fetch('/api/graph')
      .then(res => res.json())
      .then(data => setGraphData(data))
      .catch(console.error);
  }, []);

  useEffect(() => {
    if (fgRef.current) {
      try {
        fgRef.current.d3Force('charge')?.strength(-120);
      } catch { /* ignore */ }
    }
  }, [graphData]);

  const nodeCanvasObject = useCallback((node: GraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const x = node.x ?? 0;
    const y = node.y ?? 0;
    const radius = node.val / (node.type === 'page' ? 1.5 : 2);
    const fontSize = Math.max(10 / globalScale, 3);

    // Node circle
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, 2 * Math.PI);
    ctx.fillStyle = node.color;
    ctx.fill();

    // Glow for page/city
    if (node.type === 'page' || node.type === 'city') {
      ctx.shadowColor = node.color;
      ctx.shadowBlur = 15;
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, 2 * Math.PI);
      ctx.fillStyle = node.color;
      ctx.fill();
      ctx.shadowBlur = 0;
    }

    // Label
    if (globalScale > 0.5 || node.type === 'page' || node.type === 'city') {
      ctx.font = `${node.type === 'page' ? 'bold ' : ''}${fontSize}px Inter, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillStyle = '#e0e0e8';
      ctx.fillText(
        node.name.length > 20 ? node.name.slice(0, 20) + '…' : node.name,
        x,
        y + radius + 3
      );
    }

    // Emoji icons
    const emoji = node.type === 'page' ? '🪷' :
      node.type === 'city' ? '🏙️' :
        node.type === 'post' ? '📝' : '👤';
    ctx.font = `${radius * 1.2}px serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(emoji, x, y);
  }, []);

  if (!graphData) {
    return <div className="loading-spinner"><div className="spinner" /></div>;
  }

  return (
    <div style={{ position: 'relative' }}>
      <div className="graph-container">
        {/* @ts-expect-error - dynamic import type mismatch */}
        <ForceGraph2D
          ref={fgRef}
          graphData={graphData}
          nodeCanvasObject={nodeCanvasObject}
          nodePointerAreaPaint={(node: GraphNode, color: string, ctx: CanvasRenderingContext2D) => {
            const x = node.x ?? 0;
            const y = node.y ?? 0;
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(x, y, node.val / 2, 0, 2 * Math.PI);
            ctx.fill();
          }}
          linkColor={() => 'rgba(99, 102, 241, 0.2)'}
          linkWidth={1.5}
          onNodeHover={(node: GraphNode | null) => setHoveredNode(node)}
          onNodeClick={(node: GraphNode) => {
            if (node.type === 'user' && node.dbId) {
              // Navigate to seeker detail page with clean numeric ID
              window.location.href = `/seekers/${node.dbId}`;
            } else if (node.fbUrl) {
              const url = node.fbUrl.startsWith('http') ? node.fbUrl : `https://facebook.com/${node.fbUrl}`;
              window.open(url, '_blank');
            }
          }}
          backgroundColor="#1a1a2e"
          width={typeof window !== 'undefined' ? window.innerWidth - 340 : 1000}
          height={typeof window !== 'undefined' ? window.innerHeight - 200 : 600}
          warmupTicks={50}
          cooldownTicks={100}
        />
      </div>

      {/* Hover tooltip */}
      {hoveredNode && (
        <div
          style={{
            position: 'absolute',
            top: 16,
            right: 16,
            background: 'var(--bg-secondary)',
            border: '1px solid var(--border-glow)',
            borderRadius: '12px',
            padding: '16px',
            minWidth: '220px',
            boxShadow: 'var(--shadow-glow)',
            zIndex: 10,
          }}
        >
          <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)', marginBottom: '4px' }}>
            {hoveredNode.type}
          </div>
          <div style={{ fontSize: '15px', fontWeight: 700, color: hoveredNode.color }}>
            {hoveredNode.name}
          </div>
          {hoveredNode.fbUrl && (
            <div style={{ marginTop: '8px' }}>
              <a href={hoveredNode.fbUrl.startsWith('http') ? hoveredNode.fbUrl : `https://facebook.com/${hoveredNode.fbUrl}`} target="_blank" rel="noopener noreferrer" className="fb-link" style={{ fontSize: '12px' }}>
                Facebook Profile ↗
              </a>
            </div>
          )}
          {hoveredNode.type === 'user' && hoveredNode.dbId && (
            <div style={{ marginTop: '4px' }}>
              <a href={`/seekers/${hoveredNode.dbId}`} className="fb-link" style={{ fontSize: '12px', color: '#818cf8' }}>
                View Seeker Details →
              </a>
            </div>
          )}
          {hoveredNode.phone && (
            <div style={{ marginTop: '4px', fontSize: '12px', color: 'var(--text-secondary)' }}>
              📞 {hoveredNode.phone}
            </div>
          )}
        </div>
      )}

      {/* Legend */}
      <div
        style={{
          position: 'absolute',
          bottom: 16,
          left: 16,
          display: 'flex',
          gap: '16px',
          background: 'rgba(10, 10, 15, 0.8)',
          padding: '12px 20px',
          borderRadius: '10px',
          backdropFilter: 'blur(8px)',
        }}
      >
        {[
          { emoji: '🪷', label: 'Page', color: '#10b981' },
          { emoji: '🏙️', label: 'City', color: '#3b82f6' },
          { emoji: '📝', label: 'Post', color: '#f59e0b' },
          { emoji: '👤', label: 'User', color: '#8b5cf6' },
        ].map(item => (
          <div key={item.label} style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            <span>{item.emoji}</span>
            <span style={{ color: item.color }}>{item.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
