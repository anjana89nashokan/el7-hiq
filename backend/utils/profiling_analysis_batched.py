# utils/profiling_analysis_batched.py
"""
Multi-Pass LLM Analysis for Profiling Results - Production BSA Tool

This module enables accurate, complete analysis of 100+ tables by splitting
the LLM analysis into multiple focused batches, avoiding token limits while
maintaining full accuracy.

Architecture:
- Batch Size: 10-15 tables per LLM call (~80K-150K tokens per call)
- NO SAMPLING: Includes ALL columns for ALL tables
- Token Budget: Each batch stays under 200K tokens (well within 1M limit)
- Chat Support: Generates searchable index for followup questions
"""

import json
import logging
from typing import Dict, Any, List, Optional
from statistics import mean, median

logger = logging.getLogger(__name__)

logger.warning(
    "[PROFILING TOOL EXECUTED] MODE=BATCHED TRIGERREDDD| file=profiling_functions_batched.py"
)

def build_batch_profiling_analysis_prompt(
    batch_tables: List[Dict[str, Any]],
    batch_index: int,
    total_batches: int
) -> str:
    """
    Build LLM prompt for analyzing a BATCH of tables (10-15 tables).

    CRITICAL DIFFERENCES from profiling_analysis.py:
    - Includes ALL columns for each table (no 15-column limit)
    - Focused on detailed per-table analysis
    - Token budget: 10-15 tables × 8K-12K = 80K-150K tokens (safe)

    Args:
        batch_tables: List of complete table profiling results (10-15 tables)
        batch_index: Current batch number (1-based)
        total_batches: Total number of batches

    Returns:
        LLM prompt string for this batch
    """

    num_tables = len(batch_tables)

    # Build comprehensive table summaries with ALL columns
    tables_detail = []

    for idx, table in enumerate(batch_tables, 1):
        table_ref = table.get("table_reference", "unknown")
        table_name = table_ref.split(".")[-1] if "." in table_ref else table_ref

        column_analysis = table.get("column_analysis", {})
        enhanced_analysis = table.get("enhanced_analysis", {})
        quality_score = table.get("data_quality_score", 0)
        total_rows = table.get("table_summary", {}).get("total_rows", 0)

        # Build detailed column table with ALL columns
        columns_markdown = []
        columns_markdown.append("| Column | Type | Null % | Unique % | Default % | PK | FK | Notes |")
        columns_markdown.append("|--------|------|--------|----------|-----------|----|----|-------|")

        for col_name, col_data in column_analysis.items():
            data_type = col_data.get("data_type", "UNKNOWN")
            null_pct = round(col_data.get("null_percentage", 0), 1)
            unique_pct = round(col_data.get("uniqueness_percentage", 0), 1)

            # Get default percentage
            default_analysis = table.get("default_value_analysis", {})
            default_pct = 0
            if col_name in default_analysis:
                default_pct = round(default_analysis[col_name].get("default_pct", 0), 1)

            # PK/FK indicators
            pk = "✅" if col_data.get("primary_key_candidate") else "❌"
            fk = "✅" if col_data.get("foreign_key_candidate") else "❌"

            # Notes
            notes = []
            if null_pct > 50:
                notes.append(f"HIGH NULLS")
            if unique_pct >= 95:
                notes.append("PK Candidate")
            if default_pct > 80:
                notes.append(f"Dominant value")

            notes_str = ", ".join(notes) if notes else "-"

            columns_markdown.append(
                f"| {col_name} | {data_type} | {null_pct}% | {unique_pct}% | {default_pct}% | {pk} | {fk} | {notes_str} |"
            )

        # Enhanced analysis summary
        enhanced_summary = ""
        if enhanced_analysis.get("available"):
            table_context = enhanced_analysis.get("table_context", {})
            detected_level = table_context.get("detected_level", "unknown")
            confidence = table_context.get("confidence", 0) * 100

            enhanced_summary = f"""
**Table Context:** {detected_level} (confidence: {confidence:.0f}%)
**Business Context:** {table_context.get("business_context", "Not detected")}
"""

            # PK recommendations
            pk_recs = enhanced_analysis.get("primary_key_recommendations", [])
            if pk_recs:
                top_pk = pk_recs[0]
                enhanced_summary += f"""
**Recommended Primary Key:** {top_pk.get('column_name', 'None')} ({top_pk.get('confidence', 'UNKNOWN')} confidence, {top_pk.get('uniqueness_percentage', 0):.1f}% unique)
**Reason:** {top_pk.get('reason', 'Statistical analysis')}
"""

            # Composite key recommendations
            composite_recs = enhanced_analysis.get("composite_key_recommendations", {})
            two_col = composite_recs.get("two_column", [])
            if two_col:
                top_composite = two_col[0]
                enhanced_summary += f"""
**Composite Key Option:** [{', '.join(top_composite.get('columns', []))}] ({top_composite.get('uniqueness_percentage', 0):.1f}% unique)
**Business Meaning:** {top_composite.get('business_meaning', 'Combined uniqueness')}
"""

        # Assemble table detail
        table_detail = f"""
### Table {idx}: {table_name}

**Reference:** `{table_ref}`

| Metric | Value |
|--------|-------|
| **Data Quality Score** | **{quality_score * 100:.1f}%** |
| Total Rows | {total_rows:,} |
| Total Columns | {len(column_analysis)} |

{enhanced_summary}

**Column Analysis (ALL {len(column_analysis)} columns):**

{chr(10).join(columns_markdown)}

**Key Observations:**
"""

        # Add observations for critical issues
        critical_issues = []
        for col_name, col_data in column_analysis.items():
            null_pct = col_data.get("null_percentage", 0)
            unique_pct = col_data.get("uniqueness_percentage", 0)

            if null_pct > 50:
                critical_issues.append(f"- ⚠️ **{col_name}**: {null_pct:.1f}% null rate - investigate data collection")

            if col_data.get("primary_key_candidate") and unique_pct < 100:
                critical_issues.append(f"- 🔑 **{col_name}**: PK candidate but {100-unique_pct:.1f}% duplicates exist")

        if not critical_issues:
            critical_issues.append("- ✅ No critical data quality issues detected")

        table_detail += "\n" + "\n".join(critical_issues)
        table_detail += "\n\n---\n"

        tables_detail.append(table_detail)

    # Build final prompt
    prompt = f"""You are analyzing batch {batch_index} of {total_batches} from a comprehensive data profiling analysis.

This batch contains {num_tables} tables with complete column-level analysis, including statistical metrics and enhanced analysis from LLM-powered context detection.

**CRITICAL INSTRUCTIONS:**

1. **Response Format:**
   - Use markdown format with clear headings and proper table syntax
   - Start with: ## Batch {batch_index}/{total_batches}: Detailed Table Analysis
   - For EACH table, you MUST include:
     * Table overview section with quality score and table context (detected level, confidence)
     * **The complete column analysis table** (copy the exact table provided below for each table)
     * Enhanced Analysis summary (Primary Key recommendations, Composite Key options from enhanced_analysis)
     * Key observations and critical issues from both statistical and enhanced analysis
     * Actionable recommendations based on table context and business meaning
   - Use **bold** for critical findings and emojis for visual clarity (⚠️, ✅, 🔑, 🔗)

2. **IMPORTANT: Column Analysis Tables**
   - For each table analyzed below, you will see a markdown table with all columns
   - You MUST include that EXACT table in your response for that table
   - The table format is:
     | Column | Type | Null % | Unique % | Default % | PK | FK | Notes |
     |--------|------|--------|----------|-----------|----|----|-------|
   - Copy ALL rows from the provided table into your response
   - This ensures the UI displays proper markdown tables matching the profiling_functions.py output structure

3. **Analysis Requirements for EACH Table:**
   - **Data Quality Assessment**: Evaluate the quality score and explain what it means for business operations
   - **Table Context**: Reference the detected_level (transaction/reference/lookup/fact/dimension) and confidence from enhanced_analysis
   - **Column Analysis**: Include the complete table, then highlight critical issues
   - **Primary Key Validation**:
     * Reference enhanced_analysis.primary_key_recommendations (merged statistical + LLM analysis)
     * Assess confidence level (HIGH/MEDIUM/LOW) and uniqueness percentage
     * Explain the business reasoning behind the recommendation
   - **Composite Key Options**:
     * Reference enhanced_analysis.composite_key_recommendations (two_column, three_column, four_column)
     * Explain business_meaning for each suggested composite key
     * Note uniqueness_percentage from BigQuery validation
   - **Business Context**: Explain table's role based on table_context.business_context
   - **Actionable Recommendations**: Specific steps to improve data quality, aligned with table's business purpose

4. **Focus Areas (Aligned with profiling_functions.py Enhanced Analysis):**
   - Critical data quality issues requiring immediate attention
   - Primary key recommendations from enhanced_analysis (not just statistical candidates)
   - Composite key options validated by BigQuery with business context
   - Columns with unusual patterns (high nulls, dominant values, default percentages)
   - Foreign key candidates and relationship implications for data integration
   - Table context insights (transaction vs reference vs lookup patterns)

5. **Tone:**
   - Professional, actionable insights for Business System Analysts
   - Explain technical findings in business terms using table_context
   - Prioritize findings by impact and urgency
   - Reference specific metrics from enhanced_analysis (confidence scores, uniqueness percentages)

---

## Tables in This Batch

{chr(10).join(tables_detail)}

---

**Generate your detailed analysis now, following the structure above. Remember to include the complete column analysis table for EACH table:**
"""

    # Estimate token count
    estimated_tokens = len(prompt.split()) * 1.3
    logger.info(
        f"Built batch analysis prompt: Batch {batch_index}/{total_batches}, "
        f"{num_tables} tables, ~{int(estimated_tokens)} tokens"
    )

    return prompt


