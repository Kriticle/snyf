from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any


DEFAULT_CACHE_TTL_SECONDS = 60 * 60 * 24 * 30


class NullSearchCache:
    def get_embedding(self, text: str, model: str) -> list[float] | None:
        return None

    def get_embeddings(self, texts: list[str], model: str) -> dict[str, list[float]]:
        return {}

    def set_embedding(self, text: str, model: str, vector: list[float]) -> None:
        return None

    def set_embeddings(self, values: dict[str, list[float]], model: str) -> None:
        return None

    def get_query_parse(self, query: str, attribute_namespace: str, model: str) -> dict[str, Any] | None:
        return None

    def set_query_parse(
        self,
        query: str,
        attribute_namespace: str,
        model: str,
        parsed: dict[str, Any],
    ) -> None:
        return None


class DynamoSearchCache(NullSearchCache):
    def __init__(
        self,
        table_name: str,
        ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        import boto3

        self.table_name = table_name
        self.ttl_seconds = ttl_seconds
        self.dynamodb = boto3.resource("dynamodb")
        self.table = self.dynamodb.Table(table_name)
        self.client = boto3.client("dynamodb")

    def get_embedding(self, text: str, model: str) -> list[float] | None:
        cached = self.get_embeddings([text], model)
        return cached.get(text)

    def get_embeddings(self, texts: list[str], model: str) -> dict[str, list[float]]:
        unique_texts = list(dict.fromkeys(text for text in texts if text))
        if not unique_texts:
            return {}
        key_to_text = {embedding_cache_key(text, model): text for text in unique_texts}
        keys = list(key_to_text)
        results: dict[str, list[float]] = {}
        try:
            for start in range(0, len(keys), 100):
                chunk = keys[start : start + 100]
                response = self.client.batch_get_item(
                    RequestItems={
                        self.table_name: {
                            "Keys": [{"cache_key": {"S": key}} for key in chunk],
                        }
                    }
                )
                for item in response.get("Responses", {}).get(self.table_name, []):
                    cache_key_value = item.get("cache_key", {}).get("S")
                    text = key_to_text.get(cache_key_value or "")
                    payload = item.get("value_json", {}).get("S")
                    if not text or not payload:
                        continue
                    parsed = json.loads(payload)
                    if isinstance(parsed, list):
                        results[text] = [float(value) for value in parsed]
        except Exception:
            return results
        return results

    def set_embedding(self, text: str, model: str, vector: list[float]) -> None:
        self.set_embeddings({text: vector}, model)

    def set_embeddings(self, values: dict[str, list[float]], model: str) -> None:
        if not values:
            return
        try:
            with self.table.batch_writer() as batch:
                for text, vector in values.items():
                    normalized_text = normalize_cache_text(text)
                    if not normalized_text or not vector:
                        continue
                    batch.put_item(
                        Item={
                            "cache_key": embedding_cache_key(text, model),
                            "kind": "embedding",
                            "model": model,
                            "normalized_text": normalized_text,
                            "value_json": json.dumps(vector, separators=(",", ":")),
                            "created_at": now_epoch_seconds(),
                            "expires_at": now_epoch_seconds() + self.ttl_seconds,
                        }
                    )
        except Exception:
            return

    def get_query_parse(self, query: str, attribute_namespace: str, model: str) -> dict[str, Any] | None:
        try:
            response = self.table.get_item(
                Key={"cache_key": query_parse_cache_key(query, attribute_namespace, model)}
            )
            item = response.get("Item")
            if not item:
                return None
            payload = item.get("value_json")
            parsed = json.loads(payload) if isinstance(payload, str) else None
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    def set_query_parse(
        self,
        query: str,
        attribute_namespace: str,
        model: str,
        parsed: dict[str, Any],
    ) -> None:
        try:
            self.table.put_item(
                Item={
                    "cache_key": query_parse_cache_key(query, attribute_namespace, model),
                    "kind": "query_parse",
                    "model": model,
                    "attribute_namespace": attribute_namespace,
                    "normalized_text": normalize_cache_text(query),
                    "value_json": json.dumps(parsed, separators=(",", ":"), sort_keys=True),
                    "created_at": now_epoch_seconds(),
                    "expires_at": now_epoch_seconds() + self.ttl_seconds,
                }
            )
        except Exception:
            return


def create_search_cache_from_environment() -> NullSearchCache:
    table_name = os.getenv("SEARCH_CACHE_TABLE_NAME")
    if not table_name:
        return NullSearchCache()
    ttl_seconds = int(os.getenv("SEARCH_CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS)))
    return DynamoSearchCache(table_name=table_name, ttl_seconds=ttl_seconds)


def embedding_cache_key(text: str, model: str) -> str:
    return cache_key("embedding", model, normalize_cache_text(text))


def query_parse_cache_key(query: str, attribute_namespace: str, model: str) -> str:
    return cache_key("query_parse", model, attribute_namespace, normalize_cache_text(query))


def attribute_namespace(attribute_names: list[str]) -> str:
    return sha256_json(sorted(attribute_names))


def cache_key(*parts: str) -> str:
    return "snyf:" + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def normalize_cache_text(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def sha256_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()


def now_epoch_seconds() -> int:
    return int(time.time())
