"""
BigQuery Data Profiling with Chunked Processing for Large Tables

This script profiles BigQuery tables with intelligent chunking for tables > 20K rows.
"""
from pydantic import BaseModel, Field
try:
    from google.cloud import dataplex_v1  # optional; unused in standalone mode
except Exception:  # noqa: BLE001
    dataplex_v1 = None
from utils import local_warehouse as bigquery
import time
from datetime import datetime
from dotenv import load_dotenv
import os
from pathlib import Path
import json
import numpy as np
import pandas as pd
from ydata_profiling import ProfileReport
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import vertexai
from vertexai.generative_models import GenerativeModel, HarmCategory, HarmBlockThreshold
from google import genai
from google.genai import types
from config.settings import Config

class OutputFormat(BaseModel):
    table_context: dict
    primary_key_recommendations: list
    composite_key_recommendations: dict

# Initialize clients
config = Config()
PROJECT_ID = config.PROJECT_ID
LOCATION = config.LOCATION
DATASET_ID = config.DATASET_ID
VERTEX_LOCATION = config.LOCATION
GEMINI_MODEL = config.AGENT_MODEL

try:
    bq_client = bigquery.Client(project=PROJECT_ID)
    vertexai.init(project=PROJECT_ID, location=VERTEX_LOCATION)
    gemini_model = genai.Client(vertexai=True, location=VERTEX_LOCATION, project=PROJECT_ID)
except Exception as e:
    print(f"Warning: Client initialization failed: {e}")

file_lock = Lock()
MAX_WORKERS = os.cpu_count() + 1
print(f"MAX_WORKERS: {MAX_WORKERS}")

# JSON Encoder for NumPy types
class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.bool_):
            return bool(obj)
        if pd.isna(obj):
            return None
        return super(NpEncoder, self).default(obj)

def get_table_row_count(project_id, dataset_id, table_id):
    """Get total row count for a table."""
    try:
        if "covid" in table_id:
            query = "SELECT COUNT(*) as cnt FROM `bigquery-public-data.covid19_ecdc_eu.covid_19_geographic_distribution_worldwide`"
        else:
            query = f"SELECT COUNT(*) as cnt FROM `{project_id}.{dataset_id}.{table_id}`"
        
        result = bq_client.query(query).result()
        row_count = list(result)[0]['cnt']
        print(f"Table {table_id} has {row_count:,} rows")
        return row_count
    except Exception as e:
        print(f"Error getting row count for {table_id}: {e}")
        raise

def fetch_table_chunk(project_id, dataset_id, table_id, offset, limit):
    """Fetch a specific chunk of data from BigQuery."""
    try:
        
        query = f"""
            SELECT * FROM `{project_id}.{dataset_id}.{table_id}`
            LIMIT {limit} OFFSET {offset}
        """
        full_table_ref = f"{project_id}.{dataset_id}.{table_id}"
        
        print(f"Fetching chunk: offset={offset}, limit={limit}")
        df = bq_client.query(query).to_dataframe()
        print(f"Chunk fetched: {len(df)} rows, {len(df.columns)} columns")
        return df, full_table_ref
    except Exception as e:
        print(f"Error fetching chunk at offset {offset}: {e}")
        raise

def run_ydata_profiling_on_chunk(df, chunk_id):
    """Run ydata-profiling on a chunk."""
    try:
        print(f"Profiling chunk {chunk_id}...")
        profile = ProfileReport(df, title=f"Chunk {chunk_id}", minimal=True)
        return profile.get_description()
    except Exception as e:
        print(f"Error profiling chunk {chunk_id}: {e}")
        raise

