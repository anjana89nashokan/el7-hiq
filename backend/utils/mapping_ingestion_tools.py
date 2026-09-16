import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd
import re

from agents.mapping_ingestion.models import (
    AlternateKeyGroup,
    DataModelGraph,
    GraphEdge,
    GraphMetadata,
    GraphNode,
    SCDHints,
    SourceColumn,
    SourceSchema,
    SourceFile,
    TargetColumn,
    TargetSchema,
    TargetTable,
)
from config.settings import config
from utils.mapping_artifact_store import save_json

logger = logging.getLogger(__name__)


# -----------------------
# Helpers
# -----------------------


def _clean_header_cell(val) -> str:
    if pd.isna(val):
        return ""
    return str(val).strip()


def _normalize_header_token(val) -> str:
    """
    Normalize header labels so we can support minor template variations.
    """
    if val is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(val).strip().lower())


def _row_value_by_alias(row_dict: dict, aliases: List[str]):
    """
    Fetch a row value by trying multiple header aliases (case/spacing insensitive).
    """
    if not row_dict:
        return None
    normalized = {_normalize_header_token(k): v for k, v in row_dict.items() if k is not None}
    for alias in aliases:
        key = _normalize_header_token(alias)
        if key in normalized:
            return normalized[key]
    return None


def _normalize_bool(val) -> bool:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    s = str(val).strip().lower()
    return s in {"y", "yes", "true", "1", "pk", "ak", "fk"}


def _normalize_nullable(val) -> bool:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return True
    s = str(val).strip().lower()
    if s in {"n", "no", "false", "0"}:
        return False
    return True


def _normalize_int(val):
    try:
        if pd.isna(val):
            return None
    except Exception:
        pass
    try:
        return int(val)
    except Exception:
        return None


def _normalize_length(val):
    """
    Excel sometimes encodes unknown length as 0. Treat that as missing so
    downstream heuristics don't misinterpret it as a real zero-length field.
    """
    length = _normalize_int(val)
    if length is None:
        return None
    return length if length > 0 else None


def _maybe_str(val):
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except Exception:
        pass
    return str(val).strip()


def _parse_source_filespec_physical_names(xls: pd.ExcelFile) -> dict[str, str]:
    """
    Parse old-style FileSpecs tab when present:
      row "Tab Name" -> sheet labels
      row "Physical File Name" -> file names aligned by column
    """
    out: dict[str, str] = {}
    filespec_sheet = next((s for s in xls.sheet_names if s.strip().lower() == "filespecs"), None)
    if not filespec_sheet:
        return out
    try:
        fs = pd.read_excel(xls, sheet_name=filespec_sheet, header=None)
    except Exception:
        return out
    if fs.empty or fs.shape[1] < 2:
        return out

    tab_row_idx = None
    physical_row_idx = None
    for idx, val in enumerate(fs.iloc[:, 0].tolist()):
        norm = _normalize_header_token(val)
        if norm == "tablename":
            tab_row_idx = idx
        elif norm == "physicalfilename":
            physical_row_idx = idx
    if tab_row_idx is None or physical_row_idx is None:
        return out

    tab_row = fs.iloc[tab_row_idx].tolist()
    physical_row = fs.iloc[physical_row_idx].tolist()
    limit = min(len(tab_row), len(physical_row))
    for i in range(1, limit):
        sheet_label = _maybe_str(tab_row[i])
        physical_name = _maybe_str(physical_row[i])
        if sheet_label and physical_name:
            out[sheet_label] = physical_name
    return out


