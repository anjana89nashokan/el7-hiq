from utils.vertex_ai_search_utils import (full_import_to_existing_datastore,upload_local_file_to_gcs)

gcs_path = upload_local_file_to_gcs(r"C:\Users\299956\Desktop\ibx-DataMap-Copilot\server\data\vertex_data_dictionary.jsonl")

print(gcs_path)

res = full_import_to_existing_datastore(
    gcs_uris=[gcs_path])

print(res)