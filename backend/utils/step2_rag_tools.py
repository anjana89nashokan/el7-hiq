"""
Shared RAG/EvidenceHub tools for Step 2 (EvidenceHub retrieval).

When wiring RAG/LLM, use these helpers to:
    - Build evidence queries (target col + context)
    - Parse evidence packs into EvidenceRef objects

Evidence is helper-only and must never introduce unknown entities/columns.
"""

from __future__ import annotations

import json
from typing import List, Optional

from config.settings import config

from agents.mapping_generation.models import EvidenceRef, EvidenceSource
from utils.vectorstore_bigquery_utils import (
    ensure_vectorstore_metadata_table_exists,
    fetch_evidence_rows_by_datapoint_ids,
    fetch_recent_evidence_rows_by_target,
)
from utils.vectorstore_vertex_utils import embed_texts_gemini_embedding, find_neighbors


def build_evidence_query(interface_code: str, target_table: str, target_column: str, forced_rule: str | None = None) -> str:
    """
    Build a concise evidence query string for EvidenceHub.

    Include only context that helps retrieve patterns; avoid sending full schemas.
    """
    parts = [
        f"interface:{interface_code}",
        f"target:{target_table}.{target_column}",
    ]
    if forced_rule:
        parts.append(f"forced_rule:{forced_rule}")
    return " | ".join(parts)


def parse_evidence_response(resp: dict) -> List[EvidenceRef]:
    """
    Convert a raw evidence response (placeholder schema) into EvidenceRef list.

    Expected resp shape (example/placeholder):
    {
        "results": [
            {"id": "doc1#chunk3", "title": "...", "snippet": "...", "score": 0.78}
        ]
    }
    """
    refs: List[EvidenceRef] = []
    for item in resp.get("results", []):
        refs.append(
            EvidenceRef(
                source=EvidenceSource.EVIDENCE_HUB,
                title=item.get("title"),
                snippet=item.get("snippet"),
                locator=item.get("id"),
                relevance_score=item.get("score"),
            )
        )
    return refs


def retrieve_evidence_pack_dummy(
    interface_code: str,
    target_table_id: str,
    target_column_name: str,
    forced_rule_type: Optional[str] = None,
) -> List[EvidenceRef]:
    """
    Dummy retrieval that returns synthetic evidence snippets.

    Why this exists:
      - We want the whole RAG pipeline (retrieve -> interpret -> self-check) wired now,
        even if the real EvidenceHub KB is not yet populated.
      - This function is ONLY used when STEP2_RAG_ENABLED=true and STEP2_RAG_DUMMY_RETRIEVAL=true.

    Guardrail:
      - The returned EvidenceRef snippets are generic patterns, not schema truth.
      - They MUST NOT be used to introduce new entities/columns.
    """
    if not config.STEP2_RAG_ENABLED or not config.STEP2_RAG_DUMMY_RETRIEVAL:
        return []

    col = target_column_name.upper()
    table = target_table_id.upper()
    locator = build_evidence_query(interface_code, target_table_id, target_column_name, forced_rule_type)

    snippets: List[str] = []
    if col.endswith("_CD"):
        snippets.append("Pattern: columns ending with _CD are often LOOKUP from reference/code tables.")
    if col.endswith("_SK") or "SURROGATE" in col:
        snippets.append("Pattern: surrogate keys (_SK) are generated via SK process using a natural key (AK/composite key).")
    if table.endswith("_MAP"):
        snippets.append("Pattern: *_MAP tables commonly hold natural-key to surrogate-key mappings (SK creation).")
    if any(tok in col for tok in ("EFF", "EXP", "CURRENT", "CURR", "ROW_EFF", "ROW_EXP")):
        snippets.append("Pattern: EFF/EXP/CURRENT style columns are typically ETL/SCD scaffolding (TECHNICAL).")

    if not snippets:
        # Minimal neutral hint to exercise the pipeline without forcing decisions.
        snippets.append("General note: evidence is helper-only; confirm with target metadata and instructions.")

    return [
        EvidenceRef(
            source=EvidenceSource.EVIDENCE_HUB,
            title="Dummy EvidenceHub Pattern",
            snippet=s,
            locator=locator,
            relevance_score=0.5,
        )
        for s in snippets
    ]


def _normalize_ws_underscores(s: str) -> str:
    return " ".join((s or "").replace("_", " ").split()).strip()


