"""Local SQLite warehouse — drop-in replacement for the BigQuery client.

``get_bigquery_client()`` (utils/bg_query_utils.py) returns a ``LocalBQClient``
from here instead of a real ``bigquery.Client``. The shim implements the subset
of the BigQuery client API the app uses (query / load_table_from_dataframe /
get_table / dataset / create_*) against a single SQLite file
(``config.WAREHOUSE_DB_PATH``). BigQuery table references of the form
``project.dataset.table`` (optionally back-quoted) map to a SQLite table named by
the final segment, so existing SQL keeps working after light normalization.

This keeps full feature parity for the common operations (load a DataFrame, then
``SELECT ... FROM `proj.ds.table``). BigQuery-only SQL functions
(APPROX_TOP_COUNT, STRUCT, ML.DISTANCE, …) are not translated and should be
handled at the call site.
"""

from __future__ import annotations

import logging
import re
import threading

import pandas as pd
from sqlalchemy import create_engine, text

from config.settings import config

logger = logging.getLogger(__name__)

_ENGINE = None
_ENGINE_LOCK = threading.Lock()
# SQLite serializes writers; guard write ops so concurrent profiling threads are safe.
_WRITE_LOCK = threading.RLock()


def _register_sqlite_functions(dbapi_conn, _connection_record=None):
    """Register BigQuery-style scalar functions on each SQLite connection."""
    import hashlib
    import re as _re

    def _regexp(pattern, value):  # SQLite `x REGEXP y` -> regexp(y, x) == regexp(pattern, value)
        if value is None or pattern is None:
            return None
        return 1 if _re.search(pattern, str(value)) else 0

    def _regexp_contains(value, pattern):  # BigQuery REGEXP_CONTAINS(value, pattern)
        if value is None or pattern is None:
            return None
        return 1 if _re.search(pattern, str(value)) else 0

    def _regexp_replace(value, pattern, repl):
        if value is None:
            return None
        return _re.sub(pattern, repl or "", str(value))

    def _regexp_extract(value, pattern):
        if value is None:
            return None
        m = _re.search(pattern, str(value))
        return (m.group(1) if m.groups() else m.group(0)) if m else None

    def _md5(*args):
        if any(a is None for a in args):
            return None
        joined = "".join(str(a) for a in args)
        return hashlib.md5(joined.encode("utf-8")).hexdigest()

    def _initcap(value):
        return None if value is None else str(value).title()

    def _concat(*args):
        if any(a is None for a in args):
            return None  # BigQuery CONCAT returns NULL if any arg is NULL
        return "".join(str(a) for a in args)

    def _concat_ws(sep, *args):
        return (sep or "").join("" if a is None else str(a) for a in args)

    funcs = {
        "REGEXP_CONTAINS": (2, _regexp_contains),
        "REGEXP": (2, _regexp),  # for the `x REGEXP y` operator
        "REGEXP_REPLACE": (3, _regexp_replace),
        "REGEXP_EXTRACT": (2, _regexp_extract),
        "MD5": (-1, _md5),
        "INITCAP": (1, _initcap),
        "CONCAT": (-1, _concat),
        "CONCAT_WS": (-1, _concat_ws),
    }
    for name, (nargs, fn) in funcs.items():
        try:
            dbapi_conn.create_function(name, nargs, fn)
        except Exception:  # noqa: BLE001
            pass


def get_engine():
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                url = f"sqlite:///{config.WAREHOUSE_DB_PATH}"
                _ENGINE = create_engine(
                    url, connect_args={"check_same_thread": False}, future=True
                )
                from sqlalchemy import event

                event.listen(_ENGINE, "connect", _register_sqlite_functions)
                logger.info("Local warehouse engine ready at %s", url)
    return _ENGINE


