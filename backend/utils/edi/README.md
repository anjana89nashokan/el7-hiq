# X12 EDI (HIPAA) support

Upload **`.edi`** or healthcare **`.dat`** files (X12 sniff: ISA/GS/ST).

**837 claims** use a companion-guide **decode table** (segment order preserved) — not HL7-style
profiling/mapping. Other guides (270/271/835) still use canonical mapping review.

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

- `POST /edi/upload` — parse; 837 → `view_mode: 837_decode` + `edi_decoded`
- `GET /edi/sessions/{id}/decoded` — 837 segment tables for a saved session
- `GET /edi/canonical-model` — governed target entities (non-837 review UI)

Sessions are stored in `hl7_sessions` with `format: "x12"` and reuse
`/hl7/sessions/{id}/mappings` for review.

## Modules

| File | Role |
| --- | --- |
| `parser.py` | ISA-delimited X12 parsing into segment/element paths (`NM1-9`, …) |
| `canonical_model.py` | Eligibility / envelope canonical entities |
| `decode_837.py` | 837 segment/element decode (companion guide) |
| `guides/` | Element names and code sets for 837 |
| `mapping_engine.py` | Deterministic X12 → canonical rules (non-837) |
