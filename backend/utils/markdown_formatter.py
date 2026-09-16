# utils/markdown_formatter.py
"""
Markdown formatting utilities for DataMap Copilot responses.

This module provides reusable markdown formatters that follow the agent's
style guidelines defined in profiling_agent/prompts.py:
- Clear headings and bullet points
- Tabular format for complex data
- Bold key findings and recommendations
- Confidence percentages
- Business-friendly language
"""

from typing import List, Dict, Any


def generate_profiling_summary_markdown(all_results: List[Dict]) -> str:
    """
    Generate full markdown summary for all profiled tables.

    Used when agent successfully formats response OR when /send-batched
    accumulates all batch results.

    Args:
        all_results: List of all table profiling results

    Returns:
        Comprehensive markdown summary following agent style
    """
    if not all_results:
        return "No profiling results available."

    md = "# 📊 Data Profiling Summary\n\n"

    # Executive summary
    total_tables = len(all_results)
    successful_tables = sum(1 for r in all_results if r.get('status') == 'success')
    avg_quality = sum(r.get('data_quality_score', 0) for r in all_results if r.get('status') == 'success') / max(successful_tables, 1)

    md += f"## Executive Summary\n\n"
    md += f"- **Total Tables Analyzed:** {total_tables}\n"
    md += f"- **Successful:** {successful_tables}\n"
    md += f"- **Average Data Quality Score:** {avg_quality:.1%}\n\n"
    md += "---\n\n"

    # Per-table details
    for idx, table_result in enumerate(all_results, 1):
        md += generate_table_card_markdown(table_result, table_number=idx)
        md += "\n"

    return md


def generate_batch_progress_markdown(batch_results: List[Dict], batch_num: int, total_batches: int) -> str:
    """
    Generate markdown for a single batch (used in streaming).

    Args:
        batch_results: Results for tables in this batch
        batch_num: Current batch number (1-indexed)
        total_batches: Total number of batches

    Returns:
        Markdown string for this batch's progress
    """
    md = f"## 📦 Batch {batch_num}/{total_batches} Complete\n\n"
    md += f"**Successfully analyzed {len(batch_results)} table(s) in this batch.** "
    md += f"Overall progress: **{batch_num}/{total_batches}** ({batch_num/total_batches*100:.0f}%)\n\n"
    md += "---\n\n"

    for table_result in batch_results:
        md += generate_table_card_markdown(table_result)
        md += "\n"

    return md


def generate_table_card_markdown(table_result: Dict, table_number: int = None) -> str:
    """
    Generate markdown card for a single table's profiling result.
    Reusable component for both full summaries and batch progress.

    Args:
        table_result: Single table's profiling result
        table_number: Optional table number for sequential numbering

    Returns:
        Markdown card for this table
    """
    if table_result.get('status') == 'error':
        table_ref = table_result.get('table_reference', 'Unknown')
        table_name = table_ref.split('.')[-1] if table_ref else 'Unknown'
        header = f"### ❌ {table_number}. {table_name}" if table_number else f"### ❌ {table_name}"

        md = f"{header}\n\n"
        md += f"**Status:** Error\n\n"
        md += f"**Error Message:** {table_result.get('error_message', 'Unknown error')}\n\n"
        md += "---\n\n"
        return md

    # Extract table info
    table_ref = table_result.get('table_reference', 'Unknown')
    table_name = table_ref.split('.')[-1] if table_ref else 'Unknown'

    header = f"### 📊 {table_number}. {table_name}" if table_number else f"### 📊 {table_name}"

    md = f"{header}\n\n"

    # Basic metrics table
    quality_score = table_result.get('data_quality_score', 0)
    table_summary = table_result.get('table_summary', {})
    total_rows = table_summary.get('total_rows', 0)
    total_columns = table_summary.get('total_columns', 0)

    md += "| Metric | Value |\n"
    md += "|--------|-------|\n"
    md += f"| **Data Quality Score** | **{quality_score:.1%}** |\n"
    md += f"| Total Rows | {total_rows:,} |\n"
    md += f"| Total Columns | {total_columns} |\n"

    # Enhanced analysis details
    enhanced_analysis = table_result.get('enhanced_analysis', {})
    if enhanced_analysis.get('available'):
        # Primary key recommendation
        pk_recs = enhanced_analysis.get('primary_key_recommendations', [])
        if pk_recs:
            top_pk = pk_recs[0]
            pk_col = top_pk.get('column', 'N/A')
            pk_conf = top_pk.get('confidence', 'N/A')
            pk_uniq = top_pk.get('uniqueness_percentage', 0)
            md += f"| **Recommended Primary Key** | **{pk_col}** ({pk_conf}, {pk_uniq:.1f}% unique) |\n"

        # Table context
        table_context = enhanced_analysis.get('table_context', {})
        detected_level = table_context.get('detected_level', 'unknown')
        confidence = table_context.get('confidence', 0)
        md += f"| **Table Type** | {detected_level.replace('_', ' ').title()} ({confidence*100:.0f}% confidence) |\n"

        # Composite keys (if available)
        composite_recs = enhanced_analysis.get('composite_key_recommendations', {})
        two_col = composite_recs.get('two_column', [])
        if two_col:
            top_composite = two_col[0]
            cols = ', '.join(top_composite.get('columns', []))
            uniq = top_composite.get('uniqueness_percentage', 0)
            md += f"| **Composite Key Option** | [{cols}] ({uniq:.1f}% unique) |\n"

    md += "\n"

    # Enhanced recommendations (pre-formatted with emojis)
    if enhanced_analysis.get('available'):
        enhanced_recs = enhanced_analysis.get('enhanced_recommendations', [])
        if enhanced_recs:
            md += "**Key Insights:**\n\n"
            for rec in enhanced_recs[:5]:  # Top 5 insights
                md += f"- {rec}\n"
            md += "\n"
    else:
        # Fallback to basic recommendations
        recommendations = table_result.get('recommendations', [])
        if recommendations:
            md += "**Recommendations:**\n\n"
            for rec in recommendations[:3]:  # Top 3 recommendations
                md += f"- {rec}\n"
            md += "\n"

    md += "---\n\n"
    return md


