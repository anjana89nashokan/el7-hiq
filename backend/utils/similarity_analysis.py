"""
Similarity Analysis - LLM Prompt Builder
Generates intelligent insights from column similarity matching results.
"""

import logging
from typing import List, Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def build_similarity_analysis_prompt(enriched_matches: List[Dict]) -> str:
    """
    Build compressed LLM prompt for generating similarity insights.

    Similar pattern to profiling_analysis.py and relationship_analysis.py.
    Compresses match data and asks LLM to generate executive summary.

    Args:
        enriched_matches: List of matches with overlap validation results
            [
                {
                    "rank": 1,
                    "source_table_name": "...",
                    "source_column_name": "...",
                    "dart_table_name": "...",
                    "dart_column_name": "...",
                    "header_name_similarity": 85.0,
                    "data_overlap_similarity": 92.5,
                    "combined_score": 89.5,
                    "confidence": "HIGH",
                    "null_blank_percent": 1.5,
                    "total_rows": 10000,
                    "overlap_count": 9250,
                    ...
                }
            ]

    Returns:
        String prompt for LLM
    """
    if not enriched_matches:
        return """No column matches were found between the source tables and DART reference tables.

Please generate a brief markdown report explaining:
1. Possible reasons for no matches (different naming conventions, no semantic overlap, etc.)
2. Recommendations for the Business System Analyst
"""

    # Group by confidence level
    high_confidence = [m for m in enriched_matches if m["confidence"] == "HIGH"]
    medium_confidence = [m for m in enriched_matches if m["confidence"] == "MEDIUM"]
    low_confidence = [m for m in enriched_matches if m["confidence"] == "LOW"]

    # Calculate average scores
    avg_header = sum(m["header_name_similarity"] for m in enriched_matches) / len(enriched_matches)
    avg_overlap = sum(m["data_overlap_similarity"] for m in enriched_matches) / len(enriched_matches)
    avg_combined = sum(m["combined_score"] for m in enriched_matches) / len(enriched_matches)

    prompt = f"""You are a data mapping analyst reviewing column similarity results between source tables and DART reference tables.

## Match Statistics
- **Total potential matches analyzed:** {len(enriched_matches)}
- **High confidence matches (≥75%):** {len(high_confidence)}
- **Medium confidence matches (50-74%):** {len(medium_confidence)}
- **Low confidence matches (<50%):** {len(low_confidence)}
- **Average header name similarity:** {avg_header:.1f}%
- **Average data overlap similarity:** {avg_overlap:.1f}%
- **Average combined score:** {avg_combined:.1f}%

## Scoring Methodology
- **Header Name Similarity (0-100%):** Semantic similarity between column names based on AI analysis of naming patterns, abbreviations, and business context
- **Data Overlap Similarity (0-100%):** Percentage of distinct source values that exist in the DART reference table (actual data validation)
- **Combined Score:** Weighted average = (Header Similarity × 40%) + (Data Overlap × 60%)
  - Why weighted? Data overlap is more reliable than name similarity alone
- **Confidence Levels:**
  - HIGH (≥75%): Strong match - ready for immediate mapping
  - MEDIUM (50-74%): Moderate match - requires BSA review before mapping
  - LOW (<50%): Weak match - may need data transformation or not a valid match

## Top High Confidence Matches
{format_matches_for_prompt(high_confidence[:10])}

## Top Medium Confidence Matches
{format_matches_for_prompt(medium_confidence[:5])}

## Top Low Confidence Matches (if any)
{format_matches_for_prompt(low_confidence[:3])}

---

## Your Task

Generate a professional markdown report for Business System Analysts with the following sections:

### 1. Executive Summary
- Key findings (2-3 sentences)
- Overall match quality assessment
- Critical recommendations

### 2. High Confidence Matches (≥75% Combined Score)

Create a markdown table with these exact columns:
| Rank | DART Table | DART Column | Source Table | Source Column | Header Similarity | Data Overlap | Combined Score | Match Quality |

Include ALL high confidence matches (up to 20). For each match:
- Rank: From the data
- DART Table: Short table name (not full path)
- DART Column: Column name
- Source Table: Short table name
- Source Column: Column name
- Header Similarity: Percentage (e.g., "85.0%")
- Data Overlap: Percentage (e.g., "92.5%")
- Combined Score: Percentage (e.g., "89.5%")
- Match Quality: Brief assessment (e.g., "Excellent - strong semantic + data match")

### 3. Medium Confidence Matches (50-74% Combined Score)

Use the same table format as above. Include up to 10 medium confidence matches.

Add a note: "⚠️ These matches require Business System Analyst review before implementation."

### 4. Insights & Patterns

Analyze the matches and provide insights:
- Common naming patterns observed (e.g., "Source uses camelCase, DART uses snake_case")
- Data quality observations (e.g., "High null percentages in certain columns")
- Semantic match patterns (e.g., "Abbreviations: MBR → member, GRP → group")
- Any anomalies or interesting findings

### 5. Recommendations

Provide actionable recommendations:
- Which matches can be implemented immediately (HIGH confidence)
- Which matches need BSA review (MEDIUM confidence)
- Which matches should be investigated further (LOW confidence)
- Any data quality improvements needed
- Suggested next steps for the data mapping team

### 6. Mapping Implementation Checklist

For high confidence matches, provide a checklist:
- [ ] Review and approve all high confidence matches
- [ ] Investigate medium confidence matches
- [ ] Implement approved column mappings
- [ ] Test data transformations
- [ ] Validate data integrity post-mapping

---

**Important Instructions:**
- Use clear, professional language suitable for Business System Analysts
- Be specific and actionable in recommendations
- Use tables for match listings (easier to scan)
- Highlight any critical issues or concerns
- Keep the report concise but comprehensive
- Use markdown formatting for readability

Generate the report now:
"""

    return prompt