def _truncate(s: str, max_chars: int) -> str:
    t = (s or "").strip()
    if not t:
        return ""
    if len(t) <= max_chars:
        return t
    return t[: max(0, max_chars - 3)].rstrip() + "..."


def _vector_query_text(
    *,
    target_table_id: str,
    target_column_name: str,
    target_logical_name: Optional[str] = None,
    target_description: Optional[str] = None,
    target_data_type: Optional[str] = None,
    target_key: Optional[str] = None,
    forced_rule_type: Optional[str] = None,
) -> str:
    """
    Deterministic query builder for Vector Search.

    Why:
      - Our playbooks/transcripts are human text; deterministic queries using the target column context
        work well enough to start. We can add LLM query rewrite later only if needed.
    """
    table = (target_table_id or "").strip()
    col = (target_column_name or "").strip()
    logical = _truncate(_normalize_ws_underscores(target_logical_name or ""), 120)
    desc = _truncate(_normalize_ws_underscores(target_description or ""), 240)
    dtype = (target_data_type or "").strip()
    key = (target_key or "").strip()

    parts: list[str] = []
    if table and col:
        parts.append(f"target {table}.{col}")
    if logical:
        parts.append(f"logical {logical}")
    if desc:
        parts.append(f"description {desc}")
    if dtype:
        parts.append(f"datatype {dtype}")
    if key:
        parts.append(f"key {key}")
    if forced_rule_type:
        parts.append(f"forced_rule {forced_rule_type}")

    # Deterministic keyword expansion for common mapping patterns.
    ucol = col.upper()
    if ucol.endswith("_CD"):
        parts.append("code column lookup reference table translation")
    if ucol.endswith("_SK"):
        parts.append("surrogate key natural key alternate key composite key")
    if ucol.endswith(("_IND", "_FLG")):
        parts.append("indicator flag boolean")
    if any(tok in ucol for tok in ("EFF", "EXP", "CURRENT")):
        parts.append("effective date expiry date current flag scd")
    if any(tok in ucol for tok in ("TS", "DTTM", "TIMESTAMP")):
        parts.append("timestamp audit technical column")

    # Join everything into one compact query.
    return " | ".join([p for p in parts if p]).strip()


def _format_bsa_table_feedback_snippet(chunk_text: str, *, authority_level: str, max_chars: int) -> str:
    """
    Convert stored JSON payload into a compact snippet string for LLM consumption.

    The model only sees evidence_snippets[] (strings), so we encode priority/type in the prefix.
    """
    try:
        payload = json.loads(chunk_text or "{}")
    except Exception:
        payload = {}

    patch = (payload or {}).get("row_patch") or {}
    baseline = (payload or {}).get("baseline_row") or {}
    target = (payload or {}).get("target") or {}
    effective = (payload or {}).get("effective_mapping") or {}

    feedback = (patch or {}).get("reasoning_summary") or ""
    # Prefer explicit effective mapping (covers "no source_entity changed" cases).
    rule_type = (effective or {}).get("rule_type") or (patch or {}).get("rule_type")
    src_fields = (effective or {}).get("source_field_names") or (patch or {}).get("source_field_names") or (patch or {}).get("source_fields") or []
    src_ent = (effective or {}).get("source_entity") or (patch or {}).get("source_entity") or (baseline or {}).get("source_entity") or {}
    src_ent_id = src_ent.get("entity_id") if isinstance(src_ent, dict) else None
    join_text = None
    jc = (patch or {}).get("join_condition") or {}
    if isinstance(jc, dict):
        join_text = jc.get("join_text") or jc.get("text")

    tgt_label = f"{target.get('table_id') or baseline.get('target_table_id')}.{target.get('column') or baseline.get('target_column_name')}"
    patch_parts: list[str] = []
    if rule_type:
        patch_parts.append(f"rule_type={rule_type}")
    if src_ent_id:
        patch_parts.append(f"source_entity={src_ent_id}")
    if src_fields:
        patch_parts.append("source_fields=" + ", ".join([str(x) for x in src_fields if x]))
    if join_text:
        patch_parts.append(f"join={join_text}")

    body = " ; ".join([p for p in patch_parts if p]).strip()
    if feedback:
        body = (body + " | feedback: " + str(feedback).strip()).strip(" |")

    auth = (authority_level or "").strip().upper() or "MED"
    snippet = f"[BSA_TABLE_FEEDBACK|{auth}] target={tgt_label} {body}".strip()
    return _truncate(snippet, max_chars)


