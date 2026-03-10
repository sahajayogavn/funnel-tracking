// code:web-page-002:seeker-detail
import { getSeekerById } from '@/lib/queries';
import { SeekerDetailView } from '@/components/seeker-detail';
import Link from 'next/link';

export const dynamic = 'force-dynamic';

export default async function SeekerDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const detail = getSeekerById(decodeURIComponent(id));

  if (!detail) {
    return (
      <>
        <div className="page-header">
          <h1 className="page-title">Seeker Not Found</h1>
          <p className="page-subtitle">
            <Link href="/seekers" style={{ color: 'var(--accent-indigo)' }}>← Back to Seekers</Link>
          </p>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="page-header">
        <p className="page-subtitle" style={{ marginBottom: '4px' }}>
          <Link href="/seekers" style={{ color: 'var(--accent-indigo)', textDecoration: 'none' }}>← Back to Seekers</Link>
        </p>
        <h1 className="page-title">👤 {detail.seeker.name}</h1>
      </div>
      <SeekerDetailView detail={detail} />
    </>
  );
}
