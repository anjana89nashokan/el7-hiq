"""
Google Cloud Secret Manager utility for securely fetching credentials.

This module provides functions to retrieve secrets from GCP Secret Manager,
primarily used for Indemap DB credentials.
"""

from google.cloud import secretmanager
from config.settings import config
import json
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def fetch_secret_from_sm(secret_id: str, version: str = "latest") -> str:
    """
    Fetch secret from GCP Secret Manager.

    Args:
        secret_id: The secret identifier (e.g., 'indemap-db-credentials')
        version: Secret version to fetch (default: 'latest')

    Returns:
        The secret data as a string

    Raises:
        Exception: If secret fetch fails

    Example:
        >>> secret_data = fetch_secret_from_sm("indemap-db-credentials")
        >>> print(secret_data)
        '{"host": "...", "username": "...", "password": "..."}'
    """
    try:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{config.GOOGLE_CLOUD_PROJECT}/secrets/{secret_id}/versions/{version}"

        logger.info(f"Fetching secret: {secret_id} from project: {config.GOOGLE_CLOUD_PROJECT}")

        response = client.access_secret_version(request={"name": name})
        secret_payload = response.payload.data.decode("utf-8")

        logger.info(f"Successfully fetched secret: {secret_id}")
        return secret_payload

    except Exception as e:
        logger.exception(f"Failed to fetch secret {secret_id} from Secret Manager")
        raise Exception(f"Secret Manager error for {secret_id}: {str(e)}")


def get_indemap_credentials() -> Dict[str, str]:
    """
    Get Indemap DB credentials (username + password) from Secret Manager.

    Host, port, and database are read from config — only auth credentials
    come from the secret. Supports two secret formats:

    1. JSON: {"username": "SRV_MDR_NP", "password": "***"}
    2. Plain text with separate secrets for username and password,
       configured via INDEMAP_SECRET_ID (username) and
       INDEMAP_PASSWORD_SECRET_ID (password).

    Returns:
        Dictionary with 'username' and 'password' keys.
    """
    try:
        username = config.INDEMAP_SERVICE_ACCOUNT
        password = fetch_secret_from_sm(config.INDEMAP_SECRET_ID).strip()
        logger.info(f"Indemap credentials loaded for user: {username}")
        return {"username": username, "password": password}

    except Exception as e:
        logger.exception("Failed to get Indemap credentials from Secret Manager")
        raise


def fetch_secret_value(secret_id: str, key: str, version: str = "latest") -> str:
    """
    Fetch a specific key value from a JSON secret.

    Args:
        secret_id: The secret identifier
        key: The JSON key to extract
        version: Secret version (default: 'latest')

    Returns:
        The value for the specified key

    Example:
        >>> password = fetch_secret_value("indemap-db-credentials", "password")
        >>> print(password)
        'my_secure_password'
    """
    try:
        secret_data = fetch_secret_from_sm(secret_id, version)
        secret_dict = json.loads(secret_data)

        if key not in secret_dict:
            raise KeyError(f"Key '{key}' not found in secret '{secret_id}'")

        return secret_dict[key]

    except Exception as e:
        logger.exception(f"Failed to fetch key '{key}' from secret '{secret_id}'")
        raise
