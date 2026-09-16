# utils/relationship_analysis.py
"""
LLM Analysis Prompt Builder for Relationship Analysis Results

Generates intelligent analysis prompts from relationship tool output.
Uses token compression while PRESERVING cross-table relationship context.
Unlike profiling, relationships MUST maintain connections between tables.
"""

import json
import logging
from typing import Dict, Any, List, Set
from collections import defaultdict

logger = logging.getLogger(__name__)


def extract_relationship_summary(tool_response: dict) -> Dict[str, Any]:
    """
    Extract high-level relationship summary statistics.

    Compresses metadata while preserving relationship counts and patterns.

    Args:
        tool_response: Full relationship analysis tool response

    Returns:
        Compressed summary statistics dict
    """
    table_details = tool_response.get("table_details", {})
    relationships = tool_response.get("cross_table_relationships", [])

    total_tables = len(table_details)
    total_relationships = len(relationships)

    # Relationship type distribution
    relationship_types = defaultdict(int)
    confidence_distribution = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

    for rel in relationships:
        rel_type = rel.get("relationship_type", "unknown")
        relationship_types[rel_type] += 1

        confidence = rel.get("confidence_level", "UNKNOWN")
        if confidence in confidence_distribution:
            confidence_distribution[confidence] += 1

    # Table connectivity analysis
    connected_tables = set()
    isolated_tables = []
    table_connection_counts = defaultdict(int)

    for rel in relationships:
        source = rel.get("source_table")
        target = rel.get("target_table")

        if source:
            connected_tables.add(source)
            table_connection_counts[source] += 1
        if target:
            connected_tables.add(target)
            table_connection_counts[target] += 1

    # Find isolated tables (no relationships)
    all_table_names = set(table_details.keys())
    isolated_tables = list(all_table_names - connected_tables)

    # Find hub tables (highly connected)
    hub_tables = []
    for table, count in table_connection_counts.items():
        if count >= 3:  # Tables with 3+ relationships
            hub_tables.append({"table": table, "connections": count})
    hub_tables.sort(key=lambda x: x["connections"], reverse=True)

    summary = {
        "total_tables": total_tables,
        "total_relationships": total_relationships,
        "connected_tables": len(connected_tables),
        "isolated_tables_count": len(isolated_tables),
        "isolated_tables": isolated_tables[:5],  # Show first 5
        "relationship_type_distribution": dict(relationship_types),
        "confidence_distribution": confidence_distribution,
        "hub_tables": hub_tables[:5],  # Top 5 most connected
        "avg_relationships_per_table": round(total_relationships * 2 / total_tables, 1) if total_tables > 0 else 0
    }

    logger.info(f"Relationship summary: {total_relationships} relationships across {total_tables} tables")

    return summary