def _format_bsa_qa_feedback_snippet(chunk_text: str, *, authority_level: str, max_chars: int) -> str:
    try:
        payload = json.loads(chunk_text or "{}")
    except Exception:
        payload = {}
    target = (payload or {}).get("target") or {}
    question = (payload or {}).get("question") or {}
    answer = (payload or {}).get("answer") or {}

    tgt_label = f"{target.get('table_id')}.{target.get('column')}"
    q_text = (question or {}).get("question_text") or ""
    q_kind = (question or {}).get("kind") or ""
    a_text = (answer or {}).get("answer_text") or ""

    body = f"{q_kind}: {_truncate(str(q_text), 220)} | answer: {_truncate(str(a_text), 220)}"
    auth = (authority_level or "").strip().upper() or "MED"
    snippet = f"[BSA_QA_FEEDBACK_APPLIED|{auth}] target={tgt_label} {body}".strip()
    return _truncate(snippet, max_chars)


def _format_playbook_or_transcript_snippet(chunk_text: str, *, evidence_type: str, source_ref: str, max_chars: int) -> str:
    et = (evidence_type or "").strip().upper() or "EVIDENCE"
    src = (source_ref or "").strip()
    prefix = f"[{et}|LOW]"
    if src:
        prefix += f" source={src}"
    body = _truncate(chunk_text or "", max_chars)
    return f"{prefix} {body}".strip()


def _format_indemap_history_snippet(
    *,
    target_table_id: str,
    target_column_name: str,
    candidate_id: str,
    score: float,
    conflict_flag: bool,
    summary: str,
    source_ref: str | None,
    max_chars: int,
) -> str:
    """
    Compact formatter for IndeMap historical mapping evidence snippets.
    """
    score_v = max(0.0, min(1.0, float(score or 0.0)))
    cf = "true" if bool(conflict_flag) else "false"
    src = (source_ref or "").strip()
    prefix = (
        f"[INDEMAP_HISTORY|MED|score={score_v:.2f}|conflict={cf}] "
        f"target={str(target_table_id or '').strip()}.{str(target_column_name or '').strip()} "
        f"candidate={str(candidate_id or '').strip()}"
    ).strip()
    if src:
        prefix += f" source={src}"
    body = _truncate(summary or "", max_chars)
    return f"{prefix} {body}".strip()