def map_ydata_to_schema(col_name, ydata_col_stats, total_rows):
    """Map YData Profiling output to schema."""
    try:
        def safe_cast(val, default=0):
            if pd.isna(val): return default
            if isinstance(val, (np.integer, int)): return int(val)
            if isinstance(val, (np.floating, float)): return float(val)
            return default

        n_distinct = safe_cast(ydata_col_stats.get('n_distinct', 0))
        n_missing = safe_cast(ydata_col_stats.get('n_missing', 0))
        p_missing = safe_cast(ydata_col_stats.get('p_missing', 0.0), 0.0)
        p_distinct = safe_cast(ydata_col_stats.get('p_distinct', 0.0), 0.0)
        d_type = str(ydata_col_stats.get('type', 'UNKNOWN'))

        stats = {
            'data_type': d_type,
            'total_count': int(total_rows),
            'unique_count': n_distinct,
            'uniqueness_percentage': float(p_distinct * 100),
            'null_count': n_missing,
            'null_percentage': float(p_missing * 100),
            'blank_count': 0,
            'blank_percentage': 0.0
        }

        if d_type in ['Categorical', 'Text']:
            stats['avg_length'] = safe_cast(ydata_col_stats.get('mean_length', 0))
        
        if d_type == 'Numeric':
            stats['min_value'] = safe_cast(ydata_col_stats.get('min', None), None)
            stats['max_value'] = safe_cast(ydata_col_stats.get('max', None), None)
            stats['avg_value'] = safe_cast(ydata_col_stats.get('mean', None), None)

        try:
            counts = ydata_col_stats.get('value_counts_without_nan', pd.Series(dtype='object'))
            if not counts.empty:
                top_val = counts.index[0]
                top_count = safe_cast(counts.iloc[0])
                stats['default_value'] = str(top_val)
                stats['default_count'] = top_count
                stats['default_pct'] = float((top_count / total_rows * 100) if total_rows > 0 else 0)
            else:
                stats['default_value'] = None
                stats['default_count'] = 0
                stats['default_pct'] = 0.0
        except Exception:
            stats['default_value'] = None
            stats['default_count'] = 0
            stats['default_pct'] = 0.0

        return stats
    except Exception as e:
        print(f"Error mapping stats for {col_name}: {e}")
        return {}

def merge_chunk_statistics(chunk_results):
    """Merge statistics from multiple chunks into unified results."""
    print(f"Merging {len(chunk_results)} chunk results...")
    
    if not chunk_results:
        return {}
    
    # Get all columns from first chunk
    all_columns = set(chunk_results[0]['columns'].keys())
    merged = {}
    
    for col_name in all_columns:
        # Collect stats from all chunks
        col_stats = [chunk['columns'][col_name] for chunk in chunk_results if col_name in chunk['columns']]
        
        if not col_stats:
            continue
        
        # Aggregate statistics
        total_count = sum(s['total_count'] for s in col_stats)
        unique_count = sum(s['unique_count'] for s in col_stats)
        null_count = sum(s['null_count'] for s in col_stats)
        
        # Weighted averages for percentages
        null_pct = (null_count / total_count * 100) if total_count > 0 else 0
        unique_pct = (unique_count / total_count * 100) if total_count > 0 else 0
        
        merged[col_name] = {
            'data_type': col_stats[0]['data_type'],
            'total_count': total_count,
            'unique_count': unique_count,
            'uniqueness_percentage': unique_pct,
            'null_count': null_count,
            'null_percentage': null_pct,
            'blank_count': sum(s.get('blank_count', 0) for s in col_stats),
            'blank_percentage': 0.0  # Recalculate if needed
        }
        
        # Handle numeric fields
        if 'min_value' in col_stats[0]:
            merged[col_name]['min_value'] = min(s.get('min_value', float('inf')) for s in col_stats if s.get('min_value') is not None)
            merged[col_name]['max_value'] = max(s.get('max_value', float('-inf')) for s in col_stats if s.get('max_value') is not None)
            merged[col_name]['avg_value'] = np.mean([s.get('avg_value', 0) for s in col_stats if s.get('avg_value') is not None])
        
        # Handle string fields
        if 'avg_length' in col_stats[0]:
            merged[col_name]['avg_length'] = np.mean([s.get('avg_length', 0) for s in col_stats])
        
        # Default value (use most common across chunks)
        default_vals = [s.get('default_value') for s in col_stats if s.get('default_value')]
        if default_vals:
            merged[col_name]['default_value'] = max(set(default_vals), key=default_vals.count)
            merged[col_name]['default_count'] = sum(s.get('default_count', 0) for s in col_stats)
            merged[col_name]['default_pct'] = (merged[col_name]['default_count'] / total_count * 100) if total_count > 0 else 0
    
    print(f"Merged statistics for {len(merged)} columns")
    return merged

