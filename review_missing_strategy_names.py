import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd


DEFAULT_INPUT_FILE = Path("output/master_cleaned_tos_data.csv")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8767
STRATEGY_COLUMN = "Strategy_Name"
DEFAULT_VALID_STRATEGY_NAMES = [
    "Discretionary",
    "Opt025-TradeBusters-7DTE-Naked-Puts",
    "Opt026-60m0DTE-PutSpread",
    "Opt021-1DTE-ShortStraddle-Mon-Thurs",
]
DISPLAY_COLUMNS = [
    STRATEGY_COLUMN,
    "Exec Time",
    "Spread",
    "Side",
    "Qty",
    "Pos Effect",
    "Symbol",
    "Exp",
    "Type",
    "Price",
    "Net Price",
]


def clean_strategy_name(value):
    if value is None or pd.isna(value):
        return ""

    return " ".join(str(value).strip().split())


def ensure_strategy_column(df):
    if STRATEGY_COLUMN not in df.columns:
        df[STRATEGY_COLUMN] = ""

    return df


def valid_strategy_names(extra_strategy_names=None):
    names = {
        clean_strategy_name(value)
        for value in DEFAULT_VALID_STRATEGY_NAMES
    }

    if extra_strategy_names:
        names.update(
            clean_strategy_name(value)
            for value in extra_strategy_names
        )

    names.discard("")

    return sorted(names, key=str.casefold)


def missing_strategy_mask(df, extra_strategy_names=None):
    ensure_strategy_column(df)

    cleaned = df[STRATEGY_COLUMN].apply(clean_strategy_name)
    valid = set(valid_strategy_names(extra_strategy_names))

    return (cleaned == "") | (
        ~cleaned.isin(valid)
    )


def existing_strategy_names(df, extra_strategy_names=None):
    return valid_strategy_names(extra_strategy_names)


def row_value(value):
    if value is None or pd.isna(value):
        return ""

    return str(value)


def load_strategy_review_data(input_file, extra_strategy_names=None):
    input_file = Path(input_file)

    if not input_file.exists():
        raise FileNotFoundError(f"Could not find {input_file}")

    df = ensure_strategy_column(pd.read_csv(input_file))
    missing = df[missing_strategy_mask(df, extra_strategy_names)].copy()
    rows = []

    for index, row in missing.iterrows():
        rows.append(
            {
                "row_id": int(index),
                **{
                    column: row_value(row.get(column, ""))
                    for column in DISPLAY_COLUMNS
                },
            }
        )

    return {
        "input_file": str(input_file),
        "total_rows": int(len(df)),
        "missing_count": int(len(rows)),
        "strategies": existing_strategy_names(df, extra_strategy_names),
        "display_columns": DISPLAY_COLUMNS,
        "rows": rows,
    }


def apply_strategy_updates(input_file, updates, extra_strategy_names=None):
    input_file = Path(input_file)

    if not input_file.exists():
        raise FileNotFoundError(f"Could not find {input_file}")

    if not isinstance(updates, list):
        raise ValueError("Updates must be a list")

    df = ensure_strategy_column(pd.read_csv(input_file))
    saved_rows = 0
    skipped_rows = []

    for update in updates:
        try:
            row_id = int(update.get("row_id"))
        except (TypeError, ValueError):
            skipped_rows.append(update)
            continue

        strategy_name = clean_strategy_name(update.get("strategy_name"))
        if not strategy_name or row_id < 0 or row_id >= len(df):
            skipped_rows.append(update)
            continue

        df.at[row_id, STRATEGY_COLUMN] = strategy_name
        saved_rows += 1

    if saved_rows:
        df.to_csv(input_file, index=False)

    return {
        "saved_rows": saved_rows,
        "skipped_rows": len(skipped_rows),
        "remaining_missing": int(
            missing_strategy_mask(df, extra_strategy_names).sum()
        ),
        "strategies": existing_strategy_names(df, extra_strategy_names),
    }


