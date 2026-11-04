import test from 'node:test';
import assert from 'node:assert/strict';
import { demoSummary, selectRuns, summarizeRuns, outcomeReason } from '../recovery_assurance/static/summary.mjs';

test('sample completion describes recorded outcomes instead of assuming success', () => {
  const message = demoSummary([{status:'passed'},{status:'failed'},{status:'error'}]);
  assert.equal(message, '3 sample drills recorded: 1 passed, 1 failed, 1 error. Open the evidence for details.');
});

test('all-error result never claims a verified recovery', () => {
  const message = demoSummary([{status:'error'},{status:'error'},{status:'error'}]);
  assert.ok(message.includes('0 passed'));
  assert.ok(message.includes('3 errors'));
  assert.ok(!message.includes('verified restore'));
});

const runs = [
  { id: 'a1', name: 'Finance', backend: 'restic', sample: false, status: 'passed', integrity_ok: true, rto_met: true, total_seconds: 2 },
  { id: 'b2', name: 'Finance sample', backend: 'directory', sample: true, status: 'failed', integrity_ok: false, rto_met: true, total_seconds: 4 },
  { id: 'c3', name: 'Operations', backend: 'restic', sample: false, status: 'error', integrity_ok: false, rto_met: false, total_seconds: 100 },
  { id: 'd4', name: 'Finance slow', backend: 'directory', sample: false, status: 'failed', integrity_ok: true, rto_met: false, total_seconds: 8 },
];

test('dataset, outcome, and normalized search combine without changing records', () => {
  const result = selectRuns(runs, { dataset: 'live', status: 'failed', query: ' FINANCE ' });
  assert.deepEqual(result.map(run => run.id), ['d4']);
  assert.equal(runs.length, 4);
  assert.deepEqual(selectRuns(runs, { query: 'RESTIC' }).map(run => run.id), ['a1', 'c3']);
  assert.deepEqual(selectRuns(runs, { query: 'B2' }).map(run => run.id), ['b2']);
});

test('recovery summaries count errors but exclude incomplete timings from the median', () => {
  assert.deepEqual(summarizeRuns(runs), {
    total: 4, passed: 1, failed: 2, errors: 1,
    integrityPassed: 2, targetsMet: 2, medianSeconds: 4,
  });
  assert.equal(summarizeRuns(runs.slice(0, 2)).medianSeconds, 3);
  assert.equal(summarizeRuns([runs[2]]).medianSeconds, null);
});

test('empty evidence has no invented timings or successes', () => {
  assert.deepEqual(summarizeRuns([]), {
    total: 0, passed: 0, failed: 0, errors: 0,
    integrityPassed: 0, targetsMet: 0, medianSeconds: null,
  });
});

test('outcome reasons distinguish file issues, missed targets, and incomplete runs', () => {
  assert.equal(outcomeReason(runs[0]), 'All checks passed');
  assert.equal(outcomeReason(runs[2]), 'Restore or verification error');
  assert.equal(outcomeReason(runs[3]), 'Recovery target missed');
  assert.equal(outcomeReason({ ...runs[1], comparison: { changed: ['a'], missing: ['b'], unexpected: ['c'] } }), '3 file issues');
  assert.equal(outcomeReason({ ...runs[1], comparison: { changed: ['a'], missing: [], unexpected: [] } }), '1 file issue');
  assert.equal(outcomeReason(runs[1]), 'Verification incomplete');
});
