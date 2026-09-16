# utils/relationship_analysis_batched.py
"""
Batched Relationship Analysis - LLM Prompt Builders for Large-Scale Datasets

Extends relationship analysis with:
1. Intelligent batching for 100+ tables with 1000+ relationships
2. Cluster-based batching (preserves relationship context)
3. Multi-pass LLM analysis with progressive refinement
4. Searchable index generation for instant chat followup

Architecture:
- Phase 1: Cluster Detection (identify connected table groups)
- Phase 2: Batch Analysis (analyze each cluster independently)
- Phase 3: Aggregate Summary (cross-cluster insights)
- Phase 4: Searchable Index (fast lookups for chat)

Unlike profiling (table-independent batching), relationships require
context-aware batching to preserve cross-table dependencies.
"""

import json
import logging
from typing import Dict, Any, List, Set, Tuple
from collections import defaultdict
import networkx as nx

logger = logging.getLogger(__name__)


# ==========================================
# PHASE 1: CLUSTER DETECTION
# ==========================================

def detect_relationship_clusters(relationships: List[Dict[str, Any]],
                                 all_tables: Set[str]) -> List[Set[str]]:
    """
    Detect clusters of highly connected tables using graph analysis.

    Uses NetworkX to identify connected components in the relationship graph.

    Args:
        relationships: All cross-table relationships
        all_tables: Set of all table names

    Returns:
        List of table clusters (each cluster is a set of table names)
    """

    # Build relationship graph
    graph = nx.Graph()

    # Add all tables as nodes
    graph.add_nodes_from(all_tables)

    # Add edges for relationships
    for rel in relationships:
        source = rel.get("source_table")
        target = rel.get("target_table")
        if source and target:
            graph.add_edge(source, target)

    # Find connected components (clusters)
    clusters = list(nx.connected_components(graph))

    logger.info(f"Detected {len(clusters)} relationship clusters from {len(all_tables)} tables")

    # Sort clusters by size (largest first)
    clusters.sort(key=len, reverse=True)

    return clusters


def create_batches_from_clusters(clusters: List[Set[str]],
                                 max_tables_per_batch: int = 15) -> List[Dict[str, Any]]:
    """
    Create analysis batches from relationship clusters.

    Strategy:
    - Small clusters (<= max_tables_per_batch): One batch per cluster
    - Large clusters (> max_tables_per_batch): Split into sub-batches

    Args:
        clusters: List of table clusters from detect_relationship_clusters()
        max_tables_per_batch: Maximum tables per batch (default: 15)

    Returns:
        List of batch configurations with table assignments
    """

    batches = []

    for cluster_idx, cluster in enumerate(clusters):
        cluster_tables = list(cluster)

        if len(cluster_tables) <= max_tables_per_batch:
            # Small cluster: Single batch
            batches.append({
                "batch_id": len(batches) + 1,
                "cluster_id": cluster_idx + 1,
                "tables": cluster_tables,
                "batch_type": "complete_cluster",
                "table_count": len(cluster_tables)
            })
        else:
            # Large cluster: Split into sub-batches
            num_sub_batches = (len(cluster_tables) + max_tables_per_batch - 1) // max_tables_per_batch

            for sub_batch_idx in range(num_sub_batches):
                start_idx = sub_batch_idx * max_tables_per_batch
                end_idx = min(start_idx + max_tables_per_batch, len(cluster_tables))

                batches.append({
                    "batch_id": len(batches) + 1,
                    "cluster_id": cluster_idx + 1,
                    "sub_batch": f"{sub_batch_idx + 1}/{num_sub_batches}",
                    "tables": cluster_tables[start_idx:end_idx],
                    "batch_type": "partial_cluster",
                    "table_count": end_idx - start_idx
                })

    logger.info(f"Created {len(batches)} batches from {len(clusters)} clusters")

    return batches


def filter_relationships_for_batch(all_relationships: List[Dict[str, Any]],
                                   batch_tables: List[str]) -> List[Dict[str, Any]]:
    """
    Filter relationships to only include those within a batch's table scope.

    Args:
        all_relationships: Complete list of relationships
        batch_tables: Tables in the current batch

    Returns:
        Filtered relationships where both source and target are in batch_tables
    """

    batch_table_set = set(batch_tables)

    filtered = [
        rel for rel in all_relationships
        if rel.get("source_table") in batch_table_set and
           rel.get("target_table") in batch_table_set
    ]

    logger.info(f"Filtered {len(filtered)} relationships for batch with {len(batch_tables)} tables")

    return filtered


# ==========================================
# PHASE 2: BATCH ANALYSIS PROMPT BUILDER
# ==========================================

