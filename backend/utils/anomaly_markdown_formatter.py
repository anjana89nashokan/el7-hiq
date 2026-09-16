# utils/anomaly_markdown_formatter.py
"""
Deterministic markdown formatter for anomaly analysis results.

Used by /send-stream so anomaly insights stream just like profiling/relationship
without firing a second LLM call.
"""

from __future__ import annotations

from typing import Dict, Iterable, List


def _severity_table(severity: Dict[str, int]) -> str:
    headers = "| Severity | Count |\n|----------|-------|\n"
    rows = []
    for level in ("high", "medium", "low"):
        rows.append(f"| {level.title()} | {severity.get(level, 0)} |")
    return headers + "\n".join(rows)


def _top_tables(table_reports: Dict[str, Dict], limit: int = 5) -> List[Dict]:
    tables = list(table_reports.values())
    tables.sort(key=lambda t: t.get("total_anomalies_found", 0), reverse=True)
    return tables[:limit]


def _table_row(report: Dict) -> str:
    severity = report.get("anomaly_summary", {}).get("severity_distribution", {})
    high = severity.get("high", 0)
    medium = severity.get("medium", 0)
    low = severity.get("low", 0)
    return (
        f"| {report.get('table_name','-')} | "
        f"{report.get('anomaly_summary', {}).get('columns_with_anomalies', 0)} | "
        f"{report.get('total_anomalies_found', 0)} | "
        f"{high}/{medium}/{low} |"
    )


def generate_anomaly_markdown(tool_response: Dict) -> str:
    summary = tool_response.get("summary_statistics", {})
    severity = summary.get("severity_distribution", {})
    tables_analyzed = summary.get("total_tables_analyzed") or tool_response.get("tables_analyzed", 0)
    overall_score = summary.get("overall_data_quality_score", 0)
    total_anomalies = summary.get("total_anomalies", 0)

    md: List[str] = [
        "# 🔍 Data Anomaly Insights",
        "",
        "## Executive Summary",
        f"- **Tables analyzed:** {tables_analyzed}",
        f"- **Total anomalies detected:** {total_anomalies}",
        f"- **Overall data quality score:** {overall_score * 100:.1f}%",
        f"- **Processing mode:** {tool_response.get('processing_mode', 'bigquery_batched')}",
    ]

    md.extend(
        [
            "",
            "## Severity Snapshot",
            _severity_table(severity or {}),
        ]
    )

    batch_details = tool_response.get("batch_details") or []
    if batch_details:
        avg_time = sum(b.get("duration_seconds", 0) for b in batch_details) / max(len(batch_details), 1)
        md.extend(
            [
                "",
                "## Processing Stats",
                f"- Batches processed: **{len(batch_details)}**",
                f"- Avg batch duration: **{avg_time:.2f}s**",
                f"- Tables processed: **{tool_response.get('processing_stats', {}).get('tables_processed', 0)}**",
            ]
        )

    reports = tool_response.get("table_anomaly_reports", {})
    top_tables = _top_tables(reports)
    if top_tables:
        md.extend(
            [
                "",
                "## Top Impacted Tables",
                "| Table | Columns w/ Issues | Total Anomalies | High/Med/Low |",
                "|-------|-------------------|-----------------|--------------|",
            ]
        )
        for report in top_tables:
            md.append(_table_row(report))

    return "\n".join(md).strip()


def chunk_markdown(markdown_text: str, chunk_size: int = 500) -> Iterable[str]:
    """
    Split markdown into chunks so SSE can stream them incrementally.
    """
    if not markdown_text:
        return []

    paragraphs = [p.strip() for p in markdown_text.split("\n\n") if p.strip()]
    chunk = ""
    for paragraph in paragraphs:
        if len(chunk) + len(paragraph) + 2 > chunk_size and chunk:
            yield chunk + "\n\n"
            chunk = paragraph
        else:
            chunk = f"{chunk}\n\n{paragraph}" if chunk else paragraph
    if chunk:
        yield chunk + "\n\n"

