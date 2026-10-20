from __future__ import annotations

import pytest
from pydantic import ValidationError

from medbot.config import Settings
from medbot.document_processing import ExtractedTextBlock
from medbot.errors import ApplicationError
from medbot.model_extraction import ModelExtraction, map_model_output


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_hosted_provider_requires_explicit_medbot_key(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-unrelated-ambient-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic-unrelated-ambient-key")
    with pytest.raises(ValueError, match="explicit MedBot API key"):
        Settings(ai_provider=provider).validate()


def test_keys_are_not_in_settings_representation() -> None:
    settings = Settings(
        openai_api_key="synthetic-openai-secret", gemini_api_key="synthetic-gemini-secret"
    )
    assert "synthetic-openai-secret" not in repr(settings)
    assert "synthetic-gemini-secret" not in repr(settings)


@pytest.mark.parametrize(
    "provider,url,hosted",
    [
        ("openai", "http://ollama:11434/v1", True),
        ("gemini", "http://ollama:11434/v1", True),
        ("ollama", "http://host.docker.internal:11434/v1", False),
        ("ollama", "https://remote.invalid/v1", True),
        ("fixture", "http://ollama:11434/v1", False),
    ],
)
def test_processing_context_identifies_remote_text_transfer(
    provider: str, url: str, hosted: bool
) -> None:
    assert Settings(ai_provider=provider, ollama_base_url=url).ai_hosted is hosted


@pytest.mark.parametrize(
    "model,url",
    [
        ("qwen-cloud", "http://localhost:11434/v1"),
        ("qwen", "https://ollama.com/v1"),
        ("qwen", "http://remote.invalid/v1"),
    ],
)
def test_local_provider_cannot_silently_use_cloud(model: str, url: str) -> None:
    with pytest.raises(ValueError):
        Settings(ai_provider="ollama", ai_model=model, ollama_base_url=url).validate()


def test_source_metadata_comes_from_backend_and_model_cannot_skip_review() -> None:
    block = ExtractedTextBlock("block", "doc", 2, 0, "DOB: 1970-01-02", "a" * 64, 0, 15)
    output = ModelExtraction.model_validate(
        {
            "fields": [
                {
                    "path": "patient.date_of_birth",
                    "value": "1970-01-02",
                    "source_index": 0,
                    "quote": block.text,
                    "confidence": 1,
                }
            ]
        }
    )
    mapped = map_model_output(output, (block,))
    assert mapped.requires_review
    assert mapped.fields[0].evidence[0].document_id == "doc"
    assert mapped.fields[0].evidence[0].page_number == 2
    assert mapped.fields[0].evidence[0].text_sha256 == block.text_sha256
    with pytest.raises(ApplicationError, match="unknown source"):
        map_model_output(output, ())
    with pytest.raises(ValidationError):
        ModelExtraction.model_validate({"fields": [], "route": "attacker_queue"})
