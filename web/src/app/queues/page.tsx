import { getActionQueueItems } from '@/lib/queries';
import ActionQueues from '@/components/action-queues';

export const dynamic = 'force-dynamic';

export default function QueuesPage() {
  return <>
    <div className="page-header"><h1 className="page-title">✅ Human approval queues</h1><p className="page-subtitle">Duyệt từng outbound action do MAS đề xuất.</p></div>
    <ActionQueues initialItems={getActionQueueItems()} />
  </>;
}
