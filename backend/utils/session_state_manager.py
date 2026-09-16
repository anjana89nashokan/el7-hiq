"""
Session State Manager for ADK
Stores intermediate results (profiling, relationship) to avoid redundant data transfer.

This module provides utilities to store and retrieve large analysis results
in ADK session state, eliminating the need to send 15MB+ payloads from frontend.
"""

import logging
from typing import Dict, Any, Optional
from google.adk.sessions import Session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SessionStateManager:
    """
    Manage session state for DataMap Copilot.

    Stores profiling and relationship results to enable downstream features
    (Data Dictionary, Anomaly Analysis) without requiring frontend to resend data.
    """

    # State keys (consistent naming convention)
    PROFILING_RESULTS = "profiling_results"
    RELATIONSHIP_RESULTS = "relationship_results"
    ANOMALY_RESULTS = "anomaly_results"
    DATADICT_RESULTS = "datadict_results"

    # Extract Pipeline state keys (BSA DATAMAP Multi-Agent System)
    PIPELINE_STATE = "extract_pipeline_state"
    APPROVED_REQUIREMENTS = "approved_parsed_requirements"
    APPROVED_DRIVERS = "approved_extract_drivers"
    APPROVED_DISCOVERY = "approved_discovery_results"
    APPROVED_METADATA = "approved_metadata"
    FINAL_MAPPING = "final_mapping"
    BRD_INTENT = "brd_intent"
    EXTRACT_DRIVERS = "extract_drivers"
    DISCOVERY_RESULTS = "discovery_results"
    NORMALIZED_METADATA = "normalized_metadata"
    MAPPING_SUMMARY = "mapping_summary"

    @staticmethod
    def store_profiling_results(session: Session, results: Dict[str, Any]) -> None:
        """
        Store profiling results in session state.

        Args:
            session: ADK session object
            results: Profiling results dict with format:
                {
                    "result": [
                        {
                            "table_reference": "project.dataset.table",
                            "column_analysis": {...},
                            "table_summary": {...},
                            ...
                        }
                    ]
                }
        """
        if not session:
            logger.warning("No session provided - cannot store profiling results")
            return

        try:
            session.state[SessionStateManager.PROFILING_RESULTS] = results
            num_tables = len(results.get("result", []))
            logger.info(f"✓ Stored profiling results in session state: {num_tables} tables")
        except Exception as e:
            logger.error(f"Failed to store profiling results: {e}")

    @staticmethod
    def get_profiling_results(session: Session) -> Optional[Dict[str, Any]]:
        """
        Retrieve profiling results from session state.

        Args:
            session: ADK session object

        Returns:
            Profiling results dict or None if not found
        """
        if not session:
            logger.warning("No session provided - cannot retrieve profiling results")
            return None

        try:
            results = session.state.get(SessionStateManager.PROFILING_RESULTS)
            if results:
                num_tables = len(results.get("result", []))
                logger.info(f"✓ Retrieved profiling results from session state: {num_tables} tables")
            else:
                logger.warning("No profiling results found in session state")
            return results
        except Exception as e:
            logger.error(f"Failed to retrieve profiling results: {e}")
            return None

    @staticmethod
    def store_relationship_results(session: Session, results: Dict[str, Any]) -> None:
        """
        Store relationship analysis results in session state.

        Args:
            session: ADK session object
            results: Relationship results dict with format:
                {
                    "tables_analyzed": 5,
                    "table_details": {...},
                    "cross_table_relationships": [...],
                    ...
                }
        """
        if not session:
            logger.warning("No session provided - cannot store relationship results")
            return

        try:
            session.state[SessionStateManager.RELATIONSHIP_RESULTS] = results
            num_tables = results.get("tables_analyzed", 0)
            num_relationships = len(results.get("cross_table_relationships", []))
            logger.info(
                f"✓ Stored relationship results in session state: "
                f"{num_tables} tables, {num_relationships} relationships"
            )
        except Exception as e:
            logger.error(f"Failed to store relationship results: {e}")

    @staticmethod
    def get_relationship_results(session: Session) -> Optional[Dict[str, Any]]:
        """
        Retrieve relationship analysis results from session state.

        Args:
            session: ADK session object

        Returns:
            Relationship results dict or None if not found
        """
        if not session:
            logger.warning("No session provided - cannot retrieve relationship results")
            return None

        try:
            results = session.state.get(SessionStateManager.RELATIONSHIP_RESULTS)
            if results:
                num_tables = results.get("tables_analyzed", 0)
                logger.info(f"✓ Retrieved relationship results from session state: {num_tables} tables")
            else:
                logger.warning("No relationship results found in session state")
            return results
        except Exception as e:
            logger.error(f"Failed to retrieve relationship results: {e}")
            return None

    @staticmethod
    def store_anomaly_results(session: Session, results: Dict[str, Any]) -> None:
        """
        Store anomaly analysis results in session state.

        Args:
            session: ADK session object
            results: Anomaly results dict
        """
        if not session:
            logger.warning("No session provided - cannot store anomaly results")
            return

        try:
            session.state[SessionStateManager.ANOMALY_RESULTS] = results
            num_tables = results.get("tables_analyzed", 0)
            logger.info(f"✓ Stored anomaly results in session state: {num_tables} tables")
        except Exception as e:
            logger.error(f"Failed to store anomaly results: {e}")

    @staticmethod
    def get_anomaly_results(session: Session) -> Optional[Dict[str, Any]]:
        """
        Retrieve anomaly analysis results from session state.

        Args:
            session: ADK session object

        Returns:
            Anomaly results dict or None if not found
        """
        if not session:
            logger.warning("No session provided - cannot retrieve anomaly results")
            return None

        try:
            results = session.state.get(SessionStateManager.ANOMALY_RESULTS)
            if results:
                num_tables = results.get("tables_analyzed", 0)
                logger.info(f"✓ Retrieved anomaly results from session state: {num_tables} tables")
            else:
                logger.warning("No anomaly results found in session state")
            return results
        except Exception as e:
            logger.error(f"Failed to retrieve anomaly results: {e}")
            return None

    @staticmethod
    def store_datadict_results(session: Session, results: Dict[str, Any]) -> None:
        """
        Store data dictionary results in session state.

        Args:
            session: ADK session object
            results: Data dictionary results dict
        """
        if not session:
            logger.warning("No session provided - cannot store datadict results")
            return

        try:
            session.state[SessionStateManager.DATADICT_RESULTS] = results
            num_columns = len(results.get("result", []))
            logger.info(f"✓ Stored data dictionary results in session state: {num_columns} columns")
        except Exception as e:
            logger.error(f"Failed to store datadict results: {e}")

    @staticmethod
    def get_datadict_results(session: Session) -> Optional[Dict[str, Any]]:
        """
        Retrieve data dictionary results from session state.

        Args:
            session: ADK session object

        Returns:
            Data dictionary results dict or None if not found
        """
        if not session:
            logger.warning("No session provided - cannot retrieve datadict results")
            return None

        try:
            results = session.state.get(SessionStateManager.DATADICT_RESULTS)
            if results:
                num_columns = len(results.get("result", []))
                logger.info(f"✓ Retrieved data dictionary results from session state: {num_columns} columns")
            else:
                logger.warning("No data dictionary results found in session state")
            return results
        except Exception as e:
            logger.error(f"Failed to retrieve datadict results: {e}")
            return None

    @staticmethod
    def clear_all_results(session: Session) -> None:
        """
        Clear all stored results from session state.
        Useful for testing or when starting a new analysis session.

        Args:
            session: ADK session object
        """
        if not session:
            logger.warning("No session provided - cannot clear results")
            return

        try:
            for key in [
                SessionStateManager.PROFILING_RESULTS,
                SessionStateManager.RELATIONSHIP_RESULTS,
                SessionStateManager.ANOMALY_RESULTS,
                SessionStateManager.DATADICT_RESULTS
            ]:
                if key in session.state:
                    del session.state[key]

            logger.info("✓ Cleared all results from session state")
        except Exception as e:
            logger.error(f"Failed to clear session state: {e}")

    @staticmethod
    def get_session_summary(session: Session) -> Dict[str, Any]:
        """
        Get summary of what's stored in session state.
        Useful for debugging and monitoring.

        Args:
            session: ADK session object

        Returns:
            Summary dict with counts of stored items
        """
        if not session:
            return {"error": "No session provided"}

        summary = {
            "profiling_available": SessionStateManager.PROFILING_RESULTS in session.state,
            "relationship_available": SessionStateManager.RELATIONSHIP_RESULTS in session.state,
            "anomaly_available": SessionStateManager.ANOMALY_RESULTS in session.state,
            "datadict_available": SessionStateManager.DATADICT_RESULTS in session.state
        }

        # Add counts if available
        if summary["profiling_available"]:
            profiling = session.state.get(SessionStateManager.PROFILING_RESULTS, {})
            summary["profiling_tables"] = len(profiling.get("result", []))

        if summary["relationship_available"]:
            relationship = session.state.get(SessionStateManager.RELATIONSHIP_RESULTS, {})
            summary["relationship_tables"] = relationship.get("tables_analyzed", 0)
            summary["relationship_count"] = len(relationship.get("cross_table_relationships", []))

        if summary["anomaly_available"]:
            anomaly = session.state.get(SessionStateManager.ANOMALY_RESULTS, {})
            summary["anomaly_tables"] = anomaly.get("tables_analyzed", 0)

        if summary["datadict_available"]:
            datadict = session.state.get(SessionStateManager.DATADICT_RESULTS, {})
            summary["datadict_columns"] = len(datadict.get("result", []))

        return summary
