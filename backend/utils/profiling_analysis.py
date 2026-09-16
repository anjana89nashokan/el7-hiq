# utils/profiling_analysis.py
"""
LLM Analysis Prompt Builder for Profiling Results

Generates intelligent analysis prompts from profiling tool output.
Uses token compression (500K → 10K) for scalability with 100+ tables.
Follows profiling agent instructions for response formatting.
"""

import json
import logging
from typing import Dict, Any, List, Optional
from statistics import mean, median
from typing import List, Any, Dict, Optional
from pydantic import BaseModel
import json
from agents.models import ColumnAnalysisItem, ToolResultItem
from utils.profiling_artifact_store import load_profiling_report_json

logger = logging.getLogger(__name__)


def extract_summary_statistics(tool_response: dict) -> Dict[str, Any]:
    """
    Extract high-level summary statistics from profiling results.

    Compresses 500K tokens → ~2K tokens while preserving key insights.

    Args:
        tool_response: Full profiling tool response with all table results

    Returns:
        Compressed summary statistics dict
    """
    results = tool_response.get("result", [])

    if not results:
        return {
            "total_tables": 0,
            "total_columns": 0,
            "quality_score": 0,
            "type_distribution": {},
            "null_distribution": {},
            "uniqueness_distribution": {}
        }

    # Aggregate statistics
    total_tables = len(results)
    total_columns = 0
    all_data_types = []
    all_null_percentages = []
    all_uniqueness_percentages = []
    quality_scores = []
    tables_with_pks = 0
    tables_with_composite_keys = 0

    for table in results:
        # Column analysis
        column_analysis = table.get("column_analysis", {})
        total_columns += len(column_analysis)

        for col_name, col_data in column_analysis.items():
            # Data types
            data_type = col_data.get("data_type", "UNKNOWN")
            all_data_types.append(data_type)

            # Null rates
            null_pct = col_data.get("null_percentage", 0)
            all_null_percentages.append(null_pct)

            # Uniqueness
            uniqueness_pct = col_data.get("uniqueness_percentage", 0)
            all_uniqueness_percentages.append(uniqueness_pct)

        # Quality scores
        quality_score = table.get("data_quality_score", 0)
        if quality_score > 0:
            quality_scores.append(quality_score)

        # Enhanced analysis
        enhanced = table.get("enhanced_analysis", {})
        if enhanced.get("available"):
            pk_recs = enhanced.get("primary_key_recommendations", [])
            if pk_recs:
                tables_with_pks += 1

            composite_recs = enhanced.get("composite_key_recommendations", {})
            if composite_recs.get("two_column") or composite_recs.get("three_column"):
                tables_with_composite_keys += 1

    # Calculate distributions
    data_type_counts = {}
    for dt in all_data_types:
        data_type_counts[dt] = data_type_counts.get(dt, 0) + 1

    data_type_distribution = {
        dt: round(count / len(all_data_types) * 100, 1)
        for dt, count in sorted(data_type_counts.items(), key=lambda x: x[1], reverse=True)
    }

    # Null distribution
    null_buckets = {"0-10%": 0, "10-20%": 0, "20-50%": 0, "50%+": 0}
    for null_pct in all_null_percentages:
        if null_pct < 10:
            null_buckets["0-10%"] += 1
        elif null_pct < 20:
            null_buckets["10-20%"] += 1
        elif null_pct < 50:
            null_buckets["20-50%"] += 1
        else:
            null_buckets["50%+"] += 1

    # Uniqueness distribution
    uniqueness_buckets = {"0-25%": 0, "25-50%": 0, "50-75%": 0, "75-95%": 0, "95-100%": 0}
    for uniq_pct in all_uniqueness_percentages:
        if uniq_pct < 25:
            uniqueness_buckets["0-25%"] += 1
        elif uniq_pct < 50:
            uniqueness_buckets["25-50%"] += 1
        elif uniq_pct < 75:
            uniqueness_buckets["50-75%"] += 1
        elif uniq_pct < 95:
            uniqueness_buckets["75-95%"] += 1
        else:
            uniqueness_buckets["95-100%"] += 1

    # Calculate overall quality score
    avg_quality_score = round(mean(quality_scores), 1) if quality_scores else 0

    summary = {
        "total_tables": total_tables,
        "total_columns": total_columns,
        "avg_columns_per_table": round(total_columns / total_tables, 1),
        "quality_score": avg_quality_score,
        "tables_with_primary_keys": tables_with_pks,
        "tables_with_composite_keys": tables_with_composite_keys,
        "data_type_distribution": data_type_distribution,
        "null_distribution": null_buckets,
        "uniqueness_distribution": uniqueness_buckets,
        "avg_null_percentage": round(mean(all_null_percentages), 1) if all_null_percentages else 0,
        "median_null_percentage": round(median(all_null_percentages), 1) if all_null_percentages else 0,
        "avg_uniqueness": round(mean(all_uniqueness_percentages), 1) if all_uniqueness_percentages else 0,
    }

    logger.info(f"Extracted summary statistics: {total_tables} tables, {total_columns} columns, quality={avg_quality_score}")

    return summary