def compress_table_for_relationships(table_name: str, table_data: Dict[str, Any],
                                   relationships: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compress table data while preserving relationship-relevant information.

    ONLY includes columns that participate in:
    - Primary keys
    - Foreign keys (source or target)
    - Composite/Alternate keys referenced in relationships

    Args:
        table_name: Name of the table
        table_data: Full table details from tool response
        relationships: All cross-table relationships

    Returns:
        Compressed table data focusing on key columns only
    """

    # Find columns involved in relationships for this table
    involved_columns = set()

    # Check cross-table relationships
    for rel in relationships:
        if rel.get("source_table") == table_name:
            involved_columns.add(rel.get("source_column"))
        if rel.get("target_table") == table_name:
            involved_columns.add(rel.get("target_column"))

    # Get column classifications and column details (for data types)
    column_classifications = table_data.get("column_classifications", {})
    columns_detail = table_data.get("columns", {})

    # Add all PK, FK, and AK columns
    for col_name, classification in column_classifications.items():
        if classification.get("pk") == "yes":
            involved_columns.add(col_name)
        if classification.get("fk") == "yes":
            involved_columns.add(col_name)
        if classification.get("ak"):  # Has alternate keys
            involved_columns.add(col_name)

    # Build compressed column info (only key columns) - INCLUDE DATA TYPES
    compressed_columns = {}
    for col_name in involved_columns:
        if col_name in column_classifications:
            classification = column_classifications[col_name]

            # Get data type from columns detail
            data_type = "UNKNOWN"
            if col_name in columns_detail:
                data_type = columns_detail[col_name].get("data_type", "UNKNOWN")

            # Extract AK positions for this column (e.g., ["AK1.1", "AK3.1"])
            ak_positions = []
            for ak_entry in classification.get("ak", []):
                if "position" in ak_entry:
                    ak_positions.append(ak_entry["position"])

            compressed_columns[col_name] = {
                "data_type": data_type,  # Include data type
                "pk": classification.get("pk"),
                "fk": classification.get("fk"),
                "associated_files": classification.get("associated_files", []),
                "ak_positions": ak_positions  # NEW: Full AK positions (e.g., ["AK1.1", "AK3.1"])
            }

    # Include composite keys information (preserves uniqueness percentages and column combinations)
    composite_keys = table_data.get("composite_keys", {})

    return {
        "table_name": table_name,
        "total_rows": table_data.get("total_rows", 0),
        "total_columns": table_data.get("total_columns", 0),
        "key_columns_count": len(compressed_columns),
        "key_columns": compressed_columns,
        "composite_keys": composite_keys  # NEW: Include composite key combinations
    }


def group_relationships_by_source(relationships: List[Dict[str, Any]]) -> Dict[str, List[Dict]]:
    """
    Group relationships by source table for easier LLM comprehension.

    Returns:
        Dict mapping source_table -> list of its outgoing relationships
    """
    grouped = defaultdict(list)

    for rel in relationships:
        source = rel.get("source_table")
        if source:
            grouped[source].append({
                "target_table": rel.get("target_table"),
                "source_column": rel.get("source_column"),
                "target_column": rel.get("target_column"),
                "confidence": rel.get("confidence_level"),
                "overlap_pct": rel.get("data_overlap_details", {}).get("overlap_percentage", 0),
                "interpretation": rel.get("interpretation", "")
            })

    return dict(grouped)


def select_representative_relationships(relationships: List[Dict[str, Any]],
                                       max_relationships: int = 50) -> List[Dict[str, Any]]:
    """
    Select most important relationships when count is very high (100+ tables).

    Prioritizes:
    - HIGH confidence relationships
    - Hub table relationships (highly connected tables)
    - Diverse table pairs (not all from same source)

    Args:
        relationships: All relationships
        max_relationships: Maximum to include in analysis

    Returns:
        Filtered list of most important relationships
    """
    if len(relationships) <= max_relationships:
        return relationships

    # Categorize by confidence
    high_confidence = [r for r in relationships if r.get("confidence_level") == "HIGH"]
    medium_confidence = [r for r in relationships if r.get("confidence_level") == "MEDIUM"]
    low_confidence = [r for r in relationships if r.get("confidence_level") == "LOW"]

    # Priority: 70% HIGH, 20% MEDIUM, 10% LOW
    selected = []
    selected.extend(high_confidence[:int(max_relationships * 0.7)])
    selected.extend(medium_confidence[:int(max_relationships * 0.2)])
    selected.extend(low_confidence[:int(max_relationships * 0.1)])

    # Fill remaining slots with HIGH if available
    if len(selected) < max_relationships and len(high_confidence) > len(selected):
        remaining = max_relationships - len(selected)
        selected.extend(high_confidence[len(selected):len(selected) + remaining])

    logger.info(f"Selected {len(selected)} representative relationships from {len(relationships)} total")

    return selected


def build_relationship_analysis_prompt(tool_response: dict) -> str:
    """
    Build intelligent analysis prompt from relationship tool output.

    Preserves cross-table relationship context while compressing token count.

    Args:
        tool_response: Full relationship analysis tool response

    Returns:
        Compressed analysis prompt for LLM (relationship-context preserved)
    """

    # Extract data
    table_details = tool_response.get("table_details", {})
    relationships = tool_response.get("cross_table_relationships", [])

    # Get summary statistics
    summary = extract_relationship_summary(tool_response)

    # Select representative relationships (if too many)
    max_relationships = 50 if len(relationships) > 50 else len(relationships)
    selected_relationships = select_representative_relationships(relationships, max_relationships)

    # Group relationships by source for context
    grouped_relationships = group_relationships_by_source(selected_relationships)

    # Compress table data (only key columns)
    compressed_tables = {}
    for table_name, table_data in table_details.items():
        compressed_tables[table_name] = compress_table_for_relationships(
            table_name, table_data, relationships
        )

    # Build prompt
    prompt = f"""Based on the relationship analysis for {summary['total_tables']} tables, provide an intelligent analysis of data relationships in markdown format.

**CRITICAL: Follow Relationship Analysis Response Guidelines:**

1. **Response Style:**
   - Use clear headings (##, ###) and bullet points
   - Include confidence levels for all FK relationships
   - Use tables/structured format for relationship mappings
   - **Bold critical findings and hub tables**
   - Explain business implications of relationships

2. **Your Response MUST Include These Sections:**

   **A. Executive Summary**
   - Total tables analyzed: {summary['total_tables']}
   - Total relationships found: {summary['total_relationships']}
   - Connected vs Isolated tables: {summary['connected_tables']} connected, {summary['isolated_tables_count']} isolated
   - Key findings (2-3 sentences about overall data model quality)

   **B. Data Model Architecture**
   - Identify **hub tables** (highly connected): {json.dumps(summary['hub_tables'])}
   - Identify **isolated tables**: {json.dumps(summary['isolated_tables'])}
   - Relationship confidence distribution: {json.dumps(summary['confidence_distribution'])}
   - Overall data model pattern (star schema, snowflake, normalized, etc.)

   **C. Per-Table Relationship Details**

   For EACH table with relationships, provide:

   ### 🔗 [Table Name]

   **Table Metadata:**

   | Metric | Value |
   |--------|-------|
   | **Row Count** | X,XXX |
   | **Total Columns** | XX |
   | **Key Columns** | XX (PK/FK/AK) |
   | **Outgoing FKs** | X relationships |
   | **Referenced By** | X tables |

   **CRITICAL: Column Details Table (Key Columns Only)**

   You MUST create this table with the EXACT format below for EVERY table:

   | Column | Data Type | Primary Key | Foreign Key | References | Composite Keys |
   |--------|-----------|-------------|-------------|------------|----------------|
   | claim_id | STRING | ✓ (95% conf) | — | — | AK1.1, AK3.1 |
   | line_number | INTEGER | — | — | — | AK1.2, AK2.1 |
   | service_date | DATE | — | — | — | AK2.2, AK3.2 |

   **CRITICAL AK NOTATION RULES:**
   - **AK1, AK2, AK3** are composite key GROUP identifiers (each group represents one composite key)
   - **AK1.1, AK1.2** are individual COLUMN positions within AK1
   - **AK2.1, AK2.2** are individual COLUMN positions within AK2
   - In the "Composite Keys" column, **ALWAYS use the column position format (AK1.1, AK1.2, NOT just AK1)**
   - **NEVER display just "AK1" or "AK2"** for individual columns - ALWAYS use the full position notation
   - Multiple positions mean the column participates in multiple composite keys (e.g., "AK1.1, AK3.1")

   **Example Explanation:**
   - `claim_id` is in 2 alternate keys: AK1 (position 1) and AK3 (position 1) → Show "AK1.1, AK3.1"
   - `line_number` is in AK1 (position 2) and AK2 (position 1) → Show "AK1.2, AK2.1"
   - `service_date` is in AK2 (position 2) and AK3 (position 2) → Show "AK2.2, AK3.2"

   This means:
   - **AK1** = [claim_id, line_number] (2-column composite key)
   - **AK2** = [line_number, service_date] (2-column composite key)
   - **AK3** = [claim_id, service_date] (2-column composite key)

   **MANDATORY FORMATTING RULES FOR THIS TABLE:**
   - Include ONLY key columns (where pk="yes" OR fk="yes" OR ak_positions is not empty)
   - **Data Type column**: Extract from key_columns[column_name]["data_type"] - THIS IS MANDATORY
   - **Primary Key column**: Show "✓ (conf%)" if pk="yes", otherwise show "—"
   - **Foreign Key column**: Show "✓ (confidence_level)" if fk="yes", otherwise show "—"
   - **References column**: For FK columns, show "target_table.target_column" from associated_files, otherwise show "—"
   - **Composite Keys column**: Extract from key_columns[column_name]["ak_positions"] array and join with ", "
     - Example: If ak_positions = ["AK1.1", "AK3.1"], show "AK1.1, AK3.1"
     - If ak_positions is empty, show "—"
   - Use "—" (em dash) for empty cells, NOT blank or "N/A"

   **Foreign Key Relationships (Detailed Analysis):**

   | FK Column | Data Type | → References | Target Table | Target Column | Confidence | Overlap % | Interpretation |
   |-----------|-----------|--------------|--------------|---------------|------------|-----------|----------------|
   | customer_id | STRING | → | customers | customer_id | HIGH | 98.5% | Strong referential integrity |
   | product_id | INTEGER | → | products | product_id | MEDIUM | 75.2% | Some orphaned records exist |

   **Composite/Alternate Keys:**

   **CRITICAL: Use this EXACT format for each composite key group:**

   - **AK1**: [customer_id (STRING) + order_date (DATE)] - 99.2% unique
     - Columns: customer_id (AK1.1), order_date (AK1.2)
     - Business meaning: Track customer daily orders
     - Recommended for: Order deduplication

   - **AK2**: [product_id (INTEGER) + location (STRING) + date (DATE)] - 97.8% unique
     - Columns: product_id (AK2.1), location (AK2.2), date (AK2.3)
     - Business meaning: Product availability tracking
     - Recommended for: Inventory analysis

   **Formatting Rules:**
   1. Each composite key group has a header: **AK1**, **AK2**, **AK3**, etc.
   2. Under "Columns:", list each column with its position: column_name (AK1.1), column_name (AK1.2)
   3. ALWAYS show the position notation (AK1.1, AK1.2) when listing individual columns
   4. The pattern MUST be consistent: AK[group].[position] format

   **Business Interpretation:**
   - Explain what this table represents and its role in the data model
   - Explain key relationships in business terms
   - Highlight data quality issues (low FK overlap percentages, missing PKs, etc.)

   ---

   **D. Cross-Table Relationship Patterns**
   - Common naming patterns across FKs
   - Data quality of relationships (high overlap = good referential integrity)
   - Potential missing relationships (columns that look like FKs but weren't matched)
   - Circular dependencies or unusual patterns

   **E. Data Quality & Recommendations**
   - **Referential integrity issues**: Low overlap percentages indicate orphaned records
   - **Missing PKs**: Tables without clear primary keys
   - **Suggested improvements**: Additional indexes, FK constraints, composite keys
   - **Data governance**: Key tables that need attention

---

## Relationship Summary Statistics

**Tables Analyzed:** {summary['total_tables']}
**Total Relationships Found:** {summary['total_relationships']}
**Average Relationships per Table:** {summary['avg_relationships_per_table']}

**Confidence Distribution:**
{json.dumps(summary['confidence_distribution'], indent=2)}

**Relationship Types:**
{json.dumps(summary['relationship_type_distribution'], indent=2)}

---

## Table Details (Key Columns Only)

{json.dumps(compressed_tables, indent=2)}

---

## Relationships by Source Table

{json.dumps(grouped_relationships, indent=2)}

---

**IMPORTANT FORMATTING RULES:**
- YOUR RESPONSE MUST ALWAYS ADHERE TO MARKDOWN FORMATTING AND STRUCTURE
- Start with ## heading for main sections
- Use ### for per-table sections
- Use bullet points with - for lists
- Use **bold** for hub tables and critical issues
- Use tables (|...|) for FK mappings
- Include confidence levels for all relationships
- Explain technical terms in business language
- PRESERVE CROSS-TABLE CONTEXT - relationships connect tables, don't analyze in isolation

**CRITICAL AK NOTATION REMINDER:**
- When showing composite keys in column tables, use: AK1.1, AK1.2, AK2.1, AK2.2, etc. (NEVER just AK1, AK2)
- When explaining composite key groups, show: **AK1**: [col1 + col2], then list "Columns: col1 (AK1.1), col2 (AK1.2)"
- This pattern MUST be consistent throughout your entire response

Generate your intelligent relationship analysis now, following the relationship analysis guidelines:
"""

    # Estimate token count
    estimated_tokens = len(prompt.split()) * 1.3
    original_estimate = summary['total_tables'] * 1000 + summary['total_relationships'] * 200
    logger.info(f"Built relationship analysis prompt: ~{int(estimated_tokens)} tokens (compressed from ~{int(original_estimate)})")

    return prompt
