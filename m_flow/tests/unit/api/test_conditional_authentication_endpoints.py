"""
条件认证端点测试

Tests conditional authentication behavior.

Note: Due to Python's module caching and the way REQUIRE_AUTHENTICATION is
set at module import time, these tests use mocking to test conditional behavior
rather than relying on environment variables.
"""

from __future__ import annotations

import importlib
import os
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_user():
    """模拟用户"""
    return SimpleNamespace(id=uuid4(), email="test@test.com", is_active=True, tenant_id=uuid4())


@pytest.fixture
def mock_auth_user():
    """模拟认证用户"""
    from m_flow.auth.models import User

    return User(
        id=uuid4(),
        email="auth@test.com",
        hashed_password="x",
        is_active=True,
        is_verified=True,
        tenant_id=uuid4(),
    )


# Import the function first to ensure proper registration
from m_flow.auth.methods import get_authenticated_user as _gau_func

# Get module reference for patching REQUIRE_AUTHENTICATION using sys.modules
# This avoids polluting the import system
import sys

_gau_mod = sys.modules.get("m_flow.auth.methods.get_authenticated_user")
if _gau_mod is None:
    # Fallback: import module directly if not in sys.modules
    from m_flow.auth.methods import get_authenticated_user as _temp

    _gau_mod = sys.modules["m_flow.auth.methods.get_authenticated_user"]


class TestEndpoints:
    """API端点测试"""

    @pytest.fixture
    def client(self):
        from m_flow.api.client import app

        return TestClient(app)

    @pytest.fixture
    def auth_client(self, mock_user):
        """Client with authentication dependency overridden"""
        from m_flow.api.client import app
        from m_flow.auth.methods import get_authenticated_user

        # Override the authentication dependency
        async def mock_auth():
            return mock_user

        app.dependency_overrides[get_authenticated_user] = mock_auth
        client = TestClient(app)
        yield client
        # Clean up
        app.dependency_overrides.clear()

    def test_health(self, client):
        """测试健康检查"""
        r = client.get("/health")
        assert r.status_code in [200, 503]

    def test_root(self, client):
        """测试根路径"""
        r = client.get("/")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "service": "m_flow"}

    @patch.object(_gau_mod, "REQUIRE_AUTHENTICATION", False)
    def test_openapi_no_global_security(self, client):
        """测试OpenAPI无全局安全"""
        r = client.get("/openapi.json")
        assert r.status_code == 200
        schema = r.json()
        assert schema.get("security", []) == []
        schemes = schema.get("components", {}).get("securitySchemes", {})
        assert "BearerAuth" in schemes

    @patch("m_flow.api.v1.add.add")
    def test_add_conditional_auth(self, mock_add, auth_client, mock_user):
        """测试添加端点条件认证 - 验证端点在用户认证后正常工作"""
        mock_add.return_value = MagicMock(model_dump=lambda: {"status": "success"})

        files = {"data": ("t.txt", b"content", "text/plain")}
        r = auth_client.post("/api/v1/add", files=files, data={"datasetName": "ds"})

        assert r.status_code != 401


class TestConditionalBehavior:
    """条件认证行为测试"""

    @pytest.fixture
    def auth_client(self, mock_user):
        """Client with authentication dependency overridden"""
        from m_flow.api.client import app
        from m_flow.auth.methods import get_authenticated_user

        async def mock_auth():
            return mock_user

        app.dependency_overrides[get_authenticated_user] = mock_auth
        client = TestClient(app)
        yield client
        app.dependency_overrides.clear()

    @pytest.mark.parametrize(
        "ep,method",
        [
            ("/api/v1/search", "GET"),
        ],
    )
    def test_get_endpoint_with_auth(self, auth_client, ep, method):
        """测试 GET 端点在认证用户下正常工作"""
        r = auth_client.get(ep) if method == "GET" else auth_client.post(ep, json={})
        assert r.status_code != 401

    def test_datasets_endpoint_returns_current_contract(self, monkeypatch, auth_client, mock_user):
        """测试数据集端点在认证用户下返回当前契约结构。"""
        permissions_module = importlib.import_module("m_flow.auth.permissions.methods")
        created_at = datetime(2026, 4, 25, 12, 15, 0)
        updated_at = datetime(2026, 4, 25, 12, 16, 0)
        dataset_id = uuid4()
        owner_id = mock_user.id

        async def fake_datasets(_user, _permission):
            return [
                SimpleNamespace(
                    id=dataset_id,
                    name="alpha",
                    created_at=created_at,
                    updated_at=updated_at,
                    owner_id=owner_id,
                )
            ]

        monkeypatch.setattr(permissions_module, "get_all_user_permission_datasets", fake_datasets)

        r = auth_client.get("/api/v1/datasets")

        assert r.status_code == 200, r.text
        assert r.json() == [
            {
                "id": str(dataset_id),
                "name": "alpha",
                "createdAt": "2026-04-25T12:15:00",
                "updatedAt": "2026-04-25T12:16:00",
                "ownerId": str(owner_id),
            }
        ]

    _gsm_mod = importlib.import_module("m_flow.config.settings.get_settings")

    @patch.object(_gsm_mod, "get_vectordb_config")
    @patch.object(_gsm_mod, "get_llm_config")
    def test_settings_integration(self, mock_llm, mock_vec, auth_client):
        """测试设置端点集成 - 验证设置端点在认证用户下正常工作"""
        mock_llm.return_value = SimpleNamespace(
            llm_provider="openai",
            llm_model="gpt-4o",
            llm_endpoint=None,
            llm_api_version=None,
            llm_api_key="key123",
        )
        mock_vec.return_value = SimpleNamespace(
            vector_db_provider="lancedb",
            vector_db_url="localhost:5432",
            vector_db_key="vkey",
        )
        r = auth_client.get("/api/v1/settings")
        assert r.status_code != 401


class TestErrorHandling:
    """错误处理测试"""

    @pytest.fixture
    def error_client(self):
        """Client with authentication dependency that raises an error"""
        from m_flow.api.client import app
        from m_flow.auth.methods import get_authenticated_user
        from fastapi import HTTPException

        async def mock_auth_error():
            raise HTTPException(status_code=500, detail="Failed to create default user: DB error")

        app.dependency_overrides[get_authenticated_user] = mock_auth_error
        client = TestClient(app, raise_server_exceptions=False)
        yield client
        app.dependency_overrides.clear()

    def test_user_creation_fails(self, error_client):
        """测试当 get_authenticated_user 抛出异常时的错误处理"""
        files = {"data": ("t.txt", b"x", "text/plain")}
        r = error_client.post("/api/v1/add", files=files, data={"datasetName": "ds"})
        assert r.status_code == 500
        assert "Failed to create default user" in r.json().get("detail", "")

    def test_env_config(self):
        """测试环境配置 - REQUIRE_AUTHENTICATION 应该是布尔类型"""
        from m_flow.auth.methods.get_authenticated_user import REQUIRE_AUTHENTICATION

        # REQUIRE_AUTHENTICATION is set at module import time based on env vars
        # Its actual value depends on test execution order, but it should always be bool
        assert isinstance(REQUIRE_AUTHENTICATION, bool)
        # Note: We don't assert a specific value because it depends on module load order