def build_batch_relationship_analysis_prompt(batch_config: Dict[str, Any],
                                            table_details: Dict[str, Any],
                                            batch_relationships: List[Dict[str, Any]],
                                            batch_num: int,
                                            total_batches: int) -> str:
    """
    Build analysis prompt for a single batch of tables/relationships.

    Similar to profiling batches, but preserves relationship context.

    Args:
        batch_config: Batch configuration from create_batches_from_clusters()
        table_details: Table metadata for batch tables
        batch_relationships: Relationships within this batch
        batch_num: Current batch number (1-indexed)
        total_batches: Total number of batches

    Returns:
        LLM analysis prompt for this batch
    """

    batch_tables = batch_config["tables"]

    # Extract table details for this batch
    batch_table_data = {
        table_name: table_details.get(table_name, {})
        for table_name in batch_tables
    }

    # Build compressed table summaries
    table_summaries = []
    for table_name in batch_tables:
        table_info = batch_table_data.get(table_name, {})

        # Get PK columns
        pk_columns = []
        fk_columns = []
        ak_count = 0

        classifications = table_info.get("column_classifications", {})
        for col_name, classification in classifications.items():
            if classification.get("pk") == "yes":
                pk_columns.append(col_name)
            if classification.get("fk") == "yes":
                fk_columns.append(col_name)
            ak_count += len(classification.get("ak", []))

        table_summaries.append({
            "table": table_name,
            "rows": table_info.get("total_rows", 0),
            "columns": table_info.get("total_columns", 0),
            "pk_candidates": pk_columns,
            "fk_columns": fk_columns,
            "composite_keys": len(table_info.get("composite_keys", {}))
        })

    # Group relationships by type
    high_confidence_rels = [r for r in batch_relationships if r.get("confidence_level") == "HIGH"]
    medium_confidence_rels = [r for r in batch_relationships if r.get("confidence_level") == "MEDIUM"]
    low_confidence_rels = [r for r in batch_relationships if r.get("confidence_level") == "LOW"]

    # Build prompt
    prompt = f"""You are analyzing **Batch {batch_num} of {total_batches}** in a multi-batch relationship analysis.

**Batch Context:**
- Cluster ID: {batch_config.get('cluster_id')}
- Batch Type: {batch_config.get('batch_type')}
- Tables in Batch: {len(batch_tables)}
- Relationships in Batch: {len(batch_relationships)}

**Tables Being Analyzed:**
{json.dumps(table_summaries, indent=2)}

**Relationships Found (Confidence Distribution):**
- HIGH confidence: {len(high_confidence_rels)} relationships
- MEDIUM confidence: {len(medium_confidence_rels)} relationships
- LOW confidence: {len(low_confidence_rels)} relationships

---

## Detailed Relationship Data

**High Confidence Relationships (≥80% overlap):**
{json.dumps(high_confidence_rels[:20], indent=2) if high_confidence_rels else "None"}

**Medium Confidence Relationships (60-80% overlap):**
{json.dumps(medium_confidence_rels[:10], indent=2) if medium_confidence_rels else "None"}

**Low Confidence Relationships (<60% overlap):**
{json.dumps(low_confidence_rels[:5], indent=2) if low_confidence_rels else "None"}

---

## Full Table Details (Key Columns)
{json.dumps(batch_table_data, indent=2)}

---

**YOUR TASK:**

Provide a **business-focused markdown analysis** for this batch. Follow these guidelines:

1. **Batch Summary** (3-5 sentences):
   - What business domain do these tables represent?
   - Overall relationship quality (confidence distribution)
   - Key findings for this batch

2. **Per-Table Analysis** (for each table in batch):
   - Table name and row count
   - Primary key recommendations
   - Foreign key relationships (with target tables and confidence)
   - Composite/alternate keys
   - Business interpretation

3. **Relationship Patterns**:
   - Common FK patterns in this batch
   - Hub tables (highly connected)
   - Isolated tables (no relationships)
   - Data quality concerns (low overlap, missing PKs)

4. **Data Quality Issues**:
   - Tables without clear primary keys
   - Low-confidence relationships requiring validation
   - Referential integrity concerns

**FORMATTING REQUIREMENTS:**
- Use ## for main sections
- Use ### for per-table sections
- Use **bold** for critical findings
- Use tables (|...|) for FK mappings
- Include confidence levels and overlap percentages
- Explain technical terms in business language

Generate your batch analysis now:
"""

    return prompt


# ==========================================
# PHASE 3: AGGREGATE SUMMARY PROMPT BUILDER
# ==========================================