def _canonicalize_data_type(raw) -> str:
    """
    Normalize raw Excel data-type strings (e.g., "Number(15,0)") into a small
    canonical set that Step 2 understands.
    """
    if raw is None:
        return "STRING"
    try:
        if isinstance(raw, float) and pd.isna(raw):
            return "STRING"
    except Exception:
        pass

    raw_upper = str(raw).strip().upper()
    if not raw_upper:
        return "STRING"

    # Strip size/precision suffixes like "(15,0)" or " (10)" so we only match on the base token.
    token = re.split(r"[\\s(]", raw_upper, maxsplit=1)[0]
    mapping = {
        "STRING": "STRING",
        "STR": "STRING",
        "TEXT": "STRING",
        "CHAR": "STRING",
        "CHARACTER": "STRING",
        "VARCHAR": "STRING",
        "VARCHAR2": "STRING",
        "NVARCHAR": "STRING",
        "BOOLEAN": "BOOLEAN",
        "BOOL": "BOOLEAN",
        "BIT": "BOOLEAN",
        "INT": "INTEGER",
        "INTEGER": "INTEGER",
        "SMALLINT": "INTEGER",
        "BIGINT": "INTEGER",
        "NUMBER": "NUMERIC",
        "NUM": "NUMERIC",
        "NUMERIC": "NUMERIC",
        "DEC": "NUMERIC",
        "DECIMAL": "NUMERIC",
        "FLOAT": "NUMERIC",
        "DOUBLE": "NUMERIC",
        "REAL": "NUMERIC",
        "DATE": "DATE",
        "DATETIME": "TIMESTAMP",
        "TIMESTAMP": "TIMESTAMP",
        "TIME": "TIMESTAMP",
    }

    if token in mapping:
        return mapping[token]
    # Fall back to the whole string in case tokenization stripped too much.
    return mapping.get(raw_upper, "STRING")


ID_TOKENS = [
    "ID",
    "IDENTIFIER",
    "IDENT",
    "PHONE",
    "PHN",
    "FAX",
    "ZIP",
    "POSTAL",
    "TIN",
    "NPI",
    "SSN",
    "CODE",
    "CD",
    "NUMBER",
    "NUM",
    "ACCT",
    "ACCOUNT",
    "REGISTRY",
]

DATE_TOKENS = [
    "DATE",
    "EFF",
    "EXP",
    "BIRTH",
    "DOB",
    "ANNIV",
    "ATTES",
]

TIMESTAMP_TOKENS = [
    "TIMESTAMP",
    "TMS",
    "LOAD_TS",
    "LOAD_TMS",
    "UPDATED_TS",
    "UPDATE_TS",
    "CREATED_TS",
    "LAST_MODIFIED",
    "RUN_TS",
    "INSERT_TS",
    "CHANGE_TS",
]

SCD_EFF_TOKENS = ["EFF", "EFFECTIVE", "START", "BEGIN"]
SCD_EXP_TOKENS = ["EXP", "EXPIRE", "END", "TERM"]
SCD_DATE_MARKERS = ["DT", "DATE"]
SCD_CURRENT_TOKENS = ["CURR", "CURRENT", "ACTIVE"]
SCD_FLAG_MARKERS = ["FL", "FLAG", "FLG", "IND", "INDICATOR"]
SYSTEM_TIMESTAMP_TOKENS = [
    "LOAD_TS",
    "LOAD_TMS",
    "INSERT_TS",
    "UPDATE_TS",
    "UPDT_TS",
    "ROW_INS_TS",
    "ROW_UPD_TS",
]


def _adjust_data_type(column_name: str, canonical_type: str) -> str:
    name_upper = column_name.upper()
    if any(token in name_upper for token in TIMESTAMP_TOKENS):
        return "TIMESTAMP"
    if any(token in name_upper for token in DATE_TOKENS):
        return "DATE"
    if canonical_type in {"NUMERIC", "INTEGER"}:
        if any(token in name_upper for token in ID_TOKENS):
            return "STRING"
    return canonical_type


def _first_non_empty(series: Optional[pd.Series]) -> Optional[str]:
    if series is None:
        return None
    for val in series:
        candidate = _maybe_str(val)
        if candidate:
            return candidate
    return None


def _derive_table_name_hint(file_path: Path) -> Optional[str]:
    stem = file_path.stem  # e.g., "Table_Metadata_PRV_MAP(target metadata)"
    candidate = stem
    if "Table_Metadata_" in stem:
        candidate = stem.split("Table_Metadata_", 1)[1]
    candidate = candidate.split("(")[0].strip()
    return candidate or None


ID_TOKENS = [
    "ID",
    "IDENTIFIER",
    "IDENT",
    "PHONE",
    "PHN",
    "FAX",
    "ZIP",
    "POSTAL",
    "TIN",
    "NPI",
    "SSN",
    "CODE",
    "CD",
    "NUMBER",
    "NUM",
    "ACCT",
    "ACCOUNT",
    "REGISTRY",
]

DATE_TOKENS = [
    "DATE",
    "EFF",
    "EXP",
    "BIRTH",
    "DOB",
    "ANNIV",
    "ATTES",
]

