from __future__ import annotations

import hashlib
import re


def embed(text: str, dimensions: int = 64) -> list[float]:
    """Hashing-vector embedder for local semantic retrieval.

    It is not a model replacement; it gives a stable vector shape and reasonable
    lexical similarity so the retrieval and re-ranking pipeline can be exercised.
    """

    vector = [0.0] * dimensions
    for token in tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign
    return vector


def enriched_text(metadata: dict[str, Any], attrs: dict[str, Any]) -> str:
    parts = [
        str(metadata.get("name", "")),
        str(metadata.get("category", "")),
        str(metadata.get("description", "")),
    ]
    parts.extend(f"{key}: {value}" for key, value in attrs.items() if value is not None)
    return ", ".join(part for part in parts if part)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())

