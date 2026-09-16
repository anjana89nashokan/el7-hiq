from google.adk.agents import LlmAgent
from google.adk.tools.bigquery import BigQueryToolset
from config.settings import config

from google.adk.tools.bigquery import BigQueryCredentialsConfig
from google.adk.tools.bigquery import BigQueryToolset
from google.adk.tools.bigquery.config import BigQueryToolConfig, WriteMode

import google.auth
from utils.bg_query_utils import get_bigquery_client


tool_config = BigQueryToolConfig(write_mode=WriteMode.ALLOWED)


from utils.gcp_compat import bigquery_credentials  # standalone: optional creds
creds = bigquery_credentials()
credentials_config = BigQueryCredentialsConfig(credentials=creds)

bigquery_toolset = BigQueryToolset(credentials_config=credentials_config,   tool_filter=[
'list_dataset_ids',
'get_dataset_info',
'list_table_ids',
'get_table_info',
'bigquery_execution_tool',
     ])




root_agent = LlmAgent(
   model="gemini-2.5-flash",
   name="TextToSql",
   description=(
       "Agent that answers questions about BigQuery data by executing SQL queries"
   ),
   instruction=f""" 
   
    
   You are a SQL agent with access to several BigQuery tools. Make use of those tools to query bigquery database.
   You only return the result of the query. Do not explain or justify the result.

        INFORMATION:
        PROJECT ID: {config.BQ_PROJECT_ID}
        DATASET ID: {config.BQ_DATASET_ID}


IMPORTANT: 

- You can use the BigQuery tools to get the schema of any table.
- You retun the results/tables in json format.
- You do not interpret the results.
- Make sure to return all the requested tables and information.

   """,
   tools=[bigquery_toolset],
)


