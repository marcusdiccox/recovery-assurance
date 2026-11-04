"""Portable, escaped HTML evidence report with no remote dependencies."""

from html import escape


def html_report(report: dict) -> str:
    def cell(value):
        return escape(str(value))

    comparison = report.get("comparison")
    findings = ""
    if comparison:
        for kind in ("changed", "missing", "unexpected"):
            items = "".join(f"<li><code>{cell(path)}</code></li>" for path in comparison[kind])
            findings += f"<h3>{kind.title()} ({len(comparison[kind])})</h3><ul>{items}</ul>"
        findings += f"<p>Verified unchanged: {comparison['matched']} files.</p>"
    rows = "".join(
        f"<tr><th>{cell(key.replace('_', ' ').title())}</th><td>{cell(value)}</td></tr>"
        for key, value in report.items()
        if key != "comparison"
    )
    label = (
        "Synthetic sample data · real measured drill"
        if report["sample"]
        else "Recorded recovery drill"
    )
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Recovery evidence · {cell(report["name"])}</title>
<style>body{{max-width:900px;margin:48px auto;padding:0 24px;font:16px/1.6 system-ui;
color:#162f2d}}h1{{font-size:36px}}table{{border-collapse:collapse;width:100%}}
th,td{{text-align:left;border-bottom:1px solid #dbe6e4;padding:10px;overflow-wrap:anywhere}}
th{{width:35%}}code{{overflow-wrap:anywhere}}.label{{color:#42645d}}
@media print{{body{{margin:0}}}}</style>
<p class="label">RECOVERY ASSURANCE / EVIDENCE REPORT</p>
<h1>{cell(report["name"])}</h1>
<p>{label}</p>
<table>{rows}</table><h2>Integrity findings</h2>{findings}
<p>Scope: file paths, byte sizes, and SHA-256. Recovery time includes restore and verification.
This report does not certify application recovery, access controls, metadata, or business readiness.
Stored reports are append-only through the application, not cryptographically signed.</p></html>"""
