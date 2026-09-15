// code:web-component-005:funnel-filter-bar
'use client';

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import {
  DATE_RANGES,
  DEFAULT_FUNNEL_FILTERS,
  FUNNEL_STORAGE_KEY,
  getStoredFilters,
  saveStoredFilters,
  type DateRange,
  type FunnelFilters,
} from '@/lib/funnel-filters';

export type FilterState = FunnelFilters;

const DEFAULT_CITIES = [
  'all',
  'Hà Nội',
  'TP. Hồ Chí Minh',
  'Đà Nẵng',
  'Bắc Ninh',
  'Hải Phòng',
  'Hưng Yên',
  'Nghệ An',
  'Huế',
  'Hội An',
  'Online',
  'Unknown',
];

interface FunnelFilterBarProps {
  onFilterChange: (filters: FilterState) => void;
  availableCities?: string[];
  totalCount?: number;
  filteredCount?: number;
  unitLabel?: string;
  extraControls?: React.ReactNode;
}

export function FunnelFilterBar({
  onFilterChange,
  availableCities = DEFAULT_CITIES,
  totalCount,
  filteredCount,
  unitLabel = 'mục',
  extraControls,
}: FunnelFilterBarProps) {
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FUNNEL_FILTERS);
  const [isLoaded, setIsLoaded] = useState(false);
  const onFilterChangeRef = useRef(onFilterChange);

  useEffect(() => {
    onFilterChangeRef.current = onFilterChange;
  }, [onFilterChange]);

  const cities = useMemo(() => {
    const combined = new Set([...DEFAULT_CITIES, ...availableCities]);
    if (filters.city && filters.city !== 'all') {
      combined.add(filters.city);
    }
    return Array.from(combined);
  }, [availableCities, filters.city]);

  useEffect(() => {
    const syncFromStorage = () => {
      try {
        const restored = getStoredFilters();
        setFilters(restored);
        onFilterChangeRef.current(restored);
      } catch {
        onFilterChangeRef.current(DEFAULT_FUNNEL_FILTERS);
      }
      setIsLoaded(true);
    };

    syncFromStorage();

    const handleStorage = (e: StorageEvent) => {
      if (e.key === FUNNEL_STORAGE_KEY) {
        syncFromStorage();
      }
    };
    const handleLocalSync = () => syncFromStorage();

    window.addEventListener('storage', handleStorage);
    window.addEventListener('sahaja_funnel_filter_changed', handleLocalSync);

    return () => {
      window.removeEventListener('storage', handleStorage);
      window.removeEventListener('sahaja_funnel_filter_changed', handleLocalSync);
    };
  }, []);

  const updateFilters = (next: FilterState) => {
    setFilters(next);
    saveStoredFilters(next);
    onFilterChangeRef.current(next);
  };

  const handleRadioKeyDown = (e: KeyboardEvent<HTMLButtonElement>, currentIndex: number) => {
    let nextIndex: number | null = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      nextIndex = (currentIndex + 1) % DATE_RANGES.length;
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      nextIndex = (currentIndex - 1 + DATE_RANGES.length) % DATE_RANGES.length;
    }
    if (nextIndex !== null) {
      const nextRange = DATE_RANGES[nextIndex] as DateRange;
      updateFilters({ ...filters, dateRange: nextRange });
      const targetBtn = document.getElementById(`filter-range-${nextRange}`);
      targetBtn?.focus();
    }
  };

  const isFiltered = filters.city !== 'all' || filters.dateRange !== 'all';

  if (!isLoaded) {
    return (
      <div className="card funnel-filter-bar" style={{ opacity: 0.6 }}>
        <div className="funnel-filter-controls">
          <span className="funnel-filter-label">📍 Thành phố:</span>
          <div style={{ height: '34px', width: '130px', background: 'var(--bg-card)', borderRadius: '8px', border: '1px solid var(--border-subtle)' }} />
          <span className="funnel-filter-label">📅 Khoảng thời gian:</span>
          <div style={{ height: '34px', width: '280px', background: 'var(--bg-card)', borderRadius: '8px', border: '1px solid var(--border-subtle)' }} />
        </div>
      </div>
    );
  }

  return (
    <div className="card funnel-filter-bar" role="search" aria-label="Bộ lọc dữ liệu theo thành phố và khoảng thời gian">
      <div className="funnel-filter-controls">
        <label className="funnel-filter-label" htmlFor="filter-city">📍 Thành phố:</label>
        <select
          id="filter-city"
          value={filters.city}
          onChange={event => updateFilters({ ...filters, city: event.target.value })}
          className="funnel-filter-select"
        >
          <option value="all">Tất cả thành phố</option>
          {cities.filter(city => city !== 'all').map(city => <option key={city} value={city}>{city}</option>)}
        </select>

        <div className="funnel-range-group" style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <label className="funnel-filter-label" htmlFor="filter-date-range">📅 Khoảng thời gian:</label>
          <div
            id="filter-date-range"
            className="funnel-range-pills"
            role="radiogroup"
            aria-label="Khoảng thời gian: 1d, 3d, 7d, 14d, 30d, 60d, 90d, all"
          >
            {DATE_RANGES.map((range, index) => {
              const isActive = filters.dateRange === range;
              return (
                <button
                  id={`filter-range-${range}`}
                  key={range}
                  type="button"
                  role="radio"
                  aria-checked={isActive}
                  tabIndex={isActive ? 0 : -1}
                  className={`funnel-range-pill ${isActive ? 'active' : ''}`}
                  onClick={() => updateFilters({ ...filters, dateRange: range })}
                  onKeyDown={e => handleRadioKeyDown(e, index)}
                  title={`Khoảng thời gian: ${range}`}
                >
                  {range}
                </button>
              );
            })}
          </div>
        </div>

        {isFiltered && (
          <button
            type="button"
            onClick={() => updateFilters(DEFAULT_FUNNEL_FILTERS)}
            className="funnel-filter-reset"
            title="Đặt lại bộ lọc về mặc định"
          >
            ✕ Đặt lại
          </button>
        )}
      </div>
      <div className="funnel-filter-meta">
        {totalCount !== undefined && filteredCount !== undefined && (
          <span>Hiển thị <strong>{filteredCount}</strong> / {totalCount} {unitLabel}</span>
        )}
        {extraControls}
      </div>
    </div>
  );
}


