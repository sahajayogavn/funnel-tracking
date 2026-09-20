import { NextRequest, NextResponse } from 'next/server';
import { execute } from '@/lib/db';

export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await request.json().catch(() => ({}));
  if (!Number.isSafeInteger(Number(id)) || Number(id) < 1 || !['approve', 'reject', 'reprocess'].includes(body.decision)) {
    return NextResponse.json({ error: 'A valid action ID and explicit approve/reject/reprocess decision are required' }, { status: 400 });
  }
  const decision = body.decision === 'reject' ? 'rejected' : 'approved';
  // An operator can deliberately restore a rejected, soft-deleted, or
  // delivery-guard Out-date draft. The executor still runs its safety checks
  // before it touches Facebook, so this is an override of queue state—not a
  // bypass of recipient/context validation.
  const allowedStatuses = body.decision === 'reprocess'
    ? "('executed', 'failed') OR (status = 'executing' AND payload_json::jsonb ->> 'delivery_status' = 'drafted')"
    : decision === 'approved'
      ? "'pending', 'approved', 'rejected', 'deleted'"
      : "'pending', 'approved'";
  const approvalSource = body.decision === 'reprocess' ? 'webui_reprocess' : decision === 'approved' ? 'webui_override' : 'webui';
  const statusPredicate = body.decision === 'reprocess'
    ? `(status IN ${allowedStatuses})`
    : `status IN (${allowedStatuses})`;
  const result = await execute(`
    UPDATE action_queue
    SET status = ?, approval_source = ?,
        approved_at = CASE WHEN ? = 'approved' THEN now() ELSE approved_at END,
        error_text = CASE WHEN ? = 'approved' THEN NULL ELSE error_text END,
        updated_at = now()
    WHERE id = ? AND ${statusPredicate}
  `, [decision, approvalSource, decision, decision, Number(id)]);
  if (!result.changes) return NextResponse.json({ error: 'Item is no longer actionable' }, { status: 409 });
  return NextResponse.json({ id: Number(id), status: decision, approvalSource });
}

// Drafts that have not started executing may be removed from the approval
// queue.  Executing records deliberately remain immutable so a worker cannot
// lose the action it has already claimed.
export async function DELETE(_request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const result = await execute(`
    UPDATE action_queue
    SET status = 'deleted', updated_at = now()
    WHERE id = ? AND status IN ('pending', 'approved')
  `, [Number(id)]);
  if (!result.changes) return NextResponse.json({ error: 'Item is no longer removable' }, { status: 409 });
  return NextResponse.json({ id: Number(id), deleted: true });
}
