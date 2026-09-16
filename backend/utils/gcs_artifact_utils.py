"""Artifact storage — standalone local filesystem backend (no GCS).

Reimplements the previous Google Cloud Storage helpers against the local
filesystem under ``config.ARTIFACTS_DIR``. The ``gs://bucket/object`` URI scheme
is preserved as a *virtual* address (callers store and re-parse these URIs), but
bytes live at ``ARTIFACTS_DIR/<bucket>/<object>``. All public function signatures
are unchanged so callers (mapping/profiling artifact stores) need no edits.
"""

from __future__ import annotations

import json
import math
import shutil
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from config.settings import config


def artifact_bucket_name() -> str:
    raw = str(getattr(config, "MAPPING_ARTIFACT_BUCKET", "") or "").strip() or "artifacts"
    if raw.startswith("gs://"):
        raw = raw[5:]
    return raw.strip("/").split("/", 1)[0]


def artifact_project_id() -> str:
    return str(getattr(config, "MAPPING_ARTIFACT_PROJECT_ID", "") or "").strip()


def _artifacts_root() -> Path:
    root = Path(getattr(config, "ARTIFACTS_DIR", config.DATA_DIR / "artifacts"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _local_path(bucket_name: str, object_name: str) -> Path:
    return _artifacts_root() / bucket_name / object_name


def gcs_uri(object_name: str) -> str:
    return f"gs://{artifact_bucket_name()}/{object_name}"


def parse_gcs_uri(uri: str) -> tuple[str, str]:
    raw = str(uri or "").strip()
    if not raw.startswith("gs://"):
        raise RuntimeError(f"Expected gs:// URI, got: {uri}")
    bucket_and_path = raw[5:]
    if "/" not in bucket_and_path:
        raise RuntimeError(f"GCS URI must include object path: {uri}")
    bucket_name, object_name = bucket_and_path.split("/", 1)
    return bucket_name, object_name


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def make_json_compatible(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        as_float = float(value)
        return as_float if math.isfinite(as_float) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): make_json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [make_json_compatible(item) for item in value]
    if hasattr(value, "model_dump"):
        return make_json_compatible(value.model_dump())
    if hasattr(value, "tolist"):
        return make_json_compatible(value.tolist())
    return str(value)


def payload_to_json_text(payload: Any) -> str:
    if hasattr(payload, "model_dump_json"):
        return payload.model_dump_json(indent=2)
    if isinstance(payload, (dict, list)):
        return json.dumps(payload, indent=2, default=_json_default)
    raise RuntimeError("Unsupported JSON payload type. Expected Pydantic model, dict, or list.")


class _LocalBlob:
    """Filesystem-backed stand-in for ``storage.Blob``."""

    def __init__(self, name: str, bucket_name: str | None = None):
        self.name = name
        self._bucket_name = bucket_name or artifact_bucket_name()

    @property
    def _path(self) -> Path:
        return _local_path(self._bucket_name, self.name)

    @property
    def updated(self):
        """Last-modified time (mimics storage.Blob.updated)."""
        try:
            from datetime import timezone
            return datetime.fromtimestamp(self._path.stat().st_mtime, tz=timezone.utc)
        except Exception:  # noqa: BLE001
            return None

    @property
    def time_created(self):
        return self.updated

    @property
    def size(self):
        try:
            return self._path.stat().st_size
        except Exception:  # noqa: BLE001
            return 0

    def upload_from_string(self, data, content_type: str | None = None):
        p = self._path
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            data = data.encode("utf-8")
        p.write_bytes(data)

    def upload_from_filename(self, filename, content_type: str | None = None):
        p = self._path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(Path(filename).read_bytes())

    def download_as_text(self, encoding: str = "utf-8", **kwargs) -> str:
        return self._path.read_text(encoding=encoding)

    def download_as_bytes(self, **kwargs) -> bytes:
        return self._path.read_bytes()

    def download_to_file(self, file_obj, **kwargs):
        file_obj.write(self._path.read_bytes())

    def download_to_filename(self, filename, **kwargs):
        Path(filename).write_bytes(self._path.read_bytes())

    def exists(self, client=None) -> bool:
        return self._path.exists()

    def delete(self, **kwargs):
        if self._path.exists():
            self._path.unlink()

    def generate_signed_url(self, *args, **kwargs) -> str:
        # No signing locally; return a stable virtual URI.
        return gcs_uri(self.name)


class _LocalBucket:
    def __init__(self, name: str):
        self.name = name

    def blob(self, object_name: str) -> _LocalBlob:
        return _LocalBlob(object_name, self.name)

    def list_blobs(self, prefix: str = "", **kwargs):
        base = _local_path(self.name, "")
        out = []
        for p in base.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(base))
                if rel.startswith(prefix or ""):
                    out.append(_LocalBlob(rel, self.name))
        return out


class _LocalStorageClient:
    """Filesystem-backed stand-in for ``google.cloud.storage.Client``."""

    def bucket(self, name: str) -> _LocalBucket:
        return _LocalBucket(name)

    def list_blobs(self, bucket_or_name, prefix: str = "", **kwargs):
        name = getattr(bucket_or_name, "name", bucket_or_name)
        return _LocalBucket(name).list_blobs(prefix=prefix)


def artifact_storage_client() -> _LocalStorageClient:
    """Return a filesystem-backed storage client (no GCS)."""
    return _LocalStorageClient()


def upload_bytes(*, object_name: str, content: bytes, content_type: str) -> str:
    path = _local_path(artifact_bucket_name(), object_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return gcs_uri(object_name)


def upload_text(*, object_name: str, content: str, content_type: str = "text/plain; charset=utf-8") -> str:
    return upload_bytes(object_name=object_name, content=content.encode("utf-8"), content_type=content_type)


def upload_json(*, object_name: str, payload: Any) -> str:
    return upload_text(object_name=object_name, content=payload_to_json_text(payload), content_type="application/json")


def download_text(*, object_name: str) -> str:
    path = _local_path(artifact_bucket_name(), object_name)
    if not path.exists():
        raise FileNotFoundError(f"Artifact not found: {gcs_uri(object_name)}")
    return path.read_text(encoding="utf-8")


def download_bytes(*, object_name: str) -> bytes:
    path = _local_path(artifact_bucket_name(), object_name)
    if not path.exists():
        raise FileNotFoundError(f"Artifact not found: {gcs_uri(object_name)}")
    return path.read_bytes()


def list_blobs(*, prefix: str) -> list[_LocalBlob]:
    base = _local_path(artifact_bucket_name(), "")
    out: list[_LocalBlob] = []
    for p in base.rglob("*"):
        if p.is_file():
            name = str(p.relative_to(base))
            if name.startswith(prefix):
                out.append(_LocalBlob(name))
    return out


def uri_exists(uri: str) -> bool:
    bucket_name, object_name = parse_gcs_uri(uri)
    return _local_path(bucket_name, object_name).exists()


def download_json_uri(uri: str) -> dict[str, Any]:
    _, object_name = parse_gcs_uri(uri)
    return json.loads(download_text(object_name=object_name))


def get_in_scope_for_session(session_id: str) -> str:
    """
    Load the validated requirement layer for a session from GCS and return
    the in_scope string from validated_requirement_layer.scope.in_scope.
    """
    object_name = (
        f"{config.BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}"
        f"/extracted_data/validated_requirement_layer.json"
    )
    data = json.loads(download_text(object_name=object_name))
    layer: dict = data.get("validated_requirement_layer") or data
    return layer.get("scope", {}).get("in_scope", "")


def delete_folder(*, prefix: str) -> int:
    base = _local_path(artifact_bucket_name(), "")
    count = 0
    target = base / prefix
    if target.is_dir():
        for p in target.rglob("*"):
            if p.is_file():
                count += 1
        shutil.rmtree(target, ignore_errors=True)
        return count
    # prefix may be a partial path; delete matching files
    for p in base.rglob("*"):
        if p.is_file() and str(p.relative_to(base)).startswith(prefix):
            p.unlink()
            count += 1
    return count


def materialize_gcs_uri_to_temp_file(uri: str, *, suffix: str | None = None) -> Path:
    bucket_name, object_name = parse_gcs_uri(uri)
    src = _local_path(bucket_name, object_name)
    if not src.exists():
        raise FileNotFoundError(f"Artifact not found: {uri}")

    guessed_suffix = suffix or Path(object_name).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=guessed_suffix) as tmp:
        tmp.write(src.read_bytes())
        return Path(tmp.name)