def generate_executive_summary(all_results: List[Dict]) -> str:
    """
    Generate high-level executive summary (useful for large datasets).

    Args:
        all_results: List of all table profiling results

    Returns:
        Executive summary markdown
    """
    if not all_results:
        return "No results to summarize."

    successful = [r for r in all_results if r.get('status') == 'success']
    failed = [r for r in all_results if r.get('status') != 'success']

    avg_quality = sum(r.get('data_quality_score', 0) for r in successful) / max(len(successful), 1)
    total_rows = sum(r.get('table_summary', {}).get('total_rows', 0) for r in successful)

    # Count tables with high-confidence primary keys
    high_conf_pks = 0
    for r in successful:
        ea = r.get('enhanced_analysis', {})
        if ea.get('available'):
            pk_recs = ea.get('primary_key_recommendations', [])
            if pk_recs and pk_recs[0].get('confidence') == 'HIGH':
                high_conf_pks += 1

    md = "# 🎯 Executive Summary\n\n"
    md += "## Overall Statistics\n\n"
    md += f"- **Total Tables:** {len(all_results)}\n"
    md += f"- **Successfully Analyzed:** {len(successful)}\n"
    md += f"- **Failed:** {len(failed)}\n"
    md += f"- **Average Data Quality:** {avg_quality:.1%}\n"
    md += f"- **Total Rows Analyzed:** {total_rows:,}\n"
    md += f"- **Tables with High-Confidence Primary Keys:** {high_conf_pks}/{len(successful)}\n\n"

    # Quality distribution
    excellent = sum(1 for r in successful if r.get('data_quality_score', 0) >= 0.9)
    good = sum(1 for r in successful if 0.7 <= r.get('data_quality_score', 0) < 0.9)
    fair = sum(1 for r in successful if 0.5 <= r.get('data_quality_score', 0) < 0.7)
    poor = sum(1 for r in successful if r.get('data_quality_score', 0) < 0.5)

    md += "## Data Quality Distribution\n\n"
    md += f"- **Excellent (≥90%):** {excellent} tables\n"
    md += f"- **Good (70-89%):** {good} tables\n"
    md += f"- **Fair (50-69%):** {fair} tables\n"
    md += f"- **Poor (<50%):** {poor} tables\n\n"

    if failed:
        md += "## ⚠️ Failed Tables\n\n"
        for r in failed:
            table_name = r.get('table_reference', 'Unknown').split('.')[-1]
            error_msg = r.get('error_message', 'Unknown error')
            md += f"- **{table_name}**: {error_msg}\n"
        md += "\n"

    md += "---\n\n"
    return md


