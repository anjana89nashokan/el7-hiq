"""Compatibility shims for standalone (no-GCP) mode.

These helpers let modules that were written against GCP import and run without
crashing when no service-account / ADC credentials are present. Anything that
genuinely needs a live GCP service should be migrated to a local equivalent;
until then these keep the app importable and bootable.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def load_optional_credentials():
    """Return Google credentials if available, else ``None`` (never raises).

    Tries an explicit service-account file (config.CREDENTIALS_PATH), then ADC.
    In standalone mode this normally returns ``None`` and callers should treat a
    ``None`` result as "GCP disabled".
    """
    try:
        from config.settings import config

        creds_path = getattr(config, "CREDENTIALS_PATH", "")
        if creds_path and os.path.exists(creds_path):
            import google.auth

            creds, _ = google.auth.load_credentials_from_file(creds_path)
            return creds
    except Exception as exc:  # noqa: BLE001
        logger.debug("Service-account credentials unavailable: %s", exc)

    try:
        import google.auth

        creds, _ = google.auth.default()
        return creds
    except Exception as exc:  # noqa: BLE001
        logger.debug("ADC credentials unavailable (standalone mode): %s", exc)
        return None


def bigquery_credentials():
    """Credentials object for ADK ``BigQueryCredentialsConfig`` construction.

    Returns real credentials when available, otherwise anonymous credentials so
    the toolset can be *constructed* at import time. Actual BigQuery calls are not
    used in standalone mode (the warehouse is local SQLite); these agents degrade
    gracefully until/unless real GCP credentials are provided.
    """
    creds = load_optional_credentials()
    if creds is not None:
        return creds
    from google.auth.credentials import AnonymousCredentials

    return AnonymousCredentials()
