# Quality Judgment & KPIs

This document explains how the `server/quality/` package judges each extraction
layer's output and how it derives the four quality KPIs.

It is independent from the legacy `server/judges/` package — none of that code is
used.

---

## Endpoints

All endpoints live under the `/quality` prefix and return the same
`LayerJudgmentResponse` shape (see [schemas.py](schemas.py)).

| Method | Path                          | Layer        | Source artifacts                                       | Produced output under judgment                  |
|--------|-------------------------------|--------------|--------------------------------------------------------|--------------------------------------------------|
| POST   | `/quality/requirements/judge` | requirements | BRD JSON, layout JSON, optional transcript + markdowns | `requirement_layer` + `file_layout_tables`       |
| POST   | `/quality/metadata/judge`     | metadata     | BRD requirement_layer, layout                          | `extracted_metadata` (`extracted_filespecs` + `extracted_file1`) |
| POST   | `/quality/mapping/judge`      | mapping      | BRD requirement_layer, driver layer, metadata          | `mapping_result` (inline or via `mapping_uri`)   |
| POST   | `/quality/driver/judge`       | driver       | BRD requirement_layer                                  | `driver_mapping` + `driver_logic` + `driver_validation` |

Request payload field names mirror the existing judge endpoints in
[`server/api/routers/extracts.py`](../api/routers/extracts.py) so callers do not
have to change.

---

## How a judgment runs

Every endpoint follows the same six steps (shared orchestration lives in
[`judges/base.py`](judges/base.py)).

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. Load source artifacts                                         │
│    download_json_uri() pulls BRD / layout / driver / metadata    │
│    from GCS as needed.                                           │
├──────────────────────────────────────────────────────────────────┤
│ 2. Enumerate REQUIRED items                                      │
│    Layer-specific: every concept the layer was supposed to       │
│    address.  This is the denominator for Completeness.           │
├──────────────────────────────────────────────────────────────────┤
│ 3. Enumerate PRODUCED items                                      │
│    Layer-specific: every concrete entry actually emitted by      │
│    the layer.  This is the denominator for Hallucination and    │
│    Groundedness.                                                 │
├──────────────────────────────────────────────────────────────────┤
│ 4. Call Gemini (temp=0, JSON mode)                               │
│    The system instruction tells the LLM to emit one boolean      │
│    judgment per enumerated item, plus a short qualitative        │
│    summary.  The LLM is explicitly forbidden from computing      │
│    KPI scores.                                                   │
├──────────────────────────────────────────────────────────────────┤
│ 5. Aggregate KPIs deterministically                              │
│    kpi.compute() turns the per-item booleans into the four       │
│    KPI scores using plain Python arithmetic.  No LLM involved.   │
├──────────────────────────────────────────────────────────────────┤
│ 6. Persist + return                                              │
│    The merged artifact is uploaded to                            │
│    gs://…/quality/{layer}/rev_{n}_{ts}.json and the response     │
│    is returned inline.                                           │
└──────────────────────────────────────────────────────────────────┘
```

### Why split the LLM from the KPI math?

The LLM is the *judge* — it decides "is this item present?", "is this claim
supported by the source?", "does this entry follow the spec?".

The KPI math is the *aggregator* — it counts the booleans the LLM emitted and
produces ratios.

This separation matters because:

- **Reproducibility.** Re-running `kpi.compute()` on the persisted artifact will
  always yield the same KPI numbers.  An LLM asked to compute a ratio will not.
- **Auditability.** Every KPI score traces back to a specific list of per-item
  judgments you can read and challenge.
- **Stability across models.** Swap Gemini for a different LLM and the KPI
  *definitions* stay identical.

### Per-item judgment schema

The LLM returns one of these per enumerated item:

```jsonc
{
  "item_id": "driver_logic.common_filter.3.contract_status",
  "item_type": "required" | "produced",

  "present_in_output":    true | false | null, // required-only
  "supported_by_source":  true | false | null, // produced-only
  "contradicts_source":   true | false | null, // produced-only
  "follows_instructions": true | false,        // both

  "evidence_quote": "page 4: 'Contract status must be ACTIVE'",
  "rationale":      "Filter exists in driver_logic.common_filters with operator '=' and value 'ACTIVE'."
}
```

`null` means *not applicable for this item type* (you don't ask "is a required
concept grounded?" — grounding is only meaningful for things the pipeline
actually emitted).

---

## The four KPIs

All four are computed in [`kpi.py`](kpi.py).  Each returns
`{score, numerator, denominator, definition}` so the formula travels with the
number.

### 1. Completeness

**What it represents.** The share of things the layer *should have produced*
that it *did* produce.  Answers "did the layer cover everything the BRD asked
for?".

```
                count(required items where present_in_output == true)