def identify_outlier_tables(tool_response: dict, max_outliers: int = 10) -> List[Dict[str, Any]]:
    """
    Identify outlier tables with unusual characteristics.

    Returns top N outliers by anomaly score.

    Args:
        tool_response: Full profiling tool response
        max_outliers: Maximum number of outliers to return

    Returns:
        List of outlier table summaries
    """
    results = tool_response.get("result", [])
    outliers = []

    for table in results:
        table_ref = table.get("table_reference", "")
        table_name = table_ref.split(".")[-1] if table_ref else "unknown"

        column_analysis = table.get("column_analysis", {})
        num_columns = len(column_analysis)

        # Calculate anomaly indicators
        null_rates = [col.get("null_percentage", 0) for col in column_analysis.values()]
        uniqueness_rates = [col.get("uniqueness_percentage", 0) for col in column_analysis.values()]

        avg_null = mean(null_rates) if null_rates else 0
        max_null = max(null_rates) if null_rates else 0
        high_null_cols = sum(1 for n in null_rates if n > 50)

        low_uniqueness_cols = sum(1 for u in uniqueness_rates if u < 10)

        quality_score = table.get("data_quality_score", 100)

        # Calculate anomaly score (higher = more anomalous)
        anomaly_score = 0
        anomaly_reasons = []

        # High null rates
        if avg_null > 30:
            anomaly_score += avg_null / 2
            anomaly_reasons.append(f"High avg null rate: {avg_null:.1f}%")

        if high_null_cols > 0:
            anomaly_score += high_null_cols * 5
            anomaly_reasons.append(f"{high_null_cols} columns with >50% nulls")

        # Low quality score
        if quality_score < 70:
            anomaly_score += (70 - quality_score)
            anomaly_reasons.append(f"Low quality score: {quality_score:.1f}")

        # Very wide tables
        if num_columns > 50:
            anomaly_score += (num_columns - 50) / 5
            anomaly_reasons.append(f"Very wide table: {num_columns} columns")

        # Low uniqueness
        if low_uniqueness_cols > num_columns * 0.3:
            anomaly_score += low_uniqueness_cols * 2
            anomaly_reasons.append(f"{low_uniqueness_cols} columns with low uniqueness")

        # Missing primary key
        enhanced = table.get("enhanced_analysis", {})
        if enhanced.get("available"):
            pk_recs = enhanced.get("primary_key_recommendations", [])
            if not pk_recs:
                anomaly_score += 10
                anomaly_reasons.append("No primary key candidates found")

        if anomaly_score > 10:  # Threshold for outlier
            outliers.append({
                "table_name": table_name,
                "table_reference": table_ref,
                "anomaly_score": round(anomaly_score, 1),
                "num_columns": num_columns,
                "avg_null_percentage": round(avg_null, 1),
                "max_null_percentage": round(max_null, 1),
                "high_null_columns": high_null_cols,
                "quality_score": quality_score,
                "anomaly_reasons": anomaly_reasons
            })

    # Sort by anomaly score (descending) and return top N
    outliers.sort(key=lambda x: x["anomaly_score"], reverse=True)
    top_outliers = outliers[:max_outliers]

    logger.info(f"Identified {len(top_outliers)} outlier tables from {len(results)} total")

    return top_outliers