TIMESTAMP_TOKENS = [
    "TIMESTAMP",
    "TMS",
    "LOAD_TS",
    "LOAD_TMS",
    "UPDATED_TS",
    "UPDATE_TS",
    "CREATED_TS",
    "LAST_MODIFIED",
    "RUN_TS",
    "INSERT_TS",
    "CHANGE_TS",
]

TECHNICAL_BASE_TOKENS = [
    "LOAD_TS",
    "LOAD_TMS",
    "INSRT_TS",
    "INSERT_TS",
    "UPDT_TS",
    "UPD_TS",
    "LAST_UPD_TS",
    "AEDW_LAST_UPDT_TS",
    "ETL_BATCH",
    "BATCH_ID",
    "RUN_ID",
    "JOB_ID",
    "DEL_IND",
    "DEL_FL",
    "DELETE_FL",
    "DELETE_FLAG",
    "ACTIVE_IND",
    "ACTV_IND",
    "OMIT_IND",
    "INACTV_IND",
    "INACTIVE_IND",
]

TECHNICAL_CURRENT_TOKENS = [
    "CURRENT_FL",
    "CURR_FL",
    "CUR_FL",
    "CURRENT_IND",
    "CURR_IND",
    "CUR_IND",
    "IS_CURRENT",
]

# Some record-source fields are often technical/hardcode; we treat them as technical hints
TECHNICAL_STATE_TOKENS = [
    "REC_SRC_CD",
    "DATA_SRC_CD",
    "SRC_CD",
]


def _adjust_data_type(column_name: str, canonical_type: str) -> str:
    name_upper = column_name.upper()
    if any(token in name_upper for token in TIMESTAMP_TOKENS):
        return "TIMESTAMP"
    if any(token in name_upper for token in DATE_TOKENS):
        return "DATE"
    if canonical_type in {"NUMERIC", "INTEGER"}:
        if any(token in name_upper for token in ID_TOKENS):
            return "STRING"
    return canonical_type


def _contains_any(name: str, tokens: List[str]) -> bool:
    return any(token in name for token in tokens)


def _is_technical_column(column_name: str) -> bool:
    """
    Heuristic detection of ETL/system/audit/SCD scaffolding columns.
    """
    name = column_name.upper()
    if name.endswith(("_TS", "_DTTM", "_TIMESTAMP")):
        return True
    if _contains_any(name, TECHNICAL_BASE_TOKENS):
        return True
    if _contains_any(name, TECHNICAL_CURRENT_TOKENS):
        # Only treat as technical if also looks like a flag/indicator
        if "FL" in name or "FLAG" in name or "IND" in name:
            return True
    if "EFF" in name and ("DT" in name or "DTTM" in name):
        return True
    if "EXP" in name and ("DT" in name or "DTTM" in name):
        return True
    if "HIST" in name or "HISTORY" in name:
        return True
    if _contains_any(name, TECHNICAL_STATE_TOKENS):
        return True
    return False


def _finalize_primary_keys(columns: List[TargetColumn], pk_cols: List[str]) -> List[str]:
    """
    Ensure we always mark a reasonable PK so downstream agents can infer joins.
    """
    pk_set = [col for col in pk_cols if col]
    if pk_set:
        return pk_set

    surrogate_columns = [col for col in columns if col.is_surrogate_key]
    if len(surrogate_columns) == 1:
        return [surrogate_columns[0].attribute_name]
    if len(surrogate_columns) > 1:
        ordered = sorted(
            surrogate_columns,
            key=lambda c: c.order_no if c.order_no is not None else float("inf"),
        )
        if ordered:
            return [ordered[0].attribute_name]

    suffix_pk = [col.attribute_name for col in columns if col.attribute_name.upper().endswith("_PK")]
    if suffix_pk:
        return suffix_pk

    return pk_cols


def _apply_primary_key_flags(columns: List[TargetColumn], pk_cols: List[str]) -> None:
    pk_lookup = set(pk_cols)
    for col in columns:
        col.is_primary_key = col.attribute_name in pk_lookup


# -----------------------
# Source parsing
# -----------------------


