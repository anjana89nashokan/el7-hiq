# X12 EDI (HIPAA) support

Upload **`.edi`** or healthcare **`.dat`** files (X12 sniff: ISA/GS/ST) for profiling
and canonical mapping, using the same review workflow as HL7 v2.

## Supported transactions

| Guide / family | ST01 | Notes |
| --- | --- | --- |
| 005010X279 | 270, 271 | Eligibility (`X279-*.edi` at repo root) |
| 005010X221 | 835 | Remittance — `edi_samples/835/*.dat` |
| 005010X222/X223/X224 | 837 | Claims — `edi_samples/837/*.dat` |

Companion references: `835_compguide.pdf`, `837-health-care-claim-companion-guide 1.pdf`

Sample corpus: `edi_samples/` (`.dat` and `.edi`). Fixed-width tabular `.dat` files
are **not** X12 — use the normal upload path.

## API

- `POST /edi/upload` — parse corpus, profile, propose mappings
- `GET /edi/canonical-model` — governed target entities for review UI

Sessions are stored in `hl7_sessions` with `format: "x12"` and reuse
`/hl7/sessions/{id}/mappings` for review.

## Modules

| File | Role |
| --- | --- |
| `parser.py` | ISA-delimited X12 parsing into segment/element paths (`NM1-9`, …) |
| `canonical_model.py` | Eligibility / envelope canonical entities |
| `mapping_engine.py` | Deterministic X12 → canonical rules |
