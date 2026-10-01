# X12 EDI (HIPAA) support

Upload **`.edi`** interchange files for profiling and canonical mapping, using the
same review and publish workflow as HL7 v2.

## Supported transactions (initial)

| Guide / family | ST01 | Notes |
| --- | --- | --- |
| 005010X279 | 270, 271 | Eligibility inquiry/response (sample corpus at repo root) |
| 835 / 837 | 835, 837 | Envelope + shared segments mapped; extend rules in `mapping_engine.py` |

Companion references (repo root):

- `835_compguide.pdf`
- `837-health-care-claim-companion-guide 1.pdf`

Example 270/271 files: `X279-*.edi`

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
