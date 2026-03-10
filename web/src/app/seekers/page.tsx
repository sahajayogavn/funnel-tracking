// code:web-page-002:seekers
import { getAllSeekers } from '@/lib/queries';
import { SeekersTable } from '@/components/seekers-table';

export const dynamic = 'force-dynamic';

export default function SeekersPage() {
  const seekers = getAllSeekers();

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">👥 Seekers</h1>
        <p className="page-subtitle">
          {seekers.length} seekers tracked across DM and Comment channels
        </p>
      </div>
      <SeekersTable initialSeekers={seekers} />
    </>
  );
}
