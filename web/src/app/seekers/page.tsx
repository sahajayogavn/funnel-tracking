// code:web-page-002:seekers
import { getAllSeekers } from '@/lib/queries';
import { SeekersTable } from '@/components/seekers-table';

export const dynamic = 'force-dynamic';

export default async function SeekersPage() {
  const seekers = await getAllSeekers();

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">👥 Seekers</h1>
        <p className="page-subtitle">
          {seekers.length} contacts từ DM và bình luận · Bao gồm tất cả giai đoạn hành trình
        </p>
      </div>
      <SeekersTable initialSeekers={seekers} />
    </>
  );
}