def process_chunk(chunk_id, project_id, dataset_id, table_id, offset, limit):
    """Process a single chunk of data."""
    try:
        print(f"\n--- Processing Chunk {chunk_id} ---")
        
        # Fetch chunk
        df, table_ref = fetch_table_chunk(project_id, dataset_id, table_id, offset, limit)
        chunk_rows = len(df)
        
        # Profile chunk
        ydata_description = run_ydata_profiling_on_chunk(df, chunk_id)
        variables = ydata_description.variables
        
        # Process columns
        column_stats = {}
        for col_name, ydata_stats in variables.items():
            stats = map_ydata_to_schema(col_name, ydata_stats, chunk_rows)
            column_stats[col_name] = stats
        
        result = {
            'chunk_id': chunk_id,
            'offset': offset,
            'limit': limit,
            'actual_rows': chunk_rows,
            'columns': column_stats
        }
        
        print(f"✓ Chunk {chunk_id} completed")
        return result
        
    except Exception as e:
        print(f"✗ Error processing chunk {chunk_id}: {e}")
        import traceback
        traceback.print_exc()
        return None

def run_bigquery_query(query):
    """Run BigQuery query."""
    try:
        job = bq_client.query(query)
        return [dict(row) for row in job.result()]
    except Exception as e:
        print(f"BQ Error: {e}")
        raise

def get_enhanced_analysis(table_name, columns_analysis):
    """Use Gemini for enhanced analysis."""
    try:
        available_cols = list(columns_analysis.keys())
        simple_cols = {k: {
            'type': v.get('data_type'), 
            'unique_pct': f"{v.get('uniqueness_percentage', 0):.1f}%", 
            'top_val': v.get('default_value')
        } for k, v in columns_analysis.items()}

        prompt = f"""
        Analyze BigQuery table '{table_name}'.
        Actual Columns Available: {json.dumps(available_cols)}.
        Column Stats: {json.dumps(simple_cols, indent=2)}.
        
        Provide JSON output with:
        - table_context: detected_level, confidence, primary_entity, business_context.
        - primary_key_recommendations: list of {{column, rank, confidence, reasoning}}.
        - composite_key_recommendations: dict with keys "two_column", "three_column", "four_column". 
        
        IMPORTANT: Only use column names from "Actual Columns Available". Do not invent columns.
        """
        
        generate_content_config = types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=50000,
            thinking_config=types.ThinkingConfig(thinking_budget=-1),
            response_mime_type="application/json",
            response_schema=OutputFormat
        )

        response = gemini_model.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=generate_content_config
        )
        
        text = response.text.replace('```json', '').replace('```', '').strip()
        return json.loads(text)
    except Exception as e:
        print(f"Error in Gemini analysis: {e}")
        return create_empty_enhanced_analysis()

def create_empty_enhanced_analysis():
    return {
        "available": False,
        "composite_key_recommendations": {"two_column": [], "three_column": [], "four_column": []},
        "llm_suggested_combos": {"two_column": [], "three_column": [], "four_column": []},
        "validation_results": {}
    }

