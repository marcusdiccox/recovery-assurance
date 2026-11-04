export function demoSummary(runs) {
  const passed = runs.filter(run => run.status === 'passed').length;
  const failed = runs.filter(run => run.status === 'failed').length;
  const errors = runs.filter(run => run.status === 'error').length;
  return `${runs.length} sample drills recorded: ${passed} passed, ${failed} failed, ${errors} ${errors === 1 ? 'error' : 'errors'}. Open the evidence for details.`;
}