def select_representative_sample(tool_response: dict, max_tables: int = 10) -> List[Dict[str, Any]]:
    """
    Select representative sample of tables for detailed analysis.

    Selects diverse tables: high quality, low quality, and medium.

    Args:
        tool_response: Full profiling tool response
        max_tables: Maximum number of sample tables

    Returns:
        List of representative table summaries
    """
    results = tool_response.get("result", [])

    if len(results) <= max_tables:
        # Return all tables if under limit
        return [_summarize_table(table) for table in results]

    # Categorize tables by quality
    high_quality = []  # score >= 85
    medium_quality = []  # 70 <= score < 85
    low_quality = []  # score < 70

    for table in results:
        quality_score = table.get("data_quality_score", 0)
        summary = _summarize_table(table)

        if quality_score >= 85:
            high_quality.append(summary)
        elif quality_score >= 70:
            medium_quality.append(summary)
        else:
            low_quality.append(summary)

    # Select balanced sample - prioritize diversity across quality levels
    sample = []

    # Strategy: Try to get diverse quality representation
    # 1. Add high quality examples (up to 30% of max)
    high_quota = max(1, int(max_tables * 0.3)) if high_quality else 0
    sample.extend(high_quality[:high_quota])

    # 2. Add low quality examples to highlight issues (up to 40% of max)
    low_quota = max(2, int(max_tables * 0.4)) if low_quality else 0
    sample.extend(low_quality[:low_quota])

    # 3. Fill remaining slots with medium quality
    remaining_slots = max_tables - len(sample)
    sample.extend(medium_quality[:remaining_slots])

    # 4. If still under limit, fill from whichever bucket has most tables
    if len(sample) < max_tables:
        remaining_slots = max_tables - len(sample)

        # Determine which bucket has the most remaining tables
        remaining_high = high_quality[high_quota:]
        remaining_low = low_quality[low_quota:]

        if len(remaining_high) >= len(remaining_low):
            sample.extend(remaining_high[:remaining_slots])
        else:
            sample.extend(remaining_low[:remaining_slots])

    logger.info(f"Selected {len(sample)} representative tables from {len(results)} total (High: {len(high_quality)}, Medium: {len(medium_quality)}, Low: {len(low_quality)})")

    return sample


def _summarize_table(table: dict) -> Dict[str, Any]:
    """Create compact summary of a table for LLM context"""
    table_ref = table.get("table_reference", "")
    table_name = table_ref.split(".")[-1] if table_ref else "unknown"

    column_analysis = table.get("column_analysis", {})

    # Summarize columns with detailed metrics
    columns_summary = []
    for col_name, col_data in column_analysis.items():
        col_summary = {
            "name": col_name,
            "type": col_data.get("data_type", "UNKNOWN"),
            "null%": round(col_data.get("null_percentage", 0), 1),
            "unique%": round(col_data.get("uniqueness_percentage", 0), 1),
            "default%": round(col_data.get("default_percentage", 0), 1),  # NEW
        }

        # Add PK/FK indicators with confidence
        if col_data.get("primary_key_candidate"):
            # Determine confidence based on uniqueness
            unique_pct = col_data.get("uniqueness_percentage", 0)
            if unique_pct >= 95:
                col_summary["pk_confidence"] = "HIGH"
            elif unique_pct >= 80:
                col_summary["pk_confidence"] = "MEDIUM"
            else:
                col_summary["pk_confidence"] = "LOW"
            col_summary["pk"] = True

        if col_data.get("foreign_key_candidate"):
            col_summary["fk"] = True

        columns_summary.append(col_summary)

    # Enhanced analysis summary with detailed recommendations
    enhanced_summary = None
    enhanced = table.get("enhanced_analysis", {})
    if enhanced.get("available"):
        # Get top PK recommendation
        pk_recs = enhanced.get("primary_key_recommendations", [])
        top_pk = None
        if pk_recs:
            top_pk = {
                "column": pk_recs[0].get("column_name", ""),
                "confidence": pk_recs[0].get("confidence", ""),
                "uniqueness": pk_recs[0].get("uniqueness_percentage", 0),
                "reason": pk_recs[0].get("reason", "")
            }

        # Get top composite key recommendation
        composite_recs = enhanced.get("composite_key_recommendations", {})
        top_composite = None
        if composite_recs.get("two_column"):
            top_composite = {
                "columns": composite_recs["two_column"][0].get("columns", []),
                "uniqueness": composite_recs["two_column"][0].get("combined_uniqueness", 0)
            }

        enhanced_summary = {
            "table_context": enhanced.get("table_context", {}).get("detected_level", "other"),
            "table_context_confidence": enhanced.get("table_context", {}).get("confidence", 0),
            "business_context": enhanced.get("table_context", {}).get("business_context", ""),
            "top_pk_recommendation": top_pk,
            "top_composite_key": top_composite,
            "total_pk_candidates": len(pk_recs),
            "total_composite_keys": len(composite_recs.get("two_column", [])) + len(composite_recs.get("three_column", []))
        }

    return {
        "table_name": table_name,
        "table_reference": table_ref,
        "num_columns": len(column_analysis),
        "num_rows": table.get("row_count", 0),  # NEW: Include row count
        "quality_score": table.get("data_quality_score", 0),
        "columns": columns_summary,  # Include ALL columns now with detailed metrics
        "enhanced_analysis": enhanced_summary
    }


