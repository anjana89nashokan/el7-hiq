import pandas as pd
import json, traceback
from typing import Dict, Any
from datetime import datetime
from typing import Dict, Any

# Assuming your logger is set up ....
try:
    from utils.bg_query_utils import DataMapLogger
except ImportError:
    # Placeholder logger
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    class DataMapLogger:
        def __init__(self, name): self.name = name
        def log_agent_action(self, msg): logger.info(f"[{self.name}] {msg}")


def content_extraction_tool(tool_input: Dict[str, Any]) -> str:
    """
    Extracts tabular Vendor Data Dictionary info from Excel or CSV.
    Handles:
      - Extra text above headers
      - Unknown column names
      - Multi-line (child) rows below a parent row
    Output strictly:
      { "header_metadata": {...}, "table_data": [...] }
    """

    print("-================toolinputstart==================================")

    print(f"-====================={tool_input}=============================")

    print("-==================================================")


    # Primary param name expected by code / prompt
    # file_path = tool_input.get("file_path")

    # # Fallbacks for common mistakes by the model
    # if not file_path:
    #     # Sometimes the model might copy the label from the prompt
    #     # instead of the real param name
    #     file_path = (
    #         tool_input.get("Vendor DD Path")
    #         or tool_input.get("vendor_dd_path")
    #         or tool_input.get("VENDOR_DD_PATH")
    #         or tool_input.get("path")
    #     )

    # if not file_path or not isinstance(file_path, str) or not file_path.strip():
    #     logging.error("[content_extraction_tool] No valid file_path found in tool_input.")
    #     return {"error": "No file path provided."}

    # file_path = file_path.strip()
    # logging.info("[content_extraction_tool] Using file_path=%s", file_path)


    
    logger = DataMapLogger("content_extraction_tool")
    args = tool_input.get("args", tool_input)
    file_path = args.get("file_path", args.get("path"))

    if not file_path:
        return json.dumps({"error": "No file path provided."})

    logger.log_agent_action(f"[START] Extracting from: {file_path}")

    header_metadata = {}
    table_data = []

    # Normalize value for comparisons
    def norm(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        return str(v).strip().lower()

    try:
        #==================================================
        # CSV CASE: Convert to Excel for consistent logic
        #==================================================
        if file_path.endswith(".csv"):
            logger.log_agent_action("[CSV] Converting → Excel for parsing")

            try:
                df_csv = pd.read_csv(file_path, header=None, dtype=object)
            except Exception:
                df_csv = pd.read_csv(file_path, header=None, dtype=object, engine="python")

            tmp = file_path + f".norm_{datetime.now().timestamp()}.xlsx"
            df_csv.to_excel(tmp, index=False, header=None)
            file_path = tmp

        #==================================================
        # Excel Read
        #==================================================
        if not file_path.endswith((".xlsx", ".xls")):
            return json.dumps({"error": f"Unsupported format: {file_path}"})

        xls = pd.ExcelFile(file_path)
        sheet = xls.sheet_names[0]
        df_full = pd.read_excel(xls, sheet_name=sheet, header=None, dtype=object)

        logger.log_agent_action(f"[RAW] Loaded shape={df_full.shape}")

        # If single-column expanded file
        if df_full.shape[1] == 1:
            logger.log_agent_action("[REPAIR] Collapsed CSV detected → Split by comma")
            df_full = df_full.iloc[:,0].astype(str).str.split(",", expand=True)

        #==================================================
        # HEADER DETECTION
        #==================================================
        header_keywords = [
            "field","name","column","type","data","length","len",
            "description","comments","notes","value","format",
            "required","nullable","seq","#"
        ]

        best_header_row = -1
        best_score = -1

        for i, row in df_full.iterrows():
            if row.isnull().all():
                continue

            score = 0
            for cell in row.tolist():
                text = norm(cell)
                for kw in header_keywords:
                    if kw in text:
                        score += 1

            logger.log_agent_action(f"[SCAN HEADER] row={i} score={score}")

            if score > best_score:
                best_score = score
                best_header_row = i

        if best_header_row < 0:
            best_header_row = 0
            logger.log_agent_action("[HEADER] WARNING: No strong match → fallback row=0")
        else:
            logger.log_agent_action(f"[HEADER] Selected row={best_header_row}")

        #==================================================
        # Extract metadata above header
        #==================================================
        for i in range(best_header_row):
            row = df_full.iloc[i]
            if row.isnull().all():
                continue
            cell = str(row.iloc[0])
            if ":" in cell:
                k,v = cell.split(":",1)
                header_metadata[k.strip()] = v.strip()
                logger.log_agent_action(f"[META] {k.strip()}: {v.strip()}")

        #==================================================
        # Read clean table
        #==================================================
        df = pd.read_excel(xls, sheet_name=sheet, header=best_header_row, dtype=object)
        df.dropna(how="all", inplace=True)
        df.columns = [str(c).strip() for c in df.columns]

        logger.log_agent_action(f"[COLUMNS DETECTED] {list(df.columns)}")

        #==================================================
        # Identify columns suggesting a parent entry
        #==================================================
        id_cols = []
        for c in df.columns:
            lc = norm(c)
            if any(x in lc for x in ["field","name","column","seq","#"]):
                id_cols.append(c)

        if not id_cols:
            id_cols = list(df.columns)  # fallback

        logger.log_agent_action(f"[IDENTIFIER COLS] {id_cols}")

        #==================================================
        # Row grouping of multiline child data
        #==================================================
        def is_empty(v):
            if v is None: return True
            if isinstance(v,float) and pd.isna(v): return True
            if isinstance(v,str) and v.strip()=="":
                return True
            return False

        grouped=[]
        parent=None

        for _,row in df.iterrows():
            rd = {k: row[k] for k in df.columns}
            if all(is_empty(v) for v in rd.values()):
                continue

            # Check if this row is a parent row
            has_identifier = any(not is_empty(rd[c]) for c in id_cols)

            if has_identifier:
                if parent:
                    grouped.append(parent)
                parent = rd
            else:
                if not parent:
                    continue
                # Merge into parent row
                for c in df.columns:
                    cv = rd.get(c)
                    if is_empty(cv): 
                        continue
                    pv = parent.get(c)
                    if is_empty(pv):
                        parent[c] = cv
                    else:
                        pv = str(pv).strip()
                        cv = str(cv).strip()
                        if pv != cv:
                            parent[c] = pv + "\n" + cv

        if parent:
            grouped.append(parent)

        table_data = grouped

        logger.log_agent_action(f"[SUCCESS] rows={len(table_data)} meta={len(header_metadata)}")
        if table_data:
            logger.log_agent_action(f"[SAMPLE ROW] {table_data[0]}")

        return json.dumps({
            "header_metadata": header_metadata,
            "table_data": table_data
        }, indent=2)

    except Exception as e:
        logger.error(f"[ERROR] Failed: {e}\n{traceback.format_exc()}")
        return json.dumps({"error": str(e)})






def validation_engine_tool(tool_input: Dict[str, Any]) -> str:
    logger = DataMapLogger("validation_engine_tool")
    logger.log_agent_action("START VALIDATION ENGINE")

    try:
        confirmed_mapping = tool_input.get("confirmed_mapping", {})
        original_vendor_dd = tool_input.get("original_vendor_dd", [])
        ground_truth_summary = tool_input.get("ground_truth_summary", {})
    except Exception:
        return json.dumps([])

    logger.log_agent_action(f"MAPPING_KEYS: {confirmed_mapping}")
    logger.log_agent_action(f"VENDOR_DD_ROWS: {(original_vendor_dd)}")
    logger.log_agent_action(f"PROFILE_TOPLEVEL: {ground_truth_summary}")

    # 1: Standardize DD -->>TESTED ..BY NUTTAN
    # standardized_vendor_dd = []
    # for idx, row in enumerate(original_vendor_dd):
    #     new_row = {}
    #     for std, mp in confirmed_mapping.items():
    #         vendor_col = mp["vendor_column"] if isinstance(mp, dict) else mp
    #         if vendor_col:
    #             new_row[std] = row.get(vendor_col)
    #     standardized_vendor_dd.append(new_row)

    # logger.log_agent_action(f"STD_VENDOR_DD_ROWS: {len(standardized_vendor_dd)}")


        # 1: Standardize DD
    standardized_vendor_dd = []

    for row_idx, row in enumerate(original_vendor_dd):
        # Safety: we only handle dict rows
        if not isinstance(row, dict):
            continue

        new_row: Dict[str, Any] = {}

        for std_name, mp in confirmed_mapping.items():
            # Normalize mapping entry into a single vendor column name
            vendor_col = None

            if isinstance(mp, dict):
                # New HITL format: {"vendor_column": "...", "confidence": "..."}
                vendor_col = mp.get("vendor_column")
            elif isinstance(mp, str):
                # Legacy format: directly a column name string
                vendor_col = mp
            else:
                # Any other unexpected type → ignore for safety
                vendor_col = None

            # If no mapping, still create the key with None to keep 11-field schema
            if not vendor_col:
                new_row[std_name] = None
                continue

            # Map from standard field -> vendor value
            new_row[std_name] = row.get(vendor_col)

        standardized_vendor_dd.append(new_row)

    logger.log_agent_action(f"STD_VENDOR_DD_ROWS: {len(standardized_vendor_dd)}")




    # 2: Build truth
    def build_truth(profile):
        truth = {}
        # profile_main = profile.get("profile_results", [profile])[0] if profile.get("profile_results") else profile
        # Normalize profile structure but preserve original logic
        if isinstance(profile, dict):
            if profile.get("profile_results"):
                profile_main = profile["profile_results"][0]
            else:
                profile_main = profile

        elif isinstance(profile, list):
            profile_main = profile[0] if profile else {}

        else:
            profile_main = {}

        dq = profile_main.get("data_quality_score", {})
        per_col = dq.get("per_column_scores", {})

        for col, stats in per_col.items():
            entry = truth.setdefault(col, {})
            cmpl = stats.get("dimension_scores", {}).get("completeness")
            if cmpl is not None:
                entry["Contains Nulls"] = cmpl < 100

        for block_name in ["table_summary", "column_analysis", "enhanced_analysis"]:
            block = profile_main.get(block_name, {})
            if isinstance(block, dict):
                for col, stats in block.items():
                    if not isinstance(stats, dict):
                        continue
                    entry = truth.setdefault(col, {})
                    entry.setdefault("Data Type",
                                     stats.get("Data Type") or
                                     stats.get("data_type"))
                    entry.setdefault("Is Primary Key",
                                     stats.get("Is Primary Key") or
                                     stats.get("is_pk"))

        for c, e in truth.items():
            e.setdefault("Data Type", None)
            e.setdefault("Contains Nulls", None)
            e.setdefault("Is Primary Key", None)

        return truth

    column_truth_map = build_truth(ground_truth_summary)


    # vendor_column_names = {row.get("Field Name") for row in standardized_vendor_dd}
    # column_truth_map = {
    #     c: t for c, t in column_truth_map.items()
    #     if c in vendor_column_names
    # }

    # Field Name column in vendor DD represents real DB column names
    vendor_column_names = {
        row.get("Field Name")
        for row in standardized_vendor_dd
        if row.get("Field Name")
    }

    filtered_truth = {
        c: truth
        for c, truth in column_truth_map.items()
        if c in vendor_column_names
    }

    column_truth_map = filtered_truth




    logger.log_agent_action(f"PROFILE_COLUMNS_FOUND: {len(column_truth_map)}")

    # fallback – use vendor DD
    if not column_truth_map:
        print("NO PROFILE TRUTH FOUND — FALLBACK ENABLED")
        for row in standardized_vendor_dd:
            col = row.get("Field Name")
            if col:
                column_truth_map[col] = {
                    "Data Type": None,
                    "Contains Nulls": None,
                    "Is Primary Key": None,
                }

    logger.log_agent_action(f"FINAL_COLUMNS_TO_VALIDATE: {len(column_truth_map)}")

    findings = []

    for col, truth in column_truth_map.items():
        vendor_row = next((r for r in standardized_vendor_dd if r.get("Field Name") == col), None)

        if not vendor_row:
            findings.append({
                "column_name": col,
                "check_type": "Existence Check",
                "status": "Mismatch",
                "vendor_claim": "Column NOT found in DD",
                "system_finding": "Column exists in source data"
            })
            continue

        vdt = str(vendor_row.get("Data Type", "")).lower()
        sdt = str(truth.get("Data Type", "")).lower()

        match = (
            vdt in sdt or
            sdt in vdt or
            ("char" in vdt and "string" in sdt) or
            ("int" in vdt and ("int" in sdt or "numeric" in sdt)) or
            ("date" in vdt and ("date" in sdt or "timestamp" in sdt))
        )

        findings.append({
            "column_name": col,
            "check_type": "Data Type Validation",
            "status": "Match" if match else "Mismatch",
            "vendor_claim": vendor_row.get("Data Type"),
            "system_finding": truth.get("Data Type")
        })

        vnull = "Yes" if "null" in str(vendor_row.get("Nullable", "")).lower() else "No"
        snull = truth.get("Contains Nulls")
        snull = "Yes" if snull else "No" if snull is not None else "Unknown"

        findings.append({
            "column_name": col,
            "check_type": "Nullability Validation",
            "status": "Match" if (snull != "Unknown" and vnull == snull) else "Mismatch",
            "vendor_claim": f"Nullable: {vnull}",
            "system_finding": f"Contains Nulls: {snull}",
        })

        vpk = "Yes" if "primary" in str(vendor_row.get("Primary Key", "")).lower() else "No"
        spk = truth.get("Is Primary Key")
        spk = "Yes" if spk else "No" if spk is not None else "Unknown"

        findings.append({
            "column_name": col,
            "check_type": "Primary Key Validation",
            "status": "Match" if (spk != "Unknown" and vpk == spk) else "Mismatch",
            "vendor_claim": f"Is PK: {vpk}",
            "system_finding": f"Is PK (analysis): {spk}",
        })

    logger.log_agent_action(f"TOTAL_FINDINGS: {len(findings)}")
    logger.log_agent_action("END VALIDATION ENGINE")

    return json.dumps(findings)