completeness = ────────────────────────────────────────────────────
                              count(required items)
```

- Domain: `[0, 1]` (higher is better).
- A `0` means the layer addressed none of the required concepts.
- A `1` means every required concept appears somewhere in the produced output.

### 2. Hallucination rate

**What it represents.** The share of *produced* items that contradict the source
or cannot be backed by it.  Answers "how often does the layer invent content?".

```
                     count(produced items where
                       contradicts_source == true
                       OR supported_by_source == false)
hallucination = ────────────────────────────────────────
                       count(produced items)
```

- Domain: `[0, 1]` (lower is better — this is the only KPI where lower wins).
- A `0` means every produced item is grounded in (or at least not contradicted
  by) the source.
- A `1` means none of the produced items are supported.

### 3. Groundedness / Faithfulness

**What it represents.** The share of *produced* items the LLM could trace to a
source quote *and* that don't contradict the source.  Answers "how faithful is
the output to its sources?".

```
                   count(produced items where
                     supported_by_source == true
                     AND contradicts_source != true)
groundedness = ────────────────────────────────────
                       count(produced items)
```

- Domain: `[0, 1]` (higher is better).
- Note that Groundedness is **not** exactly `1 − Hallucination`.  A produced
  item where the LLM could not decide (`supported_by_source == null`) lowers
  groundedness without raising hallucination.  This is intentional — uncertain
  items reduce confidence without being called out as hallucinations.

### 4. Instruction adherence

**What it represents.** The share of *all* judged items (required + produced)
that obey the layer-specific rules — naming, types, required template fields,
format.  Answers "did the layer follow its own spec?".

```
                       count(all items where follows_instructions == true)
instruction_adherence = ─────────────────────────────────────────────────
                                    count(all items)
