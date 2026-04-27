function downloadText(filename, text, mime) {
  const blob = new Blob([text], { type: mime || "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function DemoExportPanel({ report }) {
  if (!report) return null;

  const base = "open-case-demo-report";

  return (
    <div className="oc-demo-exports">
      <h3>Export</h3>
      <p className="oc-demo-muted">
        Comparative summary and per-figure rows. Share links point at signed HTML reports on this API host.
      </p>
      <div className="oc-demo-export-grid">
        <button type="button" onClick={() => downloadText(`${base}.txt`, report.export_text_plain || "")}>
          Plain text
        </button>
        <button
          type="button"
          onClick={() => downloadText(`${base}.md`, report.export_text_markdown || "", "text/markdown;charset=utf-8")}
        >
          Markdown
        </button>
        <button
          type="button"
          onClick={() => downloadText(`${base}.html`, report.export_html_card || "", "text/html;charset=utf-8")}
        >
          HTML card
        </button>
        <button
          type="button"
          onClick={() => downloadText(`${base}.json`, report.export_json || "{}", "application/json")}
        >
          JSON
        </button>
        <button type="button" onClick={() => downloadText(`${base}.csv`, report.export_csv || "", "text/csv;charset=utf-8")}>
          CSV
        </button>
      </div>
    </div>
  );
}
