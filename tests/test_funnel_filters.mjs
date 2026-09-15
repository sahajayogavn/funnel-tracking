// code:test-web-006:funnel-filters-test
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, '..');

console.log('--- Running Funnel Filters & Queues Static & Contract Verification ---');

// 1. Verify funnel-filters.ts
const funnelFiltersPath = path.join(rootDir, 'web/src/lib/funnel-filters.ts');
const funnelFiltersContent = fs.readFileSync(funnelFiltersPath, 'utf8');

// Check DATE_RANGES
const expectedRanges = "['1d', '3d', '7d', '14d', '30d', '60d', '90d', 'all']";
assert(
  funnelFiltersContent.includes(expectedRanges),
  `DATE_RANGES must match exactly ${expectedRanges}`
);
console.log('✓ DATE_RANGES matches exactly 1d, 3d, 7d, 14d, 30d, 60d, 90d, all');

// Check FUNNEL_STORAGE_KEY
assert(
  funnelFiltersContent.includes("export const FUNNEL_STORAGE_KEY = 'sahaja_funnel_filters'"),
  'FUNNEL_STORAGE_KEY must be exported as sahaja_funnel_filters'
);
console.log('✓ FUNNEL_STORAGE_KEY is exported as sahaja_funnel_filters');

// Check DEFAULT_FUNNEL_FILTERS
assert(
  funnelFiltersContent.includes("export const DEFAULT_FUNNEL_FILTERS: FunnelFilters = { city: 'all', dateRange: 'all' }"),
  'DEFAULT_FUNNEL_FILTERS must default to city: all, dateRange: all'
);
console.log('✓ DEFAULT_FUNNEL_FILTERS is city: all, dateRange: all');

// 2. Verify funnel-filter-bar.tsx
const filterBarPath = path.join(rootDir, 'web/src/components/funnel-filter-bar.tsx');
const filterBarContent = fs.readFileSync(filterBarPath, 'utf8');

// Check that NO manual date input exists
assert(!filterBarContent.includes('<input type="date"'), 'Must not have manual date input');
assert(!filterBarContent.includes('type="date"'), 'Must not have type="date" input');
console.log('✓ funnel-filter-bar.tsx has NO manual date inputs');

// Check radio role & radio group
assert(filterBarContent.includes('role="radiogroup"'), 'Must have role="radiogroup"');
assert(filterBarContent.includes('role="radio"'), 'Buttons must have role="radio"');
assert(filterBarContent.includes('aria-checked={isActive}'), 'Must have aria-checked');
console.log('✓ Radio range group and radio buttons properly configured with aria attributes');

// Check persistence integration
assert(filterBarContent.includes('getStoredFilters()'), 'Must load from getStoredFilters()');
assert(filterBarContent.includes('saveStoredFilters(next)'), 'Must persist with saveStoredFilters(next)');
assert(filterBarContent.includes('FUNNEL_STORAGE_KEY'), 'Must use FUNNEL_STORAGE_KEY');
console.log('✓ funnel-filter-bar.tsx uses getStoredFilters and saveStoredFilters');

// 3. Verify action-queues.tsx
const actionQueuesPath = path.join(rootDir, 'web/src/components/action-queues.tsx');
const actionQueuesContent = fs.readFileSync(actionQueuesPath, 'utf8');

// Check all 5 action buttons exist
const expectedActions = [
  'recommendation-action--all',
  'recommendation-action--reply',
  'recommendation-action--comment',
  'recommendation-action--warmup',
  'recommendation-action--event',
];
for (const actionClass of expectedActions) {
  assert(actionQueuesContent.includes(actionClass), `action-queues.tsx must contain ${actionClass}`);
}
console.log('✓ action-queues.tsx has all 5 recommendation action buttons with variant classes');

// 4. Verify globals.css
const globalsCssPath = path.join(rootDir, 'web/src/app/globals.css');
const globalsCssContent = fs.readFileSync(globalsCssPath, 'utf8');

assert(globalsCssContent.includes('.recommendation-actions {'), 'Must define .recommendation-actions');
assert(globalsCssContent.includes('.recommendation-action {'), 'Must define .recommendation-action');
assert(globalsCssContent.includes('min-width: max-content;'), '.recommendation-action must have min-width: max-content on desktop to prevent label clipping');
assert(globalsCssContent.includes('@media (max-width: 860px)'), 'Must have responsive breakpoint for tablets');
assert(globalsCssContent.includes('@media (max-width: 520px)'), 'Must have responsive breakpoint for mobile');
console.log('✓ globals.css defines compact desktop line and responsive small screen styles');

// 5. Verify network-graph.tsx
const networkGraphPath = path.join(rootDir, 'web/src/components/network-graph.tsx');
const networkGraphContent = fs.readFileSync(networkGraphPath, 'utf8');

assert(networkGraphContent.includes('<FunnelFilterBar'), 'NetworkGraph must render FunnelFilterBar');
assert(!networkGraphContent.includes('<input'), 'NetworkGraph must NOT have manual date inputs');
assert(networkGraphContent.includes('containerRef'), 'NetworkGraph must use containerRef for responsive sizing');
console.log('✓ network-graph.tsx integrates FunnelFilterBar with dynamic container sizing and no manual date inputs');

console.log('\nAll static checks and contracts passed successfully!');
