"""Convert numpy/pandas/Decimal/datetime values to native, JSON/pydantic-safe types.

The local SQLite warehouse returns pandas/numpy scalars (numpy.int64, float64,
bool_, ndarray, NaN/Inf). Those break pydantic/JSON serialization downstream
("Unable to serialize unknown type: <class 'numpy.int64'>"). Run tool/result
payloads through ``to_native`` before returning or storing them.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def to_native(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if obj is ...:  # ellipsis
        return None
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, Decimal):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_native(v) for v in obj]
    # numpy / pandas scalars and arrays
    mod = type(obj).__module__
    if mod and (mod == "numpy" or mod.startswith("pandas")):
        if hasattr(obj, "tolist"):  # ndarray / pandas array
            return to_native(obj.tolist())
        if hasattr(obj, "item"):  # numpy scalar
            try:
                return to_native(obj.item())
            except Exception:  # noqa: BLE001
                return str(obj)
    if hasattr(obj, "tolist"):
        return to_native(obj.tolist())
    if hasattr(obj, "item") and not hasattr(obj, "__len__"):
        try:
            return to_native(obj.item())
        except Exception:  # noqa: BLE001
            pass
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "__dict__"):
        return str(obj)
    return obj