def build_aggregate_profiling_summary_prompt(
    all_tables: List[Dict[str, Any]],
    batch_analyses: List[str]
) -> str:
    """
    Build final cross-table summary prompt after all batches are analyzed.

    This generates the executive summary with cross-table insights.

    Args:
        all_tables: Complete list of all profiled tables
        batch_analyses: List of markdown analyses from each batch

    Returns:
        LLM prompt for final summary
    """

    total_tables = len(all_tables)
    total_columns = sum(len(t.get("column_analysis", {})) for t in all_tables)

    # Calculate cross-table statistics
    quality_scores = [t.get("data_quality_score", 0) * 100 for t in all_tables]
    avg_quality = mean(quality_scores) if quality_scores else 0

    # Find tables with issues
    low_quality_tables = [
        t.get("table_reference", "unknown").split(".")[-1]
        for t in all_tables
        if t.get("data_quality_score", 0) * 100 < 70
    ]

    # Count critical issues
    high_null_columns = 0
    for table in all_tables:
        for col_data in table.get("column_analysis", {}).values():
            if col_data.get("null_percentage", 0) > 50:
                high_null_columns += 1

    # Tables with PK recommendations
    tables_with_pks = sum(
        1 for t in all_tables
        if t.get("enhanced_analysis", {}).get("primary_key_recommendations", [])
    )

    prompt = f"""You have completed analyzing {total_tables} tables across {len(batch_analyses)} batches.

Now generate a comprehensive EXECUTIVE SUMMARY that synthesizes insights across all tables.

**Dataset Overview:**
- Total Tables: {total_tables}
- Total Columns: {total_columns}
- Average Quality Score: {avg_quality:.1f}%
- Tables with Quality Issues (<70%): {len(low_quality_tables)}
- Columns with High Null Rates (>50%): {high_null_columns}
- Tables with Primary Key Candidates: {tables_with_pks}/{total_tables}

**Your Executive Summary Must Include:**

## 📊 Executive Summary

### Overall Data Quality Assessment
- Overall health of the dataset
- Distribution of quality scores
- Key strengths and weaknesses

### Critical Issues Requiring Immediate Attention
- Top 5-10 most urgent data quality issues
- Impact assessment for each issue
- Recommended priority for resolution

### Cross-Table Patterns
- Common data quality patterns across tables
- Naming conventions and consistency
- Relationship and foreign key patterns
- Data type distribution and standardization

### Primary Key Analysis
- Tables with strong PK candidates vs. those needing composite keys
- Tables lacking unique identifiers
- Composite key recommendations summary

### Recommendations by Priority

**HIGH PRIORITY (Immediate Action):**
- [List critical issues with specific table.column references]

**MEDIUM PRIORITY (Within Sprint):**
- [List important improvements]

**LOW PRIORITY (Technical Debt):**
- [List minor enhancements]

### Next Steps for Business System Analysts
- Specific action items with table references
- Data collection improvements needed
- Schema modifications to consider

---

**Previous Batch Analyses for Context:**

{chr(10).join(f"### Batch {i+1}:{chr(10)}{analysis}{chr(10)}---{chr(10)}" for i, analysis in enumerate(batch_analyses))}

---

**Generate the executive summary now, synthesizing insights across all {total_tables} tables:**
"""

    estimated_tokens = len(prompt.split()) * 1.3
    logger.info(f"Built aggregate summary prompt: {total_tables} tables, ~{int(estimated_tokens)} tokens")

    return prompt


