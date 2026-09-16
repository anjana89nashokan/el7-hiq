"""
Evidence ingestion: local text extraction helpers (no LLM calls).

Supported inputs (per requirement):
  - PDF
  - TXT
  - CSV
  - Excel (XLSX)
  - Word (DOCX)

Design notes:
  - We prefer deterministic parsing where possible so ingestion is cheap and reproducible.
  - For PDFs that contain only scanned images, pypdf extraction may be empty; in that case
    we return an empty string and let the caller decide what to do.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Optional

import chardet
import pandas as pd


def extract_text_from_bytes(*, filename: str, data: bytes) -> str:
    """
    Extract text from file bytes based on filename extension.

    Returns an empty string when content cannot be extracted.
    """
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".txt"}:
        return _decode_text_bytes(data)
    if suffix in {".csv"}:
        return _decode_text_bytes(data)
    if suffix in {".xlsx", ".xls"}:
        return _extract_excel_text(data)
    if suffix in {".docx"}:
        return _extract_docx_text(data)
    if suffix in {".pdf"}:
        return _extract_pdf_text(data)

    # Unsupported extension (by this helper).
    return ""


def _decode_text_bytes(data: bytes) -> str:
    if not data:
        return ""
    # chardet returns a dict with encoding + confidence; fall back to utf-8.
    enc = (chardet.detect(data) or {}).get("encoding") or "utf-8"
    try:
        return data.decode(enc, errors="replace")
    except Exception:
        return data.decode("utf-8", errors="replace")


def _extract_excel_text(data: bytes) -> str:
    if not data:
        return ""
    try:
        # Read first sheet by default. For playbooks/transcripts, this is usually enough.
        df = pd.read_excel(io.BytesIO(data), sheet_name=0, dtype=str)
    except Exception:
        return ""

    # Normalize NaNs -> empty and stringify.
    df = df.fillna("")
    # Convert to TSV-like text for embeddings (keeps rows/columns readable).
    return df.to_csv(index=False, sep="\t")


def _extract_docx_text(data: bytes) -> str:
    """
    Minimal DOCX extraction without external dependencies.

    DOCX is a ZIP archive; primary body lives in word/document.xml.
    """
    if not data:
        return ""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        xml_bytes = z.read("word/document.xml")
    except Exception:
        return ""

    # Strip XML tags. This is crude but effective for embeddings/search.
    xml = _decode_text_bytes(xml_bytes)
    # Replace paragraph breaks with newlines.
    xml = xml.replace("</w:p>", "\n")
    text = re.sub(r"<[^>]+>", "", xml)
    # Collapse excessive whitespace.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_pdf_text(data: bytes) -> str:
    if not data:
        return ""
    try:
        # pypdf is intentionally imported here so this module can be imported even if
        # pypdf isn't installed in some environments.
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t:
                parts.append(t)
        return "\n\n".join(parts).strip()
    except Exception:
        return ""


def chunk_text(*, text: str, chunk_size_chars: int, overlap_chars: int) -> list[str]:
    """
    Chunk by characters with overlap.

    This is intentionally simple and deterministic for reproducibility.
    """
    if not text:
        return []
    if chunk_size_chars <= 0:
        return [text]
    overlap_chars = max(0, int(overlap_chars))
    chunk_size_chars = max(1, int(chunk_size_chars))
    if overlap_chars >= chunk_size_chars:
        # Prevent non-advancing windows (infinite loops).
        overlap_chars = max(0, chunk_size_chars - 1)

    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + chunk_size_chars)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(0, end - overlap_chars)
    return chunks


def sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
