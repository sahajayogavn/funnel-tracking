// code:web-component-003:network-graph
'use client';

import { useRef, useState, useEffect, useCallback } from 'react';
import dynamic from 'next/dynamic';
import { FunnelFilterBar, type FilterState } from './funnel-filter-bar';
import { getDateRangeBounds } from '@/lib/funnel-filters';

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
  profile?: Record<string, unknown>;
  messages?: { sender: string; content: string; message_timestamp: string }[];
  post?: { post_name: string; post_url: string; created_at: string; last_synced_time: string; is_orphan?: boolean };
  stats?: { total: number; unique_users: number };
  comments?: { commenter_name: string; comment_text: string; comment_timestamp: string; is_reply: number }[];
  error?: string;
}

export function NetworkGraph() {
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null);
  const [hoveredNodeDetails, setHoveredNodeDetails] = useState<HoveredNodeDetails | null>(null);
  const fgRef = useRef<{ d3Force: (name: string) => { strength: (s: number) => void } | undefined }>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState<{ width: number; height: number }>({ width: 900, height: 600 });
  const hoverTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  
  // Double-click manually tracked
  const clickNodeIdRef = useRef<string | null>(null);
  const clickTimerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    const updateDimensions = () => {
      if (containerRef.current) {
        const { clientWidth, clientHeight } = containerRef.current;
        if (clientWidth > 0 && clientHeight > 0) {
          setDimensions({ width: clientWidth, height: clientHeight });
        }
      }
    };
    updateDimensions();
    window.addEventListener('resize', updateDimensions);
    return () => window.removeEventListener('resize', updateDimensions);
  }, [graphData]);

  useEffect(() => {
    return () => {
      if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current);
      if (abortControllerRef.current) abortControllerRef.current.abort();
    };
  }, []);

  // Fetch detailed data when hovering over a node for more than 400ms
  const fetchNodeDetails = useCallback(async (node: GraphNode) => {
    try {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      const controller = new AbortController();
      abortControllerRef.current = controller;

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

      const res = await fetch(`/api/graph/details?id=${encodeURIComponent(idParam)}&type=${typeParam}`, {
        signal: controller.signal,
      });
      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        setHoveredNodeDetails({ error: errorData?.error || 'Details not found' });
        return;
      }
      const data = await res.json();
      setHoveredNodeDetails(data);
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        return;
      }
      console.error('Failed to fetch node details:', err);
      setHoveredNodeDetails({ error: 'Failed to connect to server' });
    }
  }, []);

  const handleNodeHover = useCallback((node: GraphNode | null) => {
    // Clear existing timeout
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
      hoverTimeoutRef.current = null;
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
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

  const fetchGraph = useCallback((filters: FilterState) => {
    const params = new URLSearchParams();
    if (filters.city !== 'all') params.set('city', filters.city);
    const { startDate, endDate } = getDateRangeBounds(filters.dateRange);
    if (startDate) params.set('startDate', startDate);
    if (endDate) params.set('endDate', endDate);
    const query = params.toString() ? `?${params.toString()}` : '';
    fetch(`/api/graph${query}`)
      .then(res => res.json())
      .then(data => setGraphData(data))
      .catch(console.error);
  }, []);

  const handleFilterChange = useCallback((filters: FilterState) => {
    fetchGraph(filters);
  }, [fetchGraph]);

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

  return (
    <div style={{ position: 'relative' }}>
      <FunnelFilterBar
        onFilterChange={handleFilterChange}
        totalCount={graphData?.nodes.length || 0}
        filteredCount={graphData?.nodes.length || 0}
        unitLabel="nút mạng"
      />
      {!graphData ? (
        <div className="loading-spinner"><div className="spinner" /></div>
      ) : (
        <>
          <div className="graph-container" ref={containerRef}>
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
                if (clickNodeIdRef.current === node.id && clickTimerRef.current) {
                  // Double click detected
                  clearTimeout(clickTimerRef.current);
                  clickTimerRef.current = null;
                  clickNodeIdRef.current = null;
                  
                  if (node.type === 'user' && node.dbId) {
                    // Navigate to seeker detail page with clean numeric ID
                    window.location.href = `/seekers/${node.dbId}`;
                  } else if (node.fbUrl) {
                    const url = node.fbUrl.startsWith('http') ? node.fbUrl : `https://facebook.com/${node.fbUrl}`;
                    window.open(url, '_blank');
                  }
                } else {
                  // Single click (start timer)
                  if (clickTimerRef.current) clearTimeout(clickTimerRef.current);
                  clickNodeIdRef.current = node.id;
                  clickTimerRef.current = setTimeout(() => {
                    clickTimerRef.current = null;
                    clickNodeIdRef.current = null;
                  }, 300);
                }
              }}
              backgroundColor="#1a1a2e"
              width={dimensions.width}
              height={dimensions.height}
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
            {hoveredNodeDetails?.post?.post_url && (
              <a href={hoveredNodeDetails.post.post_url} target="_blank" rel="noopener noreferrer" className="fb-link" style={{ fontSize: '12px', display: 'block' }}>
                Facebook Post ↗
              </a>
            )}
          </div>

          {/* FETCHED DETAILS STATES */}
          {!hoveredNodeDetails && (hoveredNode.type === 'user' || hoveredNode.type === 'ad' || hoveredNode.type === 'post') && (
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontStyle: 'italic', marginTop: '12px', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '12px' }}>
              Hover to load history...
            </div>
          )}

          {/* ERROR STATE */}
          {hoveredNodeDetails?.error && (
            <div style={{ fontSize: '11px', color: '#ef4444', fontStyle: 'italic', marginTop: '12px', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '12px' }}>
              ⚠️ {hoveredNodeDetails.error}
            </div>
          )}

          {/* USER CHAT HISTORY */}
          {hoveredNodeDetails && hoveredNode.type === 'user' && (
            <div style={{ marginTop: '12px', borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '12px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0', marginBottom: '8px' }}>Chat History</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {Array.isArray(hoveredNodeDetails.messages) && hoveredNodeDetails.messages.length > 0 ? (
                  hoveredNodeDetails.messages.map((msg: { sender: string; content?: string }, i: number) => {
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
                <span style={{ color: '#10b981' }}>
                  💬 {hoveredNodeDetails.stats?.total || 0} {hoveredNode.type === 'ad' && !hoveredNodeDetails.post?.post_url ? 'Inquiries' : 'Comments'}
                </span>
                <span style={{ color: '#8b5cf6' }}>👥 {hoveredNodeDetails.stats?.unique_users || 0} Users</span>
              </div>

              <div style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0', marginBottom: '8px' }}>
                {hoveredNode.type === 'ad' && !hoveredNodeDetails.post?.post_url ? 'Recent Inquiries' : 'Recent Comments'}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {Array.isArray(hoveredNodeDetails.comments) && hoveredNodeDetails.comments.length > 0 ? (
                  hoveredNodeDetails.comments.slice(0, 10).map((c: { commenter_name: string; comment_text?: string }, i: number) => (
                    <div key={i} style={{ fontSize: '12px', background: 'rgba(255,255,255,0.03)', padding: '6px 8px', borderRadius: '6px' }}>
                      <div style={{ color: '#cbd5e1', fontWeight: 600, marginBottom: '2px' }}>{c.commenter_name}</div>
                      <div style={{ color: 'var(--text-muted)' }}>{c.comment_text}</div>
                    </div>
                  ))
                ) : (
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                    {hoveredNode.type === 'ad' ? 'No recent inquiries found.' : 'No comments found.'}
                  </div>
                )}
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
      </>
      )}
    </div>
  );
}
