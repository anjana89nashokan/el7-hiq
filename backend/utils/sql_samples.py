"""Normalize aggregated sample values into a list.

BigQuery ``ARRAY_AGG`` returns a list; the local SQLite warehouse translates it to
``group_concat`` which returns a comma-joined string. Consumers expect a list, so
``list(value)`` on a string would split it character-by-character. Use this to get
a clean list from either shape.
"""

from __future__ import annotations

from typing import Any, List


def to_sample_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [s for s in value.split(",") if s != ""]
    try:
        return list(value)
    except TypeError:
        return [value]