# ---------------------------------------------------------------------------
# Reference / SQL normalization
# ---------------------------------------------------------------------------
def normalize_table_name(ref) -> str:
    """Reduce a BigQuery-style ref to a bare SQLite table name.

    Accepts strings ('proj.ds.table', '`proj.ds.table`', 'table'), TableRef or
    Table shims. Returns the final dotted segment, stripped of quotes/backticks.
    """
    if ref is None:
        return ""
    if isinstance(ref, _TableRef):
        return ref.table_id
    if isinstance(ref, _Table):
        return ref._name
    s = str(ref).strip().strip("`").strip('"').strip()
    s = s.split("`")[0].strip() if "`" in s else s
    return s.split(".")[-1].strip().strip("`").strip('"')


_BACKTICK_REF = re.compile(r"`([^`]+)`")


def translate_sql(sql: str) -> str:
    """Best-effort BigQuery SQL -> SQLite SQL for table references.

    Replaces back-quoted qualified refs ```a.b.c``` with ``"c"`` and bare
    ``project.dataset.table`` occurrences with ``"table"``.
    """
    if not sql:
        return sql

    def _bt(m):
        inner = m.group(1)
        return '"' + inner.split(".")[-1] + '"'

    out = _BACKTICK_REF.sub(_bt, sql)

    proj = re.escape(str(config.PROJECT_ID))
    # project.dataset.table  -> "table"
    out = re.sub(proj + r"\.[A-Za-z0-9_$]+\.([A-Za-z0-9_$]+)", r'"\1"', out)

    # BigQuery raw-string literals r'...' / r"..." -> plain string literal
    out = re.sub(r"(?<![A-Za-z0-9_])[rR](?=['\"])", "", out)

    # Translate common BigQuery SQL functions to SQLite equivalents.
    out = _bq_funcs_to_sqlite(out)
    return out


def _split_top_commas(inner: str):
    """Split ``inner`` on commas that are not inside parentheses."""
    parts, depth, last = [], 0, 0
    for k, ch in enumerate(inner):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(inner[last:k])
            last = k + 1
    parts.append(inner[last:])
    return [p.strip() for p in parts]