def filter_llm_combos(enhanced, available_cols):
    keys = ["two_column", "three_column", "four_column"]
    for section in ["composite_key_recommendations", "llm_suggested_combos"]:
        if section not in enhanced:
            continue
        for key in keys:
            combos = enhanced[section].get(key, [])
            cleaned = []
            for combo in combos:
                if isinstance(combo, dict):
                    cols = combo.get("columns", [])
                elif isinstance(combo, list):
                    cols = combo
                else:
                    continue
                if all(c in available_cols for c in cols):
                    cleaned.append(combo)
            enhanced[section][key] = cleaned
    return enhanced

def validate_composite_key(table_ref, columns_list):
    """Validate composite key uniqueness."""
    try:
        if not columns_list:
            return 0, 0, 0.0

        safe_parts = [f"COALESCE(CAST(`{col}` AS STRING), '')" for col in columns_list]
        concat_expr = "CONCAT(" + ", ".join(safe_parts) + ")"

        query = f"""
        SELECT 
          COUNT(*) AS total,
          APPROX_COUNT_DISTINCT({concat_expr}) AS unique_count
        FROM `{table_ref}`
        """

        result = run_bigquery_query(query)[0]
        total = int(result.get('total', 0))
        unique = int(result.get('unique_count', 0))
        uniq_pct = (unique / total * 100) if total > 0 else 0.0
        return total, unique, uniq_pct

    except Exception as e:
        print(f"Error validating composite key {columns_list}: {e}")
        return 0, 0, 0.0

def validate_single_composite(table_ref, task, available_columns):
    """Worker for composite key validation."""
    key, columns_list, business_meaning, llm_score = task
    
    missing_columns = [col for col in columns_list if col not in available_columns]
    
    if missing_columns:
        print(f"Skipping validation for {columns_list}: Columns not found {missing_columns}")
        rec_result = {
            'columns': columns_list,
            'uniqueness_percentage': 0.0,
            'is_candidate': False,
            'business_meaning': f"{business_meaning} (SKIPPED: Invalid columns)",
            'composite_score': 0.0
        }
        return key, rec_result, None
    else:
        total, unique, uniq_pct = validate_composite_key(table_ref, columns_list)
        is_candidate = (uniq_pct >= 99.9)
        
        rec_result = {
            'columns': columns_list,
            'uniqueness_percentage': uniq_pct,
            'is_candidate': is_candidate,
            'business_meaning': business_meaning,
            'composite_score': 1.0 if is_candidate else llm_score
        }
        
        val_result = {
            'columns': columns_list,
            'distinct_count': unique,
            'total_rows': total,
            'uniqueness_percentage': uniq_pct,
            'is_unique': is_candidate
        }
        
        return key, rec_result, val_result

def process_composite_keys(enhanced, available_columns, table_ref):
    """Process and validate composite key recommendations."""
    if not available_columns:
        return enhanced
    
    enhanced.setdefault('validation_results', {})
    enhanced.setdefault('composite_key_recommendations', {})

    validation_tasks = []
    sources = [enhanced.get('composite_key_recommendations', {}), enhanced.get('llm_suggested_combos', {})]
    
    for source in sources:
        for num in ['two_column', 'three_column', 'four_column']:
            key = num
            combos = source.get(key, [])
            if not combos: continue

            enhanced['validation_results'].setdefault(key, [])
            if key not in enhanced['composite_key_recommendations']:
                enhanced['composite_key_recommendations'][key] = []
            
            for combo in combos:
                if isinstance(combo, dict):
                    columns_list = combo.get('columns', [])
                    business_meaning = combo.get('business_meaning', 'Identifies unique records')
                    llm_score = combo.get('composite_score', 0.0)
                elif isinstance(combo, list):
                    columns_list = combo
                    business_meaning = 'Identifies unique records'
                    llm_score = 0.0
                else:
                    continue

                if columns_list and (key, columns_list, business_meaning, llm_score) not in validation_tasks:
                    validation_tasks.append((key, columns_list, business_meaning, llm_score))
    
    for k in ['two_column', 'three_column', 'four_column']:
         enhanced['composite_key_recommendations'][k] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_task = {
            executor.submit(validate_single_composite, table_ref, task, available_columns): task
            for task in validation_tasks
        }
        
        for future in as_completed(future_to_task):
            key, rec_result, val_result = future.result()
            enhanced['composite_key_recommendations'][key].append(rec_result)
            if val_result:
                enhanced['validation_results'][key].append(val_result)
    
    return enhanced

