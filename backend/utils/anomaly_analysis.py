import json

def _flatten_column_anomalies(report: dict) -> list:
    """Flatten column_anomalies into a list of rows ready for LLM rendering."""
    rows = []
    column_anomalies = report.get("column_anomalies") or {}
    table_name = report.get("table_name", "")
    for column_name, anomaly_list in column_anomalies.items():
        if not isinstance(anomaly_list, list):
            continue
        for anomaly in anomaly_list:
            if not isinstance(anomaly, dict):
                continue
            rows.append({
                "table": table_name,
                "column": column_name,
                "anomaly_type": anomaly.get("anomaly_type"),
                "severity": anomaly.get("severity", "medium"),
                "affected_count": anomaly.get("affected_count", 0),
                "affected_percentage": anomaly.get("affected_percentage", 0.0),
                "total_records_evaluated": anomaly.get("total_records_evaluated"),
                "human_readable_explanation": anomaly.get("human_readable_explanation") or anomaly.get("issue"),
                "expected_pattern": anomaly.get("expected_pattern"),
                "observed_pattern": anomaly.get("observed_pattern"),
                "dominant_examples": anomaly.get("dominant_examples") or [],
                "anomaly_examples": anomaly.get("examples") or [],
            })
    return rows

def build_anomaly_analysis_prompt(tool_response: dict) -> str:
    """
    Compress the anomaly tool output so Gemini can produce a concise, insightful report.
    """

    summary = tool_response.get("summary_statistics", {})
    severity = summary.get("severity_distribution", {})
    processing_stats = tool_response.get("processing_stats", {})
    table_reports = tool_response.get("table_anomaly_reports", {}) or {}

    top_tables = sorted(
        table_reports.values(),
        key=lambda r: r.get("total_anomalies_found", 0),
        reverse=True
    )[:5]

    compact_payload = {
        "tables_analyzed": summary.get("total_tables_analyzed") or tool_response.get("tables_analyzed", 0),
        "total_anomalies": summary.get("total_anomalies", 0),
        "severity_distribution": severity,
        "processing_mode": tool_response.get("processing_mode", "bigquery_batched"),
        "processing_stats": processing_stats,
        "top_tables": [
            {
                "table_name": report.get("table_name"),
                "total_anomalies_found": report.get("total_anomalies_found"),
                "columns_with_anomalies": report.get("anomaly_summary", {}).get("columns_with_anomalies"),
                "severity_distribution": report.get("anomaly_summary", {}).get("severity_distribution"),
                "column_anomaly_rows": _flatten_column_anomalies(report),
                "table_level_anomalies": report.get("table_level_anomalies"),
            }
            for report in top_tables
        ],
    }

#     instructions = """
# You are DataMap Copilot's anomaly expert. Produce a markdown report with:
# 1. Executive summary (tables analyzed, total anomalies, overall score).
# 2. Severity snapshot (high/medium/low).
# 3. Table hotspots (per table: anomaly types, impacted columns, severity, sample notes).
# 4. Actionable recommendations for remediation.

# Keep the tone business-friendly and highlight concrete follow-ups.
# """

    instructions = """
**CRITICAL: Data Anomaly Analysis — BSA Report Formatting Rules**

Produce a clean, business-readable Markdown report. Every anomaly row MUST show:
- How many records are affected (count and %)
- What the normal/expected pattern looks like with real examples
- What the anomalous records look like with real examples
- A plain-English explanation a BSA can act on

NEVER show raw pattern codes (AAA, NNN, AAAA.AAA or similar abstract strings) in the output.
NEVER say "unusual pattern detected" without explaining what is unusual and showing examples.

---

# Data Anomaly Analysis Report

| Metric | Value |
|:--------------------------|:----------------|
| Tables Analyzed | [tables_analyzed] |
| Total Anomalies Detected | [total_anomalies] |
| Processing Mode | [processing_mode] |

**Severity Distribution**
| Severity | Count |
|:-----------|:--------:|
| High | [severity_distribution.high] |
| Medium | [severity_distribution.medium] |
| Low | [severity_distribution.low] |

---

## Anomaly Details

For each table in `top_tables`, use `column_anomaly_rows` as the source data.
Render one row per anomaly using this table structure:

| Table | Column | Anomaly Type | Affected Records | Affected % | Most Values Look Like | Anomalous Records Look Like | Explanation |
|:------|:-------|:-------------|----------------:|-----------:|:----------------------|:----------------------------|:------------|

Column mapping from `column_anomaly_rows`:
- **Affected Records** → `affected_count`
- **Affected %** → `affected_percentage`
- **Most Values Look Like** → `expected_pattern`, with examples from `dominant_examples` (e.g. "10-digit numeric value, e.g. '1689119703', '1003438599'")
- **Anomalous Records Look Like** → `observed_pattern`, with examples from `anomaly_examples` (e.g. "61-character mixed value, e.g. 'WMFL_20250723...'")
- **Explanation** → use `human_readable_explanation` verbatim — it is already in plain English

---

## Table-Level Issues

| Table | Issue | Severity | Details |
|:------|:------|:---------|:--------|

Source: `table_level_anomalies` from each top table.
Only include this section if at least one table has non-empty `table_level_anomalies`.
If all `table_level_anomalies` arrays are empty, omit the Table-Level Issues section entirely.

---

## Business Recommendations

| Priority | Recommendation | Scope |
|:---------|:---------------|:------|

Derive from high-severity anomalies. Be specific — name the table and column.

---

If total_anomalies == 0:
**Data Quality Analysis Report — All Clear!**
No anomalies detected. Dataset appears clean and consistent.

If status == "error":
**Data Quality Analysis Failed**
| Error Message | [error_message] |
| Suggested Fix | Verify data source connection, permissions, or dataset size |

IMPORTANT NOTES:
- YOUR RESPONSES MUST ALWAYS ADHERE TO THIS FORMATTING AND STRUCTURE IN MARKDOWN. DO NOT DEVIATE.
- Always include affected_count and affected_percentage for every anomaly row.
- Always show dominant_examples (what most records look like) alongside anomaly_examples (what the bad records look like).
- Use human_readable_explanation as-is — do not paraphrase it into abstract terms.
                """

    return instructions + "\n\nTool Response (compressed JSON):\n" + json.dumps(compact_payload, indent=2)