```

- Domain: `[0, 1]` (higher is better).
- Applies to both item types, because instruction-following is meaningful for
  required items too (e.g. "if you produce this requirement, was it shaped
  correctly?").

---

## Per-layer required vs. produced

Each judge module enumerates its own items.  This table summarises the rules
used; see the individual judge files for exact code.

### Requirements layer — [`requirements_judge.py`](judges/requirements_judge.py)

| | Items |
|---|---|
| **Required** | Seven fixed section anchors: `scope`, `requirements`, `business_rules`, `filters_and_parameters`, `generic_tables`, `file_specs`, `target_tables` |
| **Produced** | Every key in the produced `requirement_layer` dict + every layout table + every column under every table |
| **Source artifacts** | `brd_gcs_uri`, `layout_gcs_uri`, optional `transcript_gcs_uri`, optional markdown variants |

### Metadata layer — [`metadata_judge.py`](judges/metadata_judge.py)

| | Items |
|---|---|
| **Required** | Every filespec-level field in the BRD (frequency, delimiter, encoding, …) + every column listed in every layout table |
| **Produced** | Every key in `extracted_filespecs` + every header field in each `extracted_file1` record + every attribute in each file's `attributes` list |
| **Source artifacts** | `brd_uri` (validated_requirement_layer JSON), `layout_uri` |

### Mapping layer — [`mapping_judge.py`](judges/mapping_judge.py)

| | Items |
|---|---|
| **Required** | Every target attribute the metadata declares + every BRD requirement |
| **Produced** | Every mapping row + every transformation + every business rule in `mapping_result` |
| **Source artifacts** | `brd_uri`, `driver_uri`, `metadata_uri` |

The mapping judge accepts the output either inline (`mapping_result`) or as a
GCS URI (`mapping_uri`).

### Driver layer — [`driver_judge.py`](judges/driver_judge.py)

| | Items |
|---|---|
| **Required** | Every `filters_and_parameters` entry, every `requirements` entry, and every `generic_tables` entry in the BRD requirement_layer |
| **Produced** | Every `filter_candidate` + every `unmapped_concept` in `driver_mapping` + every `common_filter` + the `sql_where_clause` from `driver_logic` + every validation issue + the `can_proceed` and `standards_compliant` decisions from `driver_validation` |
| **Source artifacts** | `brd_uri` |

---

## Response shape

Every endpoint returns the same shape — see
[`schemas.LayerJudgmentResponse`](schemas.py):

```jsonc
{
  "success": true,
  "session_id": "s1",
  "layer": "driver",
  "revision_number": 1,
  "judged_at": "2026-05-15T12:34:56.789+00:00",

  "kpis": {
    "completeness": {
      "score": 0.83,
      "numerator": 5,
      "denominator": 6,
      "definition": "Completeness = required items present in the produced output / total required items. …"
    },
    "hallucination_rate":    { "score": 0.0,  "numerator": 0, "denominator": 12, "definition": "…" },
    "groundedness":          { "score": 0.92, "numerator": 11, "denominator": 12, "definition": "…" },
    "instruction_adherence": { "score": 0.94, "numerator": 17, "denominator": 18, "definition": "…" }
  },

  "llm_judgment": {
    "verdict": "pass",
    "summary": "…",
    "findings": ["…"],
    "per_item_judgments": [ /* one entry per enumerated item */ ]
  },

  "artifact_gcs_uri": "gs://bsa-data-map-artifacts/bsa-extract-artifacts/s1/quality/driver/rev_1_20260515T123456Z.json"
}
```

`kpis[*].definition` is shipped on every response so dashboards never have to
infer what a number means.

---

## Persistence

Each call writes a JSON artifact to:

```
gs://{MAPPING_ARTIFACT_BUCKET}/{BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}/quality/{layer}/rev_{revision_number}_{UTC-timestamp}.json
```

The persisted artifact contains:

- session metadata (session_id, user_id, layer, revision_number, judged_at)
- a `source` block with all relevant URIs and the required/produced item counts
- the full `llm_judgment` (verdict, summary, findings, per-item judgments)
- the four `kpis` with their scores and definitions

Because the per-item judgments are persisted alongside the aggregated KPIs, the
KPI math is fully reproducible from the artifact alone.

---

## Verifying a judgment

A few spot-checks that the system is behaving as expected:

1. **Bounds.**  For every KPI in the response, `0 ≤ score ≤ 1` and
   `denominator > 0`.  If a denominator is `0`, the layer's enumeration produced
   no items of that type — investigate the layer output before trusting the
   score.

2. **Reproducibility.**  Download `artifact_gcs_uri`, re-run
   `kpi.compute(llm_judgment.per_item_judgments)` locally, and confirm every
   `(numerator, denominator, score)` reproduces exactly.  This proves the KPIs
   are deterministic and not LLM-emitted.

3. **Negative-perturbation sanity.**  Drop a known-required field from the
   layer's output and re-run the judge; Completeness should fall by roughly
   `1/denominator`.

4. **Hallucination sanity.**  Inject a fake field into the layer's output that
   does not appear in any source artifact; Hallucination should rise and
   Groundedness should fall on the next run.

---

## Module map

```
server/quality/
├── __init__.py
├── judge_docs.md              ← this file
├── llm_client.py              Vertex Gemini async client (fresh, no judges-pkg imports)
├── schemas.py                 Request + response Pydantic models
├── kpi.py                     Deterministic KPI aggregator (the formulas live here)
├── persistence.py             GCS writer
├── prompts/
│   ├── requirements.py        System + user prompt for the requirements layer
│   ├── metadata.py            System + user prompt for the metadata layer
│   ├── mapping.py             System + user prompt for the mapping layer
│   └── driver.py              System + user prompt for the driver layer
└── judges/
    ├── base.py                Shared orchestration (LLM call → KPI math → persist)
    ├── requirements_judge.py  Required/produced enumeration for requirements
    ├── metadata_judge.py      Required/produced enumeration for metadata
    ├── mapping_judge.py       Required/produced enumeration for mapping
    └── driver_judge.py        Required/produced enumeration for driver
```

The router that exposes the four endpoints lives at
[`server/api/routers/quality.py`](../api/routers/quality.py) and is registered
in [`server/api/main.py`](../api/main.py) with the `/quality` prefix.
