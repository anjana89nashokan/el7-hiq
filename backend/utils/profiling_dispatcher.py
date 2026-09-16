import logging
from typing import Dict, Any, List
from google.adk.tools import ToolContext

# Import both versions of the tool with aliases to avoid name collision
from utils.profiling_functions import (
    intelligent_profiling_tool as detailed_profiling_tool
)
from utils.profiling_functions_batched import (
    intelligent_profiling_tool as batched_profiling_tool
)

from utils.data_anomaly_functions import (
    data_anomaly_analysis_tool as detailed_anomaly_tool
)
from utils.data_anomaly_functions_batched import (
    data_anomaly_analysis_tool as batched_anomaly_tool
)

from utils.smart_similarity_functions import (
    fetch_metadata_tool as metadata_tool
)

from utils.smart_similarity_functions_batched import (
    fetch_metadata_tool as metadata_tool_batched
)

logger = logging.getLogger(__name__)

logger.warning("profiling_dispatcher.py ENTERED")
print("profiling_dispatcher.py ENTERRED")

def intelligent_profiling_tool(
    table_references: List[str], tool_context: ToolContext
) -> List[Dict[str, Any]]:
    """
    A dynamic dispatcher tool that selects the profiling implementation based on context.

    This tool reads the 'is_stream' flag from the tool_input provided to the agent.
    - If is_stream is True, it uses batched profiler.

    Args:
        table_references (list[str]): List of BigQuery table references.
        tool_context (ToolContext): The ADK tool context, which contains tool_input.
 
    Returns:
        list[dict]: Data profiling results from the selected tool.
    """
    # Default to batched (False) if the flag is not provided.
    is_stream = tool_context.session.state.get("is_stream", False)

    logger.warning("profiling_dispatcher.py--> intelligent_profiling_tool is_stream ")
    print(f"profiling_dispatcher.py is_stream == {is_stream}")

    if is_stream:
        logger.warning("Dispatching to BATCHED profiling tool (is_stream=True).")
        print("Dispatching to BATCHED profiling tool (is_stream=True). printtt")
        return batched_profiling_tool(table_references, tool_context)
    else:
        logger.warning("Dispatching to normal  profiling tool (is_stream=False).")
        print("Dispatching to normal profiling tool (is_stream=False). printtt")
        return detailed_profiling_tool(table_references, tool_context)
    
def data_anomaly_analysis_tool(
    table_references: List[str], tool_context: ToolContext
) -> Dict[str, Any]:
    """
    A dynamic dispatcher tool that selects the anomaly implementation based on context.

    This tool reads the 'is_stream' flag from the tool_input provided to the agent.
    - If is_stream is True, it uses the batched version.
    - If is_stream is False, it uses the detailed version.

    Args:
        table_references (list[str]): List of BigQuery table references.
        tool_context (ToolContext): The ADK tool context, which contains tool_input.

    Returns:
        dict: Data anomaly analysis results matching DataAnomalyAnalysisToolResponse structure.
              Contains: status, sensitivity_level, analysis_timestamp, processing_mode,
              tables_analyzed, processing_stats, summary_statistics, table_anomaly_reports.
    """
    # Default to batched (False) if the flag is not provided.
    is_stream = tool_context.session.state.get("is_stream", False)

    logger.warning("profiling_dispatcher.py--> data_anomaly_analysis_tool is_stream ")
    print(f"profiling_dispatcher.py is_stream == {is_stream}")

    if is_stream:
        logger.warning("Dispatching to batched anomaly tool (is_stream=True).")
        return batched_anomaly_tool(table_references,"medium", tool_context)
    else:
        logger.warning("Dispatching to normal anomaly tool (is_stream=False).")
        return detailed_anomaly_tool(table_references,"medium", tool_context)

def fetch_metadata_tool (
    table_references: List[str], tool_context: ToolContext
) -> List[Dict[str, Any]]:
    """Dispatcher tool that selects fetch_metdata_tool based on standard
    or streaming flow"""
    is_stream = tool_context.session.state.get("is_stream", False)
    if is_stream:
        logger.info("Dispatching to batched fetch_metadata_tool (is_stream=True).")
        return metadata_tool_batched(table_references, tool_context)
    else:
        logger.info("Dispatching to normal fetch_metadata_tool (is_stream=False).")
        return metadata_tool(table_references, tool_context)


# ============================================================================
# DATA DICTIONARY DISPATCHERS (Plan 2)
# ============================================================================

def data_dictionary_tool(tool_input: Dict[str, Any], tool_context: ToolContext) -> Dict[str, Any]:
    """
    Dispatcher for data dictionary generation from profiling/relationship data.

    Routes to:
    - Batched streaming version (is_stream=True) for large data flow
    - Standard synchronous version (is_stream=False) for normal flow

    Args:
        tool_input: Dict containing profiling_output, relationships_output, validation_output
        tool_context: ADK tool context

    Returns:
        Data dictionary result (format depends on is_stream flag)
    """
    is_stream = tool_context.session.state.get("is_stream", False)

    if is_stream:
        logger.info("[data_dictionary_tool] Dispatching to BATCHED streaming version (is_stream=True)")
        from utils.datadict_batched import batched_data_dictionary_tool
        return batched_data_dictionary_tool(
            profiling_output=tool_input.get("profiling_output", []),
            relationships_output=tool_input.get("relationships_output", {}),
            validation_output=tool_input.get("validation_output", {}),
            tool_context=tool_context
        )
    else:
        logger.info("[data_dictionary_tool] Dispatching to STANDARD synchronous version (is_stream=False)")
        from utils.datadict_functions import data_dictionary_tool as standard_dd_tool
        return standard_dd_tool(tool_input)


def extract_and_map_vendor_dd(file_path: str, tool_context: ToolContext) -> Dict[str, Any]:
    """
    Dispatcher for vendor data dictionary extraction.

    Routes to:
    - Native Gemini streaming version (is_stream=True) for large data flow
    - Standard version (is_stream=False) for normal flow (can use same native approach)

    Args:
        file_path: Path to uploaded vendor DD file
        tool_context: ADK tool context

    Returns:
        Extracted and mapped data dictionary
    """
    is_stream = tool_context.session.state.get("is_stream", False)

    if is_stream:
        logger.info("[extract_and_map_vendor_dd] Dispatching to NATIVE GEMINI streaming version (is_stream=True)")
        from utils.datadict_native_streaming import extract_and_map_vendor_dd_streaming
        return extract_and_map_vendor_dd_streaming(file_path, tool_context)
    else:
        logger.info("[extract_and_map_vendor_dd] Dispatching to STANDARD version (is_stream=False)")
        # For now, use same native approach but could optimize for sync if needed
        from utils.datadict_native_streaming import extract_and_map_vendor_dd_streaming
        return extract_and_map_vendor_dd_streaming(file_path, tool_context)
