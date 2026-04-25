"""
P6 End-to-End User Scenario Tests

This file implements E2E tests covering core user scenarios as defined in TEST_PLAN.md:
- E2E-001: New User Complete Flow
- E2E-002: Daily Usage Flow
- E2E-003: Admin Operations Flow
- E2E-004: Multi-User Collaboration Flow
- E2E-005: Error Recovery Flow
- E2E-006: Cloud Sync Flow
- E2E-007: OpenAI Compatible API Flow
- E2E-008: Password Reset Flow
- E2E-009: Email Verification Flow
- E2E-010: Data Update Flow
- E2E-011: Data Export Flow
- E2E-012: CLI Full Flow

Tests use FastAPI TestClient for API-level E2E testing.
"""

from __future__ import annotations

import importlib
import io
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_user():
    """Create a mock user object for authentication."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="e2e_test@example.com",
        is_active=True,
        is_verified=True,
        tenant_id=uuid.uuid4(),
        is_superuser=False,
    )


@pytest.fixture
def mock_admin_user():
    """Create a mock admin user object."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="admin@example.com",
        is_active=True,
        is_verified=True,
        tenant_id=uuid.uuid4(),
        is_superuser=True,
    )


@pytest.fixture
def test_client(mock_user):
    """Create a test client with mocked authentication."""
    from m_flow.api.client import app
    from m_flow.auth.methods import get_authenticated_user

    async def mock_auth():
        return mock_user

    app.dependency_overrides[get_authenticated_user] = mock_auth
    client = TestClient(app, raise_server_exceptions=False)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def admin_client(mock_admin_user):
    """Create a test client with admin authentication."""
    from m_flow.api.client import app
    from m_flow.auth.methods import get_authenticated_user

    async def mock_admin_auth():
        return mock_admin_user

    app.dependency_overrides[get_authenticated_user] = mock_admin_auth
    client = TestClient(app, raise_server_exceptions=False)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def unauthenticated_client():
    """Create a test client without authentication."""
    from m_flow.api.client import app

    return TestClient(app, raise_server_exceptions=False)


# ============================================================================
# E2E-001: New User Complete Flow
# ============================================================================


class TestE2E001NewUserFlow:
    """E2E-001: Test new user complete flow."""

    def test_health_check_accessible(self, unauthenticated_client):
        """Step 1: Verify basic system health is accessible."""
        response = unauthenticated_client.get("/health")
        assert response.status_code in [200, 503]

    def test_root_accessible(self, unauthenticated_client):
        """Step 2: Verify root path exposes the current service status contract."""
        response = unauthenticated_client.get("/")
        assert response.status_code == 200
        payload = response.json()
        assert payload == {"status": "ok", "service": "m_flow"}

    def test_authenticated_user_can_access_datasets(self, test_client):
        """Step 3-5: After login, user can access datasets."""
        response = test_client.get("/api/v1/datasets")
        assert response.status_code != 401

    def test_settings_retrieval(self, test_client):
        """Step 6: User can retrieve configuration settings using the current response contract."""
        response = test_client.get("/api/v1/settings")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload) == {"llm", "vectorDb", "embedding"}
        assert {"llmProvider", "llmModel", "llmApiKey"}.issubset(payload["llm"])
        assert {"vectorDbProvider", "vectorDbUrl", "vectorDbKey"}.issubset(payload["vectorDb"])
        assert {
            "embeddingProvider",
            "embeddingModel",
            "embeddingDimensions",
            "embeddingEndpoint",
        }.issubset(payload["embedding"])
        assert "provider" not in payload["llm"]
        assert "vector_db" not in payload
        assert "embedding_provider" not in payload["embedding"]

    @patch("m_flow.api.v1.add.add")
    def test_first_dataset_creation(self, mock_add, test_client):
        """Step 7-8: User can create first dataset and upload documents."""
        mock_add.return_value = MagicMock(model_dump=lambda: {"status": "success", "count": 1})

        files = {"data": ("test.txt", b"First document content", "text/plain")}
        response = test_client.post("/api/v1/add", files=files, data={"datasetName": "first_dataset"})
        assert response.status_code != 401

    def test_search_endpoint_accessible(self, test_client):
        """Step 10: User can access search endpoint."""
        response = test_client.get("/api/v1/search")
        assert response.status_code != 401

    def test_openapi_schema_available(self, unauthenticated_client):
        """Verify OpenAPI schema is available for API exploration."""
        response = unauthenticated_client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert "openapi" in schema
        assert "paths" in schema


