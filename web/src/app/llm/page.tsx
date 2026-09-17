import { getDb } from '@/lib/db';
import { LlmCall } from '@/lib/types';
import LlmObservabilityClient from './llm-client';

export const dynamic = 'force-dynamic';

export default function LlmPage({
  searchParams,
}: {
  searchParams: { [key: string]: string | string[] | undefined };
}) {
  const date = searchParams.date as string || '';
  const group = searchParams.group as string || '';
  const routeParam = searchParams.route as string || '';
  const trigger = searchParams.trigger as string || '';
  const agent = searchParams.agent as string || '';
  const status = searchParams.status as string || '';
  const subject = searchParams.subject as string || '';
  const search = searchParams.search as string || '';

  let query = 'SELECT * FROM llm_calls WHERE 1=1';
  const params: any[] = [];

  if (date) {
    query += ' AND date(started_at) = ?';
    params.push(date);
  }
  if (group) {
    query += ' AND route_group = ?';
    params.push(group);
  }
  if (routeParam) {
    query += ' AND route = ?';
    params.push(routeParam);
  }
  if (trigger) {
    query += ' AND trigger = ?';
    params.push(trigger);
  }
  if (agent) {
    query += ' AND agent_name = ?';
    params.push(agent);
  }
  if (status) {
    query += ' AND status = ?';
    params.push(status);
  }
  if (subject) {
    query += ' AND subject_id = ?';
    params.push(subject);
  }
  if (search) {
    query += ' AND (system_prompt LIKE ? OR messages_json LIKE ? OR response_text LIKE ? OR error LIKE ?)';
    params.push(`%${search}%`, `%${search}%`, `%${search}%`, `%${search}%`);
  }

  query += ' ORDER BY started_at DESC LIMIT 500';

  const calls = getDb().prepare(query).all(...params) as LlmCall[];

  // Calculate stats
  // p50/p95 latency, % sanitized/empty, verify vs detect rate
  
  const durations = calls.map(c => c.duration_ms).filter(d => d !== null) as number[];
  durations.sort((a, b) => a - b);
  
  const p50 = durations.length > 0 ? durations[Math.floor(durations.length * 0.5)] : 0;
  const p95 = durations.length > 0 ? durations[Math.floor(durations.length * 0.95)] : 0;

  const totalCalls = calls.length;
  const sanitizedCount = calls.filter(c => c.status === 'sanitized' || c.sanitized_text).length;
  const emptyCount = calls.filter(c => !c.response_text && !c.response_json).length;
  
  const percentSanitized = totalCalls > 0 ? (sanitizedCount / totalCalls) * 100 : 0;
  const percentEmpty = totalCalls > 0 ? (emptyCount / totalCalls) * 100 : 0;

  // "verify vs detect rate": let's say route_group 'analytics' or routes containing 'verify'/'detect'
  const verifyCount = calls.filter(c => c.route.includes('verify')).length;
  const detectCount = calls.filter(c => c.route.includes('detect')).length;
  const verifyVsDetect = detectCount > 0 ? verifyCount / detectCount : 0;

  const stats = {
    p50,
    p95,
    percentSanitized,
    percentEmpty,
    verifyCount,
    detectCount,
    verifyVsDetect
  };

  // Group by trace_id
  const traceMap = new Map<string, LlmCall[]>();
  for (const call of calls) {
    if (!traceMap.has(call.trace_id)) {
      traceMap.set(call.trace_id, []);
    }
    traceMap.get(call.trace_id)!.push(call);
  }

  // Sort calls within each trace by seq_in_trace
  const traces: { traceId: string, calls: LlmCall[] }[] = [];
  for (const [traceId, traceCalls] of traceMap.entries()) {
    traceCalls.sort((a, b) => a.seq_in_trace - b.seq_in_trace);
    traces.push({ traceId, calls: traceCalls });
  }

  return (
    <div className="p-6 max-w-[1600px] mx-auto">
      <h1 className="text-2xl font-bold mb-4">LLM Observability (Phase 3)</h1>
      <LlmObservabilityClient 
        traces={traces} 
        stats={stats} 
        filters={{ date, group, route: routeParam, trigger, agent, status, subject, search }}
      />
    </div>
  );
}