def generate_relationship_summary_markdown(relationship_data: Dict) -> str:
    """
    Generate markdown summary for relationship analysis results.

    Args:
        relationship_data: Relationship analysis results from relationship_analysis_tool

    Returns:
        Markdown summary following agent style
    """
    if not relationship_data or relationship_data.get('status') == 'error':
        error_msg = relationship_data.get('error_message', 'Unknown error') if relationship_data else 'No data provided'
        return generate_error_markdown(f"Relationship analysis failed: {error_msg}")

    md = "# 🔗 Relationship Analysis Results\n\n"

    # Executive Summary
    tables_analyzed = relationship_data.get('tables_analyzed', 0)
    relationships_found = len(relationship_data.get('cross_table_relationships', []))
    analysis_depth = relationship_data.get('analysis_depth', 'standard')
    processing_stats = relationship_data.get('processing_stats', {})
    processing_time = processing_stats.get('total_processing_time', 0)

    md += f"## 📊 Executive Summary\n\n"
    md += f"- **Tables Analyzed:** {tables_analyzed}\n"
    md += f"- **Relationships Found:** {relationships_found}\n"
    md += f"- **Analysis Depth:** {analysis_depth.title()}\n"
    md += f"- **Processing Time:** {processing_time:.2f}s\n"
    md += f"- **Processing Mode:** {relationship_data.get('processing_mode', 'standard')}\n\n"
    md += "---\n\n"

    # Cross-Table Relationships
    cross_relationships = relationship_data.get('cross_table_relationships', [])
    if cross_relationships:
        md += "## 🔑 Foreign Key Relationships\n\n"
        md += "Detected foreign key relationships with confidence scores:\n\n"

        # Group by confidence level
        high_conf = [r for r in cross_relationships if r.get('confidence_level') == 'HIGH']
        medium_conf = [r for r in cross_relationships if r.get('confidence_level') == 'MEDIUM']
        low_conf = [r for r in cross_relationships if r.get('confidence_level') == 'LOW']

        if high_conf:
            md += "### ✅ High Confidence Relationships\n\n"
            md += "| Source Table | Source Column | Target Table | Target Column | Confidence | Data Overlap |\n"
            md += "|--------------|---------------|--------------|---------------|------------|-------------|\n"
            for rel in high_conf:
                overlap = rel.get('data_overlap_details', {}).get('overlap_percentage', 0)
                conf_score = rel.get('confidence_score', 0)
                md += f"| **{rel.get('source_table')}** | {rel.get('source_column')} | **{rel.get('target_table')}** | {rel.get('target_column')} | {conf_score*100:.0f}% | {overlap:.1f}% |\n"
            md += "\n"

        if medium_conf:
            md += "### ⚠️ Medium Confidence Relationships\n\n"
            md += "| Source Table | Source Column | Target Table | Target Column | Confidence | Data Overlap |\n"
            md += "|--------------|---------------|--------------|---------------|------------|-------------|\n"
            for rel in medium_conf:
                overlap = rel.get('data_overlap_details', {}).get('overlap_percentage', 0)
                conf_score = rel.get('confidence_score', 0)
                md += f"| {rel.get('source_table')} | {rel.get('source_column')} | {rel.get('target_table')} | {rel.get('target_column')} | {conf_score*100:.0f}% | {overlap:.1f}% |\n"
            md += "\n"

        if low_conf:
            md += "### 🔍 Low Confidence Relationships (Require Validation)\n\n"
            md += "| Source Table | Source Column | Target Table | Target Column | Confidence | Data Overlap |\n"
            md += "|--------------|---------------|--------------|---------------|------------|-------------|\n"
            for rel in low_conf:
                overlap = rel.get('data_overlap_details', {}).get('overlap_percentage', 0)
                conf_score = rel.get('confidence_score', 0)
                md += f"| {rel.get('source_table')} | {rel.get('source_column')} | {rel.get('target_table')} | {rel.get('target_column')} | {conf_score*100:.0f}% | {overlap:.1f}% |\n"
            md += "\n"

        md += "---\n\n"

    # Table Details with Column Classifications
    table_details = relationship_data.get('table_details', {})
    if table_details:
        md += "## 📋 Table Details & Column Classifications\n\n"

        for table_name, table_data in table_details.items():
            md += f"### 📊 {table_name}\n\n"

            total_rows = table_data.get('total_rows', 0)
            total_columns = table_data.get('total_columns', 0)

            md += f"**Table Statistics:**\n"
            md += f"- Total Rows: {total_rows:,}\n"
            md += f"- Total Columns: {total_columns}\n\n"

            # Primary Key Candidates
            pk_candidates = table_data.get('primary_key_candidates', [])
            if pk_candidates:
                md += "**Primary Key Candidates:**\n\n"
                md += "| Column | Confidence Score |\n"
                md += "|--------|------------------|\n"
                for pk in pk_candidates:
                    col = pk.get('column', 'N/A')
                    score = pk.get('score', 0)
                    md += f"| **{col}** | {score*100:.0f}% |\n"
                md += "\n"

            # Column Classifications
            column_classifications = table_data.get('column_classifications', {})
            if column_classifications:
                md += "**Column Classifications:**\n\n"
                md += "| Column | PK | FK | Associated Tables | Alternate Keys |\n"
                md += "|--------|----|----|-------------------|----------------|\n"

                for col_name, classification in column_classifications.items():
                    is_pk = "✅" if classification.get('pk') == 'yes' else "❌"
                    is_fk = "✅" if classification.get('fk') == 'yes' else "❌"

                    associated_files = classification.get('associated_files', [])
                    assoc_str = ", ".join(associated_files) if associated_files else "-"

                    alternate_keys = classification.get('ak', [])
                    ak_str = f"{len(alternate_keys)} composite keys" if alternate_keys else "-"

                    md += f"| {col_name} | {is_pk} | {is_fk} | {assoc_str} | {ak_str} |\n"

                md += "\n"

            # Composite Keys
            composite_keys = table_data.get('composite_keys', {})
            if composite_keys:
                md += "**Composite Key Options:**\n\n"

                two_col = composite_keys.get('2_column_combinations', [])
                if two_col:
                    md += "*2-Column Combinations:*\n"
                    for combo in two_col[:3]:  # Show top 3
                        cols = ', '.join(combo.get('columns', []))
                        uniq = combo.get('uniqueness_percentage', 0)
                        md += f"- [{cols}] - {uniq:.1f}% unique\n"
                    md += "\n"

                three_col = composite_keys.get('3_column_combinations', [])
                if three_col:
                    md += "*3-Column Combinations:*\n"
                    for combo in three_col[:3]:  # Show top 3
                        cols = ', '.join(combo.get('columns', []))
                        uniq = combo.get('uniqueness_percentage', 0)
                        md += f"- [{cols}] - {uniq:.1f}% unique\n"
                    md += "\n"

            md += "---\n\n"

    # Recommendations
    md += "## 💡 Key Insights & Recommendations\n\n"

    if relationships_found > 0:
        high_count = len([r for r in cross_relationships if r.get('confidence_level') == 'HIGH'])
        md += f"- ✅ **{high_count} high-confidence foreign key relationships** detected - strong referential integrity\n"

        medium_count = len([r for r in cross_relationships if r.get('confidence_level') == 'MEDIUM'])
        if medium_count > 0:
            md += f"- ⚠️ **{medium_count} medium-confidence relationships** - recommend validation for data quality issues\n"

        low_count = len([r for r in cross_relationships if r.get('confidence_level') == 'LOW'])
        if low_count > 0:
            md += f"- 🔍 **{low_count} low-confidence relationships** - require manual validation before implementation\n"
    else:
        md += "- ℹ️ **No cross-table relationships detected** - tables may be independent or use different naming conventions\n"

    # Count tables with strong PKs
    strong_pk_count = 0
    for table_name, table_data in table_details.items():
        pk_candidates = table_data.get('primary_key_candidates', [])
        if any(pk.get('score', 0) >= 0.9 for pk in pk_candidates):
            strong_pk_count += 1

    if strong_pk_count > 0:
        md += f"- ✅ **{strong_pk_count}/{tables_analyzed} tables** have strong primary key candidates (≥90% confidence)\n"

    md += "\n"

    return md


def generate_error_markdown(error_message: str, table_references: List[str] = None) -> str:
    """
    Generate error message markdown.

    Args:
        error_message: Error description
        table_references: Optional list of tables that failed

    Returns:
        Error markdown
    """
    md = "# ❌ Profiling Error\n\n"
    md += f"**Error:** {error_message}\n\n"

    if table_references:
        md += f"**Affected Tables ({len(table_references)}):**\n\n"
        for table_ref in table_references[:10]:  # Show first 10
            table_name = table_ref.split('.')[-1] if table_ref else table_ref
            md += f"- {table_name}\n"

        if len(table_references) > 10:
            md += f"\n... and {len(table_references) - 10} more tables.\n"

    md += "\n**Please try again or contact support if the issue persists.**\n"
    return md
