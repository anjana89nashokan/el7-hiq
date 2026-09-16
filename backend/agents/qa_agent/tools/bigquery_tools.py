from utils import local_warehouse as bigquery
from typing import List, Dict, Any

def bigquery_metdata_extraction_tool(PROJECT: str,
    BQ_LOCATION: str,
    DATASET: str) -> List[Dict[str, Any]]:
    """
        This is python program that extracts the bigquery tables and columns 
        for the given dataset provides the information in the form of list of dictionary.
        
        Args:
        `PROJECT`: GCP Project to execute the query on
        `BQ_LOCATION`: Bigquery Location
        `DATASET`: Name of the dataset
        
        Returns:
        List of dictionaries, Each dictionary in list contains the keys table_name, column_name, data_type and description of the column
    """
    # Local SQLite warehouse: read table/column metadata from sqlite_master + PRAGMA
    # (there is no INFORMATION_SCHEMA in SQLite).
    from sqlalchemy import text
    from utils.local_warehouse import get_engine

    engine = get_engine()
    query_list: List[Dict[str, Any]] = []
    with engine.connect() as conn:
        tables = [
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            )
        ]
        for table_name in tables:
            for col in conn.execute(text(f'PRAGMA table_info("{table_name}")')):
                # PRAGMA columns: cid, name, type, notnull, dflt_value, pk
                query_list.append(
                    {
                        "table_name": table_name,
                        "column_name": col[1],
                        "data_type": (col[2] or "TEXT").upper(),
                        "description": "",
                    }
                )
    return query_list


def bigquery_execution_tool(PROJECT:str,
    query:str)-> List[Dict[str, Any]]:
    """
    This function is to execute a given bigquery standard sql on bigquery
    and return the results as list of dictionaries
    
    Args:
    `PROJECT` - GCP Project to execute the sql query on
    `query` - bigquery standard sql query

    Returns:
    List of dictionaries

    """


    print("Query: ", query)
    print("Project: ", PROJECT)
    
    client = bigquery.Client(project=PROJECT)

    query_job = client.query(query)
    query_list = []

    for row in query_job:
        query_list.append(dict(row.items()))
    return query_list