def build_profiling_analysis_prompt(tool_response: dict) -> str:
    """
    Build intelligent analysis prompt from profiling tool output.

    Follows profiling agent instructions for markdown response formatting.
    Uses token compression (500K → 10K) for scalability.

    Args:
        tool_response: Full profiling tool response

    Returns:
        Compressed analysis prompt for LLM
    """

    # Extract compressed summaries
    summary_stats = extract_summary_statistics(tool_response)
    outliers = identify_outlier_tables(tool_response, max_outliers=5)
    samples = select_representative_sample(tool_response, max_tables=8)

    num_tables = summary_stats["total_tables"]

    # Build prompt following profiling agent instructions
    prompt = f"""Based on the profiling tool output for {num_tables} tables, provide an intelligent analysis in markdown format.

**CRITICAL: Follow Profiling Agent Response Guidelines:**

1. **Response Style:**
   - Use clear headings (##, ###) and bullet points
   - Include confidence percentages for all recommendations
   - Use tables/structured format for column analysis
   - **Bold key findings and recommendations**
   - Include data quality implications

2. **Your Response MUST Include These Sections:**

   **A. Executive Summary**
   - Overall data quality assessment: {summary_stats['quality_score']}/100
   - Total tables: {num_tables}, Total columns: {summary_stats['total_columns']}
   - Key findings (2-3 sentences)

   **B. Per-Table Detailed Analysis**

   For EACH table in the representative sample below, provide:

   ### 📊 [Table Number]. [Table Name]

   | Metric | Value |
   |--------|-------|
   | **Data Quality Score** | **XX%** |
   | Total Rows | X,XXX |
   | Total Columns | XX |
   | **Recommended Primary Key** | **column_name** (HIGH/MEDIUM/LOW, XX% unique) |
   | **Table Type** | detected_level (XX% confidence) |
   | **Composite Key Option** | [col1, col2] (XX% unique) |

   **Key Insights:**
   - 📊 Table Context: [Business context from table_context]
   - 🔑 Primary Key: [Reasoning for PK recommendation]
   - 🔗 Composite Key: [Business meaning if applicable]

   **Column Analysis Summary:**

   | Column | Data Type | Null % | Unique % | Default % | PK Candidate | Notes |
   |--------|-----------|--------|----------|-----------|--------------|-------|
   | column1 | STRING | 5.2% | 99.8% | 0.0% | ✅ | Recommended PK |
   | column2 | INTEGER | 12.5% | 45.2% | 8.3% | ❌ | High default rate |
   | ... | ... | ... | ... | ... | ... | ... |

   **Recommendations:**
   - [Specific recommendation 1]
   - [Specific recommendation 2]

   ---

   **C. Cross-Table Pattern Detection**
   - Common data types across tables (distribution: {json.dumps(summary_stats['data_type_distribution'])})
   - Null pattern trends (avg: {summary_stats['avg_null_percentage']}%)
   - Uniqueness patterns ({summary_stats['tables_with_primary_keys']}/{num_tables} have PK candidates)
   - Naming conventions observed

   **D. Critical Anomalies**
   - Tables with unusual characteristics (from outliers below)
   - Columns with extreme null rates or cardinality
   - **Bold critical issues requiring immediate attention**

   **E. Optimization Recommendations**
   - Indexing suggestions for high-uniqueness columns
   - Partitioning recommendations for large tables
   - Data type optimization opportunities
   - Data quality improvements needed

---

## Dataset Overview (Compressed Summary)

**Tables Analyzed:** {num_tables}
**Total Columns:** {summary_stats['total_columns']}
**Avg Columns per Table:** {summary_stats['avg_columns_per_table']}
**Overall Quality Score:** {summary_stats['quality_score']}/100

**Data Type Distribution:**
{json.dumps(summary_stats['data_type_distribution'], indent=2)}

**Null Rate Distribution:**
{json.dumps(summary_stats['null_distribution'], indent=2)}

**Uniqueness Distribution:**
{json.dumps(summary_stats['uniqueness_distribution'], indent=2)}

**Key Metrics:**
- Average Null Percentage: {summary_stats['avg_null_percentage']}%
- Median Null Percentage: {summary_stats['median_null_percentage']}%
- Average Uniqueness: {summary_stats['avg_uniqueness']}%
- Tables with Primary Key Candidates: {summary_stats['tables_with_primary_keys']}/{num_tables}
- Tables with Composite Key Options: {summary_stats['tables_with_composite_keys']}/{num_tables}

---

## Outlier Tables (Top Issues)

{json.dumps(outliers, indent=2)}

---

## Representative Sample Tables (Detailed Analysis)

{json.dumps(samples, indent=2)}

---

**IMPORTANT FORMATTING RULES:**
- YOUR RESPONSE MUST ALWAYS ADHERE TO MARKDOWN FORMATTING AND STRUCTURE
- Start with ## heading for main sections
- Use ### for subsections
- Use bullet points with - for lists
- Use **bold** for critical findings
- Use tables (|...|) for structured comparisons
- Include confidence scores where applicable
- Explain all technical terms in business language

Generate your intelligent analysis now, following the profiling agent guidelines:
"""

    # Estimate token count
    estimated_tokens = len(prompt.split()) * 1.3  # Rough estimate
    logger.info(f"Built analysis prompt: ~{int(estimated_tokens)} tokens (compressed from ~{num_tables * 5000})")

    return prompt


