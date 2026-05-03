from __future__ import annotations

import base64
import json
import os
from typing import Any

from functions.prompts import build_enrichment_prompt, build_query_parser_prompt


class OpenAILLM:
    """Production OpenAI-backed LLM adapter.

    Uses the Responses API. The OpenAI docs recommend Responses for new
    projects, and it supports both text and image inputs.
    """

    def __init__(
        self,
        enrichment_model: str | None = None,
        query_model: str | None = None,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - depends on deployment image
            raise RuntimeError("Install the openai package to use OpenAILLM.") from exc

        self.client = OpenAI()
        self.enrichment_model = enrichment_model or os.getenv("OPENAI_ENRICHMENT_MODEL", "gpt-4.1")
        self.query_model = query_model or os.getenv("OPENAI_QUERY_MODEL", "gpt-4.1-mini")
        self.embedding_model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

    def enrich(
        self,
        product_id: str,
        metadata: dict[str, Any],
        image_bytes: list[bytes],
        attribute_schema: list[str],
    ) -> str:
        prompt = build_enrichment_prompt(metadata, attribute_schema)
        content: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
        content.extend(
            {
                "type": "input_image",
                "image_url": image_data_url(image),
                "detail": "low",
            }
            for image in image_bytes
        )

        response = self.client.responses.create(
            model=self.enrichment_model,
            input=[{"role": "user", "content": content}],
            text={"format": {"type": "json_object"}},
        )
        return validate_json_object(response.output_text)

    def extract_query_attributes(self, query: str, available_attributes: list[str]) -> str:
        prompt = build_query_parser_prompt(query, available_attributes)
        response = self.client.responses.create(
            model=self.query_model,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            text={"format": {"type": "json_object"}},
        )
        return validate_json_object(response.output_text)

    def embed(self, text: str) -> list[float]:
        response = self.client.embeddings.create(model=self.embedding_model, input=text)
        return response.data[0].embedding


def image_data_url(image: bytes, mime_type: str = "image/jpeg") -> str:
    encoded = base64.b64encode(image).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def validate_json_object(text: str) -> str:
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object.")
    return json.dumps(parsed, sort_keys=True)
