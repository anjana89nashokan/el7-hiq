"""X12 EDI parser for healthcare transactions (270/271, 835, 837, …).

Supports ``.edi`` and ``.dat`` interchanges (HIPAA 005010). Delimiters are read
from the ISA segment when present; transaction-only excerpts (companion guide
samples) use X12 defaults (* : ^ ~).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

PATH_RE = re.compile(r"^([A-Z0-9]{2,3})-(\d+)(?:\.(\d+))?(?:\.(\d+))?$")
ISA_SPLIT_RE = re.compile(r"(?=ISA)")

STRUCTURAL_SEGMENTS = frozenset({"ISA", "IEA", "GS", "GE", "ST", "SE"})
HEALTHCARE_TXN_RE = re.compile(r"^ST\*(835|837|270|271)\*")


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
    segments: list[Segment]
    delimiters: Delimiters
    source_file: str = ""
    _warnings: list[str] = field(default_factory=list)

    @property
    def message_type(self) -> str:
        st = self.first("ST")
        if st is None or not st.fields:
            return "UNKNOWN"
        txn = (st.fields[0] or "").strip()
        guide = (st.fields[2] if len(st.fields) > 2 else "").strip()
        if not guide:
            gs = self.first("GS")
            if gs is not None and len(gs.fields) >= 8:
                guide = (gs.fields[7] or "").strip()
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


def looks_like_x12(text: str) -> bool:
    """True for HIPAA/X12 payloads (``.edi`` / healthcare ``.dat``)."""
    sample = text.strip().lstrip("\ufeff")[:4096]
    if not sample:
        return False
    if sample.startswith("ISA"):
        return True
    if sample.startswith("GS*"):
        return True
    if HEALTHCARE_TXN_RE.match(sample):
        return True
    if re.search(r"(?:^|~|\n)ST\*(835|837|270|271)\*", sample):
        return True
    return False


def _split_segments(text: str, term: str) -> list[str]:
    parts = text.split(term)
    return [p.strip() for p in parts if p.strip()]


def _segments_from_text(text: str, delims: Delimiters) -> list[Segment]:
    normalized = text.replace("\r\n", "").replace("\n", "").replace("\r", "")
    raw_segments = _split_segments(normalized, delims.segment)
    segments: list[Segment] = []
    for i, raw in enumerate(raw_segments):
        parts = raw.split(delims.element)
        name = parts[0].strip()
        if not name:
            continue
        segments.append(Segment(name=name, fields=parts[1:], index=i, is_z=False))
    return segments


def _build_message(segments: list[Segment], delims: Delimiters, source_file: str) -> InterchangeMessage:
    warnings: list[str] = []
    if not any(s.name == "ST" for s in segments):
        warnings.append("no ST (transaction set) segment found")
    msg = InterchangeMessage(segments=segments, delimiters=delims, source_file=source_file)
    msg._warnings = warnings
    return msg


def parse(text: str, source_file: str = "") -> InterchangeMessage:
    docs = parse_documents(text, source_file=source_file)
    if not docs:
        raise ValueError("no X12 content found")
    return docs[0]


def parse_documents(text: str, source_file: str = "") -> list[InterchangeMessage]:
    text = text.strip().lstrip("\ufeff")
    if not text:
        raise ValueError("empty interchange")
    if not looks_like_x12(text):
        raise ValueError("content does not look like X12 EDI (expected ISA/GS/ST segment)")

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if "ISA" in normalized[:20]:
        chunks = [c for c in ISA_SPLIT_RE.split(normalized) if c.strip().startswith("ISA")]
        if not chunks:
            chunks = [normalized]
    else:
        chunks = [normalized]

    messages: list[InterchangeMessage] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if chunk.startswith("ISA"):
            first_seg = chunk.split("~", 1)[0] + "~"
            delims = Delimiters.from_isa(first_seg)
        else:
            delims = Delimiters()
        segments = _segments_from_text(chunk, delims)
        if segments:
            messages.append(_build_message(segments, delims, source_file))
    return messages


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_file(path: str | Path) -> InterchangeMessage:
    docs = parse_documents_file(path)
    if not docs:
        raise ValueError(f"no X12 messages in {path}")
    return docs[0]


def parse_documents_file(path: str | Path) -> list[InterchangeMessage]:
    p = Path(path)
    return parse_documents(_read_text(p), source_file=p.name)
