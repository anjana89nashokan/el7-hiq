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

bigquery_toolset = BigQueryToolset(credentials_config=credentials_config)




root_agent = LlmAgent(
   model="gemini-2.5-flash",
   name="QueryingTextToSqlAgent",
   description=(
       "Agent that answers questions about BigQuery data by executing SQL queries"
   ),
   instruction=f""" 
   
   
   You are a SQL agent with access to several BigQuery tools. Make use of those tools to query bigquery database.
   You only return the result of the query. Do not explain or justify the result.

        INFORMATION:
        PROJECT ID: {config.BQ_PROJECT_ID}
        DATASET ID: {config.BQ_DATASET_ID}



Some Tables Schamas:


###1. `[uuid]_data_quality_score` Schema```yaml
[uuid]_data_quality_score:
  - name: table_reference
    type: STRING
    mode: REQUIRED
  - name: profiling_id
    type: STRING
    mode: REQUIRED
  - name: overall_score
    type: FLOAT64
    mode: NULLABLE
  - name: dimension_scores
    type: RECORD
    mode: NULLABLE
    fields:
      - name: completeness
        type: FLOAT64
        mode: NULLABLE
      - name: uniqueness
        type: FLOAT64
        mode: NULLABLE
      - name: distribution
        type: FLOAT64
        mode: NULLABLE
      - name: validity
        type: FLOAT64
        mode: NULLABLE
  - name: per_column_scores
    type: JSON
    mode: NULLABLE

```

---

###2. `[uuid]_recommendations` Schema```yaml
[uuid]_recommendations:
  - name: table_reference
    type: STRING
    mode: REQUIRED
  - name: profiling_id
    type: STRING
    mode: REQUIRED
  - name: recommendation_index
    type: INT64
    mode: REQUIRED
  - name: recommendation_text
    type: STRING
    mode: NULLABLE

```

---

###3. `[uuid]_table_summary` Schema```yaml
[uuid]_table_summary:
  - name: table_reference
    type: STRING
    mode: REQUIRED
  - name: profiling_id
    type: STRING
    mode: REQUIRED
  - name: total_rows
    type: INT64
    mode: NULLABLE
  - name: total_columns
    type: INT64
    mode: NULLABLE

```

---

###4. `[uuid]_column_analysis` Schema```yaml
[uuid]_column_analysis:
  - name: table_reference
    type: STRING
    mode: REQUIRED
  - name: profiling_id
    type: STRING
    mode: REQUIRED
  - name: column_name
    type: STRING
    mode: REQUIRED
  - name: data_type
    type: STRING
    mode: NULLABLE
  - name: total_count
    type: INT64
    mode: NULLABLE
  - name: unique_count
    type: INT64
    mode: NULLABLE
  - name: uniqueness_percentage
    type: FLOAT64
    mode: NULLABLE
  - name: null_count
    type: INT64
    mode: NULLABLE
  - name: null_percentage
    type: FLOAT64
    mode: NULLABLE
  - name: blank_count
    type: INT64
    mode: NULLABLE
  - name: blank_percentage
    type: FLOAT64
    mode: NULLABLE
  - name: min_value
    type: STRING
    mode: NULLABLE
  - name: max_value
    type: STRING
    mode: NULLABLE
  - name: avg_value
    type: FLOAT64
    mode: NULLABLE
  - name: avg_length
    type: FLOAT64
    mode: NULLABLE
  - name: default_value
    type: STRING
    mode: NULLABLE
  - name: default_count
    type: INT64
    mode: NULLABLE
  - name: default_pct
    type: FLOAT64
    mode: NULLABLE
  - name: primary_key_candidate
    type: BOOL
    mode: NULLABLE
  - name: foreign_key_candidate
    type: BOOL
    mode: NULLABLE

```

---

###5. `[uuid]_default_value_analysis` Schema```yaml
[uuid]_default_value_analysis:
  - name: table_reference
    type: STRING
    mode: REQUIRED
  - name: profiling_id
    type: STRING
    mode: REQUIRED
  - name: column_name
    type: STRING
    mode: REQUIRED
  - name: total_rows
    type: INT64
    mode: NULLABLE
  - name: default_value
    type: STRING
    mode: NULLABLE
  - name: default_count
    type: INT64
    mode: NULLABLE
  - name: default_pct
    type: FLOAT64
    mode: NULLABLE

```

---

###6. `[uuid]_enhanced_analysis` Schema```yaml
[uuid]_enhanced_analysis:
  - name: table_reference
    type: STRING
    mode: REQUIRED
  - name: profiling_id
    type: STRING
    mode: REQUIRED
  - name: table_context
    type: RECORD
    mode: NULLABLE
    fields:
      - name: detected_level
        type: STRING
        mode: NULLABLE
      - name: confidence
        type: STRING
        mode: NULLABLE
      - name: primary_entity
        type: STRING
        mode: NULLABLE
      - name: business_context
        type: STRING
        mode: NULLABLE
  - name: primary_key_recommendations
    type: RECORD
    mode: REPEATED
    fields:
      - name: column
        type: STRING
        mode: NULLABLE
      - name: rank
        type: INT64
        mode: NULLABLE
      - name: confidence
        type: STRING
        mode: NULLABLE
      - name: reasoning
        type: STRING
        mode: NULLABLE
  - name: composite_key_recommendations
    type: RECORD
    mode: NULLABLE
    fields:
      - name: two_column
        type: RECORD
        mode: REPEATED
        fields:
          - name: columns
            type: STRING
            mode: REPEATED
          - name: uniqueness_percentage
            type: FLOAT64
            mode: NULLABLE
          - name: is_candidate
            type: BOOL
            mode: NULLABLE
          - name: business_meaning
            type: STRING
            mode: NULLABLE
          - name: composite_score
            type: FLOAT64
            mode: NULLABLE
      - name: three_column
        type: RECORD
        mode: REPEATED
        fields:
          - name: columns
            type: STRING
            mode: REPEATED
          - name: uniqueness_percentage
            type: FLOAT64
            mode: NULLABLE
          - name: is_candidate
            type: BOOL
            mode: NULLABLE
          - name: business_meaning
            type: STRING
            mode: NULLABLE
          - name: composite_score
            type: FLOAT64
            mode: NULLABLE
      - name: four_column
        type: RECORD
        mode: REPEATED
        fields:
          - name: columns
            type: STRING
            mode: REPEATED
          - name: uniqueness_percentage
            type: FLOAT64
            mode: NULLABLE
          - name: is_candidate
            type: BOOL
            mode: NULLABLE
          - name: business_meaning
            type: STRING
            mode: NULLABLE
          - name: composite_score
            type: FLOAT64
            mode: NULLABLE
  - name: validation_results
    type: JSON
    mode: NULLABLE

```


###7. `[uuid]_relationship_analysis` Schema```yaml

[uuid]_relationship_analysis:
  - name: profiling_id
    type: STRING
    mode: REQUIRED
  - name: processing_mode
    type: STRING
    mode: NULLABLE
  - name: status
    type: STRING
    mode: NULLABLE
  - name: processing_stats
    type: RECORD
    mode: NULLABLE
    fields:
      - name: relationships_found
        type: INT64
        mode: NULLABLE
      - name: tables_processed
        type: INT64
        mode: NULLABLE
      - name: total_processing_time
        type: FLOAT64
        mode: NULLABLE
      - name: optimization_techniques
        type: STRING
        mode: REPEATED
  - name: cross_table_relationships
    type: RECORD
    mode: REPEATED
    fields:
      - name: source_table
        type: STRING
        mode: NULLABLE
      - name: source_column
        type: STRING
        mode: NULLABLE
      - name: target_table
        type: STRING
        mode: NULLABLE
      - name: target_column
        type: STRING
        mode: NULLABLE
      - name: relationship_type
        type: STRING
        mode: NULLABLE
      - name: confidence_score
        type: FLOAT64
        mode: NULLABLE
  - name: tables_analyzed
    type: INT64
    mode: NULLABLE
  - name: analysis_timestamp
    type: INT64
    mode: NULLABLE
  - name: analysis_depth
    type: STRING
    mode: NULLABLE
  - name: table_details
    type: JSON
    mode: NULLABLE
```

###8. `[uuid]_data_anomaly_analysis` Schema```yaml

[uuid]_anomaly_analysis:
  - name: profiling_id
    type: STRING
    mode: REQUIRED
  - name: status
    type: STRING
    mode: NULLABLE
  - name: sensitivity_level
    type: STRING
    mode: NULLABLE
  - name: analysis_timestamp
    type: INT64
    mode: NULLABLE
  - name: processing_mode
    type: STRING
    mode: NULLABLE
  - name: tables_analyzed
    type: INT64
    mode: NULLABLE
  - name: processing_stats
    type: RECORD
    mode: NULLABLE
    fields:
      - name: anomaly_categories_detected
        type: INT64
        mode: NULLABLE
      - name: total_anomalies_detected
        type: INT64
        mode: NULLABLE
      - name: tables_processed
        type: INT64
        mode: NULLABLE
      - name: total_processing_time
        type: FLOAT64
        mode: NULLABLE
  - name: summary_statistics
    type: RECORD
    mode: NULLABLE
    fields:
      - name: total_tables_analyzed
        type: INT64
        mode: NULLABLE
      - name: total_anomalies
        type: INT64
        mode: NULLABLE
      - name: overall_data_quality_score
        type: FLOAT64
        mode: NULLABLE
      - name: anomaly_categories
        type: JSON
        mode: NULLABLE
      - name: severity_distribution
        type: RECORD
        mode: NULLABLE
        fields:
          - name: low
            type: INT64
            mode: NULLABLE
          - name: medium
            type: INT64
            mode: NULLABLE
          - name: high
            type: INT64
            mode: NULLABLE
  - name: table_anomaly_reports
    type: JSON
    mode: NULLABLE
```


IMPORTANT: 

- The given tables are only a sample of the dataset. You can use the BigQuery tools to get the schema of any table.
- You retun the results/tables in Markdown format.
- You do not interpret the results.
- Make sure to return all the requested tables and information.

   """,
   tools=[bigquery_toolset],
)


