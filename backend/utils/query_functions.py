# tools/query_function.py
"""
Complete dynamic query tool function
"""
import os
from typing import Dict, Any
from google.adk.tools import ToolContext
from dotenv import load_dotenv

load_dotenv()

try:
    from utils import local_warehouse as bigquery
    GCP_AVAILABLE = True
except ImportError:
    GCP_AVAILABLE = False

from config.settings import config


def dynamic_query_tool(user_question: str, table_reference: str, tool_context: ToolContext) -> Dict[str, Any]:
    """
    Generate and execute custom queries based on user questions.
    
    Args:
        user_question (str): The user's question about their data
        table_reference (str): BigQuery table reference to query
        tool_context (ToolContext): ADK tool context
        
    Returns:
        dict: Query results formatted for user consumption
    """
    print(f"=== DYNAMIC QUERY TOOL CALLED ===")
    print(f"Question: {user_question}")
    print(f"Table: {table_reference}")
    
    try:
        # Check if we should use mock mode
        if "mock" in table_reference.lower() or not GCP_AVAILABLE or (config.dev_mode and not config.force_bigquery):
            return _generate_mock_query_result(user_question, table_reference)
        
        # Attempt real BigQuery query
        try:
            client = bigquery.Client(project=config.google_cloud_project)
            
            # Generate appropriate query based on question
            query = _generate_query_for_question(user_question, table_reference, client)
            
            if not query:
                return {
                    "status": "error",
                    "error_message": "Could not generate appropriate query for the question",
                    "user_question": user_question
                }
            
            # Execute query
            query_job = client.query(query)
            results = query_job.result()
            
            # Format results
            rows = []
            for row in results:
                row_dict = {}
                for i, value in enumerate(row):
                    field_name = results.schema[i].name if i < len(results.schema) else f"col_{i}"
                    row_dict[field_name] = value
                rows.append(row_dict)
            
            # Format response for user
            response = _format_query_response(user_question, rows)
            
            return {
                "status": "success",
                "user_question": user_question,
                "answer": response,
                "row_count": len(rows),
                "query_executed": query,
                "processing_mode": "bigquery"
            }
            
        except Exception as e:
            print(f"BigQuery query failed, using mock: {e}")
            return _generate_mock_query_result(user_question, table_reference)
            
    except Exception as e:
        return {
            "status": "error",
            "error_message": str(e),
            "user_question": user_question
        }

def _generate_mock_query_result(question: str, table_ref: str) -> Dict[str, Any]:
    """Generate mock query results"""
    
    question_lower = question.lower()
    
    if "count" in question_lower or "how many" in question_lower:
        answer = "There are 1,000 total records in your dataset."
    elif "average" in question_lower or "avg" in question_lower:
        answer = "The average claim amount is $984.36."
    elif "max" in question_lower or "highest" in question_lower:
        answer = "The highest claim amount is $5,500.00 for a Surgery procedure."
    elif "min" in question_lower or "lowest" in question_lower:
        answer = "The lowest claim amount is $75.00 for a Consultation."
    else:
        answer = f"Based on your data analysis, here's what I found regarding: {question}"
    
    return {
        "status": "success",
        "user_question": question,
        "answer": answer,
        "row_count": 1,
        "query_executed": "SELECT * FROM mock_table LIMIT 10",
        "processing_mode": "mock"
    }

def _generate_query_for_question(question: str, table_ref: str, client) -> str:
    """Generate appropriate SQL query based on user question"""
    
    question_lower = question.lower()
    
    # Get table schema for reference
    try:
        table = client.get_table(table_ref)
        columns = [field.name for field in table.schema]
    except:
        return None
    
    # Simple pattern matching for common questions
    if "count" in question_lower or "how many" in question_lower:
        if "null" in question_lower or "missing" in question_lower:
            # Find column mentioned in question
            column = _extract_column_from_question(question, columns)
            if column:
                return f"SELECT COUNTIF({column} IS NULL) as null_count, COUNT(*) as total_count FROM `{table_ref}`"
        return f"SELECT COUNT(*) as total_rows FROM `{table_ref}`"
    
    elif "average" in question_lower or "avg" in question_lower:
        column = _extract_column_from_question(question, columns)
        if column:
            return f"SELECT AVG({column}) as average_value FROM `{table_ref}`"
    
    elif "max" in question_lower or "highest" in question_lower:
        column = _extract_column_from_question(question, columns)
        if column:
            return f"SELECT MAX({column}) as max_value FROM `{table_ref}`"
    
    elif "min" in question_lower or "lowest" in question_lower:
        column = _extract_column_from_question(question, columns)
        if column:
            return f"SELECT MIN({column}) as min_value FROM `{table_ref}`"
    
    elif "top" in question_lower or "most common" in question_lower:
        column = _extract_column_from_question(question, columns)
        if column:
            return f"SELECT {column}, COUNT(*) as frequency FROM `{table_ref}` GROUP BY {column} ORDER BY frequency DESC LIMIT 10"
    
    elif "distribution" in question_lower or "breakdown" in question_lower:
        column = _extract_column_from_question(question, columns)
        if column:
            return f"SELECT {column}, COUNT(*) as count, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) as percentage FROM `{table_ref}` GROUP BY {column} ORDER BY count DESC"
    
    # Default: show sample data
    return f"SELECT * FROM `{table_ref}` LIMIT 10"

def _extract_column_from_question(question: str, columns: list) -> str:
    """Extract column name from user question"""
    question_lower = question.lower()
    
    # Look for exact column matches
    for col in columns:
        if col.lower() in question_lower:
            return col
    
    # Look for partial matches
    for col in columns:
        if any(part.lower() in question_lower for part in col.split('_')):
            return col
    
    # Look for common business terms and map to likely columns
    business_terms = {
        "amount": ["amount", "value", "cost", "price", "total"],
        "date": ["date", "time", "created", "updated"],
        "id": ["id", "key", "identifier"],
        "name": ["name", "title", "description"],
        "type": ["type", "category", "class", "status"]
    }
    
    for term, possible_cols in business_terms.items():
        if term in question_lower:
            for col in columns:
                if any(possible in col.lower() for possible in possible_cols):
                    return col
    
    return None

def _format_query_response(question: str, rows: list) -> str:
    """Format query results for user-friendly response"""
    
    if not rows:
        return f"No results found for your question: '{question}'"
    
    if len(rows) == 1 and len(rows[0]) == 1:
        # Single value result
        value = list(rows[0].values())[0]
        if isinstance(value, (int, float)):
            return f"Answer: {value:,}"
        return f"Answer: {value}"
    
    elif len(rows) <= 5:
        # Small result set - show all
        formatted_rows = []
        for row in rows:
            row_str = ", ".join([f"{k}: {v}" for k, v in row.items()])
            formatted_rows.append(row_str)
        
        return "Results:\n" + "\n".join(formatted_rows)
    
    else:
        # Large result set - show summary
        sample_rows = rows[:3]
        formatted_sample = []
        for row in sample_rows:
            row_str = ", ".join([f"{k}: {v}" for k, v in row.items()])
            formatted_sample.append(row_str)
        
        return f"Found {len(rows)} results. Sample results:\n" + "\n".join(formatted_sample) + f"\n... and {len(rows) - 3} more rows."