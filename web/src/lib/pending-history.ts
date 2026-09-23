import type { MessageRow, PendingHistory } from './types';

// Observations are evidence, not conversation turns. Never expose their actor
// or guessed message date through the canonical MessageRow contract.
export function pendingHistoryFromObservation(
  payload: string, observedAt: string, canonical: MessageRow[],
): PendingHistory | null {
  const admitted = new Set(canonical.filter(m => m.sourceId && m.senderConfidence === 'explicit')
    .map(m => m.sourceId));
  try {
    const value = JSON.parse(payload);
    if (!Array.isArray(value.messages)) throw new Error('Invalid observation');
    const issues = Array.isArray(value.issues) ? value.issues : [];
    const reasons = [...new Set<string>(issues.map((i: { reason?: unknown }) =>
      typeof i?.reason === 'string' ? i.reason : 'unverified_evidence'))];
    const messages = value.messages.flatMap((m: Record<string, unknown>) => {
      if (!m || m.kind === 'system_banner' || (typeof m.source_id === 'string' && admitted.has(m.source_id))) return [];
      return [{sourceId: typeof m.source_id === 'string' ? m.source_id : null,
        content: typeof m.body === 'string' ? m.body : typeof m.text === 'string' ? m.text : '(Không đọc được nội dung)',
        reasons: [...new Set<string>(issues.filter((i: { source_id?: unknown }) => i?.source_id === m.source_id)
          .map((i: { reason?: string }) => i.reason || 'unverified_evidence'))],
      }];
    });
    return messages.length ? {observedAt, reasons, messages} : null;
  } catch {
    return {observedAt, reasons: ['observation_decode_failed'], messages: []};
  }
}
