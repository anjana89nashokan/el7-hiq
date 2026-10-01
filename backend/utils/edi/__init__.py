"""HIPAA X12 EDI ingestion: parse, profile, map to canonical eligibility/claims model."""

from . import canonical_model
from .parser import (
    InterchangeMessage,
    looks_like_x12,
    parse,
    parse_documents,
    parse_documents_file,
    parse_file,
)
from utils.hl7.profiler import CorpusProfile, profile
from .mapping_engine import (
    build_mappings,
    entities_in_play,
    summarise,
)
from .decode_837 import (
    decode_from_stored as decode_edi_837_from_stored,
    decode_messages as decode_edi_837,
    serialize_messages as serialize_edi_messages,
)

__all__ = [
    "canonical_model",
    "InterchangeMessage",
    "looks_like_x12",
    "parse",
    "parse_documents",
    "parse_documents_file",
    "parse_file",
    "CorpusProfile",
    "profile",
    "build_mappings",
    "entities_in_play",
    "summarise",
    "decode_edi_837",
    "decode_edi_837_from_stored",
    "serialize_edi_messages",
]
