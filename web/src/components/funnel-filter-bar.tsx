// code:web-component-005:funnel-filter-bar
'use client';

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import {
  DATE_RANGES,
  DEFAULT_FUNNEL_FILTERS,
  FUNNEL_STORAGE_KEY,
  getStoredFilters,
  saveStoredFilters,
  type DateRange,
  type FunnelFilters,
} from '@/lib/funnel-filters';
import { normalizeProgramCity } from '@/lib/programs';

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
  value?: FilterState;
  dateOnly?: boolean;
  onFilterChange: (filters: FilterState) => void;
  availableCities?: string[];
  availablePrograms?: { code: string; city: string }[];
  totalCount?: number;
  filteredCount?: number;
  unitLabel?: string;
  extraControls?: React.ReactNode;
}

export function FunnelFilterBar({
  value,
  dateOnly = false,
  onFilterChange,
  availableCities = DEFAULT_CITIES,
  availablePrograms = [],
  totalCount,
  filteredCount,
  unitLabel = 'mục',
  extraControls,
}: FunnelFilterBarProps) {
  const id = useId();
  const [storedFilters, setFilters] = useState<FilterState>(DEFAULT_FUNNEL_FILTERS);
  const filters = value ?? storedFilters;
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
    if (value) return;
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
  }, [value]);

  const updateFilters = (next: FilterState) => {
    setFilters(next);
    if (!value) saveStoredFilters(next);
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
      const targetBtn = document.getElementById(`${id}-filter-range-${nextRange}`);
      targetBtn?.focus();
    }
  };

  const visiblePrograms = availablePrograms.filter(program => filters.city === 'all' || program.city === normalizeProgramCity(filters.city));
  const isFiltered = filters.city !== 'all' || filters.programCode !== 'all' || filters.dateRange !== 'all';

  if (!isLoaded && !value) {
    return (
      <div className="card funnel-filter-bar" style={{ opacity: 0.6 }}>
        <div className="funnel-filter-controls">
          <span className="funnel-filter-label">📍 Thành phố:</span>
          <div style={{ height: '34px', width: '130px', background: 'var(--bg-card)', borderRadius: '8px', border: '1px solid var(--border-subtle)' }} />
          <span className="funnel-filter-label">📚 Chương trình:</span>
          <div style={{ height: '34px', width: '280px', background: 'var(--bg-card)', borderRadius: '8px', border: '1px solid var(--border-subtle)' }} />
        </div>
      </div>
    );
  }

  return (
    <div className="card funnel-filter-bar" role="search" aria-label="Bộ lọc dữ liệu theo thành phố và khoảng thời gian">
      <div className="funnel-filter-controls">
        {!dateOnly && <div className="funnel-filter-field">
        <label className="funnel-filter-label" htmlFor={`${id}-filter-city`}>📍 Thành phố:</label>
        <select
          id={`${id}-filter-city`}
          value={filters.city}
          onChange={event => updateFilters({ ...filters, city: event.target.value, programCode: 'all' })}
          className="funnel-filter-select"
        >
          <option value="all">Tất cả thành phố</option>
          {cities.filter(city => city !== 'all').map(city => <option key={city} value={city}>{city}</option>)}
        </select>
        </div>}

        {!dateOnly && availablePrograms.length > 0 && <div className="funnel-filter-field">
          <label className="funnel-filter-label" htmlFor={`${id}-filter-program`}>📚 Chương trình:</label>
          <select
            id={`${id}-filter-program`}
            value={filters.programCode}
            onChange={event => updateFilters({ ...filters, programCode: event.target.value })}
            className="funnel-filter-select"
          >
            <option value="all">Tất cả chương trình</option>
            {visiblePrograms.map(program => <option key={program.code} value={program.code}>{program.code}</option>)}
          </select>
        </div>}

        <div className="funnel-range-group" style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <span className="funnel-filter-label" id={`${id}-range-label`}>📅 Khoảng thời gian:</span>
          <div
            id={`${id}-filter-date-range`}
            className="funnel-range-pills"
            role="radiogroup"
            aria-label="Khoảng thời gian: 1d, 3d, 7d, 14d, 30d, 60d, 90d, all"
          >
            {DATE_RANGES.map((range, index) => {
              const isActive = filters.dateRange === range;
              return (
                <button
                  id={`${id}-filter-range-${range}`}
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
