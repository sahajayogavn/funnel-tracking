import { query, queryOne } from './db';
import { normalizeProgramCity } from './programs';

export async function statsRange(value: string | undefined, now = new Date()) {
  let days = [1, 3, 7, 14, 30, 60, 90].includes(Number(value)) ? Number(value) : 7;
  const today = new Date(now.getTime() + 7 * 3600000).toISOString().slice(0, 10);
  if (value === 'all') {
    const row = await queryOne<{ day: string | null }>(`SELECT MIN(day)::text AS day FROM (
      SELECT message_at::date AS day FROM messages UNION ALL SELECT timestamp::date AS day FROM messages
      UNION ALL SELECT started_at::date AS day FROM llm_calls UNION ALL SELECT fetched_at::date AS day FROM fetch_log
      UNION ALL SELECT approved_at::date AS day FROM action_queue UNION ALL SELECT executed_at::date AS day FROM action_queue
    ) dates WHERE day <= ?::date`, [today]);
    days = Math.max(1, Math.round((Date.parse(today) - Date.parse(row?.day || today)) / 86400000) + 1);
  }
  const start = new Date(`${today}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - days + 1);
  const end = new Date(`${today}T00:00:00Z`);
  end.setUTCDate(end.getUTCDate() + 1);
  return { days, from: start.toISOString().slice(0, 10), to: end.toISOString().slice(0, 10), today };
}
export type Ranking = { label: string; count: number };
export async function getPeriodStats(range: Awaited<ReturnType<typeof statsRange>>) {
  const bounds = [range.from, range.to];
  // message_at is the parsed local Messenger wall time; operational logs are UTC.
  const during = (field: string) => `${field}::date >= ?::date AND ${field}::date < ?::date`;
  const count = async (sql: string) => Number((await queryOne<{ n: number }>(sql, bounds))?.n || 0);
    const inbound = `FROM messages m LEFT JOIN users u ON u.thread_id = m.thread_id
      WHERE m.sender = 'Customer' AND m.kind = 'message' AND ${during('m.message_at')}`;
    const ranking = (field: string) => query<Ranking>(`SELECT COALESCE(NULLIF(TRIM(${field}), ''), 'Chưa xác định') label, COUNT(*)::int count ${inbound} GROUP BY label ORDER BY count DESC, label`, bounds);
    const cityMap = new Map<string, number>();
    for (const row of await ranking('u.city')) {
      const label = row.label === 'Unknown' ? 'Chưa xác định' : normalizeProgramCity(row.label)!;
      cityMap.set(label, (cityMap.get(label) || 0) + row.count);
    }
    const cities = [...cityMap].map(([label, count]) => ({ label, count })).sort((a, b) => b.count - a.count);
    const daily = await query<{ day: string; count: number }>(`SELECT m.message_at::date::text AS day, COUNT(*)::int AS count ${inbound} GROUP BY m.message_at::date`, bounds);
    const series = Array.from({ length: range.days }, (_, i) => {
      const date = new Date(`${range.from}T00:00:00Z`);
      date.setUTCDate(date.getUTCDate() + i);
      const day = date.toISOString().slice(0, 10);
      return { day, count: daily.find(row => row.day === day)?.count || 0 };
    });
    const models = await query<{ label: string; calls: number; input: number; output: number; missing: number }>(`SELECT COALESCE(model, 'Không rõ model') label, COUNT(*)::int calls,
      COALESCE(SUM(tokens_in), 0)::double precision input, COALESCE(SUM(tokens_out), 0)::double precision output,
      SUM(CASE WHEN tokens_in IS NULL OR tokens_out IS NULL THEN 1 ELSE 0 END)::int missing
      FROM llm_calls WHERE COALESCE(model, '') <> 'deterministic' AND ${during('started_at')}
      GROUP BY model ORDER BY COALESCE(SUM(tokens_in), 0) + COALESCE(SUM(tokens_out), 0) DESC`, bounds);
    return {
      cities, programs: await ranking('u.program_code'), series, models,
      incoming: await count(`SELECT COUNT(*)::int n ${inbound}`),
      conversations: await count(`SELECT COUNT(DISTINCT m.thread_id)::int n ${inbound}`),
      fetched: await count(`SELECT COUNT(*)::int n FROM messages WHERE kind = 'message' AND ${during('timestamp')}`),
      totalFetched: Number((await queryOne<{ n: number }>("SELECT COUNT(*)::int n FROM messages WHERE kind = 'message'"))?.n || 0),
      fetchRuns: await count(`SELECT COUNT(*)::int n FROM fetch_log WHERE ${during('fetched_at')}`),
      masRuns: await count(`SELECT COUNT(*)::int n FROM (SELECT trace_id, MIN(started_at) started_at FROM llm_calls WHERE route_group = 'MAS' AND COALESCE(model, '') <> 'deterministic' GROUP BY trace_id) traces WHERE ${during('started_at')}`),
      approved: await count(`SELECT COUNT(*)::int n FROM action_queue WHERE ${during('approved_at')}`),
      executed: await count(`SELECT COUNT(*)::int n FROM action_queue WHERE ${during('executed_at')}`),
      undated: Number((await queryOne<{ n: number }>("SELECT COUNT(*)::int n FROM messages WHERE sender = 'Customer' AND kind = 'message' AND message_at IS NULL"))?.n || 0),
    };
}
