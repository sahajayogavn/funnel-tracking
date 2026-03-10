// code:web-api-001:seekers-api
// Server API routes for seekers data
import { NextResponse } from 'next/server';
import { getAllSeekers, getSeekerActivity, getSeekerTouchPoints } from '@/lib/queries';

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const action = searchParams.get('action') || 'list';
  const name = searchParams.get('name');

  try {
    if (action === 'list') {
      const seekers = getAllSeekers();
      return NextResponse.json({ seekers });
    }

    if (action === 'activity' && name) {
      const activity = getSeekerActivity(name);
      return NextResponse.json({ activity });
    }

    if (action === 'touchpoints' && name) {
      const touchPoints = getSeekerTouchPoints(name);
      return NextResponse.json({ touchPoints });
    }

    return NextResponse.json({ error: 'Invalid action' }, { status: 400 });
  } catch (error) {
    console.error('Seekers API error:', error);
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
