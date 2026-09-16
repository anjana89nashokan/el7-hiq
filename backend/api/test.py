from utils.vertex_ai_search_utils import generate_data_dictionary_in_batches
from utils.bg_query_utils import get_tables_metadata
from config.settings import config
response = get_tables_metadata(['ust-genai-pa-poc-gcp.DATAMAP_COPILOT.datamap_copilot_sf_mule_dart_provider_d_account_20250829_002607_032e7a25'])
args = response[0]['schema'][:]

answer = generate_data_dictionary_in_batches(
    project_id=config.PROJECT_ID,
    dataset_id=config.DATASET_ID,
    table_schema=args,
    dictionary_table_id='test_brd_dict_100',
    batch_size=5,
    source_table_id='test'
)
