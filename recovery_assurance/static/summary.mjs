export function demoSummary(runs) {
  const passed = runs.filter(run => run.status === 'passed').length;
  const failed = runs.filter(run => run.status === 'failed').length;
  const errors = runs.filter(run => run.status === 'error').length;
  return `${runs.length} sample drills recorded: ${passed} passed, ${failed} failed, ${errors} ${errors === 1 ? 'error' : 'errors'}. Open the evidence for details.`;
}

export function selectRuns(runs, { dataset = 'all', status = 'all', query = '' } = {}) {
  const search = query.trim().toLowerCase();
  return runs.filter(run => {
    const matchesDataset = dataset === 'all' || (dataset === 'sample' ? run.sample : !run.sample);
    const matchesStatus = status === 'all' || run.status === status;
    const matchesSearch = [run.name, run.id, run.backend]
      .some(value => value.toLowerCase().includes(search));
    return matchesDataset && matchesStatus && matchesSearch;
  });
}

export function summarizeRuns(runs) {
  const timings = runs
    .filter(run => run.status !== 'error')
    .map(run => run.total_seconds)
    .sort((left, right) => left - right);
  const middle = Math.floor(timings.length / 2);
  const medianSeconds = timings.length === 0
    ? null
    : (timings[middle] + timings[Math.floor((timings.length - 1) / 2)]) / 2;

  return {
    total: runs.length,
    passed: runs.filter(run => run.status === 'passed').length,
    failed: runs.filter(run => run.status === 'failed').length,
    errors: runs.filter(run => run.status === 'error').length,
    integrityPassed: runs.filter(run => run.integrity_ok).length,
    targetsMet: runs.filter(run => run.rto_met).length,
    medianSeconds,
  };
}

export function outcomeReason(run) {
  if (run.status === 'error') return 'Restore or verification error';
  if (!run.integrity_ok) {
    if (!run.comparison) return 'Verification incomplete';
    const count = ['changed', 'missing', 'unexpected']
      .reduce((total, kind) => total + run.comparison[kind].length, 0);
    return `${count} file ${count === 1 ? 'issue' : 'issues'}`;
  }
  return run.rto_met ? 'All checks passed' : 'Recovery target missed';
}
