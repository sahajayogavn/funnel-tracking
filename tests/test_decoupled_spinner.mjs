import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const webSrcDir = path.join(__dirname, '../web/src');

function assert(condition, message) {
  if (!condition) {
    console.error(`❌ FAIL: ${message}`);
    process.exit(1);
  }
  console.log(`✅ PASS: ${message}`);
}

async function runTests() {
  console.log('Running test_decoupled_spinner.mjs...\n');

  // S-02: No JS Date comparison for classification_verified_at
  const queriesTsPath = path.join(webSrcDir, 'lib/queries.ts');
  const queriesTsContent = fs.readFileSync(queriesTsPath, 'utf-8');
  assert(
    !queriesTsContent.match(/new Date\(.*classification_verified_at.*\)/),
    'S-02: No JS Date comparisons for classification_verified_at'
  );

  const typesTsPath = path.join(webSrcDir, 'lib/types.ts');
  const typesTsContent = fs.readFileSync(typesTsPath, 'utf-8');
  assert(
    typesTsContent.includes('classificationStatus?: \'pending\' | \'done\' | \'unknown\';'),
    'Types: Seeker has classificationStatus'
  );

  // S-06: Reduced motion CSS exists
  const globalsCssPath = path.join(webSrcDir, 'app/globals.css');
  const globalsCssContent = fs.readFileSync(globalsCssPath, 'utf-8');
  assert(
    globalsCssContent.includes('@media (prefers-reduced-motion: reduce)') && globalsCssContent.includes('.spinner'),
    'S-06: globals.css contains prefers-reduced-motion for .spinner'
  );

  // S-03, S-04, S-05 check seekers-table component static source
  const seekersTablePath = path.join(webSrcDir, 'components/seekers-table.tsx');
  const seekersTableContent = fs.readFileSync(seekersTablePath, 'utf-8');
  
  assert(
    seekersTableContent.includes('seeker.classificationStatus === \'unknown\''),
    'S-04: Component checks for unknown classificationStatus'
  );
  assert(
    seekersTableContent.includes('className="spinner"'),
    'S-03: Component renders spinner'
  );
  
  // Interval polling exists (S-08 mechanism)
  assert(
    seekersTableContent.includes('setInterval') && seekersTableContent.includes('router.refresh()'),
    'S-08: Polling mechanism exists for automatic refresh'
  );

  console.log('\nAll decoupled spinner tests passed!');
}

runTests().catch(err => {
  console.error(err);
  process.exit(1);
});
