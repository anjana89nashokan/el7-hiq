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
]
