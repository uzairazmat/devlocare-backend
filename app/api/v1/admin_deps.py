"""
Admin / super-admin FastAPI dependencies.

Authoritative role check is **always** done against the live DB row
(``current_user.is_admin`` / ``current_user.is_super_admin``). The JWT
also carries the same flags so cheap claim-only checks are possible, but
this module never trusts the token alone — that way demoting a user
takes effect immediately on their next request.

Two dependencies are exposed:

* :func:`require_admin`        — admins AND super admins.
* :func:`require_super_admin`  — super admins ONLY.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends

from app.api.v1.deps import get_current_user
from app.core.exceptions import AuthorizationError
from app.core.logging import get_logger
from app.db.models import User

logger = get_logger(__name__)


def _is_admin(user: User) -> bool:
    """Super admins always count as admins (strict superset)."""
    return bool(user.is_admin) or bool(user.is_super_admin)


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Allow the request only when the caller is an admin OR super admin.

    Raises
    ------
    AuthenticationError
        Bubbled up from :func:`get_current_user` (HTTP 401).
    AuthorizationError
        Caller is authenticated but lacks admin rights (HTTP 403).
    """
    if not _is_admin(current_user):
        logger.warning(
            "AUDIT admin.access_denied user_id=%s username=%s reason=not_admin at=%s",
            current_user.user_id,
            current_user.username,
            datetime.now(timezone.utc).isoformat(),
        )
        raise AuthorizationError(
            message="Admin privileges required",
            code="admin_required",
        )

    logger.info(
        "AUDIT admin.access_granted user_id=%s username=%s super=%s at=%s",
        current_user.user_id,
        current_user.username,
        bool(current_user.is_super_admin),
        datetime.now(timezone.utc).isoformat(),
    )
    return current_user


async def require_super_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Allow the request only when the caller is a super admin.

    This is the gate for admin-management routes (create / promote /
    demote). Plain admins explicitly cannot mint or revoke other admins.
    """
    if not bool(current_user.is_super_admin):
        logger.warning(
            "AUDIT super_admin.access_denied user_id=%s username=%s "
            "is_admin=%s at=%s",
            current_user.user_id,
            current_user.username,
            bool(current_user.is_admin),
            datetime.now(timezone.utc).isoformat(),
        )
        raise AuthorizationError(
            message="Super admin privileges required",
            code="super_admin_required",
        )

    logger.info(
        "AUDIT super_admin.access_granted user_id=%s username=%s at=%s",
        current_user.user_id,
        current_user.username,
        datetime.now(timezone.utc).isoformat(),
    )
    return current_user
