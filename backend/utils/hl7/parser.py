"""Minimal HL7 v2 parser — spike for STTM HL7 support.

Pure standard library. Parses a v2.x message into addressable segment/field/
component/subcomponent paths (``PID-5.1``, ``OBX-3.1``) and classifies each
segment as standard or site-defined (Z-segment).

Delimiters are read from MSH rather than assumed, because trading partners are
permitted to vary them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

# Segments defined by the v2.5.1 standard that appear in the ORU^R01 and
# MDM^T02 structures. Anything outside this set that matches Z_SEGMENT_RE is
# site-defined and cannot be mapped without human confirmation.
STANDARD_SEGMENTS = {
    "MSH", "EVN", "PID", "PD1", "NK1", "PV1", "PV2", "ORC", "OBR", "OBX",
    "NTE", "TXA", "SPM", "DG1", "AL1", "IN1", "MSA", "ERR", "QRD", "QRF",
}

Z_SEGMENT_RE = re.compile(r"^Z[A-Z0-9]{2}$")

PATH_RE = re.compile(r"^([A-Z][A-Z0-9]{2})-(\d+)(?:\.(\d+))?(?:\.(\d+))?$")


@dataclass
class Delimiters:
    """Encoding characters, taken from MSH-1 and MSH-2."""

    field: str = "|"
    component: str = "^"
    repetition: str = "~"
    escape: str = "\\"
    subcomponent: str = "&"

    @classmethod
    def from_msh(cls, msh_line: str) -> "Delimiters":
        if len(msh_line) < 8:
            return cls()
        sep = msh_line[3]
        enc = msh_line[4:msh_line.index(sep, 4)] if sep in msh_line[4:] else msh_line[4:8]
        enc = (enc + "^~\\&")[:4]
        return cls(field=sep, component=enc[0], repetition=enc[1],
                   escape=enc[2], subcomponent=enc[3])


@dataclass
class Segment:
    name: str
    fields: list[str]
    index: int
    is_z: bool = False

    @property
    def raw(self) -> str:
        return self.name + "|" + "|".join(self.fields)

    def field_count(self) -> int:
        """Highest populated field number (trailing empties ignored)."""
        for i in range(len(self.fields), 0, -1):
            if self.fields[i - 1].strip():
                return i
        return 0


@dataclass
class Message:
    segments: list[Segment]
    delimiters: Delimiters
    source_file: str = ""
    _warnings: list[str] = field(default_factory=list)

    @property
    def message_type(self) -> str:
        """MSH-9 as ``ORU^R01``; components joined for readability."""
        return self.get("MSH-9.1") + "^" + self.get("MSH-9.2") if self.get("MSH-9.2") else self.get("MSH-9")

    @property
    def control_id(self) -> str:
        return self.get("MSH-10")

    @property
    def version(self) -> str:
        return self.get("MSH-12")

    def by_name(self, name: str) -> list[Segment]:
        return [s for s in self.segments if s.name == name]

    def first(self, name: str) -> Segment | None:
        segs = self.by_name(name)
        return segs[0] if segs else None

    def z_segments(self) -> list[Segment]:
        return [s for s in self.segments if s.is_z]

    def segment_names(self) -> list[str]:
        return [s.name for s in self.segments]

    def get(self, path: str, segment: Segment | None = None) -> str:
        """Resolve an HL7 path such as ``PID-5.1`` or ``OBX-3.1``.

        MSH is offset by one because MSH-1 *is* the field separator, so its
        field list starts at MSH-2.
        """
        m = PATH_RE.match(path)
        if not m:
            raise ValueError(f"malformed HL7 path: {path!r}")
        seg_name, fnum, cnum, snum = m.group(1), int(m.group(2)), m.group(3), m.group(4)

        seg = segment if segment is not None else self.first(seg_name)
        if seg is None:
            return ""

        if seg.name == "MSH":
            if fnum == 1:
                return self.delimiters.field
            idx = fnum - 2
        else:
            idx = fnum - 1

        if idx < 0 or idx >= len(seg.fields):
            return ""
        value = seg.fields[idx]

        # Take the first repetition; repetition-aware mapping is a later concern.
        value = value.split(self.delimiters.repetition)[0]

        if cnum:
            parts = value.split(self.delimiters.component)
            value = parts[int(cnum) - 1] if int(cnum) - 1 < len(parts) else ""
        if snum:
            parts = value.split(self.delimiters.subcomponent)
            value = parts[int(snum) - 1] if int(snum) - 1 < len(parts) else ""
        return value.strip()

    def components(self, raw_field: str) -> list[str]:
        return [c.strip() for c in raw_field.split(self.delimiters.component)]


def strip_mllp(text: str) -> tuple[str, list[str]]:
    """Remove MLLP transport framing if present.

    Not in the current scope, but messages arriving from a listener rather than
    a file drop carry these control bytes and must be de-framed first.
    """
    warnings: list[str] = []
    if text.startswith("\x0b"):
        text = text[1:]
        if text.endswith("\x1c\r"):
            text = text[:-2]
        elif text.endswith("\x1c"):
            text = text[:-1]
            warnings.append("MLLP end block was FS without trailing CR")
        else:
            warnings.append("MLLP start block present but end block missing")
    return text, warnings


def parse(text: str, source_file: str = "") -> Message:
    text, warnings = strip_mllp(text)

    # Segments are CR-terminated on the wire; files often use LF or CRLF.
    lines = [ln for ln in re.split(r"\r\n|\r|\n", text) if ln.strip()]
    if not lines:
        raise ValueError("empty message")
    if not lines[0].startswith("MSH"):
        raise ValueError(f"message does not begin with MSH (got {lines[0][:3]!r})")

    delims = Delimiters.from_msh(lines[0])
    segments: list[Segment] = []
    for i, line in enumerate(lines):
        parts = line.split(delims.field)
        name = parts[0].strip()
        segments.append(Segment(
            name=name,
            fields=parts[1:],
            index=i,
            is_z=bool(Z_SEGMENT_RE.match(name)) and name not in STANDARD_SEGMENTS,
        ))

    msg = Message(segments=segments, delimiters=delims, source_file=source_file)
    msg._warnings = warnings
    return msg


def parse_file(path: str | Path) -> Message:
    p = Path(path)
    return parse(p.read_text(encoding="utf-8"), source_file=p.name)
