// Language-neutral command example: JSON files + environment, no evokit import.
import { readFileSync, writeFileSync } from 'node:fs';

const task = JSON.parse(readFileSync(process.env.EVOKIT_TASK_FILE, 'utf8'));
const prefix = readFileSync('prefix.txt', 'utf8').trim();
const output = prefix + task.payload.text;
const passed = output === task.payload.text;
writeFileSync('trace.txt', `echoed ${output.length} characters\n`);
writeFileSync(process.env.EVOKIT_RESULT_FILE, JSON.stringify({
  outcome: passed,
  reason: passed ? 'exact echo' : 'unexpected prefix',
  cost: { calls: 1 },
  seconds: 0.01,
  output,
  trace_path: 'trace.txt',
}));