def dashboard_html():
    columns_json = json.dumps(DISPLAY_COLUMNS)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Missing Strategy Names</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #6b7280;
      --line: #d8dde5;
      --accent: #1769aa;
      --accent-dark: #0f4f85;
      --success-bg: #e8f7ee;
      --success-text: #14532d;
      --warn-bg: #fff6d7;
      --warn-text: #7a4b00;
      --danger-bg: #feecef;
      --danger-text: #8a1c2d;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
    }}

    header {{
      position: sticky;
      top: 0;
      z-index: 20;
      background: rgba(247, 248, 250, 0.96);
      border-bottom: 1px solid var(--line);
      padding: 16px 20px 14px;
      backdrop-filter: blur(8px);
    }}

    h1 {{
      margin: 0 0 10px;
      font-size: 22px;
      font-weight: 700;
      letter-spacing: 0;
    }}

    main {{
      padding: 16px 20px 28px;
    }}

    button, select, input {{
      font: inherit;
    }}

    button {{
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      cursor: pointer;
      min-height: 34px;
      padding: 6px 10px;
    }}

    button.primary {{
      background: var(--accent);
      border-color: var(--accent);
      color: white;
      font-weight: 650;
    }}

    button.primary:hover {{
      background: var(--accent-dark);
    }}

    button:disabled {{
      cursor: not-allowed;
      opacity: 0.55;
    }}

    select, input[type="text"] {{
      width: 100%;
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      padding: 5px 8px;
      color: var(--text);
    }}

    .toolbar {{
      display: grid;
      grid-template-columns: minmax(170px, 240px) minmax(170px, 260px) auto auto auto;
      gap: 8px;
      align-items: center;
      max-width: 100%;
    }}

    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
      color: var(--muted);
    }}

    .pill {{
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: white;
      padding: 3px 10px;
    }}

    .banner {{
      display: none;
      margin-bottom: 12px;
      border-radius: 7px;
      padding: 10px 12px;
      font-weight: 650;
    }}

    .banner.show {{
      display: block;
    }}

    .banner.success {{
      background: var(--success-bg);
      color: var(--success-text);
      border: 1px solid #9ad8b0;
    }}

    .banner.warn {{
      background: var(--warn-bg);
      color: var(--warn-text);
      border: 1px solid #f1d477;
    }}

    .banner.error {{
      background: var(--danger-bg);
      color: var(--danger-text);
      border: 1px solid #f3a5b0;
    }}

    .table-wrap {{
      overflow: auto;
      border: 1px solid var(--line);
      background: var(--panel);
    }}

    table {{
      border-collapse: collapse;
      width: 100%;
      min-width: 1180px;
    }}

    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 8px;
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }}

    th {{
      position: sticky;
      top: 84px;
      z-index: 10;
      background: #eef2f7;
      font-size: 12px;
      text-transform: uppercase;
      color: #4b5563;
    }}

    tr.saved {{
      background: var(--success-bg);
    }}

    .strategy-cell {{
      min-width: 370px;
      white-space: normal;
    }}

    .strategy-controls {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px;
    }}

    .row-status {{
      display: inline-flex;
      margin-top: 6px;
      min-height: 22px;
      align-items: center;
      color: var(--muted);
      font-size: 12px;
    }}

    .row-status.saved {{
      color: var(--success-text);
      font-weight: 700;
    }}

    .empty {{
      border: 1px solid var(--line);
      background: white;
      padding: 28px;
      text-align: center;
      color: var(--muted);
    }}

    @media (max-width: 900px) {{
      .toolbar {{
        grid-template-columns: 1fr;
      }}

      th {{
        top: 180px;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Missing Strategy Names</h1>
    <div class="toolbar">
      <select id="bulkStrategy"></select>
      <input id="bulkManual" type="text" placeholder="New strategy name">
      <button id="applySelected">Apply to selected</button>
      <button class="primary" id="saveChanges">Save updates</button>
      <button id="closeDashboard">Close dashboard</button>
    </div>
  </header>

  <main>
    <div id="banner" class="banner"></div>
    <div class="meta">
      <span class="pill" id="inputFile">Loading file...</span>
      <span class="pill" id="totalRows">Rows: 0</span>
      <span class="pill" id="missingRows">Missing Strategy_Name: 0</span>
      <span class="pill" id="dirtyRows">Unsaved changes: 0</span>
    </div>
    <div id="dashboardBody"></div>
  </main>

  <script>
    const displayColumns = {columns_json};
    let rows = [];
    let strategies = [];
    let dirtyRows = new Set();
    let savedRows = new Set();

    function escapeHtml(value) {{
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }}

    function strategyOptions(selectedValue = "") {{
      const selected = String(selectedValue || "");
      const options = ['<option value="">Select strategy...</option>'];
      for (const strategy of strategies) {{
        const isSelected = strategy === selected ? " selected" : "";
        options.push(`<option value="${{escapeHtml(strategy)}}"${{isSelected}}>${{escapeHtml(strategy)}}</option>`);
      }}
      return options.join("");
    }}

    function setBanner(message, kind = "success") {{
      const banner = document.getElementById("banner");
      banner.className = `banner show ${{kind}}`;
      banner.textContent = message;
    }}

    function clearBanner() {{
      const banner = document.getElementById("banner");
      banner.className = "banner";
      banner.textContent = "";
    }}

    function currentStrategyFor(rowId) {{
      const manual = document.querySelector(`[data-manual="${{rowId}}"]`)?.value.trim() || "";
      const selected = document.querySelector(`[data-select="${{rowId}}"]`)?.value.trim() || "";
      return manual || selected;
    }}

    function refreshCounts() {{
      document.getElementById("dirtyRows").textContent = `Unsaved changes: ${{dirtyRows.size}}`;
      document.getElementById("saveChanges").disabled = dirtyRows.size === 0;
    }}

    function markDirty(rowId) {{
      if (!savedRows.has(rowId)) {{
        dirtyRows.add(rowId);
      }}
      refreshCounts();
      clearBanner();
    }}

    function renderBulkStrategySelect() {{
      document.getElementById("bulkStrategy").innerHTML = strategyOptions("");
    }}

    function renderTable() {{
      const body = document.getElementById("dashboardBody");

      if (!rows.length) {{
        body.innerHTML = '<div class="empty">No rows are missing Strategy_Name in this file.</div>';
        refreshCounts();
        return;
      }}

      const headers = [
        '<th><input type="checkbox" id="selectAll" aria-label="Select all rows"></th>',
        ...displayColumns.map(column => `<th>${{escapeHtml(column)}}</th>`)
      ].join("");

      const htmlRows = rows.map(row => {{
        const rowId = Number(row.row_id);
        const cells = displayColumns.map(column => {{
          if (column === "Strategy_Name") {{
            return `<td class="strategy-cell">
              <div class="strategy-controls">
                <select data-select="${{rowId}}">${{strategyOptions(row[column])}}</select>
                <input data-manual="${{rowId}}" type="text" placeholder="Manual strategy name" value="">
              </div>
              <span class="row-status" data-status="${{rowId}}">Needs strategy</span>
            </td>`;
          }}
          return `<td>${{escapeHtml(row[column])}}</td>`;
        }}).join("");

        return `<tr data-row="${{rowId}}">
          <td><input type="checkbox" data-check="${{rowId}}" aria-label="Select row ${{rowId}}"></td>
          ${{cells}}
        </tr>`;
      }}).join("");

      body.innerHTML = `<div class="table-wrap"><table><thead><tr>${{headers}}</tr></thead><tbody>${{htmlRows}}</tbody></table></div>`;

      document.getElementById("selectAll").addEventListener("change", event => {{
        document.querySelectorAll("[data-check]").forEach(box => {{
          box.checked = event.target.checked;
        }});
      }});

      function clearSavedState(rowId) {{
        savedRows.delete(rowId);
        document.querySelector(`[data-row="${{rowId}}"]`)?.classList.remove("saved");
        const status = document.querySelector(`[data-status="${{rowId}}"]`);
        if (status) {{
          status.textContent = "Unsaved";
          status.className = "row-status";
        }}
      }}

      document.querySelectorAll("[data-select], [data-manual]").forEach(input => {{
        input.addEventListener("change", event => {{
          const rowId = Number(event.target.dataset.select || event.target.dataset.manual);
          clearSavedState(rowId);
          markDirty(rowId);
        }});
        input.addEventListener("input", event => {{
          const rowId = Number(event.target.dataset.select || event.target.dataset.manual);
          clearSavedState(rowId);
          markDirty(rowId);
        }});
      }});

      refreshCounts();
    }}

    async function loadData() {{
      const response = await fetch("/api/data");
      if (!response.ok) {{
        throw new Error(await response.text());
      }}

      const data = await response.json();
      rows = data.rows;
      strategies = data.strategies;
      dirtyRows = new Set();
      savedRows = new Set();

      document.getElementById("inputFile").textContent = data.input_file;
      document.getElementById("totalRows").textContent = `Rows: ${{data.total_rows}}`;
      document.getElementById("missingRows").textContent = `Missing Strategy_Name: ${{data.missing_count}}`;
      renderBulkStrategySelect();
      renderTable();
    }}

    function selectedRowIds() {{
      return Array.from(document.querySelectorAll("[data-check]:checked"))
        .map(box => Number(box.dataset.check));
    }}

    document.getElementById("applySelected").addEventListener("click", () => {{
      const selected = document.getElementById("bulkStrategy").value.trim();
      const manual = document.getElementById("bulkManual").value.trim();
      const strategy = manual || selected;
      const ids = selectedRowIds();

      if (!strategy) {{
        setBanner("Choose an existing strategy or enter a new one first.", "warn");
        return;
      }}

      if (!ids.length) {{
        setBanner("Select at least one row before applying a strategy.", "warn");
        return;
      }}

      ids.forEach(rowId => {{
        const select = document.querySelector(`[data-select="${{rowId}}"]`);
        const input = document.querySelector(`[data-manual="${{rowId}}"]`);
        if (strategies.includes(strategy)) {{
          select.value = strategy;
          input.value = "";
        }} else {{
          select.value = "";
          input.value = strategy;
        }}
        savedRows.delete(rowId);
        document.querySelector(`[data-row="${{rowId}}"]`)?.classList.remove("saved");
        const status = document.querySelector(`[data-status="${{rowId}}"]`);
        if (status) {{
          status.textContent = "Unsaved";
          status.className = "row-status";
        }}
        dirtyRows.add(rowId);
      }});

      refreshCounts();
      setBanner(`Applied ${{strategy}} to ${{ids.length}} selected row(s). Save to write the CSV.`, "warn");
    }});

    document.getElementById("saveChanges").addEventListener("click", async () => {{
      const updates = Array.from(dirtyRows).map(rowId => ({{
        row_id: rowId,
        strategy_name: currentStrategyFor(rowId),
      }})).filter(update => update.strategy_name);

      if (!updates.length) {{
        setBanner("No strategy names are ready to save yet.", "warn");
        return;
      }}

      const response = await fetch("/api/save", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ updates }}),
      }});

      if (!response.ok) {{
        setBanner(await response.text(), "error");
        return;
      }}

      const result = await response.json();
      updates.forEach(update => {{
        const rowId = Number(update.row_id);
        dirtyRows.delete(rowId);
        savedRows.add(rowId);
        document.querySelector(`[data-row="${{rowId}}"]`)?.classList.add("saved");
        const status = document.querySelector(`[data-status="${{rowId}}"]`);
        if (status) {{
          status.textContent = "Saved";
          status.className = "row-status saved";
        }}
      }});

      strategies = result.strategies;
      renderBulkStrategySelect();
      document.querySelectorAll("[data-select]").forEach(select => {{
        const rowId = Number(select.dataset.select);
        select.innerHTML = strategyOptions(currentStrategyFor(rowId));
      }});
      document.getElementById("missingRows").textContent = `Missing Strategy_Name: ${{result.remaining_missing}}`;
      refreshCounts();
      setBanner(`Saved ${{result.saved_rows}} row(s) to master_cleaned_tos_data.csv.`, "success");
    }});

    document.getElementById("closeDashboard").addEventListener("click", async () => {{
      if (dirtyRows.size && !confirm("You have unsaved strategy name changes. Close without saving?")) {{
        return;
      }}

      try {{
        await fetch("/api/shutdown", {{ method: "POST" }});
      }} catch (error) {{
        // The server may close before the browser receives the response.
      }}

      setBanner("Dashboard server is closing. You can close this browser tab.", "success");
      setTimeout(() => window.close(), 400);
    }});

    window.addEventListener("beforeunload", event => {{
      if (!dirtyRows.size) {{
        return;
      }}
      event.preventDefault();
      event.returnValue = "";
    }});

    loadData().catch(error => {{
      setBanner(error.message || String(error), "error");
    }});
  </script>