def build_searchable_index(all_tables: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build searchable index for fast chat followup queries.

    This enables instant answers to questions like:
    - "Which tables have high null rates?"
    - "Show me all PK recommendations"
    - "Which columns are foreign key candidates?"

    Args:
        all_tables: Complete list of profiled tables

    Returns:
        Searchable index dictionary
    """

    index = {
        "tables_by_quality": {
            "high": [],      # >= 85%
            "medium": [],    # 70-85%
            "low": []        # < 70%
        },
        "high_null_columns": [],
        "pk_recommendations": {},
        "composite_key_recommendations": {},
        "fk_candidates": [],
        "tables_by_context": {},
        "critical_issues": [],
        "table_summary": {}
    }

    for table in all_tables:
        table_ref = table.get("table_reference", "unknown")
        table_name = table_ref.split(".")[-1] if "." in table_ref else table_ref
        quality_score = table.get("data_quality_score", 0) * 100
        column_analysis = table.get("column_analysis", {})
        enhanced = table.get("enhanced_analysis", {})

        # Quality categorization
        if quality_score >= 85:
            index["tables_by_quality"]["high"].append(table_name)
        elif quality_score >= 70:
            index["tables_by_quality"]["medium"].append(table_name)
        else:
            index["tables_by_quality"]["low"].append(table_name)

        # High null columns
        for col_name, col_data in column_analysis.items():
            null_pct = col_data.get("null_percentage", 0)
            if null_pct > 50:
                index["high_null_columns"].append({
                    "table": table_name,
                    "column": col_name,
                    "null_percentage": round(null_pct, 1),
                    "severity": "CRITICAL" if null_pct > 80 else "HIGH"
                })

        # PK recommendations
        if enhanced.get("available"):
            pk_recs = enhanced.get("primary_key_recommendations", [])
            if pk_recs:
                top_pk = pk_recs[0]
                index["pk_recommendations"][table_name] = {
                    "column": top_pk.get("column_name"),
                    "confidence": top_pk.get("confidence"),
                    "uniqueness": top_pk.get("uniqueness_percentage")
                }

            # Composite keys
            composite_recs = enhanced.get("composite_key_recommendations", {})
            if composite_recs.get("two_column") or composite_recs.get("three_column"):
                index["composite_key_recommendations"][table_name] = {
                    "two_column": composite_recs.get("two_column", []),
                    "three_column": composite_recs.get("three_column", [])
                }

            # Table context
            table_context = enhanced.get("table_context", {})
            detected_level = table_context.get("detected_level", "unknown")
            if detected_level not in index["tables_by_context"]:
                index["tables_by_context"][detected_level] = []
            index["tables_by_context"][detected_level].append(table_name)

        # FK candidates
        for col_name, col_data in column_analysis.items():
            if col_data.get("foreign_key_candidate"):
                index["fk_candidates"].append({
                    "table": table_name,
                    "column": col_name,
                    "uniqueness": round(col_data.get("uniqueness_percentage", 0), 1)
                })

        # Critical issues
        for col_name, col_data in column_analysis.items():
            null_pct = col_data.get("null_percentage", 0)
            if null_pct > 80:
                index["critical_issues"].append({
                    "table": table_name,
                    "column": col_name,
                    "issue": f"{null_pct:.1f}% null rate",
                    "type": "DATA_COMPLETENESS"
                })

        # Table summary for quick reference
        index["table_summary"][table_name] = {
            "quality_score": round(quality_score, 1),
            "total_rows": table.get("table_summary", {}).get("total_rows", 0),
            "total_columns": len(column_analysis),
            "reference": table_ref
        }

    # Sort by severity
    index["high_null_columns"].sort(key=lambda x: x["null_percentage"], reverse=True)
    index["critical_issues"].sort(key=lambda x: float(x["issue"].split("%")[0]), reverse=True)

    logger.info(
        f"Built searchable index: {len(index['pk_recommendations'])} PK recs, "
        f"{len(index['high_null_columns'])} high null columns, "
        f"{len(index['critical_issues'])} critical issues"
    )

    return index
