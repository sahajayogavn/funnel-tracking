'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { FunnelFilterBar } from '@/components/funnel-filter-bar';
import { type DateRange } from '@/lib/funnel-filters';

export function StatsFilters({ dateRange }: { dateRange: DateRange }) {
  const router = useRouter();
  const params = useSearchParams();
  return <FunnelFilterBar
    dateOnly
    value={{ city: 'all', programCode: 'all', dateRange }}
    onFilterChange={filters => {
      const next = new URLSearchParams(params.toString());
      next.set('days', filters.dateRange.replace('d', ''));
      router.replace(`/stats?${next}`, { scroll: false });
    }}
  />;
}