def parse_source_excel(interface_code: str, file_path: Path) -> List[SourceFile]:
    """
    Parse an IndeMap-style source metadata Excel into SourceFile objects.
    """
    logger.info("Parsing source metadata: %s", file_path)
    xls = pd.ExcelFile(file_path)
    source_files: List[SourceFile] = []
    skip_sheets = {"instructions", "values", "filespecs"}
    filespec_physical_names = _parse_source_filespec_physical_names(xls)

    for sheet_name in xls.sheet_names:
        if sheet_name.lower() in skip_sheets:
            continue

        df = pd.read_excel(xls, sheet_name=sheet_name)
        if df.empty:
            continue

        # Two supported layouts:
        # 1) New template: "Attribute Name" is already a DataFrame column header.
        # 2) Old template: metadata block above, with a later row containing "Attribute Name".
        if any(_normalize_header_token(c) == "attributename" for c in df.columns):
            header_row_idx = 0
            headers = [_clean_header_cell(x) for x in df.columns.tolist()]
            data_rows = df.reset_index(drop=True)
        else:
            header_row_idx = None
            for idx, val in enumerate(df.iloc[:, 0].tolist()):
                if isinstance(val, str) and _normalize_header_token(val) == "attributename":
                    header_row_idx = idx
                    break

            if header_row_idx is None:
                logger.warning("Sheet %s missing 'Attribute Name' row; skipping", sheet_name)
                continue

            header_row = df.iloc[header_row_idx].fillna("")
            headers = [_clean_header_cell(x) for x in header_row.tolist()]
            data_rows = df.iloc[header_row_idx + 1 :].reset_index(drop=True)

        columns: List[SourceColumn] = []
        pk_cols: List[str] = []
        ak_groups: dict[str, List[str]] = {}

        for _, row in data_rows.iterrows():
            first_cell = row.iloc[0]
            if pd.isna(first_cell):
                continue
            row_dict = {headers[i]: row.iloc[i] for i in range(min(len(headers), len(row)))}

            col_name = _maybe_str(_row_value_by_alias(row_dict, ["Attribute Name", "Column Name"]))
            col_name = col_name or ""
            col_name = str(col_name).strip()
            if not col_name:
                continue

            raw_type = _row_value_by_alias(row_dict, ["Data Type", "Datatype", "Type"])
            data_type = _adjust_data_type(col_name, _canonicalize_data_type(raw_type))
            length = _normalize_length(_row_value_by_alias(row_dict, ["Length"]))
            precision = _normalize_int(_row_value_by_alias(row_dict, ["Precision"]))
            nullable = _normalize_nullable(_row_value_by_alias(row_dict, ["Nullability", "Nullability "]))
            default_value = None  # avoid treating sample values as true defaults

            is_pk = _normalize_bool(_row_value_by_alias(row_dict, ["Primary Key"]))
            if is_pk:
                pk_cols.append(col_name)

            ak_memberships: List[str] = []
            for key in row_dict.keys():
                if "alternate key" in key.lower():
                    raw_value = row_dict.get(key)
                    if raw_value is None:
                        continue
                    try:
                        if pd.isna(raw_value):
                            continue
                    except Exception:
                        pass
                    text_value = str(raw_value).strip()
                    if not text_value:
                        continue
                    match = re.search(r"alternate\s*key\s*(\d+)", key, re.IGNORECASE)
                    if match:
                        ak_name = f"AK{match.group(1)}"
                    else:
                        ak_name = key.strip().upper().replace(" ", "_")
                    ak_memberships.append(ak_name)
                    ak_groups.setdefault(ak_name, []).append(col_name)

            col = SourceColumn(
                physical_name=col_name,
                logical_name=(
                    _maybe_str(_row_value_by_alias(row_dict, ["Logical Attribute Name", "Logical Name"]))
                    or col_name
                ),
                description=(
                    _maybe_str(_row_value_by_alias(row_dict, ["Attribute Description", "Description"]))
                ),
                data_type=data_type,
                length=length,
                precision=precision,
                nullable=nullable,
                default_value=None if pd.isna(default_value) else str(default_value),
                is_primary_key=is_pk,
                alternate_key_groups=ak_memberships,
            )
            columns.append(col)

        alternate_keys = [
            AlternateKeyGroup(name=name, column_names=cols) for name, cols in ak_groups.items()
        ]

        physical_name = sheet_name
        logical_name = None
        description = None
        top_block = df.iloc[: header_row_idx]
        for _, row in top_block.iterrows():
            first = str(row.iloc[0]).strip().lower() if not pd.isna(row.iloc[0]) else ""
            val = row.iloc[1] if row.shape[0] > 1 else None
            if first == "entity physical name" and val and not pd.isna(val):
                physical_name = str(val).strip()
            if first == "entity business name" and val and not pd.isna(val):
                logical_name = str(val).strip()
            if first == "entity description" and val and not pd.isna(val):
                description = str(val).strip()

        if (not physical_name or physical_name == sheet_name) and sheet_name in filespec_physical_names:
            physical_name = filespec_physical_names[sheet_name]
        if not logical_name:
            logical_name = physical_name or sheet_name

        source_file = SourceFile(
            file_id=physical_name or sheet_name,
            file_name=physical_name or sheet_name,
            logical_name=logical_name,
            description=description,
            interface_code=interface_code,
            columns=columns,
            primary_key=pk_cols,
            alternate_keys=alternate_keys,
        )
        source_files.append(source_file)

    return source_files