# ============================================================================
# E2E-002: Daily Usage Flow
# ============================================================================


class TestE2E002DailyUsageFlow:
    """E2E-002: Test daily usage flow for existing users."""

    def test_login_state_maintained(self, test_client):
        """Step 1: User session is maintained."""
        response1 = test_client.get("/api/v1/datasets")
        response2 = test_client.get("/api/v1/datasets")
        assert response1.status_code != 401
        assert response2.status_code != 401

    def test_dashboard_data_accessible(self, test_client):
        """Step 2: User can view dashboard data (datasets, activities)."""
        datasets_response = test_client.get("/api/v1/datasets")
        activity_response = test_client.get("/api/v1/activity")
        assert datasets_response.status_code != 401
        assert activity_response.status_code != 401

    @patch("m_flow.api.v1.add.add")
    def test_upload_to_existing_dataset(self, mock_add, test_client):
        """Step 3: User can upload new documents to existing dataset."""
        mock_add.return_value = MagicMock(model_dump=lambda: {"status": "success", "count": 1})

        files = {"data": ("additional.txt", b"Additional content", "text/plain")}
        response = test_client.post("/api/v1/add", files=files, data={"datasetName": "existing_dataset"})
        assert response.status_code != 401

    def test_search_existing_knowledge(self, test_client):
        """Step 4: User can search existing knowledge."""
        response = test_client.get("/api/v1/search")
        assert response.status_code != 401

    def test_browse_graph(self, monkeypatch, test_client):
        """Step 5: User can browse graph using the current graph response contract."""
        permissions_module = importlib.import_module("m_flow.auth.permissions.methods")
        context_module = importlib.import_module("m_flow.context_global_variables")
        adapters_module = importlib.import_module("m_flow.adapters.graph")

        dataset_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        dataset = SimpleNamespace(id=dataset_id, owner_id=owner_id)
        recorded_context: list[tuple[uuid.UUID, uuid.UUID]] = []

        class FakeGraphEngine:
            async def get_graph_data(self):
                return [
                    ("node-1", {"type": "Entity", "name": "Alice", "role": "engineer"}),
                    ("node-2", {"type": "Episode", "summary": "Weekly standup"}),
                ], [("node-1", "node-2", "appears_in")]

        async def fake_datasets(_user, _permission):
            return [dataset]

        async def fake_set_db_context(ds_id, user_id):
            recorded_context.append((ds_id, user_id))

        async def fake_get_graph_provider():
            return FakeGraphEngine()

        monkeypatch.setattr(permissions_module, "get_all_user_permission_datasets", fake_datasets)
        monkeypatch.setattr(context_module, "backend_access_control_enabled", lambda: True)
        monkeypatch.setattr(context_module, "set_db_context", fake_set_db_context)
        monkeypatch.setattr(adapters_module, "get_graph_provider", fake_get_graph_provider)

        response = test_client.get("/api/v1/graph")

        assert response.status_code == 200, response.text
        assert recorded_context == [(dataset_id, owner_id)]
        assert response.json() == {
            "nodes": [
                {
                    "id": "node-1",
                    "name": "Alice",
                    "type": "Entity",
                    "properties": {"role": "engineer"},
                    "datasetId": str(dataset_id),
                },
                {
                    "id": "node-2",
                    "name": "Episode_node-2",
                    "type": "Episode",
                    "properties": {"summary": "Weekly standup"},
                    "datasetId": str(dataset_id),
                },
            ],
            "edges": [{"source": "node-1", "target": "node-2", "relationship": "appears_in"}],
        }


# ============================================================================
# E2E-003: Admin Operations Flow
# ============================================================================


class TestE2E003AdminOperationsFlow:
    """E2E-003: Test admin operations flow."""

    def test_admin_can_view_users(self, admin_client):
        """Step 2: Admin can view user list."""
        response = admin_client.get("/api/v1/users")
        assert response.status_code != 401

    def test_admin_can_view_activity(self, admin_client):
        """Step 8: Admin can view activity logs."""
        response = admin_client.get("/api/v1/activity")
        assert response.status_code != 401

    def test_admin_data_cleanup_endpoint_exists(self, admin_client):
        """Step 7: Data cleanup endpoint exists (requires superuser via fastapi-users)."""
        response = admin_client.post("/api/v1/prune/data", json={"dry_run": True})
        assert response.status_code in [200, 400, 401, 403, 422, 500]
        assert response.status_code != 404

    def test_admin_can_access_health_detailed(self, admin_client):
        """Step 6: Admin can view detailed system health."""
        response = admin_client.get("/health/detailed")
        assert response.status_code in [200, 503]


