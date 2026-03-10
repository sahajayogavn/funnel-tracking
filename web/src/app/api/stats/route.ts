// code:web-api-003:stats-api
import { NextResponse } from 'next/server';
import { getDashboardStats } from '@/lib/queries';

export async function GET() {
  try {
    const stats = getDashboardStats();
    return NextResponse.json(stats);
  } catch (error) {
    console.error('Stats API error:', error);
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
