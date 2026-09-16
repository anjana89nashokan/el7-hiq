"""Run the HL7 spike across a sample corpus.

Run from the ``datamap_backend`` directory:

    python -m utils.hl7.run_spike [--samples DIR] [--out DIR]

Prints a report and writes JSON artifacts: the corpus profile, the Z-segment
inference results with reasoning, and the FHIR output for every message.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from . import parser as hl7_parser
from . import profiler as corpus_profiler
from . import zsegment_inference
from .fhir_mapper import map_message

HERE = Path(__file__).parent
# utils/hl7 -> utils -> datamap_backend -> STTM -> workspace root
WORKSPACE_ROOT = HERE.parents[3]
DEFAULT_SAMPLES = WORKSPACE_ROOT / "hl7-samples"
# datamap_backend/data/ is already gitignored, so generated artifacts stay out
# of the repo's working tree.
DEFAULT_OUT = HERE.parents[1] / "data" / "hl7_spike"

RULE = "=" * 78
THIN = "-" * 78


def bar(value: float, width: int = 20) -> str:
    filled = round(value * width)
    return "#" * filled + "." * (width - filled)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=str(DEFAULT_SAMPLES))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    sample_dir = Path(args.samples)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(sample_dir.glob("*.hl7"))
    if not files:
        raise SystemExit(f"no .hl7 files found in {sample_dir}")

    # ---------------------------------------------------------------- parse
    print(RULE)
    print("1. PARSE")
    print(RULE)
    messages = []
    for f in files:
        try:
            msg = hl7_parser.parse_file(f)
        except Exception as exc:  # noqa: BLE001 - spike surfaces all failures
            print(f"  FAIL  {f.name}: {exc}")
            continue
        messages.append(msg)
        zs = ",".join(s.name for s in msg.z_segments()) or "-"
        print(f"  ok    {f.name:38s} {msg.message_type:9s} "
              f"v{msg.version}  segs={len(msg.segments):2d}  Z={zs}")
    print(f"\n  {len(messages)}/{len(files)} messages parsed")

    # -------------------------------------------------------------- profile
    prof = corpus_profiler.profile(messages)
    print()
    print(RULE)
    print("2. CORPUS PROFILE")
    print(RULE)
    print(f"  messages       : {prof.message_count}")
    print(f"  message types  : {dict(prof.message_types)}")
    print(f"  versions       : {dict(prof.versions)}")
    print(f"  Z-segments     : {prof.z_segment_names}")
    print()
    print(f"  {'segment':9s} {'kind':9s} {'occ':>4s} {'msgs':>5s} {'fields':>7s}")
    print(f"  {THIN[:44]}")
    for s in prof.segments:
        kind = "SITE-DEF" if s.is_z else "standard"
        print(f"  {s.name:9s} {kind:9s} {s.occurrences:4d} "
              f"{s.messages_present:5d} {s.max_fields:7d}")

    print("\n  Structural variants per message type:")
    for mtype, variants in prof.per_type_structure.items():
        print(f"    {mtype}:")
        for structure, count in variants.most_common():
            print(f"      [{count}x] {structure}")

    # ------------------------------------------------------------ inference
    inferences = zsegment_inference.infer_corpus(messages)
    print()
    print(RULE)
    print("3. Z-SEGMENT INFERENCE")
    print(RULE)
    print("  semantic = confidence in what the field MEANS")
    print("  stability = how consistently it APPEARS across occurrences\n")

    counts: dict[str, int] = {}
    for seg_name, results in inferences.items():
        occ = results[0].total_occurrences if results else 0
        print(THIN)
        print(f"  {seg_name}  ({occ} occurrence(s) in corpus)")
        print(THIN)
        for inf in results:
            counts[inf.routing] = counts.get(inf.routing, 0) + 1
            print(f"\n  {inf.path}  ->  {inf.inferred_meaning}")
            print(f"    label      : {inf.label or '(none)'}")
            print(f"    datatype   : {inf.datatype}")
            print(f"    values     : {inf.observed_values}")
            print(f"    semantic   : {bar(inf.semantic_confidence)} {inf.semantic_confidence:.2f}")
            print(f"    stability  : {bar(inf.stability)} {inf.stability:.2f} "
                  f"({inf.present_in}/{inf.total_occurrences})")
            print(f"    routing    : {inf.routing}")
            print(f"    FHIR       : {inf.fhir_target}")
            print("    reasoning  :")
            for r in inf.reasoning:
                print(f"       - {r}")
        print()

    print(RULE)
    print("  ROUTING SUMMARY")
    print(RULE)
    total = sum(counts.values())
    for routing in ["AUTO_MAP", "AUTO_MAP_OPTIONAL", "REVIEW", "HUMAN_REQUIRED"]:
        n = counts.get(routing, 0)
        pct = (n / total * 100) if total else 0
        print(f"  {routing:20s} {n:3d}  ({pct:4.0f}%)  {bar(n / total if total else 0)}")
    auto = counts.get("AUTO_MAP", 0) + counts.get("AUTO_MAP_OPTIONAL", 0)
    print(f"\n  {auto}/{total} Z-segment fields resolved without human input "
          f"({auto / total * 100:.0f}%)" if total else "")

    # --------------------------------------------------- sensitivity to volume
    # Several fields score 0.80 only because the corpus contains a single
    # occurrence of their segment, which triggers the thin-evidence penalty.
    # Replaying the corpus removes that penalty without inventing new variety,
    # which separates "genuinely ambiguous" from "under-observed".
    print(RULE)
    print("3b. SENSITIVITY TO CORPUS SIZE")
    print(RULE)
    print("  Replaying the same messages removes the small-sample penalty but adds")
    print("  no new variety. Fields that move are under-observed, not ambiguous;")
    print("  fields that stay put are genuinely uncertain.\n")

    replayed = zsegment_inference.infer_corpus(messages * 5)
    moved, stuck = [], []
    baseline = {i.path: i for results in inferences.values() for i in results}
    for results in replayed.values():
        for inf in results:
            before = baseline[inf.path]
            if inf.routing != before.routing:
                moved.append((inf.path, before, inf))
            elif before.routing in ("REVIEW", "HUMAN_REQUIRED"):
                stuck.append((inf.path, before))

    print(f"  Reclassified with more evidence ({len(moved)}):")
    for path, before, after in moved:
        print(f"    {path:8s} {before.routing:18s} -> {after.routing:18s} "
              f"(semantic {before.semantic_confidence:.2f} -> {after.semantic_confidence:.2f})")
    print(f"\n  Still requiring human judgement ({len(stuck)}):")
    for path, before in stuck:
        reason = "no inline label" if before.label is None else "cardinality varies"
        print(f"    {path:8s} {before.routing:18s} {reason}")

    # ---------------------------------------------------------------- FHIR
    print()
    print(RULE)
    print("4. FHIR TRANSFORM")
    print(RULE)
    fhir_dir = out_dir / "fhir"
    fhir_dir.mkdir(exist_ok=True)
    ok = 0
    for msg in messages:
        try:
            result = map_message(msg, inferences)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL  {msg.source_file}: {exc}")
            continue
        ok += 1
        target = fhir_dir / (Path(msg.source_file).stem + ".fhir.json")
        target.write_text(json.dumps(result, indent=2), encoding="utf-8")
        if "observations" in result:
            summary = f"Patient + {len(result['observations'])} Observation"
        else:
            body = result["documentReference"]["content"][0]["attachment"]["data"]
            summary = f"Patient + DocumentReference ({len(body.splitlines())} text lines)"
        print(f"  ok    {msg.source_file:38s} -> {summary}")
    print(f"\n  {ok}/{len(messages)} messages transformed to FHIR")

    # ------------------------------------------------------------ artifacts
    (out_dir / "corpus_profile.json").write_text(
        json.dumps({
            "message_count": prof.message_count,
            "message_types": dict(prof.message_types),
            "versions": dict(prof.versions),
            "z_segment_names": prof.z_segment_names,
            "per_type_structure": {k: dict(v) for k, v in prof.per_type_structure.items()},
            "segments": [asdict(s) for s in prof.segments],
        }, indent=2), encoding="utf-8")

    (out_dir / "zsegment_inference.json").write_text(
        json.dumps({k: [asdict(i) for i in v] for k, v in inferences.items()}, indent=2),
        encoding="utf-8")

    print(f"\n  artifacts written to {out_dir}")


if __name__ == "__main__":
    main()
