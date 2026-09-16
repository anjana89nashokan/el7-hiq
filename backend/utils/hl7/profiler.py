"""Corpus profiling — the HL7 equivalent of STTM's column profiler.

STTM profiles tabular data as columns with null rates and cardinality. The
analogous unit for HL7 is the segment/field position: how often a segment
appears, which fields within it are populated, and how much the structure
varies between trading partners.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field as dc_field


@dataclass
class FieldProfile:
    path: str
    populated_in: int
    total: int
    samples: list[str] = dc_field(default_factory=list)

    @property
    def population_rate(self) -> float:
        return self.populated_in / self.total if self.total else 0.0


@dataclass
class SegmentProfile:
    name: str
    is_z: bool
    occurrences: int
    messages_present: int
    max_fields: int
    fields: list[FieldProfile] = dc_field(default_factory=list)


@dataclass
class CorpusProfile:
    message_count: int
    message_types: Counter
    versions: Counter
    segments: list[SegmentProfile]
    z_segment_names: list[str]
    per_type_structure: dict[str, Counter]


def profile(messages: list) -> CorpusProfile:
    seg_occurrences: Counter = Counter()
    seg_messages: dict[str, set] = defaultdict(set)
    seg_is_z: dict[str, bool] = {}
    field_populated: dict[str, Counter] = defaultdict(Counter)
    field_samples: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    max_fields: Counter = Counter()

    msg_types: Counter = Counter()
    versions: Counter = Counter()
    per_type_structure: dict[str, Counter] = defaultdict(Counter)

    for msg in messages:
        mtype = msg.message_type
        msg_types[mtype] += 1
        versions[msg.version] += 1
        per_type_structure[mtype][" ".join(msg.segment_names())] += 1

        for seg in msg.segments:
            seg_occurrences[seg.name] += 1
            seg_messages[seg.name].add(msg.source_file)
            seg_is_z[seg.name] = seg.is_z
            count = seg.field_count()
            max_fields[seg.name] = max(max_fields[seg.name], count)
            for pos in range(1, count + 1):
                raw = seg.fields[pos - 1].strip() if pos - 1 < len(seg.fields) else ""
                if raw:
                    field_populated[seg.name][pos] += 1
                    samples = field_samples[seg.name][pos]
                    if raw not in samples and len(samples) < 4:
                        samples.append(raw)

    profiles: list[SegmentProfile] = []
    for name in sorted(seg_occurrences, key=lambda n: (seg_is_z[n], n)):
        total = seg_occurrences[name]
        fields = [
            FieldProfile(
                path=f"{name}-{pos}",
                populated_in=field_populated[name][pos],
                total=total,
                samples=field_samples[name][pos],
            )
            for pos in range(1, max_fields[name] + 1)
            if field_populated[name][pos]
        ]
        profiles.append(SegmentProfile(
            name=name,
            is_z=seg_is_z[name],
            occurrences=total,
            messages_present=len(seg_messages[name]),
            max_fields=max_fields[name],
            fields=fields,
        ))

    return CorpusProfile(
        message_count=len(messages),
        message_types=msg_types,
        versions=versions,
        segments=profiles,
        z_segment_names=sorted(n for n, z in seg_is_z.items() if z),
        per_type_structure=dict(per_type_structure),
    )
