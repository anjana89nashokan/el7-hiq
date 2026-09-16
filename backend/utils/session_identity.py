"""Resolve the owning user's email for an app session (no hardcoded names)."""

from __future__ import annotations

import logging

from config.settings import config

logger = logging.getLogger(__name__)


def session_user_email(session_id: str | None) -> str:
    """Return the user_email that owns ``session_id``.

    Falls back to the neutral default identity when unknown — never a personal name.
    """
    fallback = config.APP_SESSION_DEV_USER_EMAIL
    if not session_id:
        return fallback
    try:
        from db.engine import app_db_session, is_app_db_enabled
        from db.models import AppSession

        if not is_app_db_enabled():
            return fallback
        with app_db_session() as s:
            obj = s.get(AppSession, session_id)
            return (getattr(obj, "user_email", None) or fallback) if obj else fallback
    except Exception as exc:  # noqa: BLE001
        logger.debug("session_user_email lookup failed for %s: %s", session_id, exc)
        return fallback
