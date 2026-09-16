"""
Mapping agent tools package.

Re-exports the original ADK tools (check_indimap_reuse, generate_field_mappings,
finalize_mapping) from their new home inside the package, plus the new
Excel/BQ persistence tool.  All existing imports of the form
    from .tools import check_indimap_reuse, ...
continue to work without any change to agent.py.
"""

from agents.extract_agent.mapping_agent.tools.indemap_search_tools import (
    search_indemap_mappings,
    run_indemap_embedding_pipeline,
)
from agents.extract_agent.mapping_agent.tools.standards_search_tools import (
    search_standards_for_mapping,
)
from agents.extract_agent.mapping_agent.tools.mapping_tools import (
    build_mapping_row_tool,
    BuildMappingRowInput,
)

# L3 — available when code merges with fyi_search_tools.py
try:
    from agents.extract_agent.mapping_agent.tools.fyi_search_tools import (
        search_fyi_table_columns,
        run_fyi_tbl_cols_pipeline,
    )
    _fyi_exports = ["search_fyi_table_columns", "run_fyi_tbl_cols_pipeline"]
except ImportError:
    _fyi_exports = []

__all__ = [
    "search_indemap_mappings",
    "run_indemap_embedding_pipeline",
    "search_standards_for_mapping",
    "build_mapping_row_tool",
    "BuildMappingRowInput",
    *_fyi_exports,
]
