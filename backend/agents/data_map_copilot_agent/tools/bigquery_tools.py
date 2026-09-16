from utils import local_warehouse as bigquery
from typing import List, Dict, Any
from config.settings import config
from pydantic import BaseModel, Field
from typing import List

# 1. Define the structure of the metadata rows
class TableMetadata(BaseModel):
    table_name: str = Field(description="The name of the table.")
    column_name: str = Field(description="The name of the column.")
    data_type: str = Field(description="The data type of the column.")
    description: str = Field(description="The description of the column (if available).", default=None)


    # 2. Define the tool
def bigquery_metdata_extraction_tool(table_name:str) -> List[TableMetadata]:
    """
        This is python program that extracts the bigquery tables and columns 
        for the given dataset.
        
        Args:
        `table_name` - Name of the table to extract metadata from
        
        Returns:
        List of dictionaries, Each dictionary in list contains the keys table_name, column_name, data_type and description of the column
    """
    PROJECT = config.BQ_PROJECT_ID
    BQ_LOCATION = config.LOCATION
    DATASET = config.DATASET_ID
    client = bigquery.Client(project=PROJECT)
    table_name = table_name.split(".")[-1] if table_name.startswith(PROJECT) or table_name.startswith(DATASET) else table_name

    query = f"""
    SELECT 
  table_name, 
  column_name,
  data_type, 
  description
FROM 
  `{PROJECT}.{DATASET}.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS`
WHERE 
  table_schema = "{DATASET}"
  AND table_name = "{table_name}"
    """

    print("query: ", query)


    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("dataset_id", "STRING", DATASET),
            bigquery.ScalarQueryParameter("table_id", "STRING", table_name),
        ]
    )

    query_job = client.query(query, job_config=job_config)
    query_list = []
    for row in query_job:
        query_list.append(dict(row.items()))
    
    print("query_list: ", query_list)
    return query_list


def bigquery_execution_tool(query:str)-> List[Dict[str, Any]]:
    """
    This function is to execute a given bigquery standard sql on bigquery
    and return the results as list of dictionaries
    
    Args:
    `PROJECT` - GCP Project to execute the sql query on
    `query` - bigquery standard sql query

    Returns:
    List of dictionaries

    """
    PROJECT = config.BQ_PROJECT_ID
    client = bigquery.Client(project=PROJECT)

    query_job = client.query(query)
    query_list = []

    for row in query_job:
        query_list.append(dict(row.items()))
    return query_list