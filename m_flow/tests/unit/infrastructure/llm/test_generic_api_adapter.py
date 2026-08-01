"""
Unit tests for GenericAPIAdapter — the OpenAI-compatible LLM backend.

These tests verify the adapter's contract without making real network calls.
Every external dependency (litellm, instructor, rate-limiter) is mocked so
that the suite runs in < 1 s with zero side-effects.

The real ``extract_structured`` method is wrapped by a tenacity ``@retry``
decorator (120 s ceiling).  To avoid test hangs, tests that exercise
``extract_structured`` patch the retry decorator away or mock ``_call_llm``
directly so that the method either succeeds or raises on the first attempt.

References
----------
- GenericAPIAdapter source: m_flow/llm/backends/litellm_instructor/llm/generic_llm_api/adapter.py
- LLMBackend protocol:      m_flow/llm/backends/litellm_instructor/llm/llm_interface.py
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Pydantic response models used across tests
# ---------------------------------------------------------------------------
class SimpleResponse(BaseModel):
    """Minimal schema for structured output tests."""

    answer: str


class DetailedResponse(BaseModel):
    """Multi-field schema for richer extraction tests."""

    summary: str
    confidence: float
    tags: list[str]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MODULE = "m_flow.llm.backends.litellm_instructor.llm.generic_llm_api.adapter"


def _noop_rate_limiter():
    """Return a no-op async context manager that replaces the real limiter."""
    return nullcontext()


def _build_adapter(
    *,
    endpoint: str = "https://api.example.com/v1",
    api_key: str = "test-key-000",
    model: str = "gpt-4o",
    name: str = "TestAdapter",
    max_tokens: int = 4096,
    instructor_mode: str | None = None,
    fallback_model: str | None = None,
    fallback_api_key: str | None = None,
    fallback_endpoint: str | None = None,
):
    """Construct a GenericAPIAdapter with mocked instructor client."""
    with patch(f"{_MODULE}.instructor") as mock_instructor, patch(f"{_MODULE}.litellm"):
        mock_instructor.from_litellm.return_value = MagicMock()
        mock_instructor.Mode = MagicMock(side_effect=lambda x: x)

        from m_flow.llm.backends.litellm_instructor.llm.generic_llm_api.adapter import (
            GenericAPIAdapter,
        )

        adapter = GenericAPIAdapter(
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            name=name,
            max_completion_tokens=max_tokens,
            instructor_mode=instructor_mode,
            fallback_model=fallback_model,
            fallback_api_key=fallback_api_key,
            fallback_endpoint=fallback_endpoint,
        )
    return adapter


# ---------------------------------------------------------------------------
# Test: Initialisation
# ---------------------------------------------------------------------------
class TestAdapterInit:
    """Verify constructor stores configuration correctly."""

    def test_basic_fields_stored(self):
        adapter = _build_adapter()
        assert adapter.name == "TestAdapter"
        assert adapter.model == "gpt-4o"
        assert adapter.api_key == "test-key-000"
        assert adapter.endpoint == "https://api.example.com/v1"
        assert adapter.max_completion_tokens == 4096

    def test_default_instructor_mode_is_json_mode(self):
        adapter = _build_adapter()
        assert adapter.instructor_mode == "json_mode"

    def test_custom_instructor_mode_overrides_default(self):
        adapter = _build_adapter(instructor_mode="tool_call")
        assert adapter.instructor_mode == "tool_call"

    def test_fallback_fields_none_by_default(self):
        adapter = _build_adapter()
        assert adapter.fallback_model is None
        assert adapter.fallback_api_key is None
        assert adapter.fallback_endpoint is None

    def test_fallback_fields_stored_when_provided(self):
        adapter = _build_adapter(
            fallback_model="gpt-4o-mini",
            fallback_api_key="fb-key",
            fallback_endpoint="https://fallback.example.com/v1",
        )
        assert adapter.fallback_model == "gpt-4o-mini"
        assert adapter.fallback_api_key == "fb-key"
        assert adapter.fallback_endpoint == "https://fallback.example.com/v1"


# ---------------------------------------------------------------------------
# Test: Protocol compliance
# ---------------------------------------------------------------------------
class TestProtocolCompliance:
    """Ensure GenericAPIAdapter satisfies the LLMBackend protocol."""

    def test_has_extract_structured_method(self):
        adapter = _build_adapter()
        assert hasattr(adapter, "extract_structured")
        assert callable(adapter.extract_structured)

    def test_runtime_checkable_protocol(self):
        from m_flow.llm.backends.litellm_instructor.llm.llm_interface import LLMBackend

        adapter = _build_adapter()
        assert isinstance(adapter, LLMBackend)


# ---------------------------------------------------------------------------
# Test: _call_llm routing (low-level, bypasses @retry)
# ---------------------------------------------------------------------------
class TestCallLLMRouting:
    """Verify _call_llm dispatches to primary or fallback endpoint."""

    def test_primary_call_uses_primary_config(self):
        adapter = _build_adapter(
            fallback_model="fb-model",
            fallback_api_key="fb-key",
            fallback_endpoint="https://fb.example.com",
        )
        mock_create = AsyncMock(return_value=SimpleResponse(answer="ok"))
        adapter.aclient.chat.completions.create = mock_create

        asyncio.get_event_loop().run_until_complete(
            adapter._call_llm("hello", "system", SimpleResponse, use_fallback=False)
        )

        call_kwargs = mock_create.call_args
        assert call_kwargs.kwargs["model"] == "gpt-4o"
        assert call_kwargs.kwargs["api_key"] == "test-key-000"
        assert call_kwargs.kwargs["api_base"] == "https://api.example.com/v1"

    def test_fallback_call_uses_fallback_config(self):
        adapter = _build_adapter(
            fallback_model="fb-model",
            fallback_api_key="fb-key",
            fallback_endpoint="https://fb.example.com",
        )
        mock_create = AsyncMock(return_value=SimpleResponse(answer="ok"))
        adapter.aclient.chat.completions.create = mock_create

        asyncio.get_event_loop().run_until_complete(
            adapter._call_llm("hello", "system", SimpleResponse, use_fallback=True)
        )

        call_kwargs = mock_create.call_args
        assert call_kwargs.kwargs["model"] == "fb-model"
        assert call_kwargs.kwargs["api_key"] == "fb-key"
        assert call_kwargs.kwargs["api_base"] == "https://fb.example.com"

    def test_message_format_correct(self):
        adapter = _build_adapter()
        mock_create = AsyncMock(return_value=SimpleResponse(answer="ok"))
        adapter.aclient.chat.completions.create = mock_create

        asyncio.get_event_loop().run_until_complete(adapter._call_llm("user text", "sys prompt", SimpleResponse))

        call_kwargs = mock_create.call_args
        messages = call_kwargs.kwargs["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "user text"
        assert messages[1]["role"] == "system"
        assert messages[1]["content"] == "sys prompt"

    def test_response_model_passed_through(self):
        adapter = _build_adapter()
        mock_create = AsyncMock(return_value=DetailedResponse(summary="test", confidence=0.9, tags=["a"]))
        adapter.aclient.chat.completions.create = mock_create

        asyncio.get_event_loop().run_until_complete(adapter._call_llm("text", "prompt", DetailedResponse))

        call_kwargs = mock_create.call_args
        assert call_kwargs.kwargs["response_model"] is DetailedResponse


# ---------------------------------------------------------------------------
# Test: extract_structured happy path
#
# We mock _call_llm so that the @retry decorator sees immediate success
# and does not attempt real network calls or retries.
# ---------------------------------------------------------------------------
class TestExtractStructuredHappyPath:
    """Verify the main extract_structured method returns correct results."""

    @patch(f"{_MODULE}.llm_rate_limiter_context_manager", _noop_rate_limiter)
    def test_returns_validated_model(self):
        adapter = _build_adapter()
        expected = SimpleResponse(answer="42")
        adapter._call_llm = AsyncMock(return_value=expected)

        result = asyncio.get_event_loop().run_until_complete(
            adapter.extract_structured("What is 6*7?", "Be precise.", SimpleResponse)
        )

        assert isinstance(result, SimpleResponse)
        assert result.answer == "42"

    @patch(f"{_MODULE}.llm_rate_limiter_context_manager", _noop_rate_limiter)
    def test_returns_complex_model(self):
        adapter = _build_adapter()
        expected = DetailedResponse(
            summary="Test summary",
            confidence=0.95,
            tags=["math", "simple"],
        )
        adapter._call_llm = AsyncMock(return_value=expected)

        result = asyncio.get_event_loop().run_until_complete(
            adapter.extract_structured("Summarize", "Extract info", DetailedResponse)
        )

        assert isinstance(result, DetailedResponse)
        assert result.summary == "Test summary"
        assert result.confidence == 0.95
        assert result.tags == ["math", "simple"]


# ---------------------------------------------------------------------------
# Test: Content policy error handling
#
# The real extract_structured is wrapped by tenacity @retry (120 s ceiling)
# that retries all exceptions except litellm.NotFoundError.  To prevent
# test hangs, we disable the retry decorator by calling the underlying
# coroutine through __wrapped__ (tenacity stores the original function).
# ---------------------------------------------------------------------------
def _get_unwrapped_extract():
    """Return the un-retried extract_structured coroutine function."""
    from m_flow.llm.backends.litellm_instructor.llm.generic_llm_api.adapter import (
        GenericAPIAdapter,
    )

    fn = GenericAPIAdapter.extract_structured
    # tenacity stores the original function as __wrapped__
    return getattr(fn, "__wrapped__", fn)


class TestContentPolicyErrorHandling:
    """Verify fallback behaviour on content policy violations."""

    @patch(f"{_MODULE}.llm_rate_limiter_context_manager", _noop_rate_limiter)
    def test_content_policy_error_triggers_fallback(self):
        from openai import ContentFilterFinishReasonError

        adapter = _build_adapter(
            fallback_model="safe-model",
            fallback_api_key="safe-key",
            fallback_endpoint="https://safe.example.com",
        )

        primary_error = ContentFilterFinishReasonError.__new__(ContentFilterFinishReasonError)
        primary_error.args = ("content_filter",)

        call_count = 0

        async def mock_call_llm(text, sys, model, *, use_fallback=False):
            nonlocal call_count
            call_count += 1
            if not use_fallback:
                raise primary_error
            return SimpleResponse(answer="safe answer")

        adapter._call_llm = mock_call_llm
        unwrapped = _get_unwrapped_extract()

        result = asyncio.get_event_loop().run_until_complete(unwrapped(adapter, "risky text", "prompt", SimpleResponse))

        assert call_count == 2  # primary + fallback
        assert result.answer == "safe answer"

    @patch(f"{_MODULE}.llm_rate_limiter_context_manager", _noop_rate_limiter)
    def test_no_fallback_raises_content_policy_filter_error(self):
        """Without fallback config, content policy error should raise immediately."""
        from openai import ContentFilterFinishReasonError
        from m_flow.llm.exceptions import ContentPolicyFilterError

        adapter = _build_adapter()  # no fallback configured

        primary_error = ContentFilterFinishReasonError.__new__(ContentFilterFinishReasonError)
        primary_error.args = ("content_filter",)

        adapter._call_llm = AsyncMock(side_effect=primary_error)
        unwrapped = _get_unwrapped_extract()

        with pytest.raises(ContentPolicyFilterError):
            asyncio.get_event_loop().run_until_complete(unwrapped(adapter, "risky", "prompt", SimpleResponse))


# ---------------------------------------------------------------------------
# Test: _is_content_policy_error classification
# ---------------------------------------------------------------------------
class TestContentPolicyErrorClassification:
    """Verify the error classification helper."""

    def test_content_filter_finish_reason_error(self):
        from openai import ContentFilterFinishReasonError

        adapter = _build_adapter()
        err = ContentFilterFinishReasonError.__new__(ContentFilterFinishReasonError)
        err.args = ("content_filter",)

        assert adapter._is_content_policy_error(err) is True

    def test_generic_exception_not_classified(self):
        adapter = _build_adapter()
        err = ValueError("some other error")
        assert adapter._is_content_policy_error(err) is False

    def test_instructor_retry_with_policy_text(self):
        """InstructorRetryException with policy text should be classified."""
        from instructor.core import InstructorRetryException

        adapter = _build_adapter()

        # InstructorRetryException requires specific init args; use mock
        err = MagicMock(spec=InstructorRetryException)
        err.__class__ = InstructorRetryException
        err.__str__ = lambda self: "Blocked by content management policy"

        # _is_content_policy_error checks isinstance and str(err)
        assert adapter._is_content_policy_error(err) is True


# ---------------------------------------------------------------------------
# Test: Factory integration (create_llm_backend)
# ---------------------------------------------------------------------------
class TestFactoryIntegration:
    """Verify that the factory creates GenericAPIAdapter for 'custom' provider."""

    @patch("m_flow.llm.utils.get_model_max_completion_tokens", return_value=None)
    @patch("m_flow.llm.backends.litellm_instructor.llm.get_llm_client.get_llm_config")
    @patch(f"{_MODULE}.instructor")
    @patch(f"{_MODULE}.litellm")
    def test_custom_provider_creates_generic_adapter(self, mock_litellm, mock_instructor, mock_config, mock_max_tokens):
        mock_instructor.from_litellm.return_value = MagicMock()
        mock_instructor.Mode = MagicMock(side_effect=lambda x: x)

        cfg = MagicMock()
        cfg.llm_provider = "custom"
        cfg.llm_endpoint = "https://api.deepseek.com/v1"
        cfg.llm_api_key = "sk-deepseek-test"
        cfg.llm_model = "deepseek-chat"
        cfg.llm_max_completion_tokens = 4096
        cfg.llm_instructor_mode = "json_mode"
        cfg.fallback_api_key = ""
        cfg.fallback_endpoint = ""
        cfg.fallback_model = ""
        mock_config.return_value = cfg

        from m_flow.llm.backends.litellm_instructor.llm.get_llm_client import (
            create_llm_backend,
        )
        from m_flow.llm.backends.litellm_instructor.llm.generic_llm_api.adapter import (
            GenericAPIAdapter,
        )

        backend = create_llm_backend(raise_api_key_error=False)

        assert isinstance(backend, GenericAPIAdapter)
        assert backend.model == "deepseek-chat"
        assert backend.endpoint == "https://api.deepseek.com/v1"

    @pytest.mark.parametrize(
        ("endpoint", "model", "expected_endpoint", "expected_model"),
        [
            ("", "", "https://api.atlascloud.ai/v1", "deepseek-ai/deepseek-v4-pro"),
            (
                "https://atlas.example.com/v1",
                "custom/model",
                "https://atlas.example.com/v1",
                "custom/model",
            ),
        ],
    )
    @patch("m_flow.llm.utils.get_model_max_completion_tokens", return_value=None)
    @patch("m_flow.llm.backends.litellm_instructor.llm.get_llm_client.get_llm_config")
    @patch(f"{_MODULE}.instructor")
    @patch(f"{_MODULE}.litellm")
    def test_atlascloud_provider_creates_generic_adapter(
        self,
        mock_litellm,
        mock_instructor,
        mock_config,
        mock_max_tokens,
        endpoint,
        model,
        expected_endpoint,
        expected_model,
    ):
        mock_instructor.from_litellm.return_value = MagicMock()
        mock_instructor.Mode = MagicMock(side_effect=lambda value: value)

        cfg = MagicMock()
        cfg.llm_provider = "atlascloud"
        cfg.llm_endpoint = endpoint
        cfg.llm_api_key = "atlas-test-key"
        cfg.llm_model = model
        cfg.llm_max_completion_tokens = 4096
        cfg.llm_instructor_mode = "json_mode"
        cfg.fallback_api_key = ""
        cfg.fallback_endpoint = ""
        cfg.fallback_model = ""
        mock_config.return_value = cfg

        from m_flow.llm.backends.litellm_instructor.llm.get_llm_client import (
            create_llm_backend,
        )
        from m_flow.llm.backends.litellm_instructor.llm.generic_llm_api.adapter import (
            GenericAPIAdapter,
        )

        backend = create_llm_backend()

        assert isinstance(backend, GenericAPIAdapter)
        assert backend.name == "Atlas Cloud"
        assert backend.api_key == "atlas-test-key"
        assert backend.endpoint == expected_endpoint
        assert backend.model == expected_model
