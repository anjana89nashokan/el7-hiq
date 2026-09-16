# HL7 v2 support

The message-shaped ingestion path, implementing the Phase-1 design-time workflow
of the Vituity BRD: parse HL7 v2.5.1, profile the corpus, infer the meaning of
site-defined Z-segments with an auditable confidence score, propose canonical
mappings, take those through human review, and publish an immutable versioned
mapping package.

The **canonical model is the target**, per BRD design principle DP-02
("canonicalize once"). FHIR is a projection of it, not the destination — which
is why `fhir_mapper.py` sits alongside the canonical modules rather than beneath
them, and why `FR-025`/`FR-028` make canonical JSON the required output with
FHIR optional.

> **Status: wired into the upload pipeline.** `.hl7` files are detected at
> upload and routed to `/hl7/upload`, bypassing `read_file_to_dataframe()` and
> the tabular table load entirely, because an HL7 message is a tree rather than
> a row. The reviewer-facing pages are `/hl7/:id` (profile) and
> `/hl7/:id/review` (mapping workbench).

## Governance rules enforced

These are checks in code, not conventions:

| Rule | Where |
| --- | --- |
| `RULE-001` standard exact matches may auto-populate but stay visible | `mapping_package.bulk_approve_auto` |
| `RULE-002` Z-segments are never auto-promoted | `mapping_engine` sets `auto_approvable=False` for every custom field |
| `RULE-005` a published package is immutable | `mapping_package.publish` refuses to overwrite `vN.json` |
| `RULE-007` / `FR-021` required unresolved mappings block publication | `mapping_package.readiness` |
| `FR-016` ambiguity is reported, not invented | confidence below 0.50 yields no target |
| `FR-020` rejections and overrides need a reason | `mapping_package.apply_review` |
| `DQ-03` / `FR-007` nothing is silently dropped | unmatched standard fields become unresolved mappings |

## Running it

Pure standard library, no dependencies beyond Python itself. From
`datamap_backend/`:

```bash
python -m utils.hl7.run_spike
```

The sample corpus defaults to `hl7-samples/` at the workspace root; override with
`--samples DIR`. Artifacts are written to `datamap_backend/data/hl7_spike/`
(gitignored): `corpus_profile.json`, `zsegment_inference.json`, and one FHIR
document per message under `fhir/`.

## Modules

| File | Role |
| --- | --- |
| `parser.py` | Segment/field/component parsing, delimiters read from MSH, Z-segment detection, MLLP de-framing |
| `profiler.py` | Segment frequency and field population rates — the HL7 analogue of DataMap's column profiler |
| `zsegment_inference.py` | Confidence scoring and reasoning for site-defined fields |
| `canonical_model.py` | The governed target model: entities, attributes, cardinality and terminology bindings |
| `mapping_engine.py` | Proposes canonical targets with confidence, rationale and transformation (FR-012–FR-016) |
| `mapping_package.py` | Review decisions, audit trail, publication gate and immutable versions (FR-019–FR-024) |
| `fhir_mapper.py` | Optional FHIR projection: ORU→Observation, MDM→DocumentReference |
| `run_spike.py` | Standalone runner and report over the sample corpus |

## How confidence is scored

The engine scores two things separately, because they answer different
questions and carry different consequences.

**Semantic confidence** — how sure are we what the field *means*? This decides
whether a human must confirm the mapping.

| Signal | Weight |
| --- | --- |
| Field is self-describing (`LABEL^VALUE`) | +0.40 |
| Label tokens resolve against a healthcare lexicon | up to +0.30, scaled by token coverage |
| Value shape is determinate (boolean, integer, code, identifier, datetime) | +0.20 |
| Value set is small and closed | +0.10 |
| Values corroborate the label (e.g. valid DICOM modality codes) | +0.10 |
| Label differs between occurrences of the same position | −0.15 |
| Segment appears only once in the corpus | −0.10 |

**Stability** — how consistently does the field *appear* across occurrences of
its segment? This decides whether the field can be modelled as required.
It is simply `occurrences populated / total occurrences`.

The two are independent, and keeping them apart is the point. A field can be
perfectly understood and still unstable, which is exactly the case that breaks
the customer's current pipeline: a mapping that treats an optional field as
required rejects the messages that omit it.

Routing follows from the pair:

| Semantic | Stability | Routing |
| --- | --- | --- |
| ≥ 0.85 | 1.0 | `AUTO_MAP` |
| ≥ 0.85 | < 1.0 | `AUTO_MAP_OPTIONAL` — map, but mark the element optional |
| 0.50 – 0.85 | any | `REVIEW` |
| < 0.50 | any | `HUMAN_REQUIRED` |

Every score carries the list of reasons that produced it, so a reviewer audits
the judgement rather than trusting a number.

## Results

All nine messages parsed and transformed. Four Z-segments were found across the
corpus (`ZLB`, `ZPD`, `ZDR`, `ZDT`) covering 21 field positions.

Measured on the nine samples as given:

| Routing | Fields | Share |
| --- | --- | --- |
| `AUTO_MAP` | 8 | 38% |
| `AUTO_MAP_OPTIONAL` | 1 | 5% |
| `REVIEW` | 11 | 52% |
| `HUMAN_REQUIRED` | 1 | 5% |

The 52% review rate is misleading, and the sensitivity check in the runner shows
why. Eleven of those fields score 0.80 solely because their segment appears once
in a nine-message corpus, which triggers the thin-evidence penalty. Replaying the
corpus removes that penalty without adding any new variety, and all eleven move
to `AUTO_MAP`. They were under-observed, not ambiguous.

What survives more evidence is the real finding:

- **`ZLB-2` = `GTMLK`** — semantic confidence 0.30, `HUMAN_REQUIRED`. A bare
  token with no inline label. Nothing in the message declares what it means, and
  no amount of additional volume will change that. This is the irreducible case
  that needs a human or a site data dictionary.
- **`ZLB-6` = `SPECIMEN_COND^ACCEPTABLE`** — semantic confidence 0.90 but
  stability 0.50. Present in one of the two `ZLB` occurrences. The meaning is
  clear; the cardinality is not. Mapped automatically, marked optional.

So of 21 site-defined field positions, **one** genuinely requires human
judgement. The rest are resolvable deterministically, given enough messages.

## Why this works, and where it stops working

The sample Z-segments are self-describing: they carry `LABEL^VALUE` pairs, so
the message declares its own semantics. That is a real and common convention,
but it is not guaranteed. A site that emits positional Z-segments — bare values
with meaning implied by position, as `ZLB-2` already does — collapses this
approach to the `GTMLK` case for every field.

That is the boundary where a language model earns its place: proposing meaning
from surrounding clinical context when the data does not describe itself, and
drafting the data-dictionary questions a human should answer. The deterministic
engine should run first regardless, because it is free, reproducible, and
resolves the majority of positions on its own.

## Scope

The spike deliberately omits production concerns: HL7 table validation against
v2.5.1 code sets, LOINC and UCUM terminology validation, repetition and escape
sequence handling beyond first-repetition access, acknowledgement generation,
and MLLP transport. These are noted in the assessment as dependency and effort
items rather than solved here.
