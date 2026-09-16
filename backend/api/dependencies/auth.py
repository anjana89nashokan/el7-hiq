from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from config.settings import config


@dataclass(frozen=True)
class CurrentUser:
    user_key: str
    user_email: str | None


def resolve_current_user(request: Request) -> CurrentUser:
    """Resolve the current user from dev identity headers or configured defaults.

    Standalone STTM does not use Launchpad SSO. The frontend sends
    x-dev-user-id / x-dev-user-email on every request (see axios-interceptor.ts).
    """
    headers = request.headers
    header_user_key = headers.get(config.APP_SESSION_DEV_HEADER_USER_ID)
    header_user_email = headers.get(config.APP_SESSION_DEV_HEADER_USER_EMAIL)
    return CurrentUser(
        user_key=header_user_key or config.APP_SESSION_DEV_USER_ID,
        user_email=header_user_email or config.APP_SESSION_DEV_USER_EMAIL,
    )
