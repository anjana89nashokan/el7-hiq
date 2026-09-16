"""HL7 v2 support for DataMap.

The message-shaped ingestion path, covering the Phase-1 design-time workflow in
the Vituity BRD: parse, profile, infer site-defined (Z) segment meaning, propose
canonical mappings, take those through human review, and publish an immutable
versioned mapping package.

The canonical model is the target (BRD DP-02, "canonicalize once"). FHIR is a
projection of it rather than the destination, which is why ``fhir_mapper`` sits
alongside the canonical pieces rather than beneath them.
"""

from . import canonical_model
from .parser import Message, Segment, parse, parse_file
from .profiler import CorpusProfile, profile
from .zsegment_inference import Inference, infer_corpus
from .fhir_mapper import map_message
from .mapping_engine import (
    FieldMapping,
    build_mappings,
    entities_in_play,
    summarise,
)
from .mapping_package import (
    ReviewError,
    apply_review,
    bulk_approve_auto,
    bulk_approve_mappings,
    get_version,
    list_versions,
    load_audit,
    load_custom_targets,
    load_mappings,
    publish,
    readiness,
    save_mappings,
    sync_custom_targets_from_mappings,
    sync_standard_auto_approval,
)

__all__ = [
    "Message",
    "Segment",
    "parse",
    "parse_file",
    "CorpusProfile",
    "profile",
    "Inference",
    "infer_corpus",
    "map_message",
    "canonical_model",
    "FieldMapping",
    "build_mappings",
    "entities_in_play",
    "summarise",
    "ReviewError",
    "apply_review",
    "bulk_approve_auto",
    "bulk_approve_mappings",
    "get_version",
    "list_versions",
    "load_audit",
    "load_custom_targets",
    "load_mappings",
    "publish",
    "readiness",
    "save_mappings",
    "sync_custom_targets_from_mappings",
    "sync_standard_auto_approval",
]