def extract_column_analysis(profiling_json_data: str, session_id: Optional[str] = None) -> dict:
    """
    Parses a ydata-profiling/pandas-profiling JSON report dictionary
    and returns a ToolResultItem Pydantic model.
    """


    try:
        if not session_id:
            logger.error("extract_column_analysis requires session_id to load profiling report artifacts.")
            return {}  # match the success path's return type (column_analysis dict); empty = unavailable
        json_data = load_profiling_report_json(session_id, profiling_json_data)
    except Exception as e:
        logger.error(f"Error loading profiling JSON data: {e}")
        return {}  # match the success path's return type (column_analysis dict); empty = unavailable

    variables = json_data.get("variables", {})
    analysis_result = {}

    print(f"Extracting column analysis from profiling JSON for {len(variables)} columns.")

    for col_name, stats in variables.items():

        try:
            print(f"Processing column: {col_name}")
            # 1. Extract Sample Values
            # 'first_rows' is usually a dict {index: value}, we want the values.
            # If first_rows is missing (sometimes on numeric cols), try value_counts keys.
            sample_values = []
            if "first_rows" in stats and isinstance(stats["first_rows"], dict):
                sample_values = list(stats["first_rows"].values())
            elif "value_counts_without_nan" in stats:
                sample_values = list(stats["value_counts_without_nan"].keys())[:5]

            # 2. Extract Counts
            n_total = stats.get("n", 0)
            n_missing = stats.get("n_missing", 0)
            
            # Calculate percentages (inputs are often 0.0-1.0, we usually want 0-100 for 'percentage')
            # The input json has p_distinct and p_missing as probabilities (0-1)
            p_unique = stats.get("p_distinct", 0) * 100
            p_missing = stats.get("p_missing", 0) * 100

            # 3. Determine PK Candidate
            # A simple heuristic: Must be unique and have no missing values.
            is_unique = stats.get("is_unique", False)
            pk_candidate = is_unique and (n_missing == 0)

            # 4. Handle "Blank" vs "Null"
            # The JSON provided has n_missing. It doesn't explicitly list empty strings ("") count.
            # We will map missing to null. We will default blank (empty string) to 0 
            # unless specific metadata is added to the profile in the future.
            blank_count = 0
            blank_percentage = 0.0

            # 5. Extract Numeric Stats (if available)
            min_val = stats.get("min")
            max_val = stats.get("max")
            mean_val = stats.get("mean")

            # 6. Extract Length Stats
            # Numeric columns in this JSON don't have mean_length, so default to 0.0
            avg_len = stats.get("mean_length", 0.0)

            # Create the Item
            col_item = ColumnAnalysisItem(
                data_type=stats.get("type", "Unknown"),
                total_count=int(n_total) if n_total is not None else 0,
                unique_count=int(stats.get("n_distinct", 0)) if stats.get("n_distinct") is not None else 0,
                uniqueness_percentage=float(p_unique) if p_unique is not None else 0.0,
                distinct_values_sample=sample_values,
                avg_length=float(avg_len),
                blank_count=int(blank_count),
                blank_percentage=float(blank_percentage) if blank_percentage is not None else 0.0,
                min_value=float(min_val) if min_val is not None else 0.0,
                max_value=float(max_val) if max_val is not None else 0.0,
                avg_value=float(mean_val) if mean_val is not None else 0.0,
                null_count=int(n_missing),
                null_percentage=float(p_missing) if p_missing is not None else 0.0,
                primary_key_candidate=pk_candidate,
                # Foreign key detection requires multi-table context, default to False here
                foreign_key_candidate=False 
            )

            analysis_result[col_name] = col_item
        except Exception as e:
            logger.error(f"Error processing column '{col_name}': {e}")
            continue
        
    print(f"Completed column analysis extraction. Total columns processed: {len(analysis_result)}")

    return analysis_result
