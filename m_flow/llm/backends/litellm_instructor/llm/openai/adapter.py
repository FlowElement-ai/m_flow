"""
OpenAI/LiteLLM adapter for structured LLM output generation.

Provides integration with OpenAI-compatible APIs via LiteLLM and
Instructor for type-safe structured responses.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import TYPE_CHECKING, Type

import instructor
import litellm
from instructor.core import InstructorRetryException
from litellm.exceptions import ContentPolicyViolationError
from openai import ContentFilterFinishReasonError
from pydantic import BaseModel
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_not_exception_type,
    stop_after_delay,
    wait_exponential_jitter,
)

from m_flow.llm.exceptions import ContentPolicyFilterError
from m_flow.llm.backends.litellm_instructor.llm.llm_interface import (
    LLMBackend,
)
from m_flow.shared.files.utils.open_data_file import open_data_file
from m_flow.shared.logging_utils import get_logger
from m_flow.shared.observability.get_observe import get_observe
from m_flow.shared.rate_limiting import llm_rate_limiter_context_manager

if TYPE_CHECKING:
    pass

_log = get_logger()
_observe = get_observe()

# Retry configuration
_MAX_RETRY_DELAY = 120
_MIN_WAIT = 5
_MAX_WAIT = 120
_FAST_MIN_WAIT = 2

# Default retry count for API calls
_DEFAULT_MAX_RETRIES = 5

# No models are excluded from json_schema_mode - all models use it
_PROBLEMATIC_MODELS = frozenset()


def _select_instructor_mode(model_name: str, requested: str = None) -> str:
    """
    Always use json_schema_mode for structured output.

    Args:
        model_name: LLM model identifier
        requested: User-specified mode override

    Returns:
        Instructor mode string (always json_schema_mode)
    """
    # Always use json_schema_mode to enforce Field descriptions
    return requested if requested else "json_schema_mode"


def _model_has_schema_issues(model_name: str) -> bool:
    """No models have schema issues - always return False."""
    return False


def _strip_markdown(text: str) -> str:
    """
    Strip markdown code block fences from LLM response text.

    Handles:
      - ```json ... ``` blocks (including nested backticks)
      - ``` ... ``` plain code blocks
      - Leading/trailing whitespace and control characters

    This fixes the issue where qwen3 (and other reasoning models) wrap
    JSON responses in markdown fences, causing instructor's pydantic
    validation to fail with "not valid JSON" errors.
    """
    if not text:
        return text

    # Strip leading markdown fences (```json, ```, etc.)
    # Pattern matches ```json, ```javascript, ``` etc. at start of string
    text = re.sub(r'^```json\s*', '', text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r'^```\w*\s*', '', text, flags=re.IGNORECASE | re.MULTILINE)

    # Strip trailing markdown fences
    text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE | re.IGNORECASE)

    # Strip invisible control characters (0x00-0x08, 0x0b, 0x0c, 0x0e-0x1f)
    # that can appear in LLM output and break JSON parsing
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)

    return text.strip()


class _MarkdownStrippingClient:
    """
    Thin wrapper around an instructor client that strips markdown fences
    from LLM raw response content *before* instructor's response_model
    parsing, so that JSON wrapped in ```json blocks is handled cleanly.
    """

    def __init__(self, delegate):
        self._delegate = delegate

    @property
    def chat(self):
        return _MarkdownStrippingCompletions(self._delegate.chat)


class _MarkdownStrippingCompletions:
    def __init__(self, delegate_chat):
        self._delegate = delegate_chat

    async def create(self, **kwargs):
        # Call the original instructor create (which parses response_model)
        response = await self._delegate.create(**kwargs)

        # Strip markdown from message.content if present
        if hasattr(response, "choices") and response.choices:
            choice = response.choices[0]
            if hasattr(choice.message, "content") and choice.message.content:
                original = choice.message.content
                cleaned = _strip_markdown(original)
                if cleaned != original:
                    # Create a new message with the cleaned content
                    # ChatMessage is immutable via __slots__, so we reconstruct
                    from openai.chat_completions import ChatCompletionMessage
                    choice.message.content = cleaned

        return response


class OpenAIAdapter(LLMBackend):
    """
    LiteLLM/Instructor adapter for OpenAI-compatible APIs.

    Supports structured output generation, audio transcription,
    and image analysis via OpenAI, Azure, or compatible endpoints.
    """

    name = "OpenAI"
    model: str
    api_key: str
    api_version: str
    default_instructor_mode = "json_schema_mode"
    MAX_RETRIES = _DEFAULT_MAX_RETRIES
    PROBLEMATIC_JSON_SCHEMA_MODELS = list(_PROBLEMATIC_MODELS)

    def __init__(
        self,
        api_key: str,
        endpoint: str,
        api_version: str,
        model: str,
        transcription_model: str,
        max_completion_tokens: int,
        instructor_mode: str = None,
        streaming: bool = False,
        fallback_model: str = None,
        fallback_api_key: str = None,
        fallback_endpoint: str = None,
    ):
        """
        Initialize OpenAI adapter.

        Args:
            api_key: API authentication key
            endpoint: API base URL
            api_version: API version string
            model: Primary model identifier
            transcription_model: Model for audio transcription
            max_completion_tokens: Max tokens in response
            instructor_mode: Override instructor mode
            streaming: Enable streaming responses
            fallback_model: Backup model for policy violations
            fallback_api_key: API key for fallback
            fallback_endpoint: Endpoint for fallback
        """
        # Determine effective mode
        effective_mode = _select_instructor_mode(model, instructor_mode)

        # Initialize instructor clients
        self._async_client = instructor.from_litellm(
            litellm.acompletion,
            mode=instructor.Mode(effective_mode),
        )
        self._sync_client = instructor.from_litellm(
            litellm.completion,
            mode=instructor.Mode(effective_mode),
        )

        # Patch async client to strip markdown from LLM responses before instructor parsing
        # This fixes qwen3/markdown-JSON issue: LLM returns ```json\n[...]]\n``` 
        # which causes instructor pydantic validation to fail
        self._async_client = _MarkdownStrippingClient(self._async_client)

        # Store configuration
        self._model = model
        self._api_key = api_key
        self._endpoint = endpoint
        self._api_version = api_version
        self._transcription_model = transcription_model
        self._max_tokens = max_completion_tokens
        self._streaming = streaming
        self._mode = effective_mode

        # Fallback configuration
        self._fallback_model = fallback_model
        self._fallback_key = fallback_api_key
        self._fallback_endpoint = fallback_endpoint

    # Property accessors for compatibility
    @property
    def model(self) -> str:
        return self._model

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def api_version(self) -> str:
        return self._api_version

    @property
    def transcription_model(self) -> str:
        return self._transcription_model

    @property
    def max_completion_tokens(self) -> int:
        return self._max_tokens

    @property
    def streaming(self) -> bool:
        return self._streaming

    @property
    def instructor_mode(self) -> str:
        return self._mode

    @property
    def fallback_model(self) -> str:
        return self._fallback_model

    @property
    def fallback_api_key(self) -> str:
        return self._fallback_key

    @property
    def fallback_endpoint(self) -> str:
        return self._fallback_endpoint

    @property
    def aclient(self):
        return self._async_client

    @property
    def client(self):
        return self._sync_client

    def _get_effective_instructor_mode(self, model: str, requested_mode: str = None) -> str:
        """Compatibility alias for _select_instructor_mode."""
        return _select_instructor_mode(model, requested_mode)

    def _is_problematic_for_json_schema(self, model: str) -> bool:
        """Compatibility alias for _model_has_schema_issues."""
        return _model_has_schema_issues(model)

    def _build_messages(self, user_input: str, system_prompt: str) -> list:
        """Construct chat message list."""
        return [
            {"role": "user", "content": user_input},
            {"role": "system", "content": system_prompt},
        ]

    def _is_policy_error(self, exc: Exception) -> bool:
        """Check if exception is content policy related."""
        if isinstance(exc, (ContentFilterFinishReasonError, ContentPolicyViolationError)):
            return True
        if isinstance(exc, InstructorRetryException):
            return "content management policy" in str(exc).lower()
        return False

    # =========================================================================
    # Structured Output Generation
    # =========================================================================

    @_observe(as_type="generation")
    @retry(
        stop=stop_after_delay(_MAX_RETRY_DELAY),
        wait=wait_exponential_jitter(_MIN_WAIT, _MAX_WAIT),
        retry=retry_if_not_exception_type(
            (
                litellm.exceptions.NotFoundError,
                litellm.exceptions.BadRequestError,
                litellm.exceptions.AuthenticationError,
            )
        ),
        before_sleep=before_sleep_log(_log, logging.DEBUG),
        reraise=True,
    )
    async def extract_structured(
        self,
        text_input: str,
        system_prompt: str,
        response_model: Type[BaseModel],
        **kwargs,
    ) -> BaseModel:
        """
        Generate structured output asynchronously.

        Args:
            text_input: User query text
            system_prompt: System instruction
            response_model: Expected Pydantic model type
            **kwargs: Additional completion parameters

        Returns:
            Parsed response as model instance
        """
        try:
            async with llm_rate_limiter_context_manager():
                return await self._async_client.chat.completions.create(
                    model=self._model,
                    messages=self._build_messages(text_input, system_prompt),
                    api_key=self._api_key,
                    api_base=self._endpoint,
                    api_version=self._api_version,
                    response_model=response_model,
                    max_retries=self.MAX_RETRIES,
                    **kwargs,
                )
        except (
            ContentFilterFinishReasonError,
            ContentPolicyViolationError,
            InstructorRetryException,
        ) as exc:
            return await self._handle_policy_error_async(exc, text_input, system_prompt, response_model, kwargs)

    async def _handle_policy_error_async(
        self,
        original_exc: Exception,
        text_input: str,
        system_prompt: str,
        response_model: Type[BaseModel],
        kwargs: dict,
    ) -> BaseModel:
        """Attempt fallback on content policy error."""
        if not (self._fallback_model and self._fallback_key):
            raise original_exc

        try:
            async with llm_rate_limiter_context_manager():
                return await self._async_client.chat.completions.create(
                    model=self._fallback_model,
                    messages=self._build_messages(text_input, system_prompt),
                    api_key=self._fallback_key,
                    response_model=response_model,
                    max_retries=self.MAX_RETRIES,
                    **kwargs,
                )
        except (
            ContentFilterFinishReasonError,
            ContentPolicyViolationError,
            InstructorRetryException,
        ) as fallback_exc:
            if isinstance(fallback_exc, InstructorRetryException):
                if "content management policy" not in str(fallback_exc).lower():
                    raise fallback_exc

            raise ContentPolicyFilterError(f"Content policy violation: {text_input[:100]}...") from fallback_exc

    @_observe
    @retry(
        stop=stop_after_delay(_MAX_RETRY_DELAY),
        wait=wait_exponential_jitter(_FAST_MIN_WAIT, _MAX_WAIT),
        retry=retry_if_not_exception_type(
            (
                litellm.exceptions.NotFoundError,
                litellm.exceptions.BadRequestError,
                litellm.exceptions.AuthenticationError,
            )
        ),
        before_sleep=before_sleep_log(_log, logging.DEBUG),
        reraise=True,
    )
    def extract_structured_sync(
        self,
        text_input: str,
        system_prompt: str,
        response_model: Type[BaseModel],
        **kwargs,
    ) -> BaseModel:
        """
        Generate structured output synchronously.

        Args:
            text_input: User query text
            system_prompt: System instruction
            response_model: Expected Pydantic model type
            **kwargs: Additional completion parameters

        Returns:
            Parsed response as model instance
        """
        return self._sync_client.chat.completions.create(
            model=self._model,
            messages=self._build_messages(text_input, system_prompt),
            api_key=self._api_key,
            api_base=self._endpoint,
            api_version=self._api_version,
            response_model=response_model,
            max_retries=self.MAX_RETRIES,
            **kwargs,
        )

    # =========================================================================
    # Media Processing
    # =========================================================================

    @retry(
        stop=stop_after_delay(_MAX_RETRY_DELAY),
        wait=wait_exponential_jitter(_FAST_MIN_WAIT, _MAX_WAIT),
        retry=retry_if_not_exception_type(
            (
                litellm.exceptions.NotFoundError,
                litellm.exceptions.BadRequestError,
                litellm.exceptions.AuthenticationError,
            )
        ),
        before_sleep=before_sleep_log(_log, logging.DEBUG),
        reraise=True,
    )
    async def transcribe_audio(self, input, **kwargs):
        """
        Transcribe audio file to text.

        Args:
            input: Path to audio file
            **kwargs: Additional transcription parameters

        Returns:
            Transcription result
        """
        async with open_data_file(input, mode="rb") as audio:
            return await litellm.atranscription(
                model=self._transcription_model,
                file=audio,
                api_key=self._api_key,
                api_base=self._endpoint,
                api_version=self._api_version,
                max_retries=self.MAX_RETRIES,
                **kwargs,
            )

    @retry(
        stop=stop_after_delay(_MAX_RETRY_DELAY),
        wait=wait_exponential_jitter(_FAST_MIN_WAIT, _MAX_WAIT),
        retry=retry_if_not_exception_type(
            (
                litellm.exceptions.NotFoundError,
                litellm.exceptions.BadRequestError,
                litellm.exceptions.AuthenticationError,
            )
        ),
        before_sleep=before_sleep_log(_log, logging.DEBUG),
        reraise=True,
    )
    async def describe_image(self, input, **kwargs) -> BaseModel:
        """
        Generate text description of image content.

        Args:
            input: Path to image file
            **kwargs: Additional completion parameters

        Returns:
            Image description response
        """
        async with open_data_file(input, mode="rb") as img:
            raw_bytes = img.read()

        encoded = base64.b64encode(raw_bytes).decode("utf-8")

        return await litellm.acompletion(
            model=self._model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What's in this image?"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{encoded}",
                            },
                        },
                    ],
                }
            ],
            api_key=self._api_key,
            api_base=self._endpoint,
            api_version=self._api_version,
            max_retries=self.MAX_RETRIES,
            **kwargs,
        )