async def retrieve_evidence_pack(
    *,
    interface_code: str,
    target_table_id: str,
    target_column_name: str,
    target_logical_name: Optional[str] = None,
    target_description: Optional[str] = None,
    target_data_type: Optional[str] = None,
    target_key: Optional[str] = None,
    forced_rule_type: Optional[str] = None,
) -> List[EvidenceRef]:
    """
    Retrieve up to 9 evidence refs for one target column, with strict priority ordering:
      1) BigQuery experience: BSA_TABLE_FEEDBACK (top 3)
      2) BigQuery experience: BSA_QA_FEEDBACK_APPLIED (top 3)
      3) Vector Search neighbors: PLAYBOOK/TRANSCRIPT (top 3)

    Guardrails:
      - Evidence is helper-only (never truth).
      - Retrieval does NOT validate schema; Step 2 agents must validate all references against Step 1 schemas.
    """
    if not config.STEP2_RAG_ENABLED:
        return []

    if bool(getattr(config, "STEP2_RAG_DUMMY_RETRIEVAL", False)):
        return retrieve_evidence_pack_dummy(
            interface_code=interface_code,
            target_table_id=target_table_id,
            target_column_name=target_column_name,
            forced_rule_type=forced_rule_type,
        )

    ensure_vectorstore_metadata_table_exists()

    table_k = int(getattr(config, "STEP2_EVIDENCE_TABLE_FEEDBACK_TOP_K", 3))
    qa_k = int(getattr(config, "STEP2_EVIDENCE_QA_FEEDBACK_TOP_K", 3))
    vec_k = int(getattr(config, "STEP2_EVIDENCE_VECTOR_TOP_K", 3))
    max_snip = int(getattr(config, "STEP2_EVIDENCE_MAX_SNIPPET_CHARS", 1200))

    refs: list[EvidenceRef] = []
    seen: set[tuple[str, str, str]] = set()  # (evidence_type, source_ref, chunk_hash)

    # 1) Table feedback (HIGH priority).
    for r in fetch_recent_evidence_rows_by_target(
        evidence_type="BSA_TABLE_FEEDBACK",
        target_table_id=target_table_id,
        target_column_name=target_column_name,
        limit=table_k,
    ):
        et = str(r.get("evidence_type") or "").strip()
        sr = str(r.get("source_ref") or "").strip()
        ch = str(r.get("chunk_hash") or "").strip()
        if (et, sr, ch) in seen:
            continue
        seen.add((et, sr, ch))
        refs.append(
            EvidenceRef(
                source=EvidenceSource.EVIDENCE_HUB,
                evidence_type=et,
                authority_level=str(r.get("authority_level") or "HIGH"),
                interface_code=r.get("interface_code"),
                target_table_id=r.get("target_table_id"),
                target_column_name=r.get("target_column_name"),
                source_ref=sr or None,
                created_at=r.get("created_at"),
                version=r.get("version"),
                title="BSA Table Feedback",
                snippet=_format_bsa_table_feedback_snippet(
                    str(r.get("chunk_text") or ""),
                    authority_level=str(r.get("authority_level") or "MED"),
                    max_chars=max_snip,
                ),
                locator=str(r.get("datapoint_id") or ""),
            )
        )

    # 2) Q/A feedback applied (MED priority).
    for r in fetch_recent_evidence_rows_by_target(
        evidence_type="BSA_QA_FEEDBACK_APPLIED",
        target_table_id=target_table_id,
        target_column_name=target_column_name,
        limit=qa_k,
    ):
        et = str(r.get("evidence_type") or "").strip()
        sr = str(r.get("source_ref") or "").strip()
        ch = str(r.get("chunk_hash") or "").strip()
        if (et, sr, ch) in seen:
            continue
        seen.add((et, sr, ch))
        refs.append(
            EvidenceRef(
                source=EvidenceSource.EVIDENCE_HUB,
                evidence_type=et,
                authority_level=str(r.get("authority_level") or "MED"),
                interface_code=r.get("interface_code"),
                target_table_id=r.get("target_table_id"),
                target_column_name=r.get("target_column_name"),
                source_ref=sr or None,
                created_at=r.get("created_at"),
                version=r.get("version"),
                title="BSA Q/A Feedback (Applied)",
                snippet=_format_bsa_qa_feedback_snippet(
                    str(r.get("chunk_text") or ""),
                    authority_level=str(r.get("authority_level") or "MED"),
                    max_chars=max_snip,
                ),
                locator=str(r.get("datapoint_id") or ""),
            )
        )

    # 3) Vector Search neighbors (LOW priority).
    if vec_k > 0 and str(getattr(config, "VECTOR_SEARCH_DEPLOYED_INDEX_ID", "") or "").strip():
        query_text = _vector_query_text(
            target_table_id=target_table_id,
            target_column_name=target_column_name,
            target_logical_name=target_logical_name,
            target_description=target_description,
            target_data_type=target_data_type,
            target_key=target_key,
            forced_rule_type=forced_rule_type,
        )
        if query_text:
            vecs = await embed_texts_gemini_embedding(
                texts=[query_text],
                model=config.EVIDENCE_EMBEDDING_MODEL,
                output_dimensions=int(config.EVIDENCE_EMBEDDING_DIMENSIONS),
                max_concurrency=1,
            )
            query_vec = vecs[0] if vecs else None
            if query_vec:
                neighbors = await find_neighbors(
                    feature_vector=query_vec,
                    neighbor_count=vec_k,
                    restricts=[
                        {"namespace": "evidence_type", "allowList": ["PLAYBOOK", "TRANSCRIPT"]},
                        {"namespace": "is_active", "allowList": ["true"]},
                    ],
                )
                dp_ids = [n.get("datapoint_id") for n in (neighbors or []) if n.get("datapoint_id")]
                bq_rows = fetch_evidence_rows_by_datapoint_ids(datapoint_ids=[str(i) for i in dp_ids])
                by_id = {str(r.get("datapoint_id")): r for r in bq_rows}
                for n in neighbors or []:
                    dp_id = str(n.get("datapoint_id") or "")
                    r = by_id.get(dp_id)
                    if not r:
                        continue
                    et = str(r.get("evidence_type") or "").strip()
                    sr = str(r.get("source_ref") or "").strip()
                    ch = str(r.get("chunk_hash") or "").strip()
                    if (et, sr, ch) in seen:
                        continue
                    seen.add((et, sr, ch))
                    refs.append(
                        EvidenceRef(
                            source=EvidenceSource.EVIDENCE_HUB,
                            evidence_type=et,
                            authority_level=str(r.get("authority_level") or "LOW"),
                            interface_code=r.get("interface_code"),
                            target_table_id=r.get("target_table_id"),
                            target_column_name=r.get("target_column_name"),
                            source_ref=sr or None,
                            created_at=r.get("created_at"),
                            version=r.get("version"),
                            title=f"{et.title()} Evidence",
                            snippet=_format_playbook_or_transcript_snippet(
                                str(r.get("chunk_text") or ""),
                                evidence_type=et,
                                source_ref=sr,
                                max_chars=max_snip,
                            ),
                            locator=dp_id,
                        )
                    )

    return refs


