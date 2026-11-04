import test from 'node:test';
import assert from 'node:assert/strict';
import { demoSummary } from '../recovery_assurance/static/summary.mjs';

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