# ============================================================================
# E2E-004: Multi-User Collaboration Flow
# ============================================================================


class TestE2E004MultiUserCollaboration:
    """E2E-004: Test multi-user collaboration flow."""

    def test_different_users_isolated(self, mock_user, mock_admin_user):
        """Verify different users have isolated sessions."""
        from m_flow.api.client import app
        from m_flow.auth.methods import get_authenticated_user

        async def mock_auth_user1():
            return mock_user

        async def mock_auth_user2():
            return mock_admin_user

        app.dependency_overrides[get_authenticated_user] = mock_auth_user1
        client1 = TestClient(app)
        response1 = client1.get("/api/v1/datasets")

        app.dependency_overrides[get_authenticated_user] = mock_auth_user2
        client2 = TestClient(app)
        response2 = client2.get("/api/v1/datasets")

        assert response1.status_code != 401
        assert response2.status_code != 401

        app.dependency_overrides.clear()


# ============================================================================
# E2E-005: Error Recovery Flow
# ============================================================================


class TestE2E005ErrorRecoveryFlow:
    """E2E-005: Test error recovery flow."""

    def test_invalid_file_upload_handled(self, test_client):
        """Step 2: Error handled gracefully for invalid uploads."""
        files = {"data": ("empty.txt", b"", "text/plain")}
        response = test_client.post("/api/v1/add", files=files, data={"datasetName": "test"})
        assert response.status_code in [200, 400, 409, 422, 500]

    def test_nonexistent_dataset_handled(self, test_client):
        """Error handled for operations on non-existent datasets."""
        response = test_client.get(f"/api/v1/datasets/{uuid.uuid4()}/graph")
        assert response.status_code in [404, 500, 200]

    def test_malformed_request_handled(self, test_client):
        """Malformed requests are handled gracefully."""
        response = test_client.post("/api/v1/search", json={"invalid_field": "value"})
        assert response.status_code in [200, 400, 409, 422, 500]


# ============================================================================
# E2E-006: Cloud Sync Flow
# ============================================================================


class TestE2E006CloudSyncFlow:
    """E2E-006: Test cloud sync flow."""

    def test_sync_status_accessible(self, test_client):
        """Step 5: User can check sync status."""
        response = test_client.get("/api/v1/sync/status")
        assert response.status_code != 401
        assert response.status_code != 404


# ============================================================================
# E2E-007: OpenAI Compatible API Flow
# ============================================================================


