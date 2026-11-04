import { demoSummary, outcomeReason, selectRuns, summarizeRuns } from './summary.mjs';

const elements = Object.fromEntries(
  [...document.querySelectorAll('[id]')].map(element => [element.id, element]),
);
const datasetButtons = [...document.querySelectorAll('[data-dataset]')];
const state = { records: [], dataset: 'all', selectedId: null };
const dateFormat = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
const timeFormat = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' });

function createElement(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function formatDuration(seconds) {
  if (seconds === null) return '—';
  return seconds < 1 ? `${Math.round(seconds * 1000)} ms` : `${seconds.toFixed(2)} s`;
}

function formatPercent(count, total) {
  return total ? `${Math.round(count / total * 100)}%` : '—';
}

function showNotice(text, isError = false) {
  elements.message.textContent = text;
  elements.message.className = isError ? 'notice error' : 'notice';
  elements.message.hidden = false;
}

async function loadRuns() {
  const response = await fetch('/api/runs');
  if (!response.ok) throw new Error('Could not load runs. Check that the local server is running.');
  state.records = await response.json();
  elements['updated-at'].textContent = `Updated ${timeFormat.format(new Date())}`;
  elements.message.hidden = true;
  render();
}

function render() {
  const dataset = selectRuns(state.records, { dataset: state.dataset });
  const visible = selectRuns(dataset, {
    status: elements.status.value,
    query: elements.search.value,
  });
  if (!visible.some(run => run.id === state.selectedId)) {
    state.selectedId = visible[0]?.id ?? null;
  }

  renderSummary(dataset);
  renderHistory(visible, dataset.length);
  renderInspector(visible.find(run => run.id === state.selectedId));
  renderRecentRuns(dataset.slice(0, 8).reverse());

  for (const button of datasetButtons) {
    button.setAttribute('aria-pressed', String(button.dataset.dataset === state.dataset));
  }
}

function renderSummary(runs) {
  const summary = summarizeRuns(runs);
  elements.passed.textContent = `${summary.passed} / ${summary.total}`;
  elements.integrity.textContent = formatPercent(summary.integrityPassed, summary.total);
  elements.median.textContent = formatDuration(summary.medianSeconds);
  elements.rto.textContent = `${summary.targetsMet} / ${summary.total}`;
  elements['dataset-note'].textContent = runs.some(run => run.sample)
    ? 'Includes synthetic samples · measured timings'
    : 'Latest 200 recorded runs · local data';
}

function renderHistory(runs, total) {
  elements['run-count'].textContent = runs.length;
  elements['visible-count'].textContent = `${runs.length} of ${total} runs · latest 200 records`;
  elements.runs.replaceChildren(...runs.map(renderRunRow));
  elements.empty.hidden = runs.length > 0;
  const filtered = elements.search.value.trim() !== '' || elements.status.value !== 'all';
  elements['empty-heading'].textContent = filtered ? 'No matching runs' : 'No runs in this dataset';
  elements['empty-description'].textContent = filtered
    ? 'Try another name, run ID, or outcome.'
    : state.dataset === 'live'
      ? 'Use the CLI to capture a baseline and test one of your backups.'
      : 'Run sample drills above, or use the CLI to test a backup.';
  elements['clear-filters'].hidden = !filtered;
  elements['empty-command'].hidden = filtered || state.dataset === 'live';
}

function renderRunRow(run) {
  const row = createElement('tr');
  row.classList.toggle('selected', run.id === state.selectedId);
  const nameCell = createElement('td');
  const nameButton = createElement('button', run.name, 'run-name');
  nameButton.title = run.name;
  nameButton.setAttribute('aria-label', `Inspect ${run.name}`);
  nameButton.setAttribute('aria-controls', 'inspector');
  nameButton.onclick = () => inspectRun(run.id);
  const metadata = createElement('span', undefined, 'run-meta');
  metadata.append(createElement('span', run.backend));
  if (run.sample) metadata.append(createElement('span', 'Sample', 'sample-tag'));
  nameCell.append(nameButton, metadata);

  const outcomeCell = createElement('td');
  outcomeCell.append(
    createElement('span', statusLabel(run.status), `outcome ${run.status}`),
    createElement('span', outcomeReason(run), 'secondary-line'),
  );
  const filesCell = createElement('td', run.comparison
    ? `${run.comparison.matched} / ${run.comparison.expected_files}`
    : '—', run.integrity_ok ? '' : 'integrity-bad');
  filesCell.append(createElement('span', run.comparison ? 'matched' : 'not checked', 'secondary-line'));

  const durationCell = createElement('td', run.status === 'error' ? '—' : formatDuration(run.total_seconds));
  durationCell.append(createElement('span', `Target ${formatDuration(run.rto_seconds)}`, 'secondary-line'));
  const startedAt = new Date(run.started_at);
  const dateCell = createElement('td', dateFormat.format(startedAt), 'muted');
  dateCell.title = startedAt.toLocaleString();
  dateCell.append(createElement('span', timeFormat.format(startedAt), 'secondary-line'));
  row.append(nameCell, outcomeCell, filesCell, durationCell, dateCell);
  return row;
}

function statusLabel(status) {
  return { passed: 'Passed', failed: 'Failed', error: 'Error' }[status] ?? status;
}

function inspectRun(id) {
  state.selectedId = id;
  render();
  elements.inspector.focus({ preventScroll: true });
  if (window.matchMedia('(max-width: 960px)').matches) {
    elements.inspector.scrollIntoView({ block: 'start' });
  }
}

function renderInspector(run) {
  elements.exports.hidden = !run;
  elements['inspector-id'].textContent = run ? run.id.slice(0, 8) : '';
  if (!run) {
    elements['detail-body'].replaceChildren(createElement(
      'p', 'Select a run to review its evidence.', 'inspector-placeholder',
    ));
    elements['html-export'].removeAttribute('href');
    elements['json-export'].removeAttribute('href');
    return;
  }

  const content = createElement('div', undefined, 'detail-content');
  const outcome = createElement('div');
  outcome.append(createElement('span', statusLabel(run.status), `outcome ${run.status}`));
  if (run.sample) outcome.append(createElement('span', 'Sample', 'sample-tag'));
  content.append(
    outcome,
    createElement('h3', run.name, 'detail-title'),
    createElement('p', outcomeReason(run), 'detail-description'),
  );

  const facts = createElement('dl', undefined, 'detail-facts');
  const values = [
    ['Backend', run.backend],
    ['File integrity', run.integrity_ok ? 'Passed' : run.comparison ? 'Failed' : 'Not checked', run.integrity_ok ? 'good' : 'bad'],
    ['Restore', formatDuration(run.restore_seconds)],
    ['Verification', formatDuration(run.verify_seconds)],
    ['End-to-end', formatDuration(run.total_seconds)],
    ['Time target', formatDuration(run.rto_seconds)],
    ['Target result', run.rto_met ? 'Met' : run.status === 'error' ? 'Incomplete' : 'Missed', run.rto_met ? 'good' : 'bad'],
  ];
  for (const [label, value, className] of values) {
    const item = createElement('div');
    item.append(createElement('dt', label), createElement('dd', value, className));
    facts.append(item);
  }
  content.append(facts);
  if (run.error) content.append(createElement('p', run.error, 'error-detail'));
  if (run.comparison) {
    for (const kind of ['changed', 'missing', 'unexpected']) {
      content.append(renderFileGroup(kind, run.comparison[kind]));
    }
  }
  elements['detail-body'].replaceChildren(content);
  const reportPath = `/api/runs/${encodeURIComponent(run.id)}`;
  elements['html-export'].href = `${reportPath}/report.html`;
  elements['json-export'].href = `${reportPath}/report.json`;
}

function renderFileGroup(kind, paths) {
  const group = createElement('details', undefined, 'file-group');
  group.open = paths.length > 0;
  const heading = createElement('summary');
  heading.append(
    createElement('span', `${kind[0].toUpperCase()}${kind.slice(1)} files`),
    createElement('span', paths.length),
  );
  group.append(heading);
  if (paths.length) {
    const list = createElement('ul');
    list.append(...paths.map(path => createElement('li', path)));
    group.append(list);
  } else {
    group.append(createElement('p', 'None detected.'));
  }
  return group;
}

function renderRecentRuns(runs) {
  const maximum = Math.max(...runs.map(run => run.total_seconds), 0.001);
  elements['recent-runs'].replaceChildren();
  if (!runs.length) {
    elements['recent-runs'].append(createElement('p', 'No drills in this dataset.'));
    return;
  }
  for (const run of runs) {
    const button = createElement('button', undefined, 'recent-run');
    button.dataset.status = run.status;
    button.title = `${run.name}: ${outcomeReason(run)}`;
    button.setAttribute('aria-label', `Inspect ${run.name}, ${formatDuration(run.total_seconds)}, ${run.status}`);
    button.onclick = () => {
      elements.search.value = '';
      elements.status.value = 'all';
      inspectRun(run.id);
    };
    const track = createElement('span', undefined, 'bar-track');
    track.setAttribute('aria-hidden', 'true');
    const fill = createElement('span', undefined, 'bar-fill');
    fill.style.width = `${Math.max(2, run.total_seconds / maximum * 100)}%`;
    track.append(fill);
    button.append(
      createElement('span', run.status === 'error' ? 'Incomplete' : formatDuration(run.total_seconds), 'recent-value'),
      track,
      createElement('span', run.id.slice(0, 8), 'recent-id'),
    );
    elements['recent-runs'].append(button);
  }
}

async function runSamples() {
  const button = elements['demo-button'];
  const originalContent = [...button.childNodes];
  button.disabled = true;
  button.textContent = 'Running drills…';
  try {
    const response = await fetch('/api/demo', {
      method: 'POST',
      headers: { 'X-Recovery-Action': 'demo' },
    });
    if (!response.ok) {
      throw new Error(response.status === 409
        ? 'Sample drills are already running.'
        : 'Could not run sample drills. Check the local server.');
    }
    const results = await response.json();
    await loadRuns();
    showNotice(demoSummary(results), results.some(run => run.status === 'error'));
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.replaceChildren(...originalContent);
    button.disabled = false;
  }
}

for (const button of datasetButtons) {
  button.onclick = () => {
    state.dataset = button.dataset.dataset;
    render();
  };
}
elements.search.oninput = render;
elements.status.onchange = render;
elements['clear-filters'].onclick = () => {
  elements.search.value = '';
  elements.status.value = 'all';
  render();
  elements.search.focus();
};
elements.refresh.onclick = async () => {
  elements.refresh.disabled = true;
  try { await loadRuns(); }
  catch (error) { showNotice(error.message, true); }
  finally { elements.refresh.disabled = false; }
};
elements['demo-button'].onclick = runSamples;
for (const id of ['method-button', 'learn-button']) {
  elements[id].onclick = () => elements.method.showModal();
}
elements['close-method'].onclick = () => elements.method.close();
document.addEventListener('keydown', event => {
  if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
  if (elements.method.open || ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
  event.preventDefault();
  elements.search.focus();
});

loadRuns().catch(error => {
  elements['visible-count'].textContent = 'Runs could not be loaded';
  elements['dataset-note'].textContent = 'Local server unavailable';
  showNotice(error.message, true);
});
