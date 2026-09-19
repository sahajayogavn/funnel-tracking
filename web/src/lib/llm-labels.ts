export type LlmRouteInfo = {
  label: string;
  shortLabel: string;
  useCase: string;
  group: 'MAS' | 'LLM';
  accent: 'indigo' | 'amber' | 'pink' | 'cyan' | 'emerald' | 'rose' | 'purple';
};

const ROUTE_LABELS: Record<string, LlmRouteInfo> = {
  propose: { label: 'MAS · Auto-reply', shortLabel: 'Auto-reply', useCase: 'UC-05 · AI draft for a new DM', group: 'MAS', accent: 'indigo' },
  reply: { label: 'MAS · Auto-reply', shortLabel: 'Auto-reply', useCase: 'UC-05 · AI draft for a new DM', group: 'MAS', accent: 'indigo' },
  compose_reply: { label: 'MAS · Auto-reply', shortLabel: 'Auto-reply', useCase: 'UC-05 · AI draft for a new DM', group: 'MAS', accent: 'indigo' },
  inbox_pipeline: { label: 'MAS · Auto-reply', shortLabel: 'Auto-reply', useCase: 'UC-05 · AI draft for a new DM', group: 'MAS', accent: 'indigo' },
  responder: { label: 'MAS · Auto-reply', shortLabel: 'Auto-reply', useCase: 'UC-05 · AI draft for a new DM', group: 'MAS', accent: 'indigo' },
  warmup: { label: 'MAS · Warm-up', shortLabel: 'Warm-up', useCase: 'UC-07 · Re-engage a quiet seeker', group: 'MAS', accent: 'amber' },
  warm_up: { label: 'MAS · Warm-up', shortLabel: 'Warm-up', useCase: 'UC-07 · Re-engage a quiet seeker', group: 'MAS', accent: 'amber' },
  event: { label: 'MAS · Event invite', shortLabel: 'Event invite', useCase: 'UC-08 · Invite by city and interest', group: 'MAS', accent: 'pink' },
  event_invite: { label: 'MAS · Event invite', shortLabel: 'Event invite', useCase: 'UC-08 · Invite by city and interest', group: 'MAS', accent: 'pink' },
  react: { label: 'MAS · Reaction', shortLabel: 'Reaction', useCase: 'UC-06 · Human approval of an outbound proposal', group: 'MAS', accent: 'emerald' },
  reaction_pipeline: { label: 'MAS · Reaction', shortLabel: 'Reaction', useCase: 'UC-06 · Human approval of an outbound proposal', group: 'MAS', accent: 'emerald' },
  reactor: { label: 'MAS · Reaction', shortLabel: 'Reaction', useCase: 'UC-06 · Human approval of an outbound proposal', group: 'MAS', accent: 'emerald' },
  stage_gate: { label: 'MAS · Stage gate', shortLabel: 'Stage gate', useCase: 'UC-09 · Review seeker journey stage', group: 'MAS', accent: 'rose' },
  recommend: { label: 'MAS · Recommendation', shortLabel: 'Recommendation', useCase: 'UC-06 · Review an outbound proposal', group: 'MAS', accent: 'indigo' },
  classify_detect: { label: 'LLM · City / program detect', shortLabel: 'City detect', useCase: 'UC-02 · Detect city and event signals', group: 'LLM', accent: 'cyan' },
  classify_verify: { label: 'LLM · City / program verify', shortLabel: 'City verify', useCase: 'UC-02 · Verify city and event signals', group: 'LLM', accent: 'cyan' },
  extract_events: { label: 'LLM · City / event detect', shortLabel: 'City detect', useCase: 'UC-02 · Detect city and event signals', group: 'LLM', accent: 'cyan' },
  event_pipeline: { label: 'LLM · City / event detect', shortLabel: 'City detect', useCase: 'UC-02 · Detect city and event signals', group: 'LLM', accent: 'cyan' },
  classify_message: { label: 'MAS · Message classify', shortLabel: 'Classify', useCase: 'UC-05 · Understand the seeker message', group: 'MAS', accent: 'purple' },
  classifier: { label: 'MAS · Message classify', shortLabel: 'Classify', useCase: 'UC-05 · Understand the seeker message', group: 'MAS', accent: 'purple' },
};

export function getLlmRouteInfo(route?: string | null, trigger?: string | null): LlmRouteInfo {
  if (route && ROUTE_LABELS[route]) return ROUTE_LABELS[route];
  const fallback = route || trigger || 'Unknown route';
  return { label: fallback, shortLabel: fallback, useCase: 'Operational LLM call', group: 'MAS', accent: 'indigo' };
}

export function getLlmStatusLabel(status?: string | null): string {
  if (status === 'ok' || status === 'success') return 'OK';
  if (status === 'sanitized_empty') return 'Sanitized empty';
  if (status === 'empty') return 'Empty';
  if (status === 'timeout') return 'Timeout';
  if (status === 'error') return 'Error';
  if (status === 'running') return 'Running';
  return status || 'Unknown';
}

export function getLlmStatusTone(status?: string | null): 'ok' | 'attention' | 'error' | 'running' {
  if (status === 'ok' || status === 'success') return 'ok';
  if (status === 'error' || status === 'timeout') return 'error';
  if (status === 'running') return 'running';
  return 'attention';
}
