"use strict";
import { demoSummary } from './summary.mjs';
const $ = (id) => document.getElementById(id);
let records = [];
const duration = (n) => n < 1 ? `${(n * 1000).toFixed(0)} ms` : `${n.toFixed(2)} s`;
const pct = (n, total) => total ? `${Math.round(n / total * 100)}%` : "—";
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function message(text, error = false) {
  $("message").hidden = false;
  $("message").className = `message${error ? " error" : ""}`;
  $("message").textContent = text;
}
async function refresh(throwOnError = false) {
  try {
    const response = await fetch("/api/runs");
    if (!response.ok) throw new Error("Could not load recovery evidence. Check the local server.");
    records = await response.json();
    render();
  } catch (error) { message(error.message, true); if (throwOnError) throw error; }
}
function render() {
  const dataset = $("dataset").value;
  const runs = records.filter(r => dataset === "all" || (dataset === "sample" ? r.sample : !r.sample));
  const passed = runs.filter(r => r.status === "passed").length;
  $("passed").textContent = `${passed} / ${runs.length}`;
  $("passed-note").textContent = runs.length ? "Integrity and recovery target both passed" : "No evidence recorded yet";
  $("integrity").textContent = pct(runs.filter(r => r.integrity_ok).length, runs.length);
  $("rto").textContent = pct(runs.filter(r => r.rto_met).length, runs.length);
  const completed = runs.filter(r => r.status !== "error").map(r => r.total_seconds).sort((a,b) => a-b);
  const middle = Math.floor(completed.length / 2);
  const median = completed.length % 2 ? completed[middle] : (completed[middle - 1] + completed[middle]) / 2;
  $("median").textContent = completed.length ? duration(median) : "—";
  $("dataset-note").textContent = runs.some(r => r.sample) ? "Includes synthetic sample drills · real measured timings" : "Measured results from your local workspace";
  chart(runs.slice(0, 8).reverse());
  const query = $("search").value.toLowerCase();
  const visible = runs.filter(r => (r.name.toLowerCase().includes(query) || r.id.includes(query)) &&
    ($("status").value === "all" || r.status === $("status").value));
  $("run-count").textContent = visible.length;
  $("runs").replaceChildren();
  $("empty").hidden = visible.length > 0;
  $("empty").querySelector("h3").textContent = runs.length ? "No drills match these filters." : "Your first recovery starts here.";
  for (const r of visible) {
    const row = node("tr");
    const name = node("td");
    name.append(node("span", r.name, "drill-name"), node("span", `${r.sample ? "SAMPLE · " : ""}${r.backend} / ${r.id.slice(0,8)}`, "drill-sub"));
    const status = node("td");
    status.append(node("span", `${r.status === "passed" ? "✓" : "!"} ${r.status[0].toUpperCase() + r.status.slice(1)}`, `badge ${r.status}`));
    const check = node("td", r.comparison ? `${r.comparison.matched} / ${r.comparison.expected_files} verified` : "Not verified", r.integrity_ok ? "integrity-ok" : "integrity-bad");
    const timing = node("td");
    timing.append(node("span", duration(r.total_seconds)), node("span", `Target ${duration(r.rto_seconds)} · ${r.rto_met ? "met" : "missed / incomplete"}`, "drill-sub"));
    const date = new Date(r.started_at);
    const when = node("td", date.toLocaleDateString(undefined, {month:"short",day:"numeric"}), "muted");
    when.title = date.toLocaleString();
    when.append(node("span", date.toLocaleTimeString(undefined, {hour:"2-digit",minute:"2-digit"}), "drill-sub"));
    const action = node("td");
    const button = node("button", "Evidence ↗", "row-button");
    button.setAttribute("aria-label", `View evidence for ${r.name}`);
    button.onclick = () => detail(r);
    action.append(button);
    row.append(name, status, check, timing, when, action);
    $("runs").append(row);
  }
}
function chart(runs) {
  $("chart").replaceChildren();
  if (!runs.length) { $("chart").append(node("p", "Your recovery timeline will appear here.", "chart-empty")); return; }
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 600 180"); svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Recovery duration chart. Each bar represents a measured drill.");
  const max = Math.max(...runs.map(r => r.total_seconds), 0.001);
  for (let i=0;i<4;i++) {
    const y = 15+i*44;
    const line = document.createElementNS(ns, "line");
    for (const [key,value] of Object.entries({x1:55,y1:y,x2:590,y2:y,stroke:"#e6ece3","stroke-dasharray":"3 4"})) line.setAttribute(key,value);
    svg.append(line);
    const text = document.createElementNS(ns, "text"); text.setAttribute("x", 0); text.setAttribute("y", y+4); text.setAttribute("font-size",9); text.setAttribute("fill","#718174"); text.textContent = duration(max*(1-i/3)); svg.append(text);
  }
  runs.forEach((r,i) => {
    const slot = 520/runs.length, height = Math.max(2, r.total_seconds/max*132);
    const rect = document.createElementNS(ns, "rect");
    for(const [key,value] of Object.entries({x:65+i*slot,y:147-height,width:Math.min(slot*.5,45),height,rx:4,fill:r.status === "passed" ? "#39795c" : "#c2906b"})) rect.setAttribute(key,value);
    const title = document.createElementNS(ns, "title"); title.textContent = `${r.name}: ${duration(r.total_seconds)} (${r.status})`; rect.append(title); svg.append(rect);
    const label = document.createElementNS(ns, "text"); label.setAttribute("x",65+i*slot); label.setAttribute("y",169); label.setAttribute("font-size",9); label.setAttribute("fill","#718174"); label.textContent = r.id.slice(0,4); svg.append(label);
  });
  $("chart").append(svg);
}
function detail(r) {
  $("detail-title").textContent = r.name;
  const content = node("div", undefined, "detail-content");
  content.append(node("span", r.status.toUpperCase(), `badge ${r.status}`), node("p", `${r.sample ? "Synthetic sample · " : ""}${new Date(r.started_at).toLocaleString()} · ${r.backend}`));
  const stats = node("div", undefined, "detail-summary");
  for(const [title,value] of [["Restore",duration(r.restore_seconds)],["Verification",duration(r.verify_seconds)],["End-to-end",duration(r.total_seconds)],["Recovery target",duration(r.rto_seconds)],["Integrity",r.integrity_ok ? "Passed" : "Not verified"],["Time objective",r.rto_met ? "Met" : "Not met / incomplete"]]) {
    const item = node("div"); item.append(node("small",title),node("strong",value)); stats.append(item);
  }
  content.append(stats);
  if(r.error) content.append(node("p",r.error));
  if(r.comparison) for(const kind of ["changed","missing","unexpected"]) {
    content.append(node("h3",`${kind[0].toUpperCase()+kind.slice(1)} files (${r.comparison[kind].length})`));
    if(!r.comparison[kind].length) { content.append(node("p","None detected.")); continue; }
    const list = node("ul");
    for(const path of r.comparison[kind]) list.append(node("li",path));
    content.append(list);
  }
  content.append(node("p",`Run ID: ${r.id}`,"run-id"));
  $("detail-body").replaceChildren(content);
  $("json-export").href = `/api/runs/${encodeURIComponent(r.id)}/report.json`;
  $("html-export").href = `/api/runs/${encodeURIComponent(r.id)}/report.html`;
  $("detail").showModal();
}
$("demo-button").onclick = async () => {
  const button = $("demo-button"); button.disabled = true; button.textContent = "Restoring & verifying…";
  try {
    const response = await fetch("/api/demo", {method:"POST",headers:{"X-Recovery-Action":"demo"}});
    if(!response.ok) throw new Error(response.status === 409 ? "Sample drills are already running." : "Could not run sample drills. Check the local server.");
    const results = await response.json();
    await refresh(true); message(demoSummary(results), results.some(run => run.status === 'error'));
  } catch(error) { message(error.message,true); }
  finally { button.disabled=false; button.textContent="＋ Run sample drills"; }
};
for(const id of ["dataset","status"]) $(id).onchange = render;
$("search").oninput = render;
$("refresh").onclick = () => refresh();
for(const id of ["method-button","learn-button"]) $(id).onclick = () => $("method").showModal();
for(const dialog of document.querySelectorAll("dialog")) dialog.querySelector(".close").onclick = () => dialog.close();
refresh();
