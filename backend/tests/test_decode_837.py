"""837 segment decode — file order and element labels."""

from __future__ import annotations

from pathlib import Path

from utils.edi.decode_837 import decode_message
from utils.edi.parser import parse_file

SAMPLE = Path(__file__).resolve().parents[2] / "edi_samples" / "837" / "chiro.dat"


def test_chiro_decode_preserves_segment_order():
    msg = parse_file(SAMPLE)
    decoded = decode_message(msg)
    ids = [s["segment_id"] for s in decoded["segments"]]
    assert ids[0] == "ISA"
    assert ids[2] == "ST"
    assert ids[-1] == "IEA"
    assert len(ids) == len(msg.segments)


def test_nm1_entity_meaning():
    msg = parse_file(SAMPLE)
    decoded = decode_message(msg)
    nm1_submitter = next(s for s in decoded["segments"] if s["segment_id"] == "NM1")
    entity = nm1_submitter["elements"][0]
    assert entity["element_id"] == "NM101"
    assert entity["value"] == "41"
    assert "Submitter" in entity["meaning"]
