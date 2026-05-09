from __future__ import annotations

import json
import re
from typing import Any


def enrich(
    product_id: str,
    metadata: dict[str, Any],
    image_bytes: list[bytes],
    attribute_schema: list[str],
) -> str:
    """Local deterministic fake LLM for development and tests."""

    text = " ".join(str(metadata.get(key, "")) for key in ("name", "category", "description", "brand")).lower()
    values = {attribute: infer_value(text, attribute) for attribute in attribute_schema}
    return json.dumps(values, sort_keys=True)


def extract_query_attributes(query: str, available_attributes: list[str]) -> str:
    """Local fake query parser.

    It intentionally returns no hard filters by default. Production behavior
    belongs in the real LLM provider.
    """

    return json.dumps({"attributes": {}, "hard_filters": {}})


def embed(text: str, dimensions: int = 64) -> list[float]:
    from functions.defaults import embed as hashing_embed

    return hashing_embed(text, dimensions=dimensions)


def infer_value(text: str, attr: str) -> str | None:
    attr_words = tokenize(attr.replace("_", " "))
    tokens = tokenize(text)
    for word in attr_words:
        if word in tokens:
            return extract_evidence(tokens, word)
    if tokens:
        return " ".join(tokens[: min(len(tokens), 8)])
    return None


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def extract_evidence(tokens: list[str], word: str, window: int = 4) -> str:
    try:
        index = tokens.index(word)
    except ValueError:
        return word
    start = max(0, index - window)
    end = min(len(tokens), index + window + 1)
    return " ".join(tokens[start:end])
