
import pandas as pd
import csv
import os
import tempfile
import asyncio
from typing import List, Tuple, Dict, Any, Optional
from fastapi import UploadFile, HTTPException
import uuid
from datetime import datetime
from pathlib import Path
import logging
import re
from collections import defaultdict


from utils.bg_query_utils import sanitize_and_deduplicate_columns

class FixedWidthDelimitedConverter:
    """
    A class to convert fixed-width or delimited files to CSV using a metadata template.
    The conversion mode (fixed-width or delimited) is automatically detected based on the input file.
    """
    def __init__(self, metadata_file: str, input_file: str, output_file: str):
        self.metadata_file = metadata_file
        self.input_file = input_file
        self.output_file = output_file
        self.conversion_mode = None

    def _is_fixed_width(self) -> bool:
        """Determines if the input file is fixed-width based on line length consistency."""
        ext = os.path.splitext(self.input_file)[1].lower()

        if ext in [".xlsx", ".xls"]:
            return False

        try:
            with open(self.input_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = [l.rstrip("\n") for i, l in enumerate(f) if l.strip() and i < 100]
        except Exception as e:
            logging.warning(f"Error reading file {self.input_file} for detection: {e}. Assuming delimited.")
            return False

        if len(lines) < 2:
            return False

        # Check for common delimiters
        common_delimiters = [",", "\t", "|", ";"]
        for delim in common_delimiters:
            delimited_lines = [l for l in lines if delim in l]
            if len(delimited_lines) > len(lines) * 0.5:
                counts = [l.count(delim) for l in delimited_lines]
                if len(set(counts)) == 1:
                    logging.info(f"Detected consistent delimiter '{delim}'. Assuming delimited.")
                    return False

        # Check for fixed-width: all lines have the same length
        lengths = [len(l) for l in lines]
        if len(set(lengths)) == 1:
            logging.info(f"Detected consistent line length ({lengths[0]}). Assuming fixed-width.")
            return True

        return False



    widths = None


    def _load_metadata(self) -> pd.DataFrame:
        """Loads metadata from Excel or CSV."""
        ext = os.path.splitext(self.metadata_file)[1].lower()
        if ext in [".xlsx", ".xls"]:
            meta_dict = pd.read_excel(self.metadata_file, sheet_name=None, dtype=str)
            meta = pd.concat(meta_dict.values(), ignore_index=True)
        else:
            try:
                meta = pd.read_csv(self.metadata_file, dtype=str)
            except UnicodeDecodeError:
                meta = pd.read_csv(self.metadata_file, dtype=str, encoding="latin1")

        # meta.columns = [c.strip().replace(" ", "_").replace("#", "") for c in meta.columns]

        def normalize(col):
            col = col.lower().strip()
            col = re.sub(r'[^a-z0-9]+', '_', col)
            return col.strip('_')

        meta.columns = [normalize(c) for c in meta.columns]

        return meta.fillna("")


    def _apply_header_trailer_prefixes(
        self,
        df: pd.DataFrame,
        meta: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Apply header_ / trailer_ prefixes ONLY for columns
        that belong to Header / Trailer records per metadata.
        """

        import logging
        import re

        logging.info("PREFIX NORMALIZATION: Metadata-driven prefixing started")

        # --- build column → role map from metadata ---
        role_map = {}

        def detect_role(row) -> str:
            text = " ".join(row.astype(str).str.lower().tolist())
            if re.search(r"\bheader\b|\bhdr\b", text):
                return "header"
            if re.search(r"\btrailer\b|\bfooter\b", text):
                return "trailer"
            return "detail"

        for _, row in meta.iterrows():
            role = detect_role(row)
            field = row.get("field_name") or row.get("field") or row.get("name")
            if field:
                col = re.sub(r"[^a-z0-9]+", "_", field.lower()).strip("_")
                role_map[col] = role

        logging.debug("PREFIX NORMALIZATION: role_map=%s", role_map)

        # --- apply prefixes ONLY where metadata says so ---
        new_cols = []
        for col in df.columns:
            base = col.lower()
            role = role_map.get(base, "detail")

            if role == "header":
                new_cols.append(f"header_{col}")
            elif role == "trailer":
                new_cols.append(f"trailer_{col}")
            else:
                new_cols.append(col)

        if new_cols != list(df.columns):
            logging.info(
                "PREFIX NORMALIZATION: Columns before=%s",
                list(df.columns)
            )
            logging.info(
                "PREFIX NORMALIZATION: Columns after=%s",
                new_cols
            )
        else:
            logging.info("PREFIX NORMALIZATION: No prefix changes applied")

        df.columns = new_cols
        return df



    def _prepare_fixed_width_metadata(
        self, meta: pd.DataFrame
    ) -> Tuple[List[str], List[Tuple[int, int]]]:
        """
        Prepare field names and colspecs for fixed-width files.

        Rules:
        - 'field_name' column has highest priority
        - 'layout' column is OPTIONAL
        - Prefix header_ / trailer_ ONLY if layout exists
        - Detail fields remain unchanged
        - item / field # are NEVER used as field names
        - Supports Start/End OR Size/Length/Width
        """

        import logging
        import re
        from collections import defaultdict
        import pandas as pd

        logging.info("FW META DEBUG: meta.columns = %s", list(meta.columns))

        # --------------------------------------------------
        # 1. Select semantic field-name column (PRIORITY)
        # --------------------------------------------------
        if "field_name" in meta.columns:
            field_col = "field_name"
        else:
            FALLBACKS = ["field", "column", "column_name", "name", "description"]
            field_col = next((c for c in FALLBACKS if c in meta.columns), None)

        if not field_col:
            raise ValueError(
                f"Metadata must contain a semantic field name column. Found: {list(meta.columns)}"
            )

        logging.info("FW META DEBUG: selected field_col = %s", field_col)

        # --------------------------------------------------
        # 2. Normalize helper
        # --------------------------------------------------
        def normalize(name: str) -> str:
            name = str(name).lower().strip()
            name = re.sub(r"[^a-z0-9]+", "_", name)
            return name.strip("_")

        raw_field_names = meta[field_col].astype(str).str.strip().tolist()

        logging.info(
            "FW META DEBUG: raw_field_names (first 20) = %s",
            raw_field_names[:20],
        )

        # --------------------------------------------------
        # # 3. Layout handling (OPTIONAL)
        # # --------------------------------------------------
        # has_layout = ("layout" in meta.columns) or ("Section" in meta.columns)

        # if has_layout:
        #     if "layout" in meta.columns:
        #         layouts = meta["layout"].astype(str).str.lower().tolist() 
        #     else:
        #         layouts = meta["Section"].astype(str).str.lower().tolist() 
        #     logging.info("FW META DEBUG: layout column detected")
        # else:
        #     layouts = ["detail"] * len(raw_field_names)
        #     logging.info(
        #         "FW META DEBUG: no layout column found → defaulting all rows to DETAIL"
        #     )

        # # --------------------------------------------------
        # # 4. Apply prefix logic
        # # --------------------------------------------------
        # field_names: List[str] = []

        # for field, layout in zip(raw_field_names, layouts):
        #     base = normalize(field)

        #     if has_layout and "header" in layout:
        #         field_names.append(f"header_{base}")
        #     elif has_layout and "trailer" in layout:
        #         field_names.append(f"trailer_{base}")
        #     else:
        #         field_names.append(base)


        # --------------------------------------------------
        # 3. Detect header/trailer semantically (GENERIC)
        # --------------------------------------------------
        def detect_role(row) -> str:
            text = " ".join(row.astype(str).str.lower().tolist())
            if re.search(r'\bheader\b|\bhdr\b', text):
                return "header"
            if re.search(r'\btrailer\b|\bfooter\b|\btotal\b|\bcount\b|\bsum\b', text):
                return "trailer"
            return "detail"

        roles = meta.apply(detect_role, axis=1)

        logging.info(
            "FW META DEBUG: ROLES  = %s",
            roles,
        )

        # --------------------------------------------------
        # 4. Apply prefix logic (ALWAYS collision-safe)
        # --------------------------------------------------
        field_names = []

        for field, role in zip(raw_field_names, roles):
            base = normalize(field)

            if role == "header":
                field_names.append(f"header_{base}")
            elif role == "trailer":
                field_names.append(f"trailer_{base}")
            else:
                field_names.append(base)



        logging.info(
            "FW META DEBUG: field_names after prefixing  = %s",
            field_names,
        )

        # --------------------------------------------------
        # 5. Width detection (Start/End OR Size)
        # --------------------------------------------------

        def _extract_widths_from_field_type(meta: pd.DataFrame) -> list[int]:
            if "field_type" not in meta.columns:
                return []

            widths = []

            for val in meta["field_type"].astype(str):
                # match patterns like 1/80 AN or 8/8 N
                # m = re.search(r"\b\d+\s*/\s*(\d+)\b", val)
                # if not m:
                #     raise ValueError(
                #         f"Invalid field_type format for fixed-width: {val}"
                #     )
                # widths.append(int(m.group(1)))

                val = val.strip()
        
                # Match patterns like:
                # - "1/80 AN" or "8/8 N" → extract the second number (80 or 8)
                # - "2 AN" or "4 AN" → extract the single number (2 or 4)
                
                # First try to match format with slash: "min/max TYPE"
                m = re.search(r"\b\d+\s*/\s*(\d+)\b", val)
                if m:
                    widths.append(int(m.group(1)))
                    continue
                
                # If no slash, try to match single number: "width TYPE"
                m = re.search(r"^(\d+)\s+[A-Z]", val)
                if m:
                    widths.append(int(m.group(1)))
                    continue
                
                # If neither pattern matches, raise an error
                raise ValueError(
                    f"Invalid field_type format for fixed-width: '{val}'. "
                    f"Expected formats: 'min/max TYPE' (e.g., '1/80 AN') or 'width TYPE' (e.g., '2 AN')"
                )

            return widths


        START_CANDIDATES = ["start", "starting", "from", "begin"]
        END_CANDIDATES   = ["end", "ending", "to"]
        SIZE_CANDIDATES  = ["size", "length", "width"]

        start_col = next((c for c in meta.columns if c in START_CANDIDATES), None)
        end_col   = next((c for c in meta.columns if c in END_CANDIDATES), None)
        size_col  = next((c for c in meta.columns if c in SIZE_CANDIDATES), None)

        colspecs = []

        if start_col and end_col:
            logging.info("FW META DEBUG: using Start/End columns")

            starts = pd.to_numeric(meta[start_col], errors="coerce")
            ends = pd.to_numeric(meta[end_col], errors="coerce")
            valid = starts.notna() & ends.notna()

            field_names = [field_names[i] for i, v in enumerate(valid) if v]
            colspecs = list(
                zip(starts[valid].astype(int), ends[valid].astype(int))
            )

        elif size_col:
            logging.info("FW META DEBUG: using Size/Length/Width column")

            widths = (
                pd.to_numeric(meta[size_col], errors="coerce")
                .dropna()
                .astype(int)
                .tolist()
            )

            pos = 0
            for w in widths:
                if w <= 0:
                    raise ValueError("All field widths must be positive.")
                colspecs.append((pos, pos + w))
                pos += w

            field_names = field_names[: len(colspecs)]
        elif "field_type" in meta.columns:
            logging.info("FW META DEBUG: deriving widths from field_type column")

            # widths = self._extract_widths_from_field_type(meta)
            widths = _extract_widths_from_field_type(meta)

            pos = 0
            for w in widths:
                colspecs.append((pos, pos + w))
                pos += w

            field_names = field_names[: len(colspecs)]
        else:
            raise ValueError(
                "Fixed-width metadata must contain Start/End OR Size/Length/Width OR encoded Field Type (e.g. 1/80)."
            )




        # START_CANDIDATES = ["start", "starting", "from", "begin"]
        # END_CANDIDATES = ["end", "ending", "to"]
        # SIZE_CANDIDATES = ["size", "length", "width"]

        # start_col = next((c for c in meta.columns if c in START_CANDIDATES), None)
        # end_col = next((c for c in meta.columns if c in END_CANDIDATES), None)
        # size_col = next((c for c in meta.columns if c in SIZE_CANDIDATES), None)

        # colspecs: List[Tuple[int, int]] = []

        # if start_col and end_col:
        #     logging.info("FW META DEBUG: using Start/End columns")

        #     starts = pd.to_numeric(meta[start_col], errors="coerce")
        #     ends = pd.to_numeric(meta[end_col], errors="coerce")
        #     valid = starts.notna() & ends.notna()

        #     field_names = [field_names[i] for i, v in enumerate(valid) if v]
        #     colspecs = list(
        #         zip(starts[valid].astype(int), ends[valid].astype(int))
        #     )

        # elif size_col:
        #     logging.info("FW META DEBUG: using Size/Length/Width column")

        #     widths = (
        #         pd.to_numeric(meta[size_col], errors="coerce")
        #         .dropna()
        #         .astype(int)
        #         .tolist()
        #     )

        #     pos = 0
        #     for w in widths:
        #         if w <= 0:
        #             raise ValueError("All field widths must be positive.")
        #         colspecs.append((pos, pos + w))
        #         pos += w

        #     field_names = field_names[: len(colspecs)]

        # else:
        #     raise ValueError(
        #         "Fixed-width metadata must contain Start/End OR Size/Length/Width columns."
        #     )

        # --------------------------------------------------
        # 6. De-duplicate column names (FINAL SAFETY)
        # --------------------------------------------------
        seen = defaultdict(int)
        deduped = []

        for name in field_names:
            seen[name] += 1
            if seen[name] == 1:
                deduped.append(name)
            else:
                deduped.append(f"{name}_{seen[name]}")

        field_names = deduped

        logging.info(
            "FW META DEBUG: final field_names (first 30) = %s",
            field_names[:30],
        )
        logging.info(
            "FW META DEBUG: field count = %d | colspecs count = %d",
            len(field_names),
            len(colspecs),
        )

        return field_names, colspecs




    # def _prepare_delimited_metadata(self, meta: pd.DataFrame) -> Dict[str, List[str]]:
    #     """Prepares field names for delimited files, handling Header/Detail sections."""
    #     logging.info("Preparing delimited metadata.")

    #     if not all(col in meta.columns for col in ["Section", "Field_", "Field_Name"]):
    #         logging.warning("Metadata missing 'Section' or 'Field #'. Assuming all fields are 'Detail Record'.")
    #         if "Field_Name" not in meta.columns:
    #             raise ValueError("Metadata must contain a 'Field_Name' column.")
    #         detail_fields = meta["Field_Name"].tolist()
    #         return {"header": [], "detail": detail_fields}

    #     meta["Field_"] = pd.to_numeric(meta["Field_"], errors='coerce').astype('Int64')
    #     meta = meta.sort_values(by=["Section", "Field_"])

    #     header_fields = meta[meta["Section"] == "Header Record"]["Field_Name"].tolist()
    #     detail_fields = meta[meta["Section"] == "Detail Record"]["Field_Name"].tolist()

    #     return {"header": header_fields, "detail": detail_fields}



    def _prepare_delimited_metadata(self, meta: pd.DataFrame) -> List[str]:
        logging.info("Preparing delimited metadata using data dictionary")

        # if "field_name" not in meta.columns:
        #     raise ValueError(
        #         f"Delimited dictionary must contain 'Field Name'. "
        #         f"Found columns: {list(meta.columns)}"
        #     )

        from collections import defaultdict

        logging.info("Preparing delimited metadata using semantic field detection")

        # --------------------------------------------------
        # 1. Case-insensitive column normalization
        # --------------------------------------------------
        normalized_cols = {c.lower().strip(): c for c in meta.columns}

        # --------------------------------------------------
        # 2. Select semantic field-name column (SAME AS FIXED WIDTH)
        # --------------------------------------------------
        PRIORITY = [
            "field_name",
            "field name",
            "field_name",
            "field",
            "column",
            "column_name",
            "name"
        ]

        field_col = None
        for key in PRIORITY:
            if key in normalized_cols:
                field_col = normalized_cols[key]
                break

        if not field_col:
            raise ValueError(
                f"Delimited dictionary must contain a semantic field name column. "
                f"Found columns: {list(meta.columns)}"
            )

        logging.info("Delimited META: selected field_col = %s", field_col)

        def normalize(name: str) -> str:
            name = str(name).strip().lower()
            name = re.sub(r"[^a-z0-9]+", "_", name)
            return name.strip("_")

        # raw_fields = meta["field_name"].tolist()
        raw_fields = meta[field_col].astype(str).str.strip().tolist()


        logging.info("Delimited META: raw fields (first 10) = %s", raw_fields[:10])

        # --------------------------------------------------
        # 4. Normalize + deduplicate
        # --------------------------------------------------
        field_names = [normalize(f) for f in raw_fields]

        seen = defaultdict(int)
        deduped = []
        for f in field_names:
            seen[f] += 1
            deduped.append(f if seen[f] == 1 else f"{f}_{seen[f]}")

        logging.info(
            "Delimited META: final field count = %d | sample = %s",
            len(deduped),
            deduped[:10],
        )

        return deduped




    def _parse_fixed_width(self, field_names: List[str], colspecs: List[Tuple[int, int]]) -> pd.DataFrame:
        """Parses fixed-width lines into a DataFrame using pandas.read_fwf."""
        logging.info(f"Parsing fixed-width data from: {self.input_file}")

        df = pd.read_fwf(
            self.input_file, 
            colspecs=colspecs, 
            header=None, 
            names=field_names,
            dtype=str,
            encoding="utf-8",
            errors="ignore"
        ).fillna("")

        return df




    def _parse_simple_delimited(self, field_names: List[str]) -> pd.DataFrame:
        logging.info("Parsing simple delimited file: %s", self.input_file)

        with open(self.input_file, "r", encoding="utf-8", errors="ignore") as f:
            first_line = next(l for l in f if l.strip())

        if "|" in first_line:
            sep = "|"
        elif "," in first_line:
            sep = ","
        elif ";" in first_line:
            sep = ";"
        elif "\t" in first_line:
            sep = "\t"
        else:
            sep = ","

        logging.info("Detected delimiter: '%s'", sep)
        logging.info("Expected column count: %d", len(field_names))

        df = pd.read_csv(
            self.input_file,
            sep=sep,
            header=None,
            names=field_names,
            dtype=str,
            engine="python"
        ).fillna("")

        logging.info("Parsed rows: %d", len(df))
        logging.info("Parsed columns: %d", len(df.columns))

        return df



    # def _parse_delimited_file(self, metadata: Dict[str, List[str]]) -> pd.DataFrame:
    #     """Parses a delimited file (.xlsx or .csv) based on metadata."""
    #     logging.info(f"Parsing delimited data from: {self.input_file}")
    #     ext = os.path.splitext(self.input_file)[1].lower()

    #     sep = ","
    #     if ext in [".txt", ".dat"]:
    #         try:
    #             with open(self.input_file, "r", encoding="utf-8", errors="ignore") as f:
    #                 first_line = f.readline()
    #                 if "\t" in first_line:
    #                     sep = "\t"
    #                 elif "|" in first_line:
    #                     sep = "|"
    #         except Exception:
    #             pass

    #     if ext in [".xlsx", ".xls"]:
    #         df = pd.read_excel(self.input_file, header=None, dtype=str).fillna("")
    #     else:
    #         df = pd.read_csv(self.input_file, header=None, dtype=str, sep=sep).fillna("")

    #     if df.empty:
    #         return pd.DataFrame(columns=metadata["detail"])

    #     record_type_col = df.iloc[:, 0].astype(str).str.strip()
    #     unique_types = record_type_col.unique()

    #     if len(unique_types) > 1 and metadata["header"]:
    #         logging.info("Detected potential Header/Detail record structure.")
    #         header_row_identifier = unique_types[0]
    #         header_df = df[record_type_col == header_row_identifier]
    #         detail_df = df[record_type_col != header_row_identifier].copy()

    #         if detail_df.empty:
    #             detail_df = df.copy()
    #             header_df = pd.DataFrame()
    #             logging.warning("No distinct detail records found. Treating all records as detail.")
    #     else:
    #         detail_df = df.copy()
    #         header_df = pd.DataFrame()
    #         logging.info("Assuming simple delimited file (no Header/Detail split).")

    #     detail_fields = metadata["detail"]
    #     num_detail_fields = len(detail_fields)
    #     num_data_cols = detail_df.shape[1]

    #     if num_data_cols < num_detail_fields:
    #         logging.warning(f"Input data has {num_data_cols} columns, but metadata defines {num_detail_fields} detail fields.")
    #         detail_df.columns = detail_fields[:num_data_cols]
    #     else:
    #         detail_df = detail_df.iloc[:, :num_detail_fields]
    #         detail_df.columns = detail_fields

    #     header_fields = metadata["header"]
    #     if not header_df.empty:
    #         num_header_fields = len(header_fields)
    #         header_data_row = header_df.iloc[0, :num_header_fields]
    #         header_data = {header_fields[i]: val for i, val in enumerate(header_data_row.tolist())}

    #         for col, value in header_data.items():
    #             if col not in detail_df.columns:
    #                 detail_df[col] = value

    #     final_columns = header_fields + [f for f in detail_fields if f not in header_fields]
    #     final_df = detail_df.reindex(columns=final_columns).fillna("")

    #     return final_df


    def _metadata_indicates_fixed_width(self, meta: pd.DataFrame) -> bool:
        text = " ".join(
            meta.astype(str).fillna("").values.flatten().tolist()
        ).lower()

        return any(
            token in text
            for token in ["1/80", "8/8"]
        )


    def convert(self) -> str:
        """
        The main method to execute the conversion process.
        Returns the path to the output CSV file.
        """
        input_ext = os.path.splitext(self.input_file)[1].lower()

        # ✅ LOAD METADATA FIRST
        meta = self._load_metadata()

        is_fw_ext = input_ext in [".dat", ".txt"] and self._is_fixed_width()

        if self._metadata_indicates_fixed_width(meta):
            self.conversion_mode = "fixed_width"
        elif input_ext in [".xlsx", ".xls"]:
            # self.conversion_mode = "delimited"
            raise RuntimeError(
                "Excel files must not be passed to FixedWidthDelimitedConverter"
            )
        elif self._is_fixed_width() or is_fw_ext:
            self.conversion_mode = "fixed_width"
        else:
            self.conversion_mode = "delimited"

        logging.info(f"Running in {self.conversion_mode.upper()} mode")

        meta = self._load_metadata()

        if self.conversion_mode == "fixed_width":
            field_names, colspecs = self._prepare_fixed_width_metadata(meta)
            df = self._parse_fixed_width(field_names, colspecs)

            logging.info(
                "FW PARSE: Columns right after read_fwf: %s",
                list(df.columns)
            )

            dupes = df.columns[df.columns.duplicated()].tolist()
            if dupes:
                logging.error(
                    "FW PARSE: Duplicate columns immediately after read_fwf: %s",
                    dupes
                )

        elif self.conversion_mode == "delimited":
            # metadata = self._prepare_delimited_metadata(meta)
            # df = self._parse_delimited_file(metadata)

            logging.info("DELIMITED MODE SELECTED")

            field_names = self._prepare_delimited_metadata(meta)

            logging.info(
                "Delimited schema prepared. Column count=%d",
                len(field_names)
            )

            df = self._parse_simple_delimited(field_names)

        # --------------------------------------------
        # APPLY HEADER / TRAILER PREFIXES (CRITICAL)
        # --------------------------------------------
        df = self._apply_header_trailer_prefixes(df, meta)

        logging.info(f"Saving data to CSV: {self.output_file}")
        # df.columns = sanitize_and_deduplicate_columns(df.columns.tolist())
        logging.info(
            "PRE-SANITIZE: Columns before sanitization: %s",
            list(df.columns)
        )

        df.columns = sanitize_and_deduplicate_columns(list(df.columns))

        logging.info(
            "POST-SANITIZE: Columns after sanitization: %s",
            list(df.columns)
        )

        dupes = df.columns[df.columns.duplicated()].tolist()
        if dupes:
            logging.error(
                "FINAL DUPLICATES BEFORE CSV WRITE: %s",
                dupes
            )

        df = df.loc[:, ~pd.Index(df.columns).duplicated()]
        df.to_csv(self.output_file, index=False, quoting=csv.QUOTE_MINIMAL)
        logging.info(f"Conversion successful! Total records processed: {len(df)}")

        return self.output_file


def needs_conversion(filename: str) -> bool:
    """Check if file needs conversion to CSV based on extension."""
    ext = os.path.splitext(filename)[1].lower()
    # Files that might need conversion
    convertible_extensions = [".dat", ".txt", ".xlsx", ".xls"]
    return ext in convertible_extensions


async def convert_to_csv_if_needed(
    file: UploadFile, 
    metadata_path: Optional[str] = None
) -> Tuple[UploadFile, bool]:
    """
    Convert file to CSV if needed, using metadata if provided.
    Returns the file (original or converted) and a boolean indicating if conversion happened.
    """

    ext = os.path.splitext(file.filename)[1].lower()

    # Excel files are already structured; metadata is semantic only
    if ext in [".xlsx", ".xls"]:
        logging.info(
            "Excel file detected (%s). Skipping conversion. Metadata will be applied later.",
            file.filename
        )
        return file, False



    if not needs_conversion(file.filename):
        logging.info(f"File {file.filename} doesn't need conversion")
        return file, False

    if not metadata_path or not os.path.exists(metadata_path):
        logging.info(f"No metadata provided for {file.filename}, skipping conversion")
        return file, False

    try:
        # Save uploaded file to temporary location
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix=os.path.splitext(file.filename)[1]) as temp_input:
            content = await file.read()
            temp_input.write(content)
            temp_input_path = temp_input.name

        # Reset file pointer for potential re-reading
        await file.seek(0)


        # Create temporary output CSV file securely
        temp_output = tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.csv')
        temp_output.close() # Close immediately, converter will write to it
        temp_output_path = temp_output.name

        # Run conversion in thread pool
        def run_conversion():
            converter = FixedWidthDelimitedConverter(
                metadata_file=metadata_path,
                input_file=temp_input_path,
                output_file=temp_output_path
            )
            return converter.convert()

        output_csv_path = await asyncio.to_thread(run_conversion)

        # Read converted CSV and create new UploadFile
        with open(output_csv_path, 'rb') as csv_file:
            csv_content = csv_file.read()

        # Clean up temporary files
        os.unlink(temp_input_path)
        os.unlink(output_csv_path)

        # Create new UploadFile from CSV content
        csv_filename = os.path.splitext(file.filename)[0] + '.csv'

        # Create a file-like object for the CSV content
        import io
        csv_file_obj = io.BytesIO(csv_content)

        # Create new UploadFile
        from fastapi import UploadFile as FastAPIUploadFile
        converted_file = FastAPIUploadFile(
            filename=csv_filename,
            file=csv_file_obj
        )
        converted_file.was_converted_from_fixed_width = True

        logging.info(f"Successfully converted {file.filename} to {csv_filename}")
        return converted_file, True

    except Exception as e:
        logging.error(f"Error converting file {file.filename}: {e}")
        # Clean up on error
        if 'temp_input_path' in locals() and os.path.exists(temp_input_path):
            os.unlink(temp_input_path)
        if 'temp_output_path' in locals() and os.path.exists(temp_output_path):
            os.unlink(temp_output_path)
        raise HTTPException(status_code=400, detail=f"File conversion failed: {str(e)}")
