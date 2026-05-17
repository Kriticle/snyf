from __future__ import annotations

import json
from typing import Any


def attribute_embedding_text(attribute: str, value: Any) -> str | None:
    normalized = normalize_attribute_value(value)
    if normalized is None:
        return None
    return f"{attribute}: {normalized}"


def normalize_attribute_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = " ".join(value.strip().split())
        return cleaned or None
    if isinstance(value, (list, tuple, set)):
        parts = [normalize_attribute_value(item) for item in value]
        cleaned_parts = [part for part in parts if part]
        return ", ".join(cleaned_parts) if cleaned_parts else None
    if isinstance(value, dict):
        cleaned = {
            str(key): normalize_attribute_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
        cleaned = {key: item for key, item in cleaned.items() if item}
        if not cleaned:
            return None
        return json.dumps(cleaned, sort_keys=True, separators=(",", ":"))
    return str(value)


def build_attribute_embedding_inputs(attrs: dict[str, Any]) -> tuple[list[str], list[str]]:
    attributes: list[str] = []
    texts: list[str] = []
    for attribute, value in attrs.items():
        text = attribute_embedding_text(attribute, value)
        if text is None:
            continue
        attributes.append(attribute)
        texts.append(text)
    return attributes, texts