def retrieve_experience_refs_bq(
    *,
    target_table_id: str,
    target_column_name: str,
    table_k: int = 3,
    qa_k: int = 3,
) -> List[EvidenceRef]:
    """
    BigQuery-only retrieval for "experience" evidence (no semantic search).

    Intended use:
      - Provide BSA experience to Step 2 AG1 early (candidate selection / rule decisions),
        without relying on Vector Search or long prompts.

    Returns ordered refs:
      - BSA_TABLE_FEEDBACK (up to table_k)
      - BSA_QA_FEEDBACK_APPLIED (up to qa_k)
    """
    if not config.STEP2_RAG_ENABLED or bool(getattr(config, "STEP2_RAG_DUMMY_RETRIEVAL", False)):
        return []

    ensure_vectorstore_metadata_table_exists()

    max_snip = int(getattr(config, "STEP2_EVIDENCE_MAX_SNIPPET_CHARS", 1200))
    refs: list[EvidenceRef] = []
    seen: set[tuple[str, str, str]] = set()

    for r in fetch_recent_evidence_rows_by_target(
        evidence_type="BSA_TABLE_FEEDBACK",
        target_table_id=target_table_id,
        target_column_name=target_column_name,
        limit=int(table_k),
    ):
        et = str(r.get("evidence_type") or "").strip()
        sr = str(r.get("source_ref") or "").strip()
        ch = str(r.get("chunk_hash") or "").strip()
        if (et, sr, ch) in seen:
            continue
        seen.add((et, sr, ch))
        refs.append(
            EvidenceRef(
                source=EvidenceSource.EVIDENCE_HUB,
                evidence_type=et,
                authority_level=str(r.get("authority_level") or "HIGH"),
                interface_code=r.get("interface_code"),
                target_table_id=r.get("target_table_id"),
                target_column_name=r.get("target_column_name"),
                source_ref=sr or None,
                created_at=r.get("created_at"),
                version=r.get("version"),
                title="BSA Table Feedback",
                snippet=_format_bsa_table_feedback_snippet(
                    str(r.get("chunk_text") or ""),
                    authority_level=str(r.get("authority_level") or "MED"),
                    max_chars=max_snip,
                ),
                locator=str(r.get("datapoint_id") or ""),
            )
        )

    for r in fetch_recent_evidence_rows_by_target(
        evidence_type="BSA_QA_FEEDBACK_APPLIED",
        target_table_id=target_table_id,
        target_column_name=target_column_name,
        limit=int(qa_k),
    ):
        et = str(r.get("evidence_type") or "").strip()
        sr = str(r.get("source_ref") or "").strip()
        ch = str(r.get("chunk_hash") or "").strip()
        if (et, sr, ch) in seen:
            continue
        seen.add((et, sr, ch))
        refs.append(
            EvidenceRef(
                source=EvidenceSource.EVIDENCE_HUB,
                evidence_type=et,
                authority_level=str(r.get("authority_level") or "MED"),
                interface_code=r.get("interface_code"),
                target_table_id=r.get("target_table_id"),
                target_column_name=r.get("target_column_name"),
                source_ref=sr or None,
                created_at=r.get("created_at"),
                version=r.get("version"),
                title="BSA Q/A Feedback (Applied)",
                snippet=_format_bsa_qa_feedback_snippet(
                    str(r.get("chunk_text") or ""),
                    authority_level=str(r.get("authority_level") or "MED"),
                    max_chars=max_snip,
                ),
                locator=str(r.get("datapoint_id") or ""),
            )
        )

    return refs