def build_aggregate_relationship_summary_prompt(all_table_details: Dict[str, Any],
                                               all_relationships: List[Dict[str, Any]],
                                               batch_analyses: List[str],
                                               total_batches: int) -> str:
    """
    Build final aggregate summary prompt across all batches.

    Synthesizes insights from individual batch analyses into executive summary.

    Args:
        all_table_details: Complete table metadata
        all_relationships: All cross-table relationships
        batch_analyses: LLM-generated analyses from each batch
        total_batches: Number of batches processed

    Returns:
        LLM prompt for executive summary generation
    """

    # Calculate global statistics
    total_tables = len(all_table_details)
    total_relationships = len(all_relationships)

    # Confidence distribution
    high_conf = len([r for r in all_relationships if r.get("confidence_level") == "HIGH"])
    medium_conf = len([r for r in all_relationships if r.get("confidence_level") == "MEDIUM"])
    low_conf = len([r for r in all_relationships if r.get("confidence_level") == "LOW"])

    # Find hub tables (most connected)
    table_connection_counts = defaultdict(int)
    for rel in all_relationships:
        table_connection_counts[rel.get("source_table")] += 1
        table_connection_counts[rel.get("target_table")] += 1

    hub_tables = sorted(table_connection_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    # Find isolated tables
    connected_tables = set()
    for rel in all_relationships:
        connected_tables.add(rel.get("source_table"))
        connected_tables.add(rel.get("target_table"))

    all_table_names = set(all_table_details.keys())
    isolated_tables = list(all_table_names - connected_tables)

    # Compress batch analyses (extract key insights only)
    batch_summaries = []
    for idx, analysis in enumerate(batch_analyses):
        # Extract first 500 characters as summary
        summary = analysis[:500] + "..." if len(analysis) > 500 else analysis
        batch_summaries.append(f"**Batch {idx+1}:** {summary}")

    prompt = f"""You are generating the **FINAL EXECUTIVE SUMMARY** for a comprehensive relationship analysis.

**Analysis Scope:**
- Total Tables Analyzed: {total_tables}
- Total Relationships Found: {total_relationships}
- Batches Processed: {total_batches}

**Global Statistics:**
- HIGH confidence relationships: {high_conf} ({high_conf/total_relationships*100:.1f}%)
- MEDIUM confidence relationships: {medium_conf} ({medium_conf/total_relationships*100:.1f}%)
- LOW confidence relationships: {low_conf} ({low_conf/total_relationships*100:.1f}%)

**Network Topology:**
- Connected tables: {len(connected_tables)}/{total_tables}
- Isolated tables: {len(isolated_tables)} tables
- Top 10 Hub Tables (Most Connected):
{json.dumps([{"table": t[0], "connections": t[1]} for t in hub_tables], indent=2)}

**Isolated Tables (No Relationships):**
{json.dumps(isolated_tables[:20], indent=2)}

---

## Batch Analysis Summaries

{chr(10).join(batch_summaries)}

---

**YOUR TASK:**

Generate a comprehensive **EXECUTIVE SUMMARY** that synthesizes all batch analyses. Include:

## 📊 Executive Summary

**Overview:**
- Key findings across all {total_tables} tables
- Overall data model architecture (star schema, snowflake, normalized, etc.)
- Data quality assessment (confidence distribution)

**Data Model Insights:**
- **Hub Tables**: Identify central tables that connect multiple datasets
- **Relationship Clusters**: Describe major business domains/clusters
- **Isolated Data**: Tables without relationships (potential data silos)

**Relationship Quality:**
- Overall referential integrity assessment
- High-risk relationships (low confidence, low overlap)
- Strong relationships (high confidence, high overlap)

**Business Recommendations:**
1. Primary key recommendations for tables without clear PKs
2. Foreign key constraints to enforce referential integrity
3. Data quality improvements needed
4. Data governance priorities

**Cross-Cluster Patterns:**
- Common FK naming patterns across the dataset
- Potential missing relationships (columns that look like FKs but weren't matched)
- Data architecture improvements

**FORMATTING REQUIREMENTS:**
- Use ## for main sections
- Use ### for subsections
- Use **bold** for critical findings and hub tables
- Use bullet points with - for lists
- Include metrics (percentages, counts, scores)
- Explain technical concepts in business terms
- Highlight actionable insights

Generate your executive summary now:
"""

    return prompt


# ==========================================
# PHASE 4: SEARCHABLE INDEX BUILDER
# ==========================================

def build_relationship_searchable_index(all_table_details: Dict[str, Any],
                                       all_relationships: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build searchable index for instant chat followup questions.

    Similar to profiling's searchable_index, but focused on relationship queries.

    Supports queries like:
    - "Which tables have primary keys?"
    - "Show me all foreign key relationships"
    - "Which tables are hub tables?"
    - "What are the isolated tables?"
    - "Show me composite key recommendations"

    Args:
        all_table_details: Complete table metadata
        all_relationships: All cross-table relationships

    Returns:
        Searchable index with pre-computed lookups
    """

    index = {
        "tables_by_pk_status": {
            "has_pk": [],
            "no_pk": []
        },
        "tables_by_connectivity": {
            "hub_tables": [],        # 5+ connections
            "connected": [],         # 1-4 connections
            "isolated": []           # 0 connections
        },
        "all_foreign_keys": [],
        "foreign_keys_by_table": {},
        "primary_keys_by_table": {},
        "composite_keys_by_table": {},
        "relationship_quality": {
            "high_confidence": [],
            "medium_confidence": [],
            "low_confidence": []
        },
        "data_quality_issues": [],
        "table_summary": {}
    }

    # Calculate table connectivity
    table_connection_counts = defaultdict(int)
    for rel in all_relationships:
        table_connection_counts[rel.get("source_table")] += 1
        table_connection_counts[rel.get("target_table")] += 1

    # Process each table
    for table_name, table_data in all_table_details.items():

        # Extract PKs
        pk_columns = []
        fk_columns = []
        classifications = table_data.get("column_classifications", {})

        for col_name, classification in classifications.items():
            if classification.get("pk") == "yes":
                pk_columns.append(col_name)
            if classification.get("fk") == "yes":
                fk_columns.append(col_name)

        # PK status
        if pk_columns:
            index["tables_by_pk_status"]["has_pk"].append(table_name)
            index["primary_keys_by_table"][table_name] = pk_columns
        else:
            index["tables_by_pk_status"]["no_pk"].append(table_name)
            # Add to data quality issues
            index["data_quality_issues"].append({
                "table": table_name,
                "issue": "No clear primary key identified",
                "severity": "MEDIUM",
                "recommendation": "Review table structure and define primary key"
            })

        # Connectivity classification
        connections = table_connection_counts.get(table_name, 0)
        if connections >= 5:
            index["tables_by_connectivity"]["hub_tables"].append({
                "table": table_name,
                "connections": connections
            })
        elif connections >= 1:
            index["tables_by_connectivity"]["connected"].append({
                "table": table_name,
                "connections": connections
            })
        else:
            index["tables_by_connectivity"]["isolated"].append(table_name)

        # Composite keys
        composite_keys = table_data.get("composite_keys", {})
        if composite_keys:
            index["composite_keys_by_table"][table_name] = composite_keys

        # Table summary
        index["table_summary"][table_name] = {
            "total_rows": table_data.get("total_rows", 0),
            "total_columns": table_data.get("total_columns", 0),
            "pk_columns": pk_columns,
            "fk_count": len(fk_columns),
            "connections": connections,
            "reference": table_data.get("table_reference", table_name)
        }

    # Process relationships
    for rel in all_relationships:
        source_table = rel.get("source_table")
        source_column = rel.get("source_column")
        target_table = rel.get("target_table")
        target_column = rel.get("target_column")
        confidence = rel.get("confidence_level")
        overlap = rel.get("data_overlap_details", {}).get("overlap_percentage", 0)

        # Create FK entry
        fk_entry = {
            "source_table": source_table,
            "source_column": source_column,
            "target_table": target_table,
            "target_column": target_column,
            "confidence": confidence,
            "overlap_percentage": overlap
        }

        # Add to global FK list
        index["all_foreign_keys"].append(fk_entry)

        # Add to per-table FK list
        if source_table not in index["foreign_keys_by_table"]:
            index["foreign_keys_by_table"][source_table] = []
        index["foreign_keys_by_table"][source_table].append(fk_entry)

        # Categorize by confidence
        if confidence == "HIGH":
            index["relationship_quality"]["high_confidence"].append(fk_entry)
        elif confidence == "MEDIUM":
            index["relationship_quality"]["medium_confidence"].append(fk_entry)
        else:
            index["relationship_quality"]["low_confidence"].append(fk_entry)

            # Add low-confidence relationships to data quality issues
            if overlap < 60:
                index["data_quality_issues"].append({
                    "type": "REFERENTIAL_INTEGRITY",
                    "source_table": source_table,
                    "source_column": source_column,
                    "target_table": target_table,
                    "target_column": target_column,
                    "issue": f"Low data overlap ({overlap:.1f}%) indicates potential referential integrity issues",
                    "severity": "HIGH" if overlap < 40 else "MEDIUM",
                    "recommendation": "Investigate orphaned records or data quality issues"
                })

    # Sort hub tables by connection count
    index["tables_by_connectivity"]["hub_tables"].sort(key=lambda x: x["connections"], reverse=True)

    logger.info(f"Built searchable index: {len(index['all_foreign_keys'])} FKs, "
                f"{len(index['tables_by_pk_status']['has_pk'])} tables with PKs, "
                f"{len(index['tables_by_connectivity']['hub_tables'])} hub tables")

    return index
