# -*- coding: utf-8 -*-
"""Location: ./tests/unit/mcpgateway/middleware/test_rbac_loopback_guard.py
Copyright 2026
SPDX-License-Identifier: Apache-2.0

Regression tests for the loopback-bind guard on the unauthenticated-admin
override in ``get_current_user_with_permissions``.

The override (AUTH_REQUIRED=false + ALLOW_UNAUTHENTICATED_ADMIN=true) grants
browser requests platform-admin with no credentials. Its safety depends on the
gateway being bound to a loopback interface — a fork-local guard
(``_is_loopback_bind``) enforces that invariant in code so the override cannot
serve unauthenticated admin to the network if HOST is ever a routable address.
"""

# Future
from __future__ import annotations

# Standard
from unittest.mock import MagicMock, patch

# Third-Party
from fastapi import HTTPException, Request, status
import pytest

# First-Party
from mcpgateway.middleware import rbac
from mcpgateway.middleware.rbac import _is_loopback_bind


# ---------------------------------------------------------------------------
# The pure helper matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("[::1]", True),
        ("127.0.0.5", True),  # entire 127.0.0.0/8 loopback range
        ("  127.0.0.1  ", True),  # tolerant of stray whitespace
        ("0.0.0.0", False),  # binds ALL interfaces — must NOT qualify
        ("::", False),
        ("192.168.1.10", False),
        ("10.0.0.1", False),
        ("example.com", False),
        ("", False),
        (None, False),
    ],
)
def test_is_loopback_bind_matrix(host, expected):
    """_is_loopback_bind recognizes loopback binds and rejects routable ones."""
    assert _is_loopback_bind(host) is expected


# ---------------------------------------------------------------------------
# The override behavior end-to-end
# ---------------------------------------------------------------------------


def _no_token_browser_request():
    """Build a credential-less browser request that reaches the override branch."""
    req = MagicMock(spec=Request)
    req.cookies = {}
    req.headers = {"accept": "text/html", "user-agent": "pytest"}
    req.client = MagicMock()
    req.client.host = "127.0.0.1"
    req.state = MagicMock(request_id="req-guard", team_id=None, plugin_context_table=None, plugin_global_context=None)
    return req


@pytest.mark.asyncio
async def test_override_grants_admin_when_bound_to_loopback():
    """Flags on + loopback HOST → unauthenticated browser request gets platform-admin."""
    req = _no_token_browser_request()
    with patch.object(rbac.settings, "auth_required", False), patch.object(rbac.settings, "allow_unauthenticated_admin", True), patch.object(
        rbac.settings, "host", "127.0.0.1"
    ), patch.object(rbac.settings, "platform_admin_email", "admin@example.com"):
        result = await rbac.get_current_user_with_permissions(req, credentials=None, jwt_token=None)
    assert result["is_admin"] is True
    assert result["auth_method"] == "disabled"
    assert result["email"] == "admin@example.com"


@pytest.mark.asyncio
async def test_override_denied_when_bound_to_routable_interface():
    """Flags on but HOST=0.0.0.0 → override is skipped; browser falls through to login 302.

    This is the security-critical case: without the guard, flipping HOST to a
    routable address while leaving the flags on would expose unauthenticated
    platform-admin to the network. The guard must skip the grant instead.
    """
    req = _no_token_browser_request()
    with patch.object(rbac.settings, "auth_required", False), patch.object(rbac.settings, "allow_unauthenticated_admin", True), patch.object(
        rbac.settings, "host", "0.0.0.0"
    ), patch.object(rbac.settings, "platform_admin_email", "admin@example.com"), patch.object(rbac.settings, "app_root_path", ""):
        with pytest.raises(HTTPException) as exc:
            await rbac.get_current_user_with_permissions(req, credentials=None, jwt_token=None)
    assert exc.value.status_code == status.HTTP_302_FOUND
    assert "/admin/login" in exc.value.headers["Location"]
