// code:web-api-002:graph-api
import { NextRequest, NextResponse } from 'next/server';
import { getGraphData } from '@/lib/queries';

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const city = searchParams.get('city') || undefined;
    const startDate = searchParams.get('startDate') || undefined;
    const endDate = searchParams.get('endDate') || undefined;
    const graphData = await getGraphData({ city, startDate, endDate });
    return NextResponse.json(graphData);
  } catch (error) {
    console.error('Graph API error:', error);
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