def build_source_schema(interface_code: str, source_paths: List[Path]) -> SourceSchema:
    files: List[SourceFile] = []
    for path in source_paths:
        files.extend(parse_source_excel(interface_code, path))
    by_file_id = {f.file_id: f for f in files}
    return SourceSchema(interface_code=interface_code, files=files, by_file_id=by_file_id)


# -----------------------
# Target parsing
# -----------------------


def _detect_scd_hints(columns: List[TargetColumn]) -> SCDHints:
    """
    Attempt to recognize Type 2 behavior purely from column names.
    """
    hints = SCDHints()
    eff = None
    exp = None
    curr = None

    for col in columns:
        name = col.attribute_name.upper()
        if eff is None and _contains_any(name, SCD_EFF_TOKENS) and _contains_any(name, SCD_DATE_MARKERS):
            eff = col.attribute_name
        if exp is None and _contains_any(name, SCD_EXP_TOKENS) and _contains_any(name, SCD_DATE_MARKERS):
            exp = col.attribute_name
        if curr is None and _contains_any(name, SCD_CURRENT_TOKENS) and _contains_any(name, SCD_FLAG_MARKERS):
            curr = col.attribute_name

    hints.eff_dt_column = eff
    hints.exp_dt_column = exp
    hints.current_flag_column = curr

    system_cols: List[str] = []
    scd_drivers: List[str] = []
    for candidate in (eff, exp, curr):
        if candidate and candidate not in system_cols:
            system_cols.append(candidate)
            scd_drivers.append(candidate)

    for col in columns:
        name = col.attribute_name.upper()
        if any(token in name for token in SYSTEM_TIMESTAMP_TOKENS):
            if col.attribute_name not in system_cols:
                system_cols.append(col.attribute_name)
        if _is_technical_column(col.attribute_name):
            if col.attribute_name not in system_cols:
                system_cols.append(col.attribute_name)

    if scd_drivers:
        hints.scd_type_candidate = "TYPE_2"
        hints.cdc_indicator = "TYPE_2"
    hints.system_generated_columns = system_cols

    return hints


