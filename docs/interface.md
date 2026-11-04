# Interface notes

The dashboard is organized around reviewing a recovery run. History takes the main column; selecting a run opens its recorded result in the adjacent inspector. Small screens put the inspector below the table. File lists expand in place, and reports stay attached to the selected run.

The visual system uses neutral surfaces, thin borders, system fonts, and a single indigo action color. Green, amber, and red indicate recorded outcomes. The sample label remains visible, including on failed drills. A failed run can have matching content if its time target was missed, so file integrity and timing remain separate in the inspector.

## References

- [GitHub Actions run logs](https://docs.github.com/en/actions/how-tos/monitor-workflows/use-workflow-run-logs): run histories that lead to specific execution details and downloadable evidence.
- [Grafana table visualization](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/table/): comparable operational results in rows, with status attached to each record.
- [Linear's product interface](https://linear.app/features): compact navigation and restrained typography around a primary work list.

These are layout and interaction references. No third-party assets, logos, or source code are copied.

## Code organization

- `static/summary.mjs` filters recorded runs and computes summaries without accessing the DOM. Its tests cover combined filters, incomplete timings, empty datasets, and failure reasons.
- `static/app.js` loads records, handles user actions, and renders the history and inspector. File names and errors are inserted with `textContent`.
- `static/style.css` groups navigation, list, inspector, dialog, and responsive rules. It uses no external font or CSS dependency.

Summary numbers describe the chosen dataset. Search and outcome filters affect the history list. Selecting a recent duration clears those history filters so its run can be inspected. Selecting a run moves keyboard focus to the inspector; native details controls and export links then follow in tab order.