</body>
</html>
"""


class StrategyDashboardServer(HTTPServer):
    def __init__(
        self,
        server_address,
        handler_class,
        input_file,
        extra_strategy_names=None,
    ):
        super().__init__(server_address, handler_class)
        self.input_file = Path(input_file)
        self.extra_strategy_names = extra_strategy_names or []


class StrategyDashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, status=200, content_type="text/plain"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}

        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path

        try:
            if path == "/":
                self.send_text(dashboard_html(), content_type="text/html")
            elif path == "/api/data":
                self.send_json(
                    load_strategy_review_data(
                        self.server.input_file,
                        self.server.extra_strategy_names,
                    )
                )
            else:
                self.send_text("Not found", status=404)
        except Exception as exc:
            self.send_text(str(exc), status=500)

    def do_POST(self):
        path = urlparse(self.path).path

        try:
            if path == "/api/save":
                payload = self.read_json_body()
                result = apply_strategy_updates(
                    self.server.input_file,
                    payload.get("updates", []),
                    self.server.extra_strategy_names,
                )
                self.send_json(result)
            elif path == "/api/shutdown":
                self.send_json({"status": "closing"})
                threading.Thread(
                    target=self.server.shutdown,
                    daemon=True,
                ).start()
            else:
                self.send_text("Not found", status=404)
        except Exception as exc:
            self.send_text(str(exc), status=500)


def serve_dashboard(
    input_file,
    host=DEFAULT_HOST,
    port=DEFAULT_PORT,
    open_dashboard=True,
    extra_strategy_names=None,
):
    server = StrategyDashboardServer(
        (host, port),
        StrategyDashboardHandler,
        input_file,
        extra_strategy_names,
    )
    url = f"http://{host}:{port}/"

    print(f"Reviewing missing strategy names in {input_file}", flush=True)
    print(f"Dashboard: {url}", flush=True)
    if open_dashboard:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.", flush=True)
    finally:
        server.server_close()


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Review rows in master_cleaned_tos_data.csv that are missing "
            "Strategy_Name and save manual assignments from a local dashboard."
        )
    )
    parser.add_argument(
        "--input-file",
        default=str(DEFAULT_INPUT_FILE),
        help="Path to master_cleaned_tos_data.csv.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Host for the local dashboard server.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Port for the local dashboard server.",
    )
    parser.add_argument(
        "--no-open-dashboard",
        action="store_true",
        help="Start the dashboard server without opening a browser window.",
    )
    parser.add_argument(
        "--valid-strategy",
        action="append",
        default=[],
        help=(
            "Additional valid Strategy_Name option. Repeat for multiple "
            "new strategies."
        ),
    )

    return parser.parse_args()


def main():
    args = parse_args()
    serve_dashboard(
        Path(args.input_file),
        host=args.host,
        port=args.port,
        open_dashboard=not args.no_open_dashboard,
        extra_strategy_names=args.valid_strategy,
    )


if __name__ == "__main__":
    main()
