// code:web-api-001:seekers-api
// Server API routes for seekers data
import { NextResponse } from 'next/server';
import { getAllSeekers, getSeekerActivity, getSeekerClassificationProgress, getSeekerTouchPoints } from '@/lib/queries';

const ACTIVITY_BATCH_MAX = 100;

function parseActivityBatchNames(raw: string | null): string[] | null {
  if (!raw) return [];
  let values: unknown[];
  // Anything that looks like JSON (array or object) must be a JSON array of strings.
  if (/^[[{]/.test(raw.trim())) {
    try {
      const parsed: unknown = JSON.parse(raw);
      // Only a JSON array of strings is valid (e.g. {"a":1} or [1] → 400).
      if (!Array.isArray(parsed) || !parsed.every(value => typeof value === 'string')) return null;
      values = parsed;
    } catch {
      return null;
    }
  } else {
    values = raw.split(',').map(value => value.trim());
  }
  // JSON names are kept verbatim so response keys match the client's seeker.name.
  const names = values
    .filter((value): value is string => typeof value === 'string' && value.trim().length > 0);
  return Array.from(new Set(names));
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const action = searchParams.get('action') || 'list';
  const name = searchParams.get('name');

  try {
    if (action === 'list') {
      const seekers = await getAllSeekers();
      return NextResponse.json({ seekers });
    }

    if (action === 'classification-progress') {
      const threadIds = (searchParams.get('threadIds') || '')
        .split(',')
        .map(value => value.trim())
        .filter(Boolean);
      if (threadIds.length > 500) {
        return NextResponse.json({ error: 'Too many thread IDs' }, { status: 400 });
      }
      const classifications = await getSeekerClassificationProgress(threadIds);
      return NextResponse.json({ classifications }, {
        headers: { 'Cache-Control': 'no-store' },
      });
    }

    // code:web-api-001:seekers-api:activity-batch
    // Batched activity for the rows currently rendered by the lazy Seekers
    // table. `names` is a JSON array (safe for names containing commas) or a
    // comma-separated list; capped at ACTIVITY_BATCH_MAX names per request.
    if (action === 'activity-batch') {
      const names = parseActivityBatchNames(searchParams.get('names'));
      if (names === null) {
        return NextResponse.json({ error: 'Invalid names parameter' }, { status: 400 });
      }
      if (names.length > ACTIVITY_BATCH_MAX) {
        return NextResponse.json({ error: 'Too many names' }, { status: 400 });
      }
      const entries = await Promise.all(
        names.map(async seekerName => [seekerName, await getSeekerActivity(seekerName)] as const),
      );
      return NextResponse.json({ activity: Object.fromEntries(entries) });
    }

    if (action === 'activity' && name) {
      const activity = await getSeekerActivity(name);
      return NextResponse.json({ activity });
    }

    if (action === 'touchpoints' && name) {
      const touchPoints = await getSeekerTouchPoints(name);
      return NextResponse.json({ touchPoints });
    }

    return NextResponse.json({ error: 'Invalid action' }, { status: 400 });
  } catch (error) {
    console.error('Seekers API error:', error);
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
