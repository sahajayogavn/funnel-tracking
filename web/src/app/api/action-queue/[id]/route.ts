import { NextRequest, NextResponse } from 'next/server';
import { getDb } from '@/lib/db';

export async function POST(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await request.json().catch(() => ({}));
  const decision = body.decision === 'reject' ? 'rejected' : 'approved';
  const result = getDb().prepare(`
    UPDATE action_queue
    SET status = ?, approval_source = 'webui',
        approved_at = CASE WHEN ? = 'approved' THEN datetime('now') ELSE approved_at END,
        updated_at = datetime('now')
    WHERE id = ? AND status IN ('pending', 'approved')
  `).run(decision, decision, Number(id));
  if (!result.changes) return NextResponse.json({ error: 'Item is no longer actionable' }, { status: 409 });
  return NextResponse.json({ id: Number(id), status: decision });
}

// Drafts that have not started executing may be removed from the approval
// queue.  Executing records deliberately remain immutable so a worker cannot
// lose the action it has already claimed.
export async function DELETE(_request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const result = getDb().prepare(`
    DELETE FROM action_queue
    WHERE id = ? AND status IN ('pending', 'approved')
  `).run(Number(id));
  if (!result.changes) return NextResponse.json({ error: 'Item is no longer removable' }, { status: 409 });
  return NextResponse.json({ id: Number(id), deleted: true });
}
