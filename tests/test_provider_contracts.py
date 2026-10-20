"""Exercise real provider SDKs over in-memory HTTP; never send keys/documents externally."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx2
import pytest
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider

from medbot.config import Settings
from medbot.document_processing import ExtractedTextBlock
from medbot.model_extraction import StructuredAIProvider


@pytest.mark.parametrize("provider", ["ollama", "openai", "gemini"])
def test_provider_native_schema_and_explicit_credentials(provider: str) -> None:
    requests: list[httpx2.Request] = []
    output = json.dumps(
        {
            "fields": [
                {
                    "path": "patient.date_of_birth",
                    "value": "1970-01-02",
                    "source_index": 0,
                    "quote": "DOB: 1970-01-02",
                    "confidence": 0.99,
                }
            ]
        }
    )

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if provider == "gemini":
            body = {
                "candidates": [
                    {
                        "content": {"parts": [{"text": output}], "role": "model"},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 30},
                "modelVersion": "gemini-2.5-flash",
            }
        else:
            body = {
                "id": "synthetic-completion",
                "object": "chat.completion",
                "created": 1,
                "model": "synthetic-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": output},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 30, "total_tokens": 40},
            }
        return httpx2.Response(200, json=body)

    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    if provider == "gemini":
        model = GoogleModel(
            "gemini-2.5-flash",
            provider=GoogleProvider(api_key="synthetic-gemini-key", http_client=http_client),
        )
    elif provider == "openai":
        model = OpenAIChatModel(
            "gpt-4.1-mini",
            provider=OpenAIProvider(api_key="synthetic-openai-key", http_client=http_client),
            settings=OpenAIChatModelSettings(openai_store=False),
        )
    else:
        model = OllamaModel(
            "qwen3:4b",
            provider=OllamaProvider(
                base_url="http://localhost:11434/v1",
                api_key="synthetic-local-key",
                http_client=http_client,
            ),
        )
    block = ExtractedTextBlock(
        "opaque-block", "opaque-doc", 2, 0, "DOB: 1970-01-02", "a" * 64, 0, 15
    )
    with patch("medbot.model_extraction.configured_model", return_value=model):
        result = StructuredAIProvider(Settings(ai_provider=provider)).extract((block,))
    assert len(requests) == 1
    request = requests[0]
    payload = json.loads(request.content)
    if provider == "gemini":
        assert request.headers["x-goog-api-key"] == "synthetic-gemini-key"
        assert payload["generationConfig"]["responseMimeType"] == "application/json"
        assert "tools" not in payload
    else:
        assert (
            request.headers["authorization"]
            == f"Bearer synthetic-{'openai' if provider == 'openai' else 'local'}-key"
        )
        assert payload["response_format"]["type"] == "json_schema"
        assert "tools" not in payload
        if provider == "ollama":
            assert payload.get("reasoning_effort") == "none"
        if provider == "openai":
            assert payload["store"] is False
    assert result.requires_review
    assert result.fields[0].evidence[0].block_id == block.id
    assert result.fields[0].evidence[0].page_number == 2
