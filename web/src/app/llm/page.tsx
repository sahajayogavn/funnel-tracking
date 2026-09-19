import { getDb } from '@/lib/db';
import { LlmCall } from '@/lib/types';
import LlmObservabilityClient from './llm-client';

export const dynamic = 'force-dynamic';

// The MAS worker itself is terminated after four minutes. Leave one minute of
// grace for the worker to persist its final trace event, then reconcile an
// orphaned `running` row when this observability page is opened.
const MAS_DISPLAY_TIMEOUT_MS = Number(process.env.MAS_DISPLAY_TIMEOUT_MS || 300_000);

function expireStaleMasCalls() {
  const timeoutSeconds = Math.floor(MAS_DISPLAY_TIMEOUT_MS / 1000);
  const timeoutDays = MAS_DISPLAY_TIMEOUT_MS / 86_400_000;
  const message = `MAS timed out after ${Math.floor(timeoutSeconds / 60)} minutes without a completion event.`;
  return getDb().prepare(`
    UPDATE llm_calls
    SET status = 'timeout',
        finished_at = COALESCE(finished_at, datetime(started_at, '+' || ? || ' seconds')),
        duration_ms = COALESCE(duration_ms, ?),
        error = COALESCE(NULLIF(error, ''), ?)
    WHERE status = 'running'
      AND route_group = 'MAS'
      AND julianday('now') - julianday(started_at) >= ?
  `).run(timeoutSeconds, MAS_DISPLAY_TIMEOUT_MS, message, timeoutDays).changes;
}

type SearchParams = { [key: string]: string | string[] | undefined };

function param(searchParams: SearchParams, key: string): string {
  const value = searchParams[key];
  return Array.isArray(value) ? value[0] || '' : value || '';
}

export default async function LlmPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const resolvedSearchParams = await searchParams;
  const date = param(resolvedSearchParams, 'date');
  const from = param(resolvedSearchParams, 'from');
  const to = param(resolvedSearchParams, 'to');
  const group = param(resolvedSearchParams, 'group');
  const route = param(resolvedSearchParams, 'route');
  const trigger = param(resolvedSearchParams, 'trigger');
  const agent = param(resolvedSearchParams, 'agent');
  const status = param(resolvedSearchParams, 'status');
  const subject = param(resolvedSearchParams, 'subject');
  const search = param(resolvedSearchParams, 'q') || param(resolvedSearchParams, 'search');
  const trace = param(resolvedSearchParams, 'trace');

  try {
    expireStaleMasCalls();
  } catch (error) {
    // Observability must stay readable even if the shared SQLite worker holds
    // a write lock briefly; the next refresh will reconcile the stale call.
    console.warn('Could not reconcile stale MAS traces:', error);
  }

  let query = 'SELECT * FROM llm_calls WHERE 1=1';
  const params: (string | number)[] = [];
  if (date) { query += ' AND date(started_at) = ?'; params.push(date); }
  else if (from && to) { query += ' AND date(started_at) BETWEEN ? AND ?'; params.push(from, to); }
  else if (from) { query += ' AND date(started_at) >= ?'; params.push(from); }
  else if (to) { query += ' AND date(started_at) <= ?'; params.push(to); }
  if (group) { query += ' AND route_group = ?'; params.push(group); }
  if (route) { query += ' AND route = ?'; params.push(route); }
  if (trigger) { query += ' AND trigger = ?'; params.push(trigger); }
  if (agent) { query += ' AND agent_name LIKE ?'; params.push(`%${agent}%`); }
  if (status) { query += ' AND status = ?'; params.push(status); }
  if (subject) { query += ' AND (subject_id LIKE ? OR subject_label LIKE ?)'; params.push(`%${subject}%`, `%${subject}%`); }
  if (search) { query += ' AND (system_prompt LIKE ? OR messages_json LIKE ? OR state_json LIKE ? OR response_text LIKE ? OR error LIKE ?)'; params.push(`%${search}%`, `%${search}%`, `%${search}%`, `%${search}%`, `%${search}%`); }
  if (trace) { query += ' AND trace_id = ?'; params.push(trace); }
  query += ' ORDER BY started_at DESC, id DESC LIMIT 500';

  let calls: LlmCall[] = [];
  try { calls = getDb().prepare(query).all(...params) as LlmCall[]; } catch { calls = []; }

  const durations = calls.map((call) => call.duration_ms).filter((duration): duration is number => duration !== null).sort((a, b) => a - b);
  const percentile = (ratio: number) => durations.length ? durations[Math.min(durations.length - 1, Math.floor(durations.length * ratio))] : 0;
  const totalCalls = calls.length;
  const attentionCount = calls.filter((call) => call.status === 'empty' || call.status === 'sanitized_empty').length;
  const errorCount = calls.filter((call) => call.status === 'error' || call.status === 'timeout').length;
  const detectCount = calls.filter((call) => call.route.includes('detect')).length;
  const verifyCount = calls.filter((call) => call.route.includes('verify')).length;
  const stats = {
    totalCalls,
    p50: percentile(0.5),
    p95: percentile(0.95),
    tokensIn: calls.reduce((sum, call) => sum + (call.tokens_in || 0), 0),
    tokensOut: calls.reduce((sum, call) => sum + (call.tokens_out || 0), 0),
    percentAttention: totalCalls ? (attentionCount / totalCalls) * 100 : 0,
    percentError: totalCalls ? (errorCount / totalCalls) * 100 : 0,
    verifyVsDetect: detectCount ? verifyCount / detectCount : 0,
  };

  const routeMap = new Map<string, { calls: number; errors: number; attention: number; duration: number }>();
  calls.forEach((call) => {
    const current = routeMap.get(call.route) || { calls: 0, errors: 0, attention: 0, duration: 0 };
    current.calls += 1;
    current.duration += call.duration_ms || 0;
    if (call.status === 'error' || call.status === 'timeout') current.errors += 1;
    if (call.status === 'empty' || call.status === 'sanitized_empty') current.attention += 1;
    routeMap.set(call.route, current);
  });
  const routeBreakdown = Array.from(routeMap.entries()).map(([routeName, value]) => ({ route: routeName, ...value }));

  const traceMap = new Map<string, LlmCall[]>();
  calls.forEach((call) => { const traceCalls = traceMap.get(call.trace_id) || []; traceCalls.push(call); traceMap.set(call.trace_id, traceCalls); });
  const traces = Array.from(traceMap.entries()).map(([traceId, traceCalls]) => ({ traceId, calls: traceCalls.sort((a, b) => a.seq_in_trace - b.seq_in_trace) }));

  return <div className="llm-page-shell"><LlmObservabilityClient traces={traces} stats={stats} routeBreakdown={routeBreakdown} filters={{ date, from, to, group, route, trigger, agent, status, subject, search, trace }} /></div>;
}