def format_matches_for_prompt(matches: List[Dict]) -> str:
    """
    Format matches into compact text format for LLM prompt.

    Args:
        matches: List of enriched match dicts

    Returns:
        Formatted string with match details
    """
    if not matches:
        return "*None*"

    lines = []
    for match in matches:
        # Extract short table names (remove project.dataset prefix)
        source_table_short = match["source_table_name"].split(".")[-1]
        dart_table_short = match["dart_table_name"].split(".")[-1]

        lines.append(
            f"{match['rank']}. **{source_table_short}.{match['source_column_name']}** → "
            f"**{dart_table_short}.{match['dart_column_name']}**\n"
            f"   - Header Similarity: {match['header_name_similarity']:.1f}%\n"
            f"   - Data Overlap: {match['data_overlap_similarity']:.1f}%\n"
            f"   - Combined Score: {match['combined_score']:.1f}%\n"
            f"   - Overlap Details: {match['overlap_count']}/{match['source_distinct_count']} "
            f"distinct values match ({match.get('null_blank_percent', 0):.1f}% NULL)\n"
            f"   - Sample Matching Values: {', '.join(match.get('sample_matching_values', [])[:3])}"
        )

    return "\n\n".join(lines)


def format_similarity_summary(enriched_matches: List[Dict]) -> str:
    """
    Generate a concise summary of similarity analysis results.

    Used for logging and debug purposes.

    Args:
        enriched_matches: List of enriched match dicts

    Returns:
        Formatted summary string
    """
    if not enriched_matches:
        return "No matches found"

    high = sum(1 for m in enriched_matches if m["confidence"] == "HIGH")
    medium = sum(1 for m in enriched_matches if m["confidence"] == "MEDIUM")
    low = sum(1 for m in enriched_matches if m["confidence"] == "LOW")

    avg_combined = sum(m["combined_score"] for m in enriched_matches) / len(enriched_matches)

    summary = f"""
Similarity Analysis Summary:
- Total Matches: {len(enriched_matches)}
- High Confidence: {high} (≥75%)
- Medium Confidence: {medium} (50-74%)
- Low Confidence: {low} (<50%)
- Average Combined Score: {avg_combined:.1f}%
- Top Match: {enriched_matches[0]['source_column_name']} → {enriched_matches[0]['dart_column_name']} ({enriched_matches[0]['combined_score']:.1f}%)
"""

    return summary.strip()