def process_table(table_id):
    """Process table with chunking for large tables."""
    try:
        print(f"\n{'='*60}")
        print(f"Processing table: {table_id}")
        print(f"{'='*60}")
        
        # Get row count
        total_rows = get_table_row_count(PROJECT_ID, DATASET_ID, table_id)
        
        # Determine if chunking is needed
        use_chunking = total_rows > config.CHUNK_THRESHOLD
        
        if use_chunking:
            print(f"⚙️  Large table detected ({total_rows:,} rows) - Using chunked processing")
            
            # Calculate chunks
            num_chunks = (total_rows + config.CHUNK_SIZE - 1) // config.CHUNK_SIZE
            print(f"📊 Creating {num_chunks} chunks of {config.CHUNK_SIZE} rows each")
            
            # Process chunks in parallel
            chunk_results = []
            with ThreadPoolExecutor(max_workers=config.MAX_CHUNK_WORKERS) as executor:
                futures = []
                for i in range(num_chunks):
                    offset = i * config.CHUNK_SIZE
                    limit = min(config.CHUNK_SIZE, total_rows - offset)
                    future = executor.submit(process_chunk, i, PROJECT_ID, DATASET_ID, table_id, offset, limit)
                    futures.append(future)
                
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        chunk_results.append(result)
            
            # Merge chunk results
            column_analysis = merge_chunk_statistics(chunk_results)
            
            # Determine table reference
            if "covid" in table_id:
                table_ref = "bigquery-public-data.covid19_ecdc_eu.covid_19_geographic_distribution_worldwide"
            else:
                table_ref = f"{PROJECT_ID}.{DATASET_ID}.{table_id}"
                
        else:
            print(f"⚙️  Small table ({total_rows:,} rows) - Using single-pass processing")
            
            # Original single-pass logic
            df, table_ref = fetch_table_chunk(PROJECT_ID, DATASET_ID, table_id, 0, total_rows)
            ydata_description = run_ydata_profiling_on_chunk(df, 0)
            variables = ydata_description.variables
            
            column_analysis = {}
            for col_name, ydata_stats in variables.items():
                stats = map_ydata_to_schema(col_name, ydata_stats, total_rows)
                column_analysis[col_name] = stats
        
        # Continue with analysis (same for both paths)
        print(f"\n📈 Analyzing {len(column_analysis)} columns...")
        
        default_value_analysis = {}
        per_column_scores = {}
        recommendations = []
        
        for col_name, stats in column_analysis.items():
            uniq = stats.get('uniqueness_percentage', 0)
            nulls = stats.get('null_percentage', 0)
            pk = uniq > 95 and nulls < 1
            fk = 10 < uniq < 90
            
            stats['primary_key_candidate'] = pk
            stats['foreign_key_candidate'] = fk
            
            if pk: recommendations.append(f"Column '{col_name}': Excellent PK candidate.")
            if fk: recommendations.append(f"Column '{col_name}': Potential Foreign Key.")
            
            dim_scores = {
                'completeness': 100 - nulls,
                'uniqueness': uniq,
                'distribution': 100 - (stats.get('default_pct', 0) / 2),
                'validity': 100.0
            }
            overall = sum(dim_scores.values()) / 4
            dq_scores = {'overall_score': round(overall, 2), 'dimension_scores': dim_scores}
            
            default_value_analysis[col_name] = {
                'total_rows': int(total_rows),
                'default_value': stats.get('default_value'),
                'default_count': stats.get('default_count'),
                'default_pct': stats.get('default_pct')
            }
            per_column_scores[col_name] = dq_scores

        # Overall DQ Score
        if per_column_scores:
            overall_dims = {d: np.mean([s['dimension_scores'][d] for s in per_column_scores.values()]) 
                          for d in ['completeness', 'uniqueness', 'distribution', 'validity']}
            overall_score = np.mean(list(overall_dims.values()))
        else:
            overall_dims = {d: 0.0 for d in ['completeness', 'uniqueness', 'distribution', 'validity']}
            overall_score = 0.0
            
        dq_score = {
            'overall_score': round(overall_score, 2),
            'dimension_scores': {k: round(v, 2) for k, v in overall_dims.items()},
            'per_column_scores': per_column_scores
        }
        
        # Enhanced Analysis
        print(f"🤖 Calling Gemini for enhanced analysis...")
        enhanced = get_enhanced_analysis(table_id, column_analysis)
        
        # Validate Keys
        print(f"🔍 Validating composite keys...")
        available_columns = list(column_analysis.keys())
        enhanced = filter_llm_combos(enhanced, available_columns)
        enhanced = process_composite_keys(enhanced, available_columns, table_ref)
        
        # Final Profile
        profile_dict = {
            'table_reference': table_ref,
            'analysis_type': 'comprehensive',
            'processing_mode': 'chunked_parallel' if use_chunking else 'single_pass',
            'total_rows_analyzed': int(total_rows),
            'chunks_processed': num_chunks if use_chunking else 1,
            'chunk_size': config.CHUNK_SIZE if use_chunking else total_rows,
            'status': 'success',
            'data_quality_score': dq_score,
            'recommendations': recommendations,
            'table_summary': {'total_rows': int(total_rows), 'total_columns': len(column_analysis)},
            'column_analysis': column_analysis,
            'default_value_analysis': default_value_analysis,
            'enhanced_analysis': enhanced
        }
        
        # Save results
        with file_lock:
            filename = f"{table_id}_profile.json"
            with open(filename, "w") as f:
                json.dump(profile_dict, f, indent=2, cls=NpEncoder)
        
        print(f"\n✅ Completed: {table_id}")
        print(f"   - Rows: {total_rows:,}")
        print(f"   - Columns: {len(column_analysis)}")
        print(f"   - Mode: {'Chunked' if use_chunking else 'Single-pass'}")
        
        return profile_dict

    except Exception as e:
        print(f"\n❌ Error profiling '{table_id}': {e}")
        import traceback
        traceback.print_exc()
        return None

def profile_data(table_ids):
    print(f"\n{'='*60}")
    print(f"BigQuery Profiler with Intelligent Chunking")
    print(f"{'='*60}")
    print(f"Config:")
    print(f"  - Chunk Threshold: {config.CHUNK_THRESHOLD:,} rows")
    print(f"  - Chunk Size: {config.CHUNK_SIZE:,} rows")
    print(f"  - Max Chunk Workers: {config.MAX_CHUNK_WORKERS}")
    print(f"{'='*60}\n")
    
    start_time = time.time()
    all_profiles = []
    
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(table_ids))) as executor:
        future_to_table = {executor.submit(process_table, t): t for t in table_ids}
        
        for future in as_completed(future_to_table):
            res = future.result()
            if res: all_profiles.append(res)
    
    # Save combined results
    with file_lock:
        with open("all_tables_profiles.json", "w") as f:
            json.dump(all_profiles, f, indent=2, cls=NpEncoder)
    
    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"✅ All tables processed successfully!")
    print(f"   - Total time: {elapsed:.2f}s")
    print(f"   - Tables processed: {len(all_profiles)}")
    print(f"   - Output: all_tables_profiles.json")
    print(f"{'='*60}\n")

