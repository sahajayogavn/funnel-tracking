// code:web-api-002:graph-api
import { NextResponse } from 'next/server';
import { getGraphData } from '@/lib/queries';

export async function GET() {
  try {
    const graphData = getGraphData();
    return NextResponse.json(graphData);
  } catch (error) {
    console.error('Graph API error:', error);
    return NextResponse.json({ error: 'Internal server error' }, { status: 500 });
  }
}