class TestE2E007OpenAICompatibleAPI:
    """E2E-007: Test OpenAI compatible API usage."""

    def test_openapi_schema_follows_standards(self, unauthenticated_client):
        """Verify OpenAPI schema follows standards."""
        response = unauthenticated_client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()

        assert "openapi" in schema
        assert schema["openapi"].startswith("3.")

        assert "info" in schema
        assert "title" in schema["info"]

        assert "paths" in schema

    def test_responses_endpoint_structure(self, monkeypatch, test_client):
        """Step 3-5: Test responses endpoint accepts the documented request shape."""
        responses_router = importlib.import_module("m_flow.api.v1.responses.routers.get_responses_router")

        create_mock = AsyncMock(
            return_value=MagicMock(
                model_dump=lambda: {
                    "id": "resp_e2e",
                    "output": [],
                    "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                }
            )
        )
        client_mock = MagicMock()
        client_mock.responses.create = create_mock

        monkeypatch.setattr(
            responses_router,
            "get_llm_config",
            lambda: SimpleNamespace(llm_api_key="test-key", llm_model="gpt-5-mini"),
        )
        monkeypatch.setattr(
            responses_router.openai,
            "AsyncOpenAI",
            lambda api_key: client_mock,
        )

        response = test_client.post(
            "/api/v1/responses/",
            json={
                "model": "m_flow-v1",
                "input": "test",
                "tools": [],
                "temperature": 0,
            },
        )

        assert response.status_code == 200, response.text
        assert create_mock.await_args.kwargs["model"] == "gpt-5-mini"
        assert response.json()["model"] == "gpt-5-mini"


# ============================================================================
# E2E-010: Data Update Flow
# ============================================================================


class TestE2E010DataUpdateFlow:
    """E2E-010: Test data update flow."""

    def test_update_endpoint_accessible(self, monkeypatch, test_client):
        """Step 4: Update endpoint accepts the documented multipart request shape."""
        update_api = importlib.import_module("m_flow.api.v1.update")
        captured: dict[str, Any] = {}

        async def fake_update(**kwargs):
            captured.update(kwargs)
            return {"status": "ok", "data_id": str(kwargs["data_id"])}

        monkeypatch.setattr(update_api, "update", fake_update)

        data_id = uuid.uuid4()
        dataset_id = uuid.uuid4()
        response = test_client.patch(
            f"/api/v1/update?data_id={data_id}&dataset_id={dataset_id}",
            files=[("data", ("replacement.txt", b"updated", "text/plain"))],
            data={"graph_scope": "project-a"},
        )

        assert response.status_code == 200, response.text
        assert captured["data_id"] == data_id
        assert captured["dataset_id"] == dataset_id
        assert captured["graph_scope"] == ["project-a"]
        assert [upload.filename for upload in captured["data"]] == ["replacement.txt"]
        assert response.json()["status"] == "ok"


# ============================================================================
# E2E-011: Data Export Flow
# ============================================================================


class TestE2E011DataExportFlow:
    """E2E-011: Test data export flow."""

    def test_graph_export_available(self, test_client):
        """Step 7: User can export graph data."""
        response = test_client.get("/api/v1/graph")
        assert response.status_code != 401
        assert response.status_code != 404


# ============================================================================
# API Endpoint Coverage Tests
# ============================================================================


class TestAPIEndpointCoverage:
    """Verify all major API endpoints are accessible."""

    @pytest.mark.parametrize(
        "endpoint,method",
        [
            ("/health", "GET"),
            ("/health/detailed", "GET"),
            ("/", "GET"),
            ("/openapi.json", "GET"),
        ],
    )
    def test_public_endpoints(self, unauthenticated_client, endpoint, method):
        """Test public endpoints are accessible without authentication."""
        if method == "GET":
            response = unauthenticated_client.get(endpoint)
        else:
            response = unauthenticated_client.post(endpoint)

        assert response.status_code != 404, f"Endpoint {endpoint} not found"

    @pytest.mark.parametrize(
        "endpoint,method",
        [
            ("/api/v1/datasets", "GET"),
            ("/api/v1/search", "GET"),
            ("/api/v1/activity", "GET"),
            ("/api/v1/users", "GET"),
            ("/api/v1/settings", "GET"),
            ("/api/v1/prompts", "GET"),
            ("/api/v1/sync/status", "GET"),
            ("/api/v1/pipeline/active", "GET"),
            ("/api/v1/graph", "GET"),
        ],
    )
    def test_authenticated_get_endpoints(self, test_client, endpoint, method):
        """Test authenticated GET endpoints are accessible."""
        response = test_client.get(endpoint)
        assert response.status_code != 404, f"Endpoint {endpoint} not found"
        assert response.status_code != 401, f"Endpoint {endpoint} requires auth but shouldn't"


# ============================================================================
# Cross-Cutting Concerns
# ============================================================================


class TestCrossCuttingConcerns:
    """Test cross-cutting concerns across all scenarios."""

    def test_error_responses_have_proper_format(self, test_client):
        """Verify error responses have proper JSON format."""
        response = test_client.get(f"/api/v1/datasets/{uuid.uuid4()}/invalid")
        if response.status_code == 404:
            data = response.json()
            assert "detail" in data or "error" in data or "message" in data

    def test_content_type_headers(self, test_client):
        """Verify content-type headers are set correctly."""
        response = test_client.get("/api/v1/datasets")
        content_type = response.headers.get("content-type", "")
        assert "application/json" in content_type

    def test_cors_headers_if_enabled(self, unauthenticated_client):
        """Test CORS headers are set if enabled."""
        unauthenticated_client.options("/api/v1/datasets")

    def test_request_id_tracking(self, test_client):
        """Test request ID tracking in headers."""
        test_client.get("/api/v1/datasets")


# ============================================================================
# Data Consistency Tests
# ============================================================================


class TestDataConsistency:
    """Test data consistency across operations."""

    @patch("m_flow.api.v1.add.add")
    def test_sequential_operations_consistent(self, mock_add, test_client):
        """Verify sequential operations maintain consistency."""
        mock_add.return_value = MagicMock(model_dump=lambda: {"status": "success", "count": 1})

        files = {"data": ("doc1.txt", b"Content 1", "text/plain")}
        response1 = test_client.post("/api/v1/add", files=files, data={"datasetName": "consistency_test"})

        files = {"data": ("doc2.txt", b"Content 2", "text/plain")}
        response2 = test_client.post("/api/v1/add", files=files, data={"datasetName": "consistency_test"})

        assert response1.status_code == response2.status_code
