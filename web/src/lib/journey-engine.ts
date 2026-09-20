// code:web-journey-001:journey-engine
// AI Journey State Machine for Seeker Progression
import type { JourneyStage, TouchPoint } from './types';
import { JOURNEY_STAGES } from './types';

export interface JourneyTransition {
  fromStage: JourneyStage;
  toStage: JourneyStage;
  triggerType: string;
  condition: string; // Human-readable condition
  action: string;    // Human-readable action
}

// ── Transition Rules ──
// Defines how seekers progress through the funnel
export const JOURNEY_TRANSITIONS: JourneyTransition[] = [
  {
    fromStage: 'User',
    toStage: 'Seeker',
    triggerType: 'phone_provided',
    condition: 'User provides phone number via DM to register',
    action: 'Save phone, mark as Seeker, notify Telegram group',
  },
  {
    fromStage: 'User',
    toStage: 'Seeker',
    triggerType: 'ad_reply',
    condition: 'User replies to ad post and provides contact info',
    action: 'Auto-reply with program info, save registration',
  },
  {
    fromStage: 'Seeker',
    toStage: 'Seeker_Public_Program',
    triggerType: 'registration',
    condition: 'Registered for a public meditation program',
    action: 'Send event details, add to class reminder list',
  },
  {
    fromStage: 'Seeker',
    toStage: 'Seeker_Public_Program',
    triggerType: 'message',
    condition: 'Mentions class, meditation, or event interest (3+ interactions)',
    action: 'Share nearest public program schedule',
  },
  {
    fromStage: 'Seeker_Public_Program',
    toStage: 'Seeker_18_Weeks',
    triggerType: 'registration',
    condition: 'Enrolled in 18-week deep learning course',
    action: 'Add to 18-week cohort, send curriculum',
  },
  {
    fromStage: 'Seeker_18_Weeks',
    toStage: 'Seed',
    triggerType: 'class_attendance',
    condition: 'Completed 18-week course',
    action: 'Congratulate, invite to community events',
  },
  {
    fromStage: 'Seed',
    toStage: 'Sahaja_Yogi',
    triggerType: 'stage_transition',
    condition: 'Regular practice established (3+ months active)',
    action: 'Invite to collective meditations, assign mentor',
  },
  {
    fromStage: 'Sahaja_Yogi',
    toStage: 'Sahaja_Yogi_Dedicated',
    triggerType: 'stage_transition',
    condition: 'Fully dedicated practice (6+ months, mentoring others)',
    action: 'Invite to leadership activities',
  },
  {
    fromStage: 'Sahaja_Yogi_Dedicated',
    toStage: 'Sahaja_Mahayogi',
    triggerType: 'stage_transition',
    condition: 'Highest level of spiritual dedication achieved',
    action: 'Recognition and community leadership role',
  },
];

// ── Evaluate a seeker's current stage based on their touch-points ──
export function evaluateJourneyStage(touchPoints: TouchPoint[]): JourneyStage {
  if (touchPoints.length === 0) return 'User';
  // Check if any touchpoint includes a phone number pattern
  const hasPhone = touchPoints.some(tp =>
    tp.detail?.match(/0\d{9,10}/) || tp.type === 'ad_message'
  );
  if (hasPhone) return 'Seeker';
  return 'User';
}

// ── Canonical stage normalization for Journey View ──
export function normalizeJourneyStage(leadStage?: string | null): JourneyStage {
  if (!leadStage) return 'User';
  const s = leadStage.toLowerCase().trim().replace(/[- ]+/g, '_');
  if (s === 'user' || s === 'intake') return 'User';
  if (s === 'seeker' || s === 'follower' || s === 'curious_seeker' || s === 'curious') return 'Seeker';
  if (s === 'seeker_public_program' || s === 'public_program_seeker' || s === 'public_program' || s === 'registered' || s === 'attending') return 'Seeker_Public_Program';
  if (s === 'seeker_18_weeks' || s === '18_week_seeker' || s === '18_week_course' || s === 'deep_learner' || s === '18_weeks') return 'Seeker_18_Weeks';
  if (s === 'seed') return 'Seed';
  if (s === 'sahaja_yogi') return 'Sahaja_Yogi';
  if (s === 'sahaja_yogi_dedicated' || s === 'dedicated_yogi' || s === 'dedicated') return 'Sahaja_Yogi_Dedicated';
  if (s === 'sahaja_mahayogi' || s === 'mahayogi') return 'Sahaja_Mahayogi';
  return 'User';
}

// ── Get available transitions from a stage ──
/** Funnel totals: a contact in a later stage also counts toward each earlier step. */
export function cumulativeJourneyCounts(counts: Record<string, number>): Record<string, number> {
  const result: Record<string, number> = {};
  let total = 0;
  for (let i = JOURNEY_STAGES.length - 1; i >= 0; i--) {
    const key = JOURNEY_STAGES[i].key;
    total += counts[key] || 0;
    result[key] = total;
  }
  return result;
}

export function getTransitionsFromStage(stage: JourneyStage): JourneyTransition[] {
  return JOURNEY_TRANSITIONS.filter(t => t.fromStage === stage);
}

// ── Build journey flow data for React Flow ──
export interface FlowNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: {
    label: string;
    description: string;
    stage: JourneyStage;
    seekerCount?: number;
    isActive?: boolean;
  };
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  animated?: boolean;
  style?: Record<string, string | number>;
}

export function buildJourneyFlow(seekerCountByStage: Record<string, number>): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const nodes: FlowNode[] = JOURNEY_STAGES.map((stage, i) => ({
    id: stage.key,
    type: 'journeyNode',
    position: { x: i * 220, y: Math.sin(i * 0.7) * 60 + 100 },
    data: {
      label: stage.label,
      description: stage.description,
      stage: stage.key,
      seekerCount: seekerCountByStage[stage.key] || 0,
      isActive: (seekerCountByStage[stage.key] || 0) > 0,
    },
  }));

  const edges: FlowEdge[] = JOURNEY_TRANSITIONS.map((t, i) => ({
    id: `edge-${i}`,
    source: t.fromStage,
    target: t.toStage,
    label: t.triggerType.replace('_', ' '),
    animated: true,
    style: { stroke: '#6366f1' },
  }));

  // Deduplicate edges with same source-target
  const uniqueEdges = edges.reduce((acc, edge) => {
    const key = `${edge.source}-${edge.target}`;
    if (!acc.has(key)) acc.set(key, edge);
    return acc;
  }, new Map<string, FlowEdge>());

  return { nodes, edges: Array.from(uniqueEdges.values()) };
}
