import fs from 'fs';
import path from 'path';

// Read the TSX file
const clientFile = fs.readFileSync(path.join(process.cwd(), 'web/src/app/llm/llm-client.tsx'), 'utf-8');

// Extract the generateReproCommand function body
const functionMatch = clientFile.match(/export function generateReproCommand\(call: LlmCall\): string \{([\s\S]*?)\n\}/);

if (!functionMatch) {
  console.error("Failed to find generateReproCommand in llm-client.tsx");
  process.exit(1);
}

const functionBody = functionMatch[1];

// Create a safe evaluation environment
const generateReproCommand = new Function('call', functionBody);

// Test Cases
const mockCall = {
  route: 'compose_reply',
  subject_id: 'th_456',
  trace_id: 'trace_789'
};

const result = generateReproCommand(mockCall);
console.log("Generated Command:", result);

let passed = true;

if (result.includes('sk-')) {
  console.error("FAIL: API key (sk-) leaked in repro command!");
  passed = false;
}

if (!result.includes('YOUR_API_KEY')) {
  console.error("FAIL: Repro command does not contain obfuscated YOUR_API_KEY!");
  passed = false;
}

if (!result.includes('YOUR_API_BASE')) {
  console.error("FAIL: Repro command does not contain obfuscated YOUR_API_BASE!");
  passed = false;
}

if (!result.includes('--route compose_reply')) {
  console.error("FAIL: Repro command missing route parameter!");
  passed = false;
}

if (!result.includes('--subject th_456')) {
  console.error("FAIL: Repro command missing subject parameter!");
  passed = false;
}

if (!result.includes('--trace trace_789')) {
  console.error("FAIL: Repro command missing trace parameter!");
  passed = false;
}

if (passed) {
  console.log("PASS: Repro-command generation successfully tested and verified secure against API key leaks.");
  process.exit(0);
} else {
  process.exit(1);
}
