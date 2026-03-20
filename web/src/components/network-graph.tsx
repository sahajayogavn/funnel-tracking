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
  type: 'page' | 'city' | 'post' | 'ad' | 'user';
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

interface HoveredNodeDetails {
  profile?: Record<string, any>;
  messages?: { sender: string; content: string; message_timestamp: string }[];
  post?: { post_name: string; post_url: string; created_at: string; last_synced_time: string; is_orphan?: boolean };
  stats?: { total: number; unique_users: number };
  comments?: { commenter_name: string; comment_text: string; comment_timestamp: string; is_reply: number }[];
}

export function NetworkGraph() {
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null);
  const [hoveredNodeDetails, setHoveredNodeDetails] = useState<HoveredNodeDetails | null>(null);
  const fgRef = useRef<{ d3Force: (name: string) => { strength: (s: number) => void } | undefined }>(null);
  const hoverTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Fetch detailed data when hovering over a node for more than 400ms
  const fetchNodeDetails = useCallback(async (node: GraphNode) => {
    try {
      const typeParam = node.type;
      let idParam = node.id;
      
      // Clean up graph node ID prefixes so API gets clean IDs
      if (node.type === 'user') {
        idParam = node.name; // Use the actual name for thread lookup since id is 'dm-user-...'
      } else if (node.type === 'ad' || node.type === 'post') {
        idParam = node.id.replace(/^(ad|post)\-/i, '');
      }

      // City/Page don't have detail streams yet
      if (node.type === 'city' || node.type === 'page') return;

      const res = await fetch(`/api/graph/details?id=${encodeURIComponent(idParam)}&type=${typeParam}`);
      if (!res.ok) throw new Error('Failed to fetch details');
      const data = await res.json();
      setHoveredNodeDetails(data);
    } catch (err) {
      console.error(err);
    }
  }, []);

  const handleNodeHover = useCallback((node: GraphNode | null) => {
    // Clear existing timeout
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
    }

    setHoveredNode(node);
    
    // Reset details immediately if user un-hovers or moves to a new node
    setHoveredNodeDetails(null);

    // If hovering a real node, start a 400ms timer before fetching its huge history
    if (node) {
      hoverTimeoutRef.current = setTimeout(() => {
        fetchNodeDetails(node);
      }, 400);
    }
  }, [fetchNodeDetails]);

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
        node.type === 'post' ? '📝' : 
          node.type === 'ad' ? '📣' : '👤';
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
          onNodeHover={handleNodeHover}
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
            minWidth: '280px',
            maxWidth: '380px',
            boxShadow: 'var(--shadow-glow)',
            zIndex: 10,
            maxHeight: 'calc(100vh - 250px)',
            overflowY: 'auto',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
            <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)' }}>
              {hoveredNode.type.toUpperCase()}
            </div>
            {hoveredNodeDetails && (
              <div style={{ fontSize: '10px', background: 'rgba(16, 185, 129, 0.2)', color: '#10b981', padding: '2px 6px', borderRadius: '4px' }}>
                LIVE DATA
              </div>
            )}
          </div>
          
          <div style={{ fontSize: '15px', fontWeight: 700, color: hoveredNode.color, marginBottom: '8px', lineHeight: 1.4 }}>
            {hoveredNode.name}
          </div>

          {/* BASE INFO */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginBottom: '12px' }}>
            {hoveredNode.phone && (
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                📞 {hoveredNode.phone}
              </div>
            )}
            {hoveredNode.type === 'user' && hoveredNode.dbId && (
              <a href={`/seekers/${hoveredNode.dbId}`} className="fb-link" style={{ fontSize: '12px', color: '#818cf8', display: 'block' }}>
                View CRM Profile →
              </a>
            )}
            {hoveredNode.fbUrl && (
              <a href={hoveredNode.fbUrl.startsWith('http') ? hoveredNode.fbUrl : `https://facebook.com/${hoveredNode.fbUrl}`} target="_blank" rel="noopener noreferrer" className="fb-link" style={{ fontSize: '12px', display: 'block' }}>
                Facebook Profile ↗
              </a>
            )}
          </div>

          {/* FETCHED DETAILS STATES */}
          {!hoveredNodeDetails && (hoveredNode.type === 'user' || hoveredNode.type === 'ad' || hoveredNode.type === 'post') && (
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontStyle: 'italic', marginTop: '12px', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '12px' }}>
              Hover to load history...
            </div>
          )}

          {/* USER CHAT HISTORY */}
          {hoveredNodeDetails && hoveredNode.type === 'user' && (
            <div style={{ marginTop: '12px', borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '12px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0', marginBottom: '8px' }}>Chat History</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {Array.isArray(hoveredNodeDetails.messages) && hoveredNodeDetails.messages.length > 0 ? (
                  hoveredNodeDetails.messages.map((msg: Record<string, any>, i: number) => {
                    const isPage = msg.sender === '1548373332058326';
                    return (
                      <div key={i} style={{
                        alignSelf: isPage ? 'flex-end' : 'flex-start',
                        background: isPage ? 'rgba(99, 102, 241, 0.2)' : 'rgba(255,255,255,0.05)',
                        padding: '6px 10px',
                        borderRadius: '8px',
                        fontSize: '12px',
                        color: 'var(--text-secondary)',
                        maxWidth: '90%',
                        wordBreak: 'break-word'
                      }}>
                        {msg.content}
                      </div>
                    );
                  })
                ) : (
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>No messages found.</div>
                )}
              </div>
            </div>
          )}

          {/* POST / AD DETAILS */}
          {hoveredNodeDetails && (hoveredNode.type === 'post' || hoveredNode.type === 'ad') && (
            <div style={{ marginTop: '12px', borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '12px' }}>
              {hoveredNodeDetails.post?.post_name && hoveredNodeDetails.post.post_name !== hoveredNode.name && (
                <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '12px', lineHeight: 1.5, background: 'rgba(0,0,0,0.2)', padding: '8px', borderRadius: '6px' }}>
                  {hoveredNodeDetails.post.post_name}
                </div>
              )}
              
              <div style={{ display: 'flex', gap: '12px', marginBottom: '12px', fontSize: '12px' }}>
                <span style={{ color: '#10b981' }}>💬 {hoveredNodeDetails.stats?.total || 0} Comments</span>
                <span style={{ color: '#8b5cf6' }}>👥 {hoveredNodeDetails.stats?.unique_users || 0} Users</span>
              </div>

              <div style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0', marginBottom: '8px' }}>Recent Comments</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {Array.isArray(hoveredNodeDetails.comments) && hoveredNodeDetails.comments.slice(0, 10).map((c: Record<string, any>, i: number) => (
                  <div key={i} style={{ fontSize: '12px', background: 'rgba(255,255,255,0.03)', padding: '6px 8px', borderRadius: '6px' }}>
                    <div style={{ color: '#cbd5e1', fontWeight: 600, marginBottom: '2px' }}>{c.commenter_name}</div>
                    <div style={{ color: 'var(--text-muted)' }}>{c.comment_text}</div>
                  </div>
                ))}
              </div>
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
          { emoji: '📣', label: 'Ad', color: '#f97316' },
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