def _parse_table_sheet(interface_code: str, sheet_name: str, df: pd.DataFrame, table_name_hint: Optional[str] = None) -> TargetTable:
    col_rows = df[df.get("Attribute Name").notna()] if "Attribute Name" in df.columns else df
    columns: List[TargetColumn] = []
    pk_cols: List[str] = []
    ak_groups: dict[str, List[str]] = {}

    for _, row in col_rows.iterrows():
        attr = str(row.get("Attribute Name", "")).strip()
        if not attr:
            continue

        raw_type = row.get("Data Type")
        data_type = _adjust_data_type(attr, _canonicalize_data_type(raw_type))
        length = _normalize_length(row.get("Length"))
        precision = _normalize_int(row.get("Precision"))
        scale = None
        fmt = row.get("Format")

        # Target templates are inconsistent: some use "Nullability " (with a trailing space),
        # others use "Nullability". Support both so we don't silently flip required columns to nullable.
        nullable_raw = row.get("Nullability ")
        try:
            if pd.isna(nullable_raw):
                nullable_raw = None
        except Exception:
            pass
        if nullable_raw is None:
            nullable_raw = row.get("Nullability")
        nullable = _normalize_nullable(nullable_raw)

        default_value = row.get("Default Value")
        order_no = _normalize_int(row.get("Order No"))

        key_cols_raw = str(row.get("Key Columns", "")).strip() if not pd.isna(row.get("Key Columns", "")) else ""
        is_pk = "pk" in key_cols_raw.lower()
        is_fk = "fk" in key_cols_raw.lower()

        # Some target templates provide separate PK/FK boolean columns instead of (or in addition to) "Key Columns".
        if _normalize_bool(row.get("Primary Key")):
            is_pk = True
        if _normalize_bool(row.get("Foreign Key")):
            is_fk = True

        if is_pk:
            pk_cols.append(attr)

        ak_memberships: List[str] = []
        if key_cols_raw:
            parts = [p.strip() for p in key_cols_raw.split(",") if p.strip()]
            for p in parts:
                if p.upper().startswith("AK"):
                    ak_memberships.append(p.upper())
                    ak_groups.setdefault(p.upper(), []).append(attr)

        for key in row.keys():
            if "alternate key" in key.lower():
                raw_value = row.get(key)
                if raw_value is None:
                    continue
                try:
                    if pd.isna(raw_value):
                        continue
                except Exception:
                    pass
                text_value = str(raw_value).strip()
                if not text_value:
                    continue
                match = re.search(r"alternate\s*key\s*(\d+)", key, re.IGNORECASE)
                if match:
                    ak_name = f"AK{match.group(1)}"
                else:
                    ak_name = key.strip().upper().replace(" ", "_")
                if ak_name not in ak_memberships:
                    ak_memberships.append(ak_name)
                ak_groups.setdefault(ak_name, []).append(attr)

        is_technical = _is_technical_column(attr)
        col = TargetColumn(
            attribute_name=attr,
            logical_attribute_name=row.get("Logical Attribute Name") if not pd.isna(row.get("Logical Attribute Name")) else None,
            attribute_description=row.get("Attribute Description") if not pd.isna(row.get("Attribute Description")) else None,
            data_type=data_type,
            length=length,
            precision=precision,
            scale=scale,
            format=None if pd.isna(fmt) else str(fmt),
            nullability=nullable,
            default_value=None if pd.isna(default_value) else str(default_value),
            order_no=order_no,
            is_primary_key=is_pk,
            is_foreign_key=is_fk,
            alternate_key_groups=ak_memberships,
            is_surrogate_key=attr.upper().endswith("_SK"),
            is_code_column=attr.upper().endswith("_CD"),
            is_technical=is_technical,
        )
        columns.append(col)

    pk_cols = list(dict.fromkeys(_finalize_primary_keys(columns, pk_cols)))
    _apply_primary_key_flags(columns, pk_cols)

    alternate_keys = [
        AlternateKeyGroup(name=name, column_names=cols) for name, cols in ak_groups.items()
    ]

    table_candidates = [
        _first_non_empty(df.get("Entity Physical Name")),
        _first_non_empty(df.get("Table")),
        _first_non_empty(df.get("Table Name")),
    ]
    raw_table_id = next((c for c in table_candidates if c), None)

    def is_generic(name: Optional[str]) -> bool:
        if not name:
            return True
        normalized = name.strip().upper()
        return normalized in {
            sheet_name.strip().upper(),
            "TABLE_METADATA",
            "TABLE1",
            "TABLE",
            "SHEET1",
        }

    table_id = raw_table_id
    if is_generic(table_id) and table_name_hint:
        table_id = table_name_hint
    if not table_id:
        table_id = sheet_name

    logical_name = _maybe_str(df.iloc[0].get("Entity Business Name")) if "Entity Business Name" in df.columns else None
    description = _maybe_str(df.iloc[0].get("Entity Description")) if "Entity Description" in df.columns else None
    database_name = _maybe_str(df.iloc[0].get("Project Name")) if "Project Name" in df.columns else None

    # "Database" display value (dataset identifier) used by Step 3 review UI.
    database = None
    if "Entity Data Set" in df.columns:
        database = _maybe_str(df.iloc[0].get("Entity Data Set"))
    if not database and "Entity Database/Data Set" in df.columns:
        database = _maybe_str(df.iloc[0].get("Entity Database/Data Set"))

    schema_name = _maybe_str(df.iloc[0].get("Schema Name")) if "Schema Name" in df.columns else None

    scd_hints = _detect_scd_hints(columns)

    return TargetTable(
        table_id=table_id,
        database=database,
        database_name=database_name,
        schema_name=schema_name,
        table_name=table_id,
        logical_name=logical_name,
        description=description,
        columns=columns,
        primary_key=pk_cols,
        alternate_keys=alternate_keys,
        scd_hints=scd_hints,
    )


