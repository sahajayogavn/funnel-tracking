// code:web-api-001:seeker-detail-api
import { NextResponse } from 'next/server';
import { getSeekerById } from '@/lib/queries';

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const detail = getSeekerById(decodeURIComponent(id));
    if (!detail) {
      return NextResponse.json({ error: 'Seeker not found' }, { status: 404 });
    }
    return NextResponse.json(detail);
  } catch (error) {
    console.error('Seeker detail API error:', error);
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
