"""X12 EDI parser for healthcare transactions (270/271, 835, 837, …).

Supports delimited interchange files (``.edi``) such as HIPAA 005010X279
eligibility (270/271). Element and segment delimiters are read from the ISA
segment per X12.601.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

PATH_RE = re.compile(r"^([A-Z0-9]{2,3})-(\d+)(?:\.(\d+))?(?:\.(\d+))?$")

# Segments that are envelope or structural counters, not clinical payload.
STRUCTURAL_SEGMENTS = frozenset({"ISA", "IEA", "GS", "GE", "ST", "SE"})


@dataclass
class Delimiters:
    element: str = "*"
    component: str = ":"
    repetition: str = "^"
    segment: str = "~"

    @classmethod
    def from_isa(cls, isa_line: str) -> "Delimiters":
        if not isa_line.startswith("ISA"):
            return cls()
        elem = isa_line[3] if len(isa_line) > 3 else "*"
        repetition = isa_line[82] if len(isa_line) > 82 else "^"
        component = isa_line[104] if len(isa_line) > 104 else ":"
        segment = isa_line[105] if len(isa_line) > 105 else "~"
        return cls(element=elem, component=component, repetition=repetition, segment=segment)


@dataclass
class Segment:
    name: str
    fields: list[str]
    index: int
    is_z: bool = False

    def field_count(self) -> int:
        for i in range(len(self.fields), 0, -1):
            if self.fields[i - 1].strip():
                return i
        return 0


@dataclass
class InterchangeMessage:
    """X12 functional group / transaction set shaped like HL7 ``Message`` for profiling."""

    segments: list[Segment]
    delimiters: Delimiters
    source_file: str = ""
    _warnings: list[str] = field(default_factory=list)

    @property
    def message_type(self) -> str:
        """ST01 and implementation guide, e.g. ``270^005010X279A1``."""
        st = self.first("ST")
        if st is None or not st.fields:
            return "UNKNOWN"
        txn = (st.fields[0] or "").strip()
        guide = (st.fields[2] if len(st.fields) > 2 else "").strip()
        return f"{txn}^{guide}" if guide else txn

    @property
    def transaction_set(self) -> str:
        return (self.message_type or "").split("^")[0].strip()

    @property
    def implementation_guide(self) -> str:
        parts = (self.message_type or "").split("^", 1)
        return parts[1].strip() if len(parts) > 1 else ""

    @property
    def control_id(self) -> str:
        return self.get("ST-2") or self.get("ISA-13")

    @property
    def version(self) -> str:
        return self.get("ISA-12") or self.get("GS-8")

    def by_name(self, name: str) -> list[Segment]:
        return [s for s in self.segments if s.name == name]

    def first(self, name: str) -> Segment | None:
        segs = self.by_name(name)
        return segs[0] if segs else None

    def z_segments(self) -> list[Segment]:
        return []

    def segment_names(self) -> list[str]:
        return [s.name for s in self.segments]

    def get(self, path: str, segment: Segment | None = None) -> str:
        m = PATH_RE.match(path)
        if not m:
            raise ValueError(f"malformed EDI path: {path!r}")
        seg_name, fnum, cnum, _snum = m.group(1), int(m.group(2)), m.group(3), m.group(4)

        seg = segment if segment is not None else self.first(seg_name)
        if seg is None:
            return ""

        idx = fnum - 1
        if idx < 0 or idx >= len(seg.fields):
            return ""
        value = seg.fields[idx]
        value = value.split(self.delimiters.repetition)[0]
        if cnum:
            parts = value.split(self.delimiters.component)
            value = parts[int(cnum) - 1] if int(cnum) - 1 < len(parts) else ""
        return value.strip()


def _split_segments(text: str, term: str) -> list[str]:
    parts = text.split(term)
    return [p.strip() for p in parts if p.strip()]


def parse(text: str, source_file: str = "") -> InterchangeMessage:
    text = text.strip().lstrip("\ufeff")
    if not text:
        raise ValueError("empty interchange")

    # Single-line files use ~; some drops also break on newlines between segments.
    if text.startswith("ISA") and "~" in text[:120]:
        delims = Delimiters.from_isa(text.split("~", 1)[0] + "~")
        normalized = text.replace("\r\n", "").replace("\n", "").replace("\r", "")
        raw_segments = _split_segments(normalized, delims.segment)
    else:
        lines = [ln.strip() for ln in re.split(r"\r\n|\r|\n", text) if ln.strip()]
        if not lines or not lines[0].startswith("ISA"):
            raise ValueError("interchange does not begin with ISA")
        delims = Delimiters.from_isa(lines[0])
        raw_segments = []
        for line in lines:
            raw_segments.extend(_split_segments(line.replace("\n", ""), delims.segment))

    if not raw_segments or raw_segments[0][:3] != "ISA":
        raise ValueError("no ISA segment found")

    segments: list[Segment] = []
    warnings: list[str] = []
    for i, raw in enumerate(raw_segments):
        parts = raw.split(delims.element)
        name = parts[0].strip()
        if not name:
            continue
        segments.append(
            Segment(
                name=name,
                fields=parts[1:],
                index=i,
                is_z=False,
            )
        )

    if not any(s.name == "ST" for s in segments):
        warnings.append("no ST (transaction set) segment found")

    msg = InterchangeMessage(segments=segments, delimiters=delims, source_file=source_file)
    msg._warnings = warnings
    return msg


def parse_file(path: str | Path) -> InterchangeMessage:
    p = Path(path)
    raw = p.read_bytes()
    for encoding in ("utf-8", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    return parse(text, source_file=p.name)
