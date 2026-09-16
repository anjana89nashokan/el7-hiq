from dotenv import load_dotenv
import os
import tempfile
from pathlib import Path

# Get the directory where settings.py is located (server/config)
CONFIG_DIR = Path(__file__).parent
# Project/server base dir (one level up from config)
BASE_DIR = CONFIG_DIR.parent
# Load environment from STTM and parent Launchpad backend .env (optional local files).
load_dotenv(BASE_DIR.parent / ".env")
load_dotenv(BASE_DIR / ".env")
DATA_DIR = BASE_DIR / "data"
RUNS_DIR = DATA_DIR / "runs"
TMP_DIR = DATA_DIR / "tmp"


# Configuration
class Config:
    # ------------------------------------------------------------------
    # Standalone mode (no GCP / Vertex AI).
    # LLM + embeddings run via the Gemini Developer API using GOOGLE_API_KEY
    # (Google AI Studio), NOT Vertex. Setting USE_VERTEXAI=FALSE makes both
    # google-adk and google-genai route to the Developer API automatically.
    # ------------------------------------------------------------------
    GOOGLE_GENAI_USE_VERTEXAI = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
    # Standalone (no GCP/Vertex): when true, Vertex AI Search / Discovery Engine
    # RAG calls (standards/data-dictionary/BRD lookups) degrade to empty results
    # instead of calling GCP — the enterprise datastores aren't available locally.
    STANDALONE_MODE = str(GOOGLE_GENAI_USE_VERTEXAI).strip().upper() == "FALSE"

    # ------------------------------------------------------------------
    # LLM provider selection: "gemini" (Google AI Studio) or "groq".
    # Both are supported; set LLM_PROVIDER explicitly, or it auto-detects
    # (groq if only GROQ_API_KEY is present, else gemini).
    # ------------------------------------------------------------------
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
    # Primary Groq model. gpt-oss (OpenAI arch) does structured tool-calls reliably
    # (needed for ADK agent transfers). Fallback model is used automatically when
    # the primary errors (e.g. free-tier TPM "request too large").
    GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
    GROQ_FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.3-70b-versatile").strip()
    # Cap the output-token reservation. Groq's per-request token count includes the
    # max_tokens reservation; when unset it reserves a large default and inflates the
    # request (hits free-tier TPM). Capping it lowers token usage with no code/agent
    # changes. Plenty for tool calls and profiling narratives.
    GROQ_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "2048"))

    # OpenAI (ChatGPT) — used when LLM_PROVIDER=openai (BYOK from Launchpad Settings).
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"

    # ------------------------------------------------------------------
    # LLM fallback chain (litellm model ids, tried in order on error).
    # Spans models AND providers (Groq -> Gemini) for true resilience: if the
    # whole Groq account is rate-limited/down, calls fall through to Gemini
    # (and vice-versa). Override with LLM_FALLBACKS="groq/llama-3.1-8b-instant,
    # gemini/gemini-2.0-flash,..." — any litellm model id whose provider key is set.
    # ------------------------------------------------------------------
    # Current free models per provider (used to build LLM_FALLBACKS below).
    GROQ_FREE_MODELS = [
        GROQ_FALLBACK_MODEL,          # llama-3.3-70b-versatile (12K TPM — highest free budget)
        "openai/gpt-oss-120b",        # reliable tool-calls (8K TPM)
        "openai/gpt-oss-20b",         # reliable tool-calls (8K TPM)
        # NOTE: llama-3.1-8b / qwen3-32b are only 6K TPM and can't fit this app's
        # ~11K-token agent requests; gemma2-9b-it is decommissioned. Omitted.
    ]
    GEMINI_FREE_MODELS = [
        "gemini-3.1-pro-preview",
        "gemini-2.0-flash",
        "gemini-2.5-flash",
        "gemini-2.0-flash-lite",
    ]
    # Built at module level after the class is defined (see below).
    LLM_FALLBACKS: list[str] = []
    _LLM_PROVIDER_ENV = os.getenv("LLM_PROVIDER", "").strip().lower()
    LLM_PROVIDER = _LLM_PROVIDER_ENV or (
        "openai"
        if (OPENAI_API_KEY and not GOOGLE_API_KEY)
        else "groq"
        if (GROQ_API_KEY and not GOOGLE_API_KEY and not OPENAI_API_KEY)
        else "gemini"
    )

    # Generic, neutral identifiers for the local warehouse (no GCP/IBC names).
    # These are what the UI shows as Project / Dataset / Table prefix.
    GOOGLE_CLOUD_PROJECT = os.getenv("WAREHOUSE_PROJECT", "sttm")
    # Vertex AI location must match the reasoning engine location
    GOOGLE_CLOUD_LOCATION = "us-central1"
    BQ_PROJECT_ID = os.getenv("WAREHOUSE_PROJECT", "sttm")
    BQ_DATASET_ID = os.getenv("WAREHOUSE_DATASET", "sttm_data")
    BQ_TABLE_PREFIX = os.getenv("WAREHOUSE_TABLE_PREFIX", "sttm_")
    LOCATION = "us-central1"
    DATASTORE_LOCATION = "us"
    DATASTORE_ID = " test-brd-datastore-unstructured_1770207757677"
    VERTEX_AI_APP_ID = "test-brd-retriever-unstruc_1770207830455"
    STAGING_BUCKET = "gs://gen_ai_datamap_co_pilot_bucket"
    # Standalone: used as the local ADK app name (no Vertex Reasoning Engine).
    # Must be non-empty — it's the app_name for the local SQLite session store.
    REASONING_ENGINE_RESOURCE = os.getenv(
        "REASONING_ENGINE_RESOURCE", "sttm-extract-local"
    )

    PROJECT_ID = os.getenv("WAREHOUSE_PROJECT", "sttm")
    DATASET_ID = os.getenv("WAREHOUSE_DATASET", "sttm_data")

    # Paths for data artifacts
    DATA_DIR = DATA_DIR
    RUNS_DIR = RUNS_DIR
    TMP_DIR = TMP_DIR

    # ------------------------------------------------------------------
    # Standalone local stores (replace BigQuery / GCS / Vertex Vector Search)
    # ------------------------------------------------------------------
    ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", str(DATA_DIR / "artifacts")))
    WAREHOUSE_DB_PATH = os.getenv("WAREHOUSE_DB_PATH", str(DATA_DIR / "warehouse.db"))
    VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", str(DATA_DIR / "vectors.db"))
    APP_DB_PATH = os.getenv("APP_DB_PATH", str(DATA_DIR / "app.db"))
    # ADK session store (replaces Vertex Reasoning Engine sessions)
    ADK_SESSION_DB_URL = os.getenv(
        "ADK_SESSION_DB_URL", f"sqlite:///{DATA_DIR / 'adk_sessions.db'}"
    )
    # Embedding model via Gemini Developer API
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")

    # ERwin graph extraction: columns that may behave like FK links operationally
    # but should be excluded from analytic relationship modeling.
    ERWIN_SYSTEM_LINK_COLUMNS = {
        token.strip().upper()
        for token in os.getenv(
            "ERWIN_SYSTEM_LINK_COLUMNS", "ETL_BATCH_SK,DATA_SRC_CD"
        ).split(",")
        if token.strip()
    }

    # Step 1 ingest: when subject_area is provided, load latest graph artifact from GCS subject-area artifacts.
    STEP1_GRAPH_BY_SUBJECT_ENABLED = os.getenv(
        "STEP1_GRAPH_BY_SUBJECT_ENABLED", "true"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }

    # Resolve credentials path
    _raw_creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_key.json")

    # If path is relative (e.g., "service_key.json"), assume it's in the config directory
    if (
        not os.path.isabs(_raw_creds_path)
        and _raw_creds_path != "path/to/credentials.json"
    ):
        CREDENTIALS_PATH = str(CONFIG_DIR / _raw_creds_path)
    else:
        CREDENTIALS_PATH = _raw_creds_path

    # Standalone mode: export Gemini Developer API config so google-adk and
    # google-genai pick it up automatically (no Vertex / service account needed).
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = str(GOOGLE_GENAI_USE_VERTEXAI)
    if GOOGLE_API_KEY:
        os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY
        # litellm's gemini/ provider authenticates via GEMINI_API_KEY.
        os.environ.setdefault("GEMINI_API_KEY", GOOGLE_API_KEY)
    if GROQ_API_KEY:
        os.environ["GROQ_API_KEY"] = GROQ_API_KEY
    if OPENAI_API_KEY:
        os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
    print(
        f"LLM provider: {LLM_PROVIDER} "
        f"(gemini_key={'set' if GOOGLE_API_KEY else 'unset'}, "
        f"openai_key={'set' if OPENAI_API_KEY else 'unset'}, "
        f"groq_key={'set' if GROQ_API_KEY else 'unset'})"
    )
    if LLM_PROVIDER == "gemini" and not GOOGLE_API_KEY:
        print(
            "Auth: GOOGLE_API_KEY not set — add Gemini in Settings → LLM API, or set "
            "OPENAI_API_KEY / GROQ_API_KEY + LLM_PROVIDER in .env."
        )
    if LLM_PROVIDER == "openai" and not OPENAI_API_KEY:
        print("Auth: LLM_PROVIDER=openai but OPENAI_API_KEY not set.")
    if LLM_PROVIDER == "groq" and not GROQ_API_KEY:
        print("Auth: LLM_PROVIDER=groq but GROQ_API_KEY not set in datamap_backend/.env.")

    # Legacy service-account path is optional now; only export it if present so any
    # not-yet-migrated GCP client can still find it. It is NOT required to boot.
    if os.path.exists(CREDENTIALS_PATH):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_PATH

    # Mapping step artifacts (Step 1/2/3/4) shared storage.
    # Bucket name only (no gs:// prefix). This is required for mapping artifact persistence.
    MAPPING_ARTIFACT_BUCKET = os.getenv(
        "MAPPING_ARTIFACT_BUCKET", "bsa-data-map-artifacts"
    ).strip()
    MAPPING_ARTIFACT_PREFIX = os.getenv(
        "MAPPING_ARTIFACT_PREFIX", "mapping-artifacts"
    ).strip()
    # Artifact-scoped auth mode only (does not affect other services).
    MAPPING_ARTIFACT_AUTH_MODE = (
        os.getenv("MAPPING_ARTIFACT_AUTH_MODE", "service_account_json").strip().lower()
    )  # "adc" or "service_account_json"
    MAPPING_ARTIFACT_SERVICE_ACCOUNT_JSON_PATH = os.getenv(
        "MAPPING_ARTIFACT_SERVICE_ACCOUNT_JSON_PATH", CREDENTIALS_PATH
    ).strip()
    MAPPING_ARTIFACT_PROJECT_ID = os.getenv(
        "MAPPING_ARTIFACT_PROJECT_ID", "ust-genai-pa-poc-gcp"
    ).strip()
    ERWIN_DIAGRAM_ARTIFACT_PREFIX = os.getenv(
        "ERWIN_DIAGRAM_ARTIFACT_PREFIX", "erwin-diagram-artifacts"
    ).strip()
    PROFILING_ARTIFACT_PREFIX = os.getenv(
        "PROFILING_ARTIFACT_PREFIX", "profiling-artifacts"
    ).strip()

    # ------------------------------------------------------------------
    # App session persistence (Postgres)
    # ------------------------------------------------------------------
    APP_DB_HOST = os.getenv("APP_DB_HOST", "localhost").strip()
    APP_DB_PORT = int(os.getenv("APP_DB_PORT", "5432"))
    APP_DB_NAME = os.getenv("APP_DB_NAME", "bsa_datamap").strip()
    APP_DB_USER = os.getenv("APP_DB_USER", "postgres").strip()
    APP_DB_PASSWORD = os.getenv("APP_DB_PASSWORD", "Munworld@Abz01").strip()
    APP_DB_SSLMODE = os.getenv("APP_DB_SSLMODE", "prefer").strip()
    APP_DB_POOL_SIZE = int(os.getenv("APP_DB_POOL_SIZE", "5"))
    APP_DB_MAX_OVERFLOW = int(os.getenv("APP_DB_MAX_OVERFLOW", "10"))
    APP_DB_POOL_TIMEOUT_SEC = int(os.getenv("APP_DB_POOL_TIMEOUT_SEC", "30"))
    APP_DB_POOL_RECYCLE_SEC = int(os.getenv("APP_DB_POOL_RECYCLE_SEC", "1800"))
    APP_DB_CONNECT_TIMEOUT_SEC = int(os.getenv("APP_DB_CONNECT_TIMEOUT_SEC", "30"))
    APP_DB_ENABLED = all([APP_DB_HOST, APP_DB_NAME, APP_DB_USER])

    # Standalone STTM uses header-based dev identity (no SSO / JWT).
    APP_SESSION_AUTH_MODE = os.getenv("APP_SESSION_AUTH_MODE", "dev").strip().lower() or "dev"
    # Neutral, non-personal fallback identity. The real per-user identity is sent
    # by the client via the x-dev-user-id / x-dev-user-email headers (see below);
    # these defaults only apply when no header is present.
    APP_SESSION_DEV_USER_ID = os.getenv("APP_SESSION_DEV_USER_ID", "local-user").strip()
    APP_SESSION_DEV_USER_EMAIL = os.getenv(
        "APP_SESSION_DEV_USER_EMAIL", "user@local"
    ).strip()
    APP_SESSION_DEV_HEADER_USER_ID = (
        os.getenv("APP_SESSION_DEV_HEADER_USER_ID", "x-dev-user-id").strip().lower()
    )
    APP_SESSION_DEV_HEADER_USER_EMAIL = (
        os.getenv("APP_SESSION_DEV_HEADER_USER_EMAIL", "x-dev-user-email")
        .strip()
        .lower()
    )
    # Profiling creates ADK runtime lazily on /app/{id}/profiling/start — not on shell create.
    APP_SESSION_VERTEX_CREATE_ON_SESSION_CREATE = os.getenv(
        "APP_SESSION_VERTEX_CREATE_ON_SESSION_CREATE",
        "false",
    ).strip().lower() in {"1", "true", "yes", "y"}
   
    AGENT_MODEL = os.getenv("AGENT_MODEL", "gemini-3.1-pro-preview")
    # Step 2 can use a dedicated model independent of the global agent model.
    # Falls back to AGENT_MODEL when unset/empty.
    STEP2_AGENT_MODEL = (
        os.getenv("STEP2_AGENT_MODEL", "gemini-3.1-pro-preview").strip() or AGENT_MODEL
    )

    # ------------------------------------------------------------------
    # Step 2 (Mapping Generation) feature flags
    #
    # Policy:
    #   - We keep LLM capabilities enabled in Step 2 by default.
    #   - Only RAG/EvidenceHub-related behavior is gated by a flag because the KB
    #     may not be populated yet.
    # ------------------------------------------------------------------

    # Budget enforcement:
    #   - When false (default), Step 2 will NOT skip triggered LLM calls due to budget caps.
    #     Budgets remain as observability/guardrails knobs, but are not enforced.
    #   - When true, Step 2 enforces the configured max-call budgets.
    STEP2_LLM_ENFORCE_BUDGETS = os.getenv(
        "STEP2_LLM_ENFORCE_BUDGETS", "false"
    ).strip().lower() in {"1", "true", "yes", "y"}

    # Step 2 immediate-cutover switches (LLM-major chooser path).
    STEP2_LLM_INFERRED_RULES_ENABLED = os.getenv(
        "STEP2_LLM_INFERRED_RULES_ENABLED", "true"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    STEP2_LLM_TWO_PASS_ENABLED = os.getenv(
        "STEP2_LLM_TWO_PASS_ENABLED", "true"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    STEP2_GRAPH_HYPOTHESES_ENABLED = os.getenv(
        "STEP2_GRAPH_HYPOTHESES_ENABLED", "true"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    STEP2_AG1_SUBGRAPH_CONTEXT_ENABLED = os.getenv(
        "STEP2_AG1_SUBGRAPH_CONTEXT_ENABLED", "true"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    STEP2_AG1_SUBGRAPH_MAX_NODES = int(os.getenv("STEP2_AG1_SUBGRAPH_MAX_NODES", "600"))
    STEP2_AG1_SUBGRAPH_MAX_EDGES = int(
        os.getenv("STEP2_AG1_SUBGRAPH_MAX_EDGES", "3000")
    )
    STEP2_AG1_SUBGRAPH_MAX_COLUMNS_PER_NODE = int(
        os.getenv("STEP2_AG1_SUBGRAPH_MAX_COLUMNS_PER_NODE", "600")
    )
    STEP2_AG1_REQUIRE_LOOKUP_PATH_ID = os.getenv(
        "STEP2_AG1_REQUIRE_LOOKUP_PATH_ID", "true"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    # AG2 join-path selector (LLM chooses among bounded graph options; deterministic validation applies final gate).
    STEP2_AG2_LLM_JOIN_ENABLED = os.getenv(
        "STEP2_AG2_LLM_JOIN_ENABLED", "true"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    STEP2_AG2_MAX_PATH_HOPS = int(os.getenv("STEP2_AG2_MAX_PATH_HOPS", "3"))
    STEP2_AG2_MAX_PATH_OPTIONS = int(os.getenv("STEP2_AG2_MAX_PATH_OPTIONS", "200"))
    STEP2_AG2_LLM_JOIN_TEMPERATURE = float(
        os.getenv("STEP2_AG2_LLM_JOIN_TEMPERATURE", "0.1")
    )
    STEP2_AG2_LLM_JOIN_CONFIDENCE_REVIEW_THRESHOLD = float(
        os.getenv("STEP2_AG2_LLM_JOIN_CONFIDENCE_REVIEW_THRESHOLD", "0.7")
    )
    # Semantic guardrail for structurally valid but weak lookup paths.
    STEP2_LOOKUP_PATH_FITNESS_GUARD_ENABLED = os.getenv(
        "STEP2_LOOKUP_PATH_FITNESS_GUARD_ENABLED",
        "false",
    ).strip().lower() in {"1", "true", "yes", "y"}
    # When true, semantically weak lookup paths are rejected (can produce JOIN_UNKNOWN).
    # When false, they are applied with needs_review + ISSUE_LOOKUP_PATH_FIT_* (soft guard).
    STEP2_LOOKUP_PATH_FITNESS_STRICT_REJECT = os.getenv(
        "STEP2_LOOKUP_PATH_FITNESS_STRICT_REJECT",
        "false",
    ).strip().lower() in {"1", "true", "yes", "y"}
    STEP2_FORCE_TECHNICAL_RULES = os.getenv(
        "STEP2_FORCE_TECHNICAL_RULES", "true"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }

    # Low temperature by default for stable/structured rule decisions.
    STEP2_LLM_DECISION_TEMPERATURE = float(
        os.getenv("STEP2_LLM_DECISION_TEMPERATURE", "0.2")
    )

    # RAG / EvidenceHub integration (disabled by default until the KB is ready).
    STEP2_RAG_ENABLED = os.getenv("STEP2_RAG_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    # Dummy retrieval mode: returns synthetic evidence snippets so the pipeline can be wired end-to-end.
    STEP2_RAG_DUMMY_RETRIEVAL = os.getenv(
        "STEP2_RAG_DUMMY_RETRIEVAL", "false"
    ).strip().lower() in {"1", "true", "yes", "y"}

    # Budgets (upper bounds) to avoid excessive LLM calls on wide schemas.
    # Candidate discovery is called per target column that needs source selection.
    STEP2_LLM_MAX_CANDIDATE_CALLS = int(
        os.getenv("STEP2_LLM_MAX_CANDIDATE_CALLS", "10000")
    )
    STEP2_LLM_MAX_RULE_DECISIONS = int(os.getenv("STEP2_LLM_MAX_RULE_DECISIONS", "25"))
    STEP2_LLM_MAX_MULTI_RULE_CALLS = int(
        os.getenv("STEP2_LLM_MAX_MULTI_RULE_CALLS", "10")
    )
    STEP2_CASE_IFELSE_STRICT_GATE_ENABLED = os.getenv(
        "STEP2_CASE_IFELSE_STRICT_GATE_ENABLED", "true"
    ).strip().lower() in {"1", "true", "yes", "y"}
    STEP2_MULTI_RULE_REQUIRE_CONCRETE = os.getenv(
        "STEP2_MULTI_RULE_REQUIRE_CONCRETE", "true"
    ).strip().lower() in {"1", "true", "yes", "y"}
    STEP2_MULTI_RULE_MIN_INSTANCES = int(
        os.getenv("STEP2_MULTI_RULE_MIN_INSTANCES", "2")
    )

    # Candidate list size returned from catalog candidate discovery (top-N).
    STEP2_CANDIDATE_TOP_N = int(os.getenv("STEP2_CANDIDATE_TOP_N", "8"))

    # Review threshold:
    #   - Step 2 computes an initial needs_review flag during deterministic seeding.
    #   - Later LLM stages may increase confidence after candidate selection / self-check.
    #   - This threshold is used to re-evaluate needs_review at the end of AG1 so that
    #     very high-confidence inferred rows (and no open issues) don't remain "sticky" reviewed.
    STEP2_NEEDS_REVIEW_CONFIDENCE_THRESHOLD = float(
        os.getenv("STEP2_NEEDS_REVIEW_CONFIDENCE_THRESHOLD", "0.85")
    )
    STEP2_DIRECT_RECOVERY_MIN_SCORE = float(
        os.getenv("STEP2_DIRECT_RECOVERY_MIN_SCORE", "1.10")
    )

    # Per-call timeout (seconds) for Step 2 LLM invocations (semantic scoring, rule decision, re-rank, self-check, etc.).
    # Guardrail: prevents a single slow/failed call from hanging the entire Step 2 run.
    STEP2_LLM_CALL_TIMEOUT_SEC = int(os.getenv("STEP2_LLM_CALL_TIMEOUT_SEC", "120"))

    # Network timeout settings for Vertex AI connections
    VERTEX_AI_CONNECT_TIMEOUT = int(os.getenv("VERTEX_AI_CONNECT_TIMEOUT", "30"))
    VERTEX_AI_READ_TIMEOUT = int(os.getenv("VERTEX_AI_READ_TIMEOUT", "300"))

    # ------------------------------------------------------------------
    # EvidenceHub / Vector Search (RAG) configuration
    # ------------------------------------------------------------------

    # Vertex AI Vector Search resources (used by EvidenceHub ingestion/retrieval).
    VECTOR_SEARCH_INDEX_ID = os.getenv(
        "VECTOR_SEARCH_INDEX_ID", "1446334978571894784"
    ).strip()
    VECTOR_SEARCH_LOCATION = os.getenv(
        "VECTOR_SEARCH_LOCATION", GOOGLE_CLOUD_LOCATION
    ).strip()

    # Legacy Vertex Vector Search settings (unused in standalone mode; kept for
    # config compatibility). Vectors now live in sqlite-vec (VECTOR_DB_PATH).
    VECTOR_SEARCH_DEPLOYED_INDEX_ID = (os.getenv("VECTOR_SEARCH_DEPLOYED_INDEX_ID") or "").strip()
    VECTOR_SEARCH_INDEX_ENDPOINT_ID = (os.getenv("VECTOR_SEARCH_INDEX_ENDPOINT_ID") or "").strip()
    # Public Vector Search endpoints expect the PROJECT NUMBER in the resource path.
    # If unset, code derives it from REASONING_ENGINE_RESOURCE (projects/<number>/...).
    VECTOR_SEARCH_PROJECT_NUMBER = (os.getenv("VECTOR_SEARCH_PROJECT_NUMBER") or "").strip()
    VECTOR_SEARCH_PUBLIC_DOMAIN = (os.getenv("VECTOR_SEARCH_PUBLIC_DOMAIN") or "").strip()

    # EvidenceHub ingestion defaults.
    EVIDENCE_INGEST_CHUNK_SIZE_CHARS = int(
        os.getenv("EVIDENCE_INGEST_CHUNK_SIZE_CHARS", "2500")
    )
    EVIDENCE_INGEST_CHUNK_OVERLAP_CHARS = int(
        os.getenv("EVIDENCE_INGEST_CHUNK_OVERLAP_CHARS", "300")
    )

    # Vertex embeddings model used for evidence ingestion.
    # NOTE: The resulting embedding dimension MUST match the Vector Search index dimension.
    EVIDENCE_EMBEDDING_MODEL = os.getenv(
        "EVIDENCE_EMBEDDING_MODEL", "gemini-embedding-001"
    ).strip()
    EVIDENCE_EMBEDDING_DIMENSIONS = int(
        os.getenv("EVIDENCE_EMBEDDING_DIMENSIONS", "3072")
    )

    # Batch sizes for external calls (tunable without code changes).
    EVIDENCE_EMBED_MAX_CONCURRENCY = int(
        os.getenv("EVIDENCE_EMBED_MAX_CONCURRENCY", "5")
    )
    EVIDENCE_UPSERT_BATCH_SIZE = int(os.getenv("EVIDENCE_UPSERT_BATCH_SIZE", "50"))

    # BigQuery table for Vector Search datapoint catalog (audit + delete-by-metadata).
    EVIDENCE_BQ_DATASET_ID = os.getenv("EVIDENCE_BQ_DATASET_ID", BQ_DATASET_ID).strip()
    EVIDENCE_BQ_TABLE_ID = os.getenv(
        "EVIDENCE_BQ_TABLE_ID", "vectorstore_metadata"
    ).strip()

    # Step 2 evidence retrieval policy (max 9 refs/column).
    # Priority: table feedback (HIGH/MED) > applied Q/A (MED) > playbooks/transcripts (LOW).
    STEP2_EVIDENCE_TABLE_FEEDBACK_TOP_K = int(
        os.getenv("STEP2_EVIDENCE_TABLE_FEEDBACK_TOP_K", "3")
    )
    STEP2_EVIDENCE_QA_FEEDBACK_TOP_K = int(
        os.getenv("STEP2_EVIDENCE_QA_FEEDBACK_TOP_K", "3")
    )
    STEP2_EVIDENCE_VECTOR_TOP_K = int(os.getenv("STEP2_EVIDENCE_VECTOR_TOP_K", "3"))
    STEP2_EVIDENCE_MAX_SNIPPET_CHARS = int(
        os.getenv("STEP2_EVIDENCE_MAX_SNIPPET_CHARS", "10000")
    )

    # Step 2 IndeMap historical mapping evidence (helper-only).
    STEP2_INDEMAP_HISTORY_ENABLED = os.getenv(
        "STEP2_INDEMAP_HISTORY_ENABLED", "false"
    ).strip().lower() in {"1", "true", "yes", "y"}
    STEP2_INDEMAP_HISTORY_FAIL_OPEN = os.getenv(
        "STEP2_INDEMAP_HISTORY_FAIL_OPEN", "true"
    ).strip().lower() in {"1", "true", "yes", "y"}
    STEP2_INDEMAP_HISTORY_FETCH_TOP_N = int(
        os.getenv("STEP2_INDEMAP_HISTORY_FETCH_TOP_N", "10")
    )
    STEP2_INDEMAP_HISTORY_RERANK_TOP_K = int(
        os.getenv("STEP2_INDEMAP_HISTORY_RERANK_TOP_K", "5")
    )
    STEP2_INDEMAP_HISTORY_KEEP_TOP_K = int(
        os.getenv("STEP2_INDEMAP_HISTORY_KEEP_TOP_K", "3")
    )
    STEP2_INDEMAP_HISTORY_MAX_SNIPPET_CHARS = int(
        os.getenv("STEP2_INDEMAP_HISTORY_MAX_SNIPPET_CHARS", "1200")
    )
    STEP2_INDEMAP_HISTORY_MED_THRESHOLD = float(
        os.getenv("STEP2_INDEMAP_HISTORY_MED_THRESHOLD", "0.70")
    )
    STEP2_INDEMAP_HISTORY_LOW_THRESHOLD = float(
        os.getenv("STEP2_INDEMAP_HISTORY_LOW_THRESHOLD", "0.55")
    )

    # Guardrail: if the Step 2 runner experiences repeated LLM failures/timeouts, stop making further calls
    # for that stage in the current run (prevents spending minutes timing out on every column).
    STEP2_LLM_MAX_CONSECUTIVE_FAILURES = int(
        os.getenv("STEP2_LLM_MAX_CONSECUTIVE_FAILURES", "3")
    )

    # Context caching (ADK "ContextCacheConfig")
    #
    # Why:
    #   - In Step 2 AG1 we reuse long, stable prompts and (optionally) a large source catalog.
    #   - Enabling context caching can reduce latency/cost across repeated per-column calls.
    #
    # Notes:
    #   - This does NOT change correctness; hallucination prevention still comes from structured outputs + post-validation.
    STEP2_CONTEXT_CACHE_ENABLED = os.getenv(
        "STEP2_CONTEXT_CACHE_ENABLED", "true"
    ).strip().lower() in {"1", "true", "yes", "y"}
    STEP2_CONTEXT_CACHE_MIN_TOKENS = int(
        os.getenv("STEP2_CONTEXT_CACHE_MIN_TOKENS", "4096")
    )
    STEP2_CONTEXT_CACHE_TTL_SECONDS = int(
        os.getenv("STEP2_CONTEXT_CACHE_TTL_SECONDS", "1800")
    )
    STEP2_CONTEXT_CACHE_INTERVALS = int(
        os.getenv("STEP2_CONTEXT_CACHE_INTERVALS", "10")
    )

    # RAG-only budgets (only used when STEP2_RAG_ENABLED=true).
    STEP2_RAG_MAX_EVIDENCE_CALLS = int(os.getenv("STEP2_RAG_MAX_EVIDENCE_CALLS", "50"))
    STEP2_RAG_MAX_SELF_CHECK_CALLS = int(
        os.getenv("STEP2_RAG_MAX_SELF_CHECK_CALLS", "50")
    )

    DART_PROJECT_ID = os.getenv("DART_PROJECT_ID", "ust-genai-pa-poc-gcp")
    DART_DATASET_ID = os.getenv("DART_DATASET_ID", "DATAMAP_COPILOT")

    # File settings
    MAX_FILE_SIZE = (
        2 * 1024 * 1024 * 1024
    )  # 2GB (Extended limit for large-scale data processing)
    ALLOWED_EXTENSIONS = {
        ".csv",
        ".tsv",
        ".ced",
        ".json",
        ".xlsx",
        ".xls",
        ".xlsm",
        ".parquet",
        ".xml",
        ".psv",
        ".txt",
        ".zip",
        ".dat",
    }

    # Phase 1: Large Context Window Management (100+ tables, 2GB files)
    # Token budgeting for Gemini 2.5 Pro (1M input token limit)
    LLM_MAX_INPUT_TOKENS = 1_000_000  # Gemini 2.5 Pro hard limit
    LLM_TOKEN_SAFETY_MARGIN = 200_000  # Reserve for response + overhead
    LLM_USABLE_TOKENS = 800_000  # LLM_MAX_INPUT_TOKENS - LLM_TOKEN_SAFETY_MARGIN
    LLM_TOKENS_PER_TABLE_BUDGET = (
        150_000  # Max tokens per table (allows ~5 tables/batch)
    )
    LLM_MAX_BATCH_SIZE = 5  # Tables per batch (800K / 150K ≈ 5)

    # Adaptive sampling policy
    ADAPTIVE_SAMPLING_DEFAULT_ROWS = 10  # Default sample rows per table
    ADAPTIVE_SAMPLING_NULL_HEAVY_THRESHOLD = 0.8  # Columns with >80% nulls
    ADAPTIVE_SAMPLING_NULL_HEAVY_ROWS = 3  # Reduced samples for null-heavy columns
    ADAPTIVE_SAMPLING_WIDE_TABLE_THRESHOLD = 50  # Tables with >50 columns
    ADAPTIVE_SAMPLING_WIDE_TABLE_ROWS = 5  # Reduced samples for wide tables
    ADAPTIVE_SAMPLING_TALL_TABLE_THRESHOLD = 1_000_000  # Tables with >1M rows
    ADAPTIVE_SAMPLING_TALL_TABLE_ROWS = 5  # Reduced samples for tall tables
    ADAPTIVE_SAMPLING_SCHEMA_ONLY_THRESHOLD = (
        100  # Tables with >100 columns → schema-only mode (no sample rows)
    )

    # Composite key analysis - Production settings for large datasets
    MAX_COMPOSITE_KEY_SIZE = int(
        os.getenv("MAX_COMPOSITE_KEY_SIZE", "5")
    )  # Test up to 5-column keys
    MIN_COMPOSITE_UNIQUENESS = float(
        os.getenv("MIN_COMPOSITE_UNIQUENESS", "98.0")
    )  # 98% uniqueness threshold

    # Smart column filtering (Option C optimization)
    MAX_COMPOSITE_CANDIDATES_PER_TABLE = int(
        os.getenv("MAX_COMPOSITE_CANDIDATES", "20")
    )  # Top N key-like columns
    COMPOSITE_KEY_MIN_UNIQUENESS = float(
        os.getenv("COMPOSITE_KEY_MIN_UNIQUENESS", "20.0")
    )  # Min 20% unique
    COMPOSITE_KEY_MAX_UNIQUENESS = float(
        os.getenv("COMPOSITE_KEY_MAX_UNIQUENESS", "90.0")
    )  # Max 90% unique
    COMPOSITE_KEY_MAX_NULL_PCT = float(
        os.getenv("COMPOSITE_KEY_MAX_NULL_PCT", "20.0")
    )  # Max 20% nulls

    # LLM sampling for large files (adaptive)
    LLM_SAMPLE_ROWS_SMALL = int(os.getenv("LLM_SAMPLE_ROWS_SMALL", "20"))  # <10K rows
    LLM_SAMPLE_ROWS_MEDIUM = int(
        os.getenv("LLM_SAMPLE_ROWS_MEDIUM", "10")
    )  # 10K-1M rows
    LLM_SAMPLE_ROWS_LARGE = int(os.getenv("LLM_SAMPLE_ROWS_LARGE", "5"))  # >1M rows
    LLM_MAX_COLUMNS_FOR_SAMPLES = int(
        os.getenv("LLM_MAX_COLUMNS_FOR_SAMPLES", "50")
    )  # Schema-only if >50 cols

    # Note: LLM-Enhanced Relationship Analysis is ALWAYS enabled for comprehensive mode
    # Business-aware analysis is the default behavior (not configurable)

    # BigQuery settings
    BIGQUERY_CHUNK_SIZE = int(
        os.getenv("BIGQUERY_CHUNK_SIZE", "10000")
    )  # Rows per upload chunk

    # Parallel processing settings
    max_workers = int(os.getenv("MAX_WORKERS", "4"))
    query_timeout = int(os.getenv("QUERY_TIMEOUT", "300"))  # seconds
    sampling_threshold = int(os.getenv("SAMPLING_THRESHOLD", "1000000"))  # rows
    max_composite_combinations = int(os.getenv("MAX_COMPOSITE_COMBINATIONS", "20"))

    # Phase 1: Timeout and Worker Configurations
    # BigQuery operation timeouts
    BIGQUERY_QUERY_TIMEOUT = int(
        os.getenv("BIGQUERY_QUERY_TIMEOUT", "300")
    )  # 5 min per query
    BIGQUERY_MAX_RESULTS_TIMEOUT = int(
        os.getenv("BIGQUERY_MAX_RESULTS_TIMEOUT", "600")
    )  # 10 min for large result sets

    # Profiling worker pool settings
    PROFILING_MAX_WORKERS = int(
        os.getenv("PROFILING_MAX_WORKERS", "8")
    )  # Parallel BigQuery analysis threads
    PROFILING_BATCH_TIMEOUT = int(
        os.getenv("PROFILING_BATCH_TIMEOUT", "900")
    )  # 15 min per batch (includes LLM call)

    # LLM call timeouts
    LLM_SINGLE_TABLE_TIMEOUT = int(
        os.getenv("LLM_SINGLE_TABLE_TIMEOUT", "120")
    )  # 2 min for single table
    LLM_BATCH_TIMEOUT = int(
        os.getenv("LLM_BATCH_TIMEOUT", "180")
    )  # 3 min for batch (5 tables)

    # Rate limiting for LLM calls (to avoid 429 Resource Exhausted errors)
    LLM_RATE_LIMIT_DELAY = float(
        os.getenv("LLM_RATE_LIMIT_DELAY", "0.0")
    )  # Seconds between LLM batch calls (0 = no delay, let API handle it)
    LLM_MAX_RETRIES = int(
        os.getenv("LLM_MAX_RETRIES", "3")
    )  # Max retries for failed LLM calls
    LLM_RETRY_BASE_DELAY = float(
        os.getenv("LLM_RETRY_BASE_DELAY", "2.0")
    )  # Base delay for exponential backoff (2s, 4s, 8s)
    LLM_RETRY_MAX_DELAY = float(
        os.getenv("LLM_RETRY_MAX_DELAY", "30.0")
    )  # Max delay between retries (reduced from 60s)

    # Rate Limits
    LLM_RPM_LIMIT = int(os.getenv("LLM_RPM_LIMIT", "15"))
    LLM_TPM_LIMIT = int(os.getenv("LLM_TPM_LIMIT", "1000000"))

    # Format detection settings (for XML and TXT files)
    FORMAT_DETECTION_CONFIDENCE_THRESHOLD = int(
        os.getenv("FORMAT_DETECTION_CONFIDENCE_THRESHOLD", "70")
    )
    FORMAT_DETECTION_TIMEOUT = int(
        os.getenv("FORMAT_DETECTION_TIMEOUT", "10")
    )  # seconds
    XML_SAMPLE_SIZE = int(os.getenv("XML_SAMPLE_SIZE", "10240"))  # bytes (10KB)
    TXT_SAMPLE_LINES = int(os.getenv("TXT_SAMPLE_LINES", "50"))

    # Streaming & Progressive Results (Phase 2)
    ENABLE_INCREMENTAL_RESULTS = (
        os.getenv("ENABLE_INCREMENTAL_RESULTS", "true").lower() == "true"
    )
    STREAM_HEARTBEAT_INTERVAL = int(
        os.getenv("STREAM_HEARTBEAT_INTERVAL", "5")
    )  # seconds

    # google_cloud_project = os.getenv('BIGQUERY_PROJECT_ID', 'ust-genai-pa-poc-gcp')
    dev_mode = os.getenv("DEV_MODE", "false").lower() == "true"
    force_bigquery = os.getenv("FORCE_BIGQUERY", "true").lower() == "true"

    # ------------------------------------------------------------------
    # Step 3 (HITL Review - question generation) feature flags
    #
    # Policy:
    #   - Step 3 may use LLM strictly for wordsmithing/UX of questions.
    #   - It must not invent schema; all entities/columns remain constrained by Step 2 artifacts.
    # ------------------------------------------------------------------

    STEP3_LLM_ENABLED = os.getenv("STEP3_LLM_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    STEP3_LLM_WORDSMITH_ENABLED = os.getenv(
        "STEP3_LLM_WORDSMITH_ENABLED", "true"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    STEP3_LLM_CALL_TIMEOUT_SEC = int(os.getenv("STEP3_LLM_CALL_TIMEOUT_SEC", "60"))

    STEP3_CONTEXT_CACHE_ENABLED = os.getenv(
        "STEP3_CONTEXT_CACHE_ENABLED", "true"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    STEP3_CONTEXT_CACHE_MIN_TOKENS = int(
        os.getenv("STEP3_CONTEXT_CACHE_MIN_TOKENS", "2048")
    )
    STEP3_CONTEXT_CACHE_TTL_SECONDS = int(
        os.getenv("STEP3_CONTEXT_CACHE_TTL_SECONDS", "1800")
    )
    STEP3_CONTEXT_CACHE_INTERVALS = int(
        os.getenv("STEP3_CONTEXT_CACHE_INTERVALS", "10")
    )

    STEP3_SOURCE_OPTIONS_TOP_N = int(os.getenv("STEP3_SOURCE_OPTIONS_TOP_N", "5"))

    # ------------------------------------------------------------------
    # Indemap DB Integration Configuration
    #
    # Indemap DB is a SQL Server database that stores:
    # - Target table metadata
    # - Historical mapping rules
    # - Profiling results
    # - File specifications
    # ------------------------------------------------------------------

    # Secret Manager configuration for Indemap DB credentials
    INDEMAP_SECRET_ID = os.getenv(
        "INDEMAP_SECRET_ID"
    )

    # Connection settings
    INDEMAP_CONNECTION_TIMEOUT = int(
        os.getenv("INDEMAP_CONNECTION_TIMEOUT", "30")
    )  # seconds
    INDEMAP_QUERY_TIMEOUT = int(os.getenv("INDEMAP_QUERY_TIMEOUT", "60"))  # seconds
    INDEMAP_BATCH_SIZE = int(
        os.getenv("INDEMAP_BATCH_SIZE", "1000")
    )  # rows per batch insert

    # Indemap table names (configurable via environment variables)
    # These can be updated once schema discovery is complete
    INDEMAP_TARGET_METADATA_TABLE = os.getenv(
        "INDEMAP_TARGET_METADATA_TABLE", "TargetTableMetadata"
    )
    INDEMAP_MAPPING_RULES_TABLE = os.getenv(
        "INDEMAP_MAPPING_RULES_TABLE", "MappingRules"
    )
    INDEMAP_PROFILING_RESULTS_TABLE = os.getenv(
        "INDEMAP_PROFILING_RESULTS_TABLE", "ProfilingResults"
    )
    INDEMAP_FILESPECS_TABLE = os.getenv("INDEMAP_FILESPECS_TABLE", "FileSpecs")
    INDEMAP_AUDIT_LOG_TABLE = os.getenv("INDEMAP_AUDIT_LOG_TABLE", "AuditLog")

    # Indemap service account (provided by client)
    INDEMAP_SERVICE_ACCOUNT = os.getenv("INDEMAP_SERVICE_ACCOUNT", "SRV_MDR_NP")

    # Authentication mode: "windows" or "sql_server"
    # Use "windows" for development/testing with Windows Authentication
    # Use "sql_server" for production with Secret Manager
    INDEMAP_AUTH_MODE = os.getenv(
        "INDEMAP_AUTH_MODE", "windows"
    )  # "windows" or "sql_server"

    # Direct connection settings (for Windows Authentication mode)
    # These are used when INDEMAP_AUTH_MODE = "windows"
    INDEMAP_SERVER = os.getenv("INDEMAP_SERVER", "")
    INDEMAP_DATABASE = os.getenv("INDEMAP_DATABASE", "")
    INDEMAP_PORT = int(os.getenv("INDEMAP_PORT") or "1433")

    # Top-N ranking configuration for mapping rules
    INDEMAP_TOP_N_MAPPINGS = int(os.getenv("INDEMAP_TOP_N_MAPPINGS", "10"))
    INDEMAP_RANKING_CRITERIA = os.getenv(
        "INDEMAP_RANKING_CRITERIA", "relevance"
    )  # usage, recency, relevance
    # Step 4 (Apply Review / Finalization) feature flags
    #
    # Policy:
    #   - Step 4 uses LLM strictly to interpret BSA intent from free-text feedback/answers.
    #   - All LLM outputs must be structured and backed by verbatim evidence spans (no hallucination).
    # ------------------------------------------------------------------

    STEP4_LLM_ENABLED = os.getenv("STEP4_LLM_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    STEP4_LLM_CALL_TIMEOUT_SEC = int(os.getenv("STEP4_LLM_CALL_TIMEOUT_SEC", "120"))

    STEP4_CONTEXT_CACHE_ENABLED = os.getenv(
        "STEP4_CONTEXT_CACHE_ENABLED", "true"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    STEP4_CONTEXT_CACHE_MIN_TOKENS = int(
        os.getenv("STEP4_CONTEXT_CACHE_MIN_TOKENS", "4096")
    )
    STEP4_CONTEXT_CACHE_TTL_SECONDS = int(
        os.getenv("STEP4_CONTEXT_CACHE_TTL_SECONDS", "1800")
    )
    STEP4_CONTEXT_CACHE_INTERVALS = int(
        os.getenv("STEP4_CONTEXT_CACHE_INTERVALS", "10")
    )

    # Step 4 issue batching (Subagent B)
    # - Group by target_table_id, then chunk to limit prompt size and keep quality stable.
    # - Default 20 issues per call is a good trade-off between quality and latency/cost.
    STEP4_ISSUE_BATCH_SIZE = int(os.getenv("STEP4_ISSUE_BATCH_SIZE", "20"))

    # Step 4 text regeneration (Subagent D)
    STEP4_TEXT_REGEN_ENABLED = os.getenv(
        "STEP4_TEXT_REGEN_ENABLED", "true"
    ).strip().lower() in {"1", "true", "yes", "y"}
    STEP4_TEXT_REGEN_LLM_CALL_TIMEOUT_SEC = int(
        os.getenv("STEP4_TEXT_REGEN_LLM_CALL_TIMEOUT_SEC", "120")
    )
    STEP4_TEXT_REGEN_CONTEXT_CACHE_ENABLED = os.getenv(
        "STEP4_TEXT_REGEN_CONTEXT_CACHE_ENABLED", "true"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
    }
    STEP4_TEXT_REGEN_CONTEXT_CACHE_MIN_TOKENS = int(
        os.getenv("STEP4_TEXT_REGEN_CONTEXT_CACHE_MIN_TOKENS", "4096")
    )
    STEP4_TEXT_REGEN_CONTEXT_CACHE_TTL_SECONDS = int(
        os.getenv("STEP4_TEXT_REGEN_CONTEXT_CACHE_TTL_SECONDS", "1800")
    )
    STEP4_TEXT_REGEN_CONTEXT_CACHE_INTERVALS = int(
        os.getenv("STEP4_TEXT_REGEN_CONTEXT_CACHE_INTERVALS", "10")
    )
    STEP4_TEXT_REGEN_BATCH_SIZE = int(os.getenv("STEP4_TEXT_REGEN_BATCH_SIZE", "20"))
    STEP4_TEXT_REGEN_MAX_RETRIES = int(os.getenv("STEP4_TEXT_REGEN_MAX_RETRIES", "1"))

    # ------------------------------------------------------------------
    # Data Dictionary Generation Settings
    # ------------------------------------------------------------------
    # Top-N most frequent values to include in the most_occurrences column per field
    DD_MOST_OCCURRENCES_TOP_N = int(os.getenv("DD_MOST_OCCURRENCES_TOP_N", "5"))

    # ------------------------------------------------------------------
    # DART Suggestion Agent Configuration (Phase 2 - Auto-Suggest)
    # ------------------------------------------------------------------
    DART_SUGGESTION_TOP_N = int(os.getenv("DART_SUGGESTION_TOP_N", "5"))
    DART_VECTOR_TABLE = os.getenv(
        "DART_VECTOR_TABLE", "datamap_simialrity_search_FYI_search"
    )

    # ------------------------------------------------------------------
    # BSA Extract Artifacts (doc extraction / upload-extract endpoint)
    # ------------------------------------------------------------------
    BSA_EXTRACT_ARTIFACT_PREFIX = os.getenv(
        "BSA_EXTRACT_ARTIFACT_PREFIX", "bsa-extract-artifacts"
    ).strip()

    # Pages per chunk for DOCX/PDF → markdown conversion (large-file safety)
    MARKDOWN_CHUNK_PAGES = int(os.getenv("MARKDOWN_CHUNK_PAGES", "10"))
    # Pages worth of markdown content per LLM extraction chunk
    EXTRACTION_CHUNK_PAGES = int(os.getenv("EXTRACTION_CHUNK_PAGES", "10"))

    # ------------------------------------------------------------------
    # BRD extraction / validation prompt caching
    # ------------------------------------------------------------------
    BRD_CONTEXT_CACHE_ENABLED = os.getenv(
        "BRD_CONTEXT_CACHE_ENABLED", "true"
    ).strip().lower() in {"1", "true", "yes", "y"}
    BRD_CONTEXT_CACHE_TTL_SECONDS = int(
        os.getenv("BRD_CONTEXT_CACHE_TTL_SECONDS", "3600")
    )
    BRD_CONTEXT_CACHE_MIN_CHARS = int(os.getenv("BRD_CONTEXT_CACHE_MIN_CHARS", "8000"))

    # ------------------------------------------------------------------
    # Extract Pipeline Configuration (BSA DATAMAP Multi-Agent System)
    # ------------------------------------------------------------------

    # Model for extract pipeline agents (Driver, Discovery, Metadata, Mapping).
    EXTRACT_PIPELINE_MODEL = os.getenv("EXTRACT_PIPELINE_MODEL", "gemini-2.0-flash-001")

    # Discovery Layer: minimum confidence to accept a source match at each priority tier.
    EXTRACT_DISCOVERY_CONFIDENCE_THRESHOLD = float(
        os.getenv("EXTRACT_DISCOVERY_CONFIDENCE_THRESHOLD", "0.80")
    )

    # Mapping Layer: IndiMap reuse shortcut — minimum confidence to reuse a historical mapping.
    EXTRACT_INDIMAP_REUSE_THRESHOLD = float(
        os.getenv("EXTRACT_INDIMAP_REUSE_THRESHOLD", "0.90")
    )

    # Mapping Layer: match type classification thresholds.
    EXTRACT_MATCH_EXACT_THRESHOLD = float(
        os.getenv("EXTRACT_MATCH_EXACT_THRESHOLD", "0.90")
    )
    EXTRACT_MATCH_PARTIAL_THRESHOLD = float(
        os.getenv("EXTRACT_MATCH_PARTIAL_THRESHOLD", "0.60")
    )

    # Extract pipeline LLM call timeout (seconds).
    EXTRACT_LLM_CALL_TIMEOUT_SEC = int(os.getenv("EXTRACT_LLM_CALL_TIMEOUT_SEC", "120"))

    # ------------------------------------------------------------------
    # AIDataDeliveryStandards Grounding (Driver Layer — Extract Feature)
    # ISOLATION: Separate from DATASTORE_ID / VERTEX_AI_APP_ID
    # ------------------------------------------------------------------
    STANDARDS_DATASTORE_ID = os.getenv("STANDARDS_DATASTORE_ID")
    STANDARDS_APP_ID       = os.getenv("STANDARDS_APP_ID")
    STANDARDS_GCS_BUCKET   = os.getenv("STANDARDS_GCS_BUCKET")
    STANDARDS_PROJECT_ID=os.getenv("STANDARDS_PROJECT_ID")
    
    STANDARDS_GCS_FOLDER   = os.getenv("STANDARDS_GCS_FOLDER", "standards")
    STANDARDS_SEARCH_METHOD = os.getenv("STANDARDS_SEARCH_METHOD", "search").strip().lower()

    # FYI Table config
    FYI_TABLE_ID = "FYI_TBL_COLS"
    EXTRACT_FYI_PROJECT_ID = os.getenv("EXTRACT_FYI_PROJECT_ID", "ust-genai-pa-poc-gcp")
    EXTRACT_FYI_DATASET = os.getenv("EXTRACT_FYI_DATASET", "DATAMAP_COPILOT")

    #INDEMAP MAPPINGS TABLE 
    INDEMAP_SOURCE_PROJECT = "ust-genai-pa-poc-gcp"
    INDEMAP_SOURCE_DATASET = "DATAMAP_COPILOT"
    INDEMAP_SOURCE_TABLE = "INDEMAP_MOCK"


config = Config()


def _path_is_writable(dir_path: Path) -> bool:
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        probe = dir_path / ".sttm_write_probe"
        probe.write_text("1")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _resolve_sttm_storage_root(preferred: Path, cfg: Config) -> Path:
    """Pick a writable SQLite directory without requiring deploy env vars."""
    explicit_root = os.getenv("STTM_DEPLOY_DATA_DIR", "").strip()
    if explicit_root:
        return Path(explicit_root)
    if os.getenv("APP_DB_PATH", "").strip():
        return Path(cfg.APP_DB_PATH).parent
    if _path_is_writable(preferred):
        return preferred
    for fallback in (Path("/tmp/sttm"), Path(tempfile.gettempdir()) / "sttm"):
        if _path_is_writable(fallback):
            return fallback
    return preferred


def _configure_writable_storage(cfg: Config) -> None:
    """Redirect STTM SQLite files to a writable directory when package data/ is read-only."""
    if os.getenv("APP_DB_URL", "").strip():
        return
    storage_root = _resolve_sttm_storage_root(cfg.DATA_DIR, cfg)
    try:
        storage_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Warning: could not create STTM storage dir {storage_root}: {exc}")
        return
    if not os.getenv("APP_DB_PATH"):
        cfg.APP_DB_PATH = str(storage_root / "app.db")
    if not os.getenv("ADK_SESSION_DB_URL"):
        cfg.ADK_SESSION_DB_URL = f"sqlite:///{storage_root / 'adk_sessions.db'}"
    if not os.getenv("ARTIFACTS_DIR"):
        cfg.ARTIFACTS_DIR = storage_root / "artifacts"
    if not os.getenv("WAREHOUSE_DB_PATH"):
        cfg.WAREHOUSE_DB_PATH = str(storage_root / "warehouse.db")
    if not os.getenv("VECTOR_DB_PATH"):
        cfg.VECTOR_DB_PATH = str(storage_root / "vectors.db")


_configure_writable_storage(config)


def _build_llm_fallbacks(cfg: Config) -> list[str]:
    """Cross-provider LLM fallback chain (litellm model ids), tried in order on error.

    Spans Groq models then Gemini (or vice-versa) so a fully rate-limited/down
    provider falls through to the other. Override via LLM_FALLBACKS env (CSV).
    """
    env = [m.strip() for m in os.getenv("LLM_FALLBACKS", "").split(",") if m.strip()]
    if env:
        return env
    groq_chain = [f"groq/{m}" for m in cfg.GROQ_FREE_MODELS] if cfg.GROQ_API_KEY else []
    gemini_chain = [f"gemini/{m}" for m in cfg.GEMINI_FREE_MODELS] if cfg.GOOGLE_API_KEY else []
    openai_chain = [f"openai/{cfg.OPENAI_MODEL}"] if cfg.OPENAI_API_KEY else []
    if cfg.LLM_PROVIDER == "openai":
        chain = openai_chain + groq_chain + gemini_chain
        primary = f"openai/{cfg.OPENAI_MODEL}"
    elif cfg.LLM_PROVIDER == "groq":
        chain = groq_chain + gemini_chain + openai_chain
        primary = f"groq/{cfg.GROQ_MODEL}"
    else:
        chain = gemini_chain + openai_chain + groq_chain
        primary = None
    # de-dupe and drop the primary model from its own fallback list
    seen, out = set(), []
    for m in chain:
        if m != primary and m not in seen:
            seen.add(m)
            out.append(m)
    return out


config.LLM_FALLBACKS = _build_llm_fallbacks(config)


def llm_is_configured(cfg: Config | None = None) -> bool:
    """True when the active STTM LLM provider has an API key bound."""
    active = cfg or config
    provider = (active.LLM_PROVIDER or "gemini").lower()
    if provider == "openai":
        return bool((active.OPENAI_API_KEY or "").strip())
    if provider == "groq":
        return bool((active.GROQ_API_KEY or "").strip())
    return bool((active.GOOGLE_API_KEY or "").strip())


# Ensure local data directories exist (standalone storage).
for _d in (config.DATA_DIR, config.RUNS_DIR, config.TMP_DIR, config.ARTIFACTS_DIR):
    try:
        Path(_d).mkdir(parents=True, exist_ok=True)
    except Exception as _e:  # noqa: BLE001
        print(f"Warning: could not create data dir {_d}: {_e}")

os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", config.GOOGLE_GENAI_USE_VERTEXAI)

# ----------------------------------------------------------------------------
# Standalone: route google-genai to the Gemini Developer API (API key), ignoring
# any vertexai=True/project/location passed at the ~40 genai.Client(...) sites.
# Applied centrally so call sites need no edits. Stays bootable without a key
# (falls through to original behavior when GOOGLE_API_KEY is unset).
# ----------------------------------------------------------------------------
try:
    from google import genai as _genai

    if not getattr(_genai, "_ust_patched", False):
        _RealGenaiClient = _genai.Client

        def _ust_client_factory(*args, **kwargs):
            # OpenAI / Groq: litellm-backed stand-in for genai.Client.
            if config.LLM_PROVIDER in {"openai", "groq"} and (
                (config.LLM_PROVIDER == "openai" and config.OPENAI_API_KEY)
                or (config.LLM_PROVIDER == "groq" and config.GROQ_API_KEY)
            ):
                from utils.genai_groq_compat import GroqGenaiCompatClient

                return GroqGenaiCompatClient(*args, **kwargs)
            # Gemini provider: real client via Developer API key (no Vertex).
            if config.GOOGLE_API_KEY:
                kwargs.pop("vertexai", None)
                kwargs.pop("project", None)
                kwargs.pop("location", None)
                kwargs.setdefault("api_key", config.GOOGLE_API_KEY)
            return _RealGenaiClient(*args, **kwargs)

        _genai.Client = _ust_client_factory
        _genai._ust_patched = True
except Exception as _e:  # noqa: BLE001
    print(f"Warning: could not patch genai.Client for standalone mode: {_e}")

# ----------------------------------------------------------------------------
# Standalone: route ADK LlmAgent(model="gemini-...") agents to the selected
# provider. For Groq, resolve to a LiteLlm("groq/<model>") instance so all ADK
# agents (profiling/mapping/extract) use Groq with no per-agent edits. For Gemini
# the model string resolves normally (via the Developer API key).
# ----------------------------------------------------------------------------
try:
    from google.adk.agents.llm_agent import LlmAgent as _LlmAgent
    from google.adk.models.base_llm import BaseLlm as _BaseLlm

    if not getattr(_LlmAgent, "_ust_model_patched", False):

        def _ust_canonical_model(self):
            if isinstance(self.model, _BaseLlm):
                return self.model
            if config.LLM_PROVIDER == "openai" and config.OPENAI_API_KEY and self.model:
                from utils.litellm_json import FenceStrippingLiteLlm as LiteLlm

                kw = {}
                if config.LLM_FALLBACKS:
                    kw["fallbacks"] = list(config.LLM_FALLBACKS)
                model = config.OPENAI_MODEL
                if not str(model).startswith("openai/"):
                    model = f"openai/{model}"
                return LiteLlm(model=model, **kw)
            if config.LLM_PROVIDER == "groq" and config.GROQ_API_KEY and self.model:
                from utils.litellm_json import FenceStrippingLiteLlm as LiteLlm

                kw = {}
                if config.LLM_FALLBACKS:
                    kw["fallbacks"] = list(config.LLM_FALLBACKS)
                if config.GROQ_MAX_TOKENS:
                    kw["max_tokens"] = config.GROQ_MAX_TOKENS
                return LiteLlm(model=f"groq/{config.GROQ_MODEL}", **kw)
            if self.model:
                from google.adk.models.registry import LLMRegistry

                return LLMRegistry.new_llm(self.model)
            ancestor = self.parent_agent
            while ancestor is not None:
                if isinstance(ancestor, _LlmAgent):
                    return ancestor.canonical_model
                ancestor = ancestor.parent_agent
            raise ValueError(f"No model found for {self.name}.")

        _LlmAgent.canonical_model = property(_ust_canonical_model)
        _LlmAgent._ust_model_patched = True

    # Strip markdown ```json fences before ADK parses output_schema JSON. Models
    # (esp. Gemini) sometimes wrap structured output in a fence or a 'json' tag,
    # which breaks output_schema.model_validate_json. Patch the single parse point.
    if not getattr(_LlmAgent, "_ust_output_patched", False):
        from utils.litellm_json import _strip_fence as _ust_strip_fence

        def _ust_maybe_save_output_to_state(self, event):
            if event.author != self.name:
                return
            if (
                self.output_key
                and event.is_final_response()
                and event.content
                and event.content.parts
            ):
                result = "".join(
                    p.text for p in event.content.parts if p.text and not p.thought
                )
                if self.output_schema:
                    if not result.strip():
                        return
                    cleaned = _ust_strip_fence(result)
                    try:
                        result = self.output_schema.model_validate_json(
                            cleaned
                        ).model_dump(exclude_none=True)
                    except Exception:
                        # The model's output didn't fit the strict output_schema
                        # (schema/shape mismatch) or wasn't valid JSON (e.g. a
                        # single-quoted Python-literal). Don't crash the whole
                        # turn — fall back to a best-effort parsed object so the
                        # step still returns usable data and the user can proceed.
                        parsed = None
                        try:
                            import json as _json
                            parsed = _json.loads(cleaned)
                        except Exception:
                            try:
                                import ast as _ast
                                parsed = _ast.literal_eval(cleaned)
                            except Exception:
                                parsed = None
                        result = parsed if parsed is not None else cleaned
                event.actions.state_delta[self.output_key] = result

        _LlmAgent._LlmAgent__maybe_save_output_to_state = _ust_maybe_save_output_to_state
        _LlmAgent._ust_output_patched = True
except Exception as _e:  # noqa: BLE001
    print(f"Warning: could not patch ADK LlmAgent model resolution: {_e}")

# ----------------------------------------------------------------------------
# Standalone: ADK's DatabaseSessionService.to_event rebuilds EventActions via
# model_copy(update=model_dump()), which leaves the nested `compaction` field as
# a plain dict instead of an EventCompaction object. Once conversation history
# grows enough to trigger context-window compaction, ADK's compaction reader
# does `compaction.start_timestamp` and crashes with
# "'dict' object has no attribute 'start_timestamp'". Rehydrate it on load.
# ----------------------------------------------------------------------------
try:
    from google.adk.sessions import database_session_service as _dss
    from google.adk.events.event_actions import EventCompaction as _EventCompaction

    if not getattr(_dss.StorageEvent, "_ust_compaction_patched", False):
        _ust_orig_to_event = _dss.StorageEvent.to_event

        def _ust_to_event(self):
            ev = _ust_orig_to_event(self)
            try:
                actions = getattr(ev, "actions", None)
                comp = getattr(actions, "compaction", None) if actions else None
                if isinstance(comp, dict):
                    actions.compaction = _EventCompaction.model_validate(comp)
            except Exception:
                pass
            return ev

        _dss.StorageEvent.to_event = _ust_to_event
        _dss.StorageEvent._ust_compaction_patched = True
except Exception as _e:  # noqa: BLE001
    print(f"Warning: could not patch DatabaseSessionService compaction rehydration: {_e}")

# Auto-retry transient provider rate limits (free tiers have low TPM caps).
# litellm honors the provider's Retry-After header on 429s.
try:
    import litellm as _litellm

    _litellm.num_retries = int(os.getenv("LITELLM_NUM_RETRIES", "6"))
    _litellm.drop_params = True  # ignore params a given provider doesn't support
except Exception as _e:  # noqa: BLE001
    print(f"Warning: could not configure litellm retries: {_e}")

print("Configuration loaded:")
print(f"PROJECT_ID: {config.PROJECT_ID}")
print(f"DATASET_ID: {config.DATASET_ID}")