def _parse_generic_table(interface_code: str, sheet_name: str, df: pd.DataFrame, table_name_hint: Optional[str] = None) -> TargetTable:
    header_row_idx = None
    for idx, row in df.iterrows():
        if any(isinstance(v, str) and v.strip().lower() == "attribute name" for v in row.tolist()):
            header_row_idx = idx
            break

    if header_row_idx is None:
        logger.warning("Could not find column header row in sheet %s", sheet_name)
        return TargetTable(table_id=sheet_name, table_name=sheet_name, columns=[])

    header = [str(v).strip() if not pd.isna(v) else "" for v in df.iloc[header_row_idx].tolist()]
    data = df.iloc[header_row_idx + 1 :].reset_index(drop=True)
    data.columns = header + [f"extra_{i}" for i in range(len(data.columns) - len(header))]

    # Extract table-level metadata from the key/value block above the header row.
    # This is common in IBX templates where the first column holds the key name and the second holds the value.
    meta: dict[str, str] = {}
    top = df.iloc[:header_row_idx, :2]
    for _, row in top.iterrows():
        key = _maybe_str(row.iloc[0])
        val = _maybe_str(row.iloc[1]) if row.shape[0] > 1 else None
        if not key or not val:
            continue
        meta[key.strip().lower()] = val

    table = _parse_table_sheet(interface_code, sheet_name, data, table_name_hint=table_name_hint)

    # Override table-level fields when present in the key/value metadata block.
    physical_name = meta.get("entity physical name")
    if physical_name:
        table.table_id = physical_name
        table.table_name = physical_name

    business_name = meta.get("entity business name")
    if business_name:
        table.logical_name = business_name

    description = meta.get("entity description")
    if description:
        table.description = description

    # Keep `database_name` for project/server label; store dataset id in `database` for review display.
    database_name = meta.get("database server/project name") or meta.get("project name")
    if database_name:
        table.database_name = database_name

    database = meta.get("entity data set") or meta.get("entity database/data set")
    if database:
        table.database = database

    schema_name = meta.get("entity schema") or meta.get("schema name")
    if schema_name:
        table.schema_name = schema_name

    return table


def parse_target_excel(interface_code: str, file_path: Path) -> List[TargetTable]:
    logger.info("Parsing target metadata: %s", file_path)
    xls = pd.ExcelFile(file_path)
    tables: List[TargetTable] = []
    table_name_hint = _derive_table_name_hint(file_path)

    for sheet_name in xls.sheet_names:
        sheet_lower = sheet_name.lower()
        if sheet_lower in {"instructions", "values"}:
            continue

        df = pd.read_excel(xls, sheet_name=sheet_name)
        if df.empty:
            continue

        if "Attribute Name" in df.columns:
            table = _parse_table_sheet(interface_code, sheet_name, df, table_name_hint=table_name_hint)
        else:
            table = _parse_generic_table(interface_code, sheet_name, df, table_name_hint=table_name_hint)
        tables.append(table)

    return tables


def build_target_schema(interface_code: str, target_paths: List[Path]) -> TargetSchema:
    tables: List[TargetTable] = []
    for path in target_paths:
        tables.extend(parse_target_excel(interface_code, path))
    by_table_id = {t.table_id: t for t in tables}
    return TargetSchema(interface_code=interface_code, tables=tables, by_table_id=by_table_id)


# -----------------------
# Data model graph (PoC)
# -----------------------


def build_data_model_graph(interface_code: str, source_schema: SourceSchema, target_schema: TargetSchema) -> DataModelGraph:
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []

    for src in source_schema.files:
        nodes.append(
            GraphNode(
                node_id=f"SRC:{src.file_id}",
                label=src.logical_name or src.file_id,
                node_type="SOURCE_FILE",
            )
        )
    for tgt in target_schema.tables:
        nodes.append(
            GraphNode(
                node_id=f"TGT:{tgt.table_id}",
                label=tgt.logical_name or tgt.table_id,
                node_type="TARGET_TABLE",
            )
        )

    metadata = GraphMetadata(
        graph_mode="excel_only_poc",
        has_erwin=False,
        interface_code=interface_code,
        created_at=datetime.utcnow(),
    )

    return DataModelGraph(nodes=nodes, edges=edges, metadata=metadata)


# -----------------------
# Persistence helper
# -----------------------


def save_shared_state(shared_state, output_dir: Path) -> str:
    # output_dir is intentionally ignored; mapping artifacts are persisted in shared GCS storage.
    _ = output_dir
    return save_json("STEP1_SHARED_STATE", shared_state.run_id, shared_state)
