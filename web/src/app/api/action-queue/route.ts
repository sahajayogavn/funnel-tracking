import { NextRequest, NextResponse } from 'next/server';
import { getActionQueueItems, getSeekerActionQueueItems } from '@/lib/queries';
import { queryOne } from '@/lib/db';

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const targetId = searchParams.get('targetId');
  const targetName = searchParams.get('targetName');

  if (targetId || targetName) {
    const items = await getSeekerActionQueueItems(targetId, targetName);
    return NextResponse.json(items);
  }

  const items = await getActionQueueItems();
  return NextResponse.json(items);
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { queueType, pageId = '1548373332058326', targetType = 'thread', targetId, targetName, actionText, reactionType, payload } = body;

    if (!queueType || (!actionText && !reactionType)) {
      return NextResponse.json({ error: 'Missing required fields' }, { status: 400 });
    }

    const result = await queryOne<{ id: number }>(`
      INSERT INTO action_queue (queue_type, page_id, target_type, target_id, target_name, action_text, reaction_type, payload_json)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      RETURNING id
    `, [
      queueType,
      pageId,
      targetType,
      targetId || null,
      targetName || null,
      actionText || null,
      reactionType || null,
      JSON.stringify(payload || {})
    ]);

    return NextResponse.json({ id: result?.id, status: 'pending' }, { status: 201 });
  } catch (error) {
    console.error('Failed to enqueue action:', error);
    return NextResponse.json({ error: 'Failed to enqueue action' }, { status: 500 });
  }
}