def _replace_balanced_func(sql: str, func: str, render):
    """Replace ``func(<args>)`` (paren-balanced, case-insensitive) using ``render(inner)``."""
    lower = sql.lower()
    needle = func.lower() + "("
    out = []
    i = 0
    while True:
        idx = lower.find(needle, i)
        if idx == -1:
            out.append(sql[i:])
            break
        out.append(sql[i:idx])
        j = idx + len(needle)
        depth = 1
        while j < len(sql) and depth > 0:
            ch = sql[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            j += 1
        inner = sql[idx + len(needle): j - 1]
        out.append(render(inner))
        i = j
        lower = sql.lower()  # unchanged; recompute not needed but safe
    return "".join(out)


def _bq_funcs_to_sqlite(sql: str) -> str:
    # ARRAY_AGG(expr [DISTINCT] [IGNORE NULLS] [LIMIT n]) -> group_concat([DISTINCT] expr)
    # SQLite has no ARRAY_AGG and can't LIMIT inside an aggregate; group_concat
    # returns the (distinct) sample values as a comma-joined string. NULLs are
    # skipped by group_concat, so IGNORE NULLS is implicit.
    def _array_agg(inner: str) -> str:
        s = re.sub(r"\s+(IGNORE|RESPECT)\s+NULLS", "", inner, flags=re.IGNORECASE)
        s = re.sub(r"\s+LIMIT\s+\d+\s*$", "", s, flags=re.IGNORECASE)  # drop trailing LIMIT n
        return f"group_concat({s.strip()})"
    sql = _replace_balanced_func(sql, "ARRAY_AGG", _array_agg)
    # STRUCT(a, b, ...) -> ("a | b | ...") text (SQLite has no STRUCT). Common in
    # metadata queries like group_concat(STRUCT(column_name, data_type)).
    def _struct(inner: str) -> str:
        args = _split_top_commas(inner)
        casts = [f"CAST({a} AS TEXT)" for a in args if a]
        if not casts:
            return "''"
        return casts[0] if len(casts) == 1 else "(" + " || ' | ' || ".join(casts) + ")"
    sql = _replace_balanced_func(sql, "STRUCT", _struct)
    # SQLite < 3.44 can't ORDER BY inside aggregates; drop it inside group_concat.
    sql = re.sub(r"(group_concat\([^()]*?)\s+ORDER\s+BY\s+[^()]*?(\))", r"\1\2", sql, flags=re.IGNORECASE)
    # COUNTIF(cond) -> SUM(CASE WHEN (cond) THEN 1 ELSE 0 END)
    sql = _replace_balanced_func(sql, "COUNTIF", lambda inner: f"SUM(CASE WHEN ({inner}) THEN 1 ELSE 0 END)")
    # APPROX_COUNT_DISTINCT(x) -> COUNT(DISTINCT x)
    sql = _replace_balanced_func(sql, "APPROX_COUNT_DISTINCT", lambda inner: f"COUNT(DISTINCT {inner})")
    # SAFE_DIVIDE(a, b) -> (CAST(a AS REAL) / NULLIF(b, 0))
    def _safe_div(inner):
        # split on top-level comma
        depth = 0
        for k, ch in enumerate(inner):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                a, b = inner[:k], inner[k + 1:]
                return f"(CAST({a} AS REAL) / NULLIF({b}, 0))"
        return f"({inner})"
    sql = _replace_balanced_func(sql, "SAFE_DIVIDE", _safe_div)
    # SAFE_CAST(x AS t) -> CAST(x AS t); SAFE.func -> func
    sql = re.sub(r"\bSAFE_CAST\b", "CAST", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bSAFE\.", "", sql, flags=re.IGNORECASE)
    # IGNORE NULLS / RESPECT NULLS aggregate modifiers (SQLite ignores nulls anyway)
    sql = re.sub(r"\s+(IGNORE|RESPECT)\s+NULLS", "", sql, flags=re.IGNORECASE)
    return sql


# ---------------------------------------------------------------------------
# Lightweight BigQuery-API stand-ins
# ---------------------------------------------------------------------------
class _Field:
    def __init__(self, name, field_type="STRING", mode="NULLABLE"):
        self.name = name
        self.field_type = field_type
        self.mode = mode


class _Table:
    def __init__(self, name, schema=None, num_rows=0):
        self._name = name
        self.schema = schema or []
        self.num_rows = num_rows
        self.table_id = name


class _TableRef:
    def __init__(self, dataset_id, table_id):
        self.dataset_id = dataset_id
        self.table_id = table_id

    def __str__(self):
        return self.table_id


class _DatasetRef:
    def __init__(self, dataset_id):
        self.dataset_id = dataset_id

    def table(self, table_id):
        return _TableRef(self.dataset_id, table_id)


def _to_native(v):
    """Convert numpy/pandas scalars to plain Python (NaN -> None) so results are
    JSON-serializable and behave like BigQuery Row values."""
    if v is None:
        return None
    try:
        if isinstance(v, float) and v != v:  # NaN
            return None
    except Exception:  # noqa: BLE001
        pass
    item = getattr(v, "item", None)
    if callable(item):
        try:
            return v.item()
        except Exception:  # noqa: BLE001
            return v
    return v


class _Row:
    """Mimics a bigquery.Row: supports row.col, row['col'], row.items(), iteration."""

    __slots__ = ("_d",)

    def __init__(self, d):
        object.__setattr__(self, "_d", d)

    def items(self):
        return self._d.items()

    def keys(self):
        return self._d.keys()

    def values(self):
        return self._d.values()

    def get(self, key, default=None):
        return self._d.get(key, default)

    def __getitem__(self, key):
        return self._d[key]

    def __contains__(self, key):
        return key in self._d

    def __iter__(self):
        return iter(self._d.values())

    def __getattr__(self, name):
        try:
            return object.__getattribute__(self, "_d")[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _iter_rows(df: pd.DataFrame):
    cols = list(df.columns)
    for rec in df.itertuples(index=False, name=None):
        yield _Row({c: _to_native(v) for c, v in zip(cols, rec)})


class _Result:
    def __init__(self, df: pd.DataFrame):
        self._df = df if df is not None else pd.DataFrame()

    def to_dataframe(self, *args, **kwargs):
        return self._df

    @property
    def total_rows(self):
        return len(self._df)

    def __iter__(self):
        return _iter_rows(self._df)


class _Job:
    def __init__(self, df: pd.DataFrame | None = None):
        self._df = df if df is not None else pd.DataFrame()

    def result(self, *args, **kwargs):
        return _Result(self._df)

    def to_dataframe(self, *args, **kwargs):
        return self._df

    def __iter__(self):
        return _iter_rows(self._df)


def _table_exists(name: str) -> bool:
    eng = get_engine()
    with eng.connect() as conn:
        r = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
            {"n": name},
        ).fetchone()
    return r is not None


def _schema_of(name: str):
    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.execute(text(f'PRAGMA table_info("{name}")')).fetchall()
    fields = []
    for r in rows:
        # PRAGMA: cid, name, type, notnull, dflt_value, pk
        col_type = (r[2] or "STRING").upper()
        ftype = "STRING"
        if any(t in col_type for t in ("INT",)):
            ftype = "INTEGER"
        elif any(t in col_type for t in ("REAL", "FLOA", "DOUB", "NUM", "DEC")):
            ftype = "FLOAT"
        fields.append(_Field(r[1], ftype, "REQUIRED" if r[3] else "NULLABLE"))
    return fields


class LocalBQClient:
    """SQLite-backed stand-in for ``google.cloud.bigquery.Client``."""

    def __init__(self, *args, **kwargs):  # accept project/credentials/client_options
        self.project = kwargs.get("project") or config.PROJECT_ID

    # -- dataset / table refs -------------------------------------------------
    def dataset(self, dataset_id, project=None):
        return _DatasetRef(dataset_id)

    def get_dataset(self, dataset_ref):
        return dataset_ref  # datasets are implicit in SQLite

    def create_dataset(self, dataset, exists_ok=True, **kwargs):
        return dataset

    def create_table(self, table, exists_ok=True, **kwargs):
        # ``table`` may be a bigquery.Table-like with .schema; create empty table.
        name = normalize_table_name(getattr(table, "table_id", table) or table)
        schema = getattr(table, "schema", None) or []
        cols = [getattr(f, "name", str(f)) for f in schema] or ["_placeholder"]
        cols_sql = ", ".join(f'"{c}" TEXT' for c in cols)
        with _WRITE_LOCK:
            eng = get_engine()
            with eng.begin() as conn:
                conn.execute(text(f'CREATE TABLE IF NOT EXISTS "{name}" ({cols_sql})'))
        return _Table(name, [_Field(c) for c in cols])

    def get_table(self, table_reference):
        name = normalize_table_name(table_reference)
        if not _table_exists(name):
            from google.api_core.exceptions import NotFound

            raise NotFound(f"Table {name} not found in local warehouse")
        with _WRITE_LOCK:
            eng = get_engine()
            with eng.connect() as conn:
                n = conn.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar() or 0
        return _Table(name, _schema_of(name), num_rows=int(n))

    def delete_table(self, table_reference, not_found_ok=True, **kwargs):
        name = normalize_table_name(table_reference)
        with _WRITE_LOCK:
            eng = get_engine()
            with eng.begin() as conn:
                conn.execute(text(f'DROP TABLE IF EXISTS "{name}"'))

    def list_tables(self, dataset=None, **kwargs):
        eng = get_engine()
        with eng.connect() as conn:
            rows = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        return [_Table(r[0]) for r in rows]

    def list_datasets(self, *args, **kwargs):
        return [_DatasetRef(config.BQ_DATASET_ID)]

    # -- load / query ---------------------------------------------------------
    def _if_exists(self, job_config) -> str:
        wd = getattr(job_config, "write_disposition", None)
        return "append" if wd == "WRITE_APPEND" else "replace"

    def load_table_from_dataframe(self, dataframe, destination, job_config=None, **kwargs):
        name = normalize_table_name(destination)
        with _WRITE_LOCK:
            dataframe.to_sql(name, get_engine(), if_exists=self._if_exists(job_config), index=False)
        return _Job(dataframe)

    def load_table_from_json(self, json_rows, destination, job_config=None, **kwargs):
        name = normalize_table_name(destination)
        df = pd.DataFrame(list(json_rows))
        with _WRITE_LOCK:
            df.to_sql(name, get_engine(), if_exists=self._if_exists(job_config), index=False)
        return _Job(df)

    def insert_rows_json(self, table, json_rows, **kwargs):
        name = normalize_table_name(table)
        df = pd.DataFrame(list(json_rows))
        if not df.empty:
            with _WRITE_LOCK:
                df.to_sql(name, get_engine(), if_exists="append", index=False)
        return []  # BigQuery returns a list of per-row errors; empty = success

    def query(self, query, job_config=None, **kwargs):
        sql = _bind_query_parameters(query, job_config)
        if "INFORMATION_SCHEMA" in sql.upper():
            ensure_info_schema_views()
        sql = translate_sql(sql)
        stripped = sql.lstrip().lower()
        if stripped.startswith(("select", "with", "pragma")):
            with _WRITE_LOCK:
                try:
                    df = pd.read_sql_query(text(sql), get_engine())
                except Exception as exc:  # noqa: BLE001
                    fixed = _resolve_missing_table(sql, exc)
                    if fixed is None:
                        raise
                    df = pd.read_sql_query(text(fixed), get_engine())
            return _Job(df)
        # DML/DDL (CREATE/INSERT/UPDATE/DELETE/MERGE-as-rewritten)
        with _WRITE_LOCK:
            eng = get_engine()
            with eng.begin() as conn:
                conn.execute(text(sql))
        return _Job(pd.DataFrame())


def _resolve_missing_table(sql: str, exc) -> str | None:
    """If a query failed with 'no such table: X', map X to a real table whose name
    contains X (e.g. friendly 'sample_claims' -> 'sttm_sample_claims_<id>') and
    return the rewritten SQL. Returns None if it can't be resolved."""
    m = re.search(r'no such table:\s*"?([A-Za-z0-9_.]+)"?', str(exc))
    if not m:
        return None
    missing = m.group(1).strip().strip('"').split(".")[-1]
    if not missing:
        return None
    eng = get_engine()
    with eng.connect() as conn:
        tables = [
            r[0]
            for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        ]
    ml = missing.lower()
    candidates = [t for t in tables if ml in t.lower() and t.lower() != ml]
    if not candidates:
        return None
    best = min(candidates, key=len)  # closest containing match
    fixed = re.sub(r'"%s"' % re.escape(missing), f'"{best}"', sql)
    return fixed if fixed != sql else None


def ensure_info_schema_views() -> None:
    """Emulate BigQuery INFORMATION_SCHEMA.TABLES/COLUMNS as SQLite views so the
    Q&A agent's metadata queries work. Recreated on demand so they reflect current
    tables (TABLES.row_count is computed live via per-table subqueries)."""
    eng = get_engine()
    with _WRITE_LOCK:
        with eng.begin() as conn:
            tables = [
                r[0]
                for r in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            ]
            schema_lit = _sql_literal(getattr(config, "DATASET_ID", "main"))
            proj_lit = _sql_literal(getattr(config, "PROJECT_ID", ""))
            conn.execute(text('DROP VIEW IF EXISTS "COLUMNS"'))
            conn.execute(
                text(
                    'CREATE VIEW "COLUMNS" AS SELECT '
                    f"{proj_lit} AS table_catalog, {schema_lit} AS table_schema, "
                    "m.name AS table_name, p.name AS column_name, "
                    "(p.cid + 1) AS ordinal_position, p.dflt_value AS column_default, "
                    "CASE WHEN p.\"notnull\" THEN 'NO' ELSE 'YES' END AS is_nullable, "
                    "upper(p.type) AS data_type, 'NEVER' AS is_generated, "
                    "'NO' AS is_partitioning_column, NULL AS collation_name "
                    "FROM sqlite_master m JOIN pragma_table_info(m.name) p "
                    "WHERE m.type = 'table'"
                )
            )
            conn.execute(text('DROP VIEW IF EXISTS "TABLES"'))
            if tables:
                parts = " UNION ALL ".join(
                    f"SELECT {proj_lit} AS table_catalog, {schema_lit} AS table_schema, "
                    f"{_sql_literal(t)} AS table_name, 'BASE TABLE' AS table_type, "
                    f'(SELECT COUNT(*) FROM "{t}") AS row_count'
                    for t in tables
                )
                conn.execute(text(f'CREATE VIEW "TABLES" AS {parts}'))
            else:
                conn.execute(
                    text(
                        'CREATE VIEW "TABLES" AS SELECT NULL AS table_catalog, '
                        "NULL AS table_schema, NULL AS table_name, NULL AS table_type, "
                        "0 AS row_count WHERE 0"
                    )
                )


def get_local_bq_client() -> LocalBQClient:
    return LocalBQClient()


# ---------------------------------------------------------------------------
# bigquery-module drop-in: lets `from google.cloud import bigquery` be aliased to
# `from utils import local_warehouse as bigquery` so NO google.cloud.bigquery
# dependency remains — every bigquery.* symbol the app uses resolves here.
# ---------------------------------------------------------------------------
def _sql_literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _bind_query_parameters(sql: str, job_config) -> str:
    """Inline BigQuery @named query parameters into the SQL (SQLite has no @params)."""
    params = list(getattr(job_config, "query_parameters", None) or [])
    if not params:
        return sql
    for p in params:
        name = getattr(p, "name", None)
        if not name:
            continue
        values = getattr(p, "values", None)
        if values is not None:  # ArrayQueryParameter
            tup = "(" + ", ".join(_sql_literal(v) for v in values) + ")"
            sql = re.sub(r"UNNEST\(\s*@" + re.escape(name) + r"\s*\)", tup, sql, flags=re.IGNORECASE)
            sql = re.sub(r"@" + re.escape(name) + r"\b", tup, sql)
        else:  # ScalarQueryParameter
            sql = re.sub(r"@" + re.escape(name) + r"\b", _sql_literal(getattr(p, "value", None)), sql)
    return sql


# -- bigquery.* symbol stand-ins --------------------------------------------
Client = LocalBQClient
SchemaField = _Field
Dataset = _Table  # only .location etc. get set; harmless


class Table:
    def __init__(self, table_ref, schema=None):
        self.table_id = normalize_table_name(table_ref)
        self.schema = schema or []
        # permissive: allow attributes like time_partitioning/clustering_fields to be set
        self.time_partitioning = None
        self.clustering_fields = None


class _ParamHolder:
    """Generic stand-in for LoadJobConfig / QueryJobConfig (stores any kwargs)."""

    def __init__(self, *args, **kwargs):
        self.schema = kwargs.get("schema")
        self.query_parameters = kwargs.get("query_parameters", [])
        self.write_disposition = kwargs.get("write_disposition")
        self.source_format = kwargs.get("source_format")
        self.autodetect = kwargs.get("autodetect", True)
        for k, v in kwargs.items():
            setattr(self, k, v)


LoadJobConfig = _ParamHolder
QueryJobConfig = _ParamHolder


class WriteDisposition:
    WRITE_TRUNCATE = "WRITE_TRUNCATE"
    WRITE_APPEND = "WRITE_APPEND"
    WRITE_EMPTY = "WRITE_EMPTY"


class SourceFormat:
    CSV = "CSV"
    NEWLINE_DELIMITED_JSON = "NEWLINE_DELIMITED_JSON"
    PARQUET = "PARQUET"
    AVRO = "AVRO"


class TimePartitioning:
    def __init__(self, *args, **kwargs):
        self.type_ = kwargs.get("type_")
        self.field = kwargs.get("field")


class ScalarQueryParameter:
    def __init__(self, name, type_=None, value=None):
        self.name = name
        self.type_ = type_
        self.value = value


class ArrayQueryParameter:
    def __init__(self, name, array_type=None, values=None):
        self.name = name
        self.array_type = array_type
        self.values = values or []
