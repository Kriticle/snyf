from __future__ import annotations

import asyncio
import json
import os
import urllib.request
import logging
from typing import Awaitable, Callable, Any

from functions.defaults import enriched_text
from functions.providers import embed_fn as default_embed_fn
from functions.providers import enrich_fn as default_enrich_fn
from functions.providers import provider_name
from shared.models import IngestionMessage, Operation, ProductRecord, ProductStatus, utc_now
from shared.storage import JsonStore
from shared.store_factory import create_store

EnrichFn = Callable[[str, dict[str, Any], list[bytes], list[str]], Awaitable[str | dict[str, Any]] | str | dict[str, Any]]
EmbedFn = Callable[[str], Awaitable[list[float]] | list[float]]
logger = logging.getLogger(__name__)


class EnrichmentWorker:
    def __init__(
        self,
        store: JsonStore,
        enrich_fn: EnrichFn | None = None,
        embed_fn: EmbedFn | None = None,
    ) -> None:
        self.store = store
        self.enrich_fn = enrich_fn or default_enrich_fn()
        self.embed_fn = embed_fn or default_embed_fn()

    async def process_message(self, message: IngestionMessage) -> ProductRecord | None:
        if message.operation == Operation.DELETE:
            self.store.delete_record(message.collection_id, message.product_id)
            return None

        validate_image_urls(message.image_urls)

        existing = self.store.get_record(message.collection_id, message.product_id)
        version = (existing.version + 1) if existing else 1
        record = ProductRecord(
            customer_id=message.customer_id,
            collection_id=message.collection_id,
            product_id=message.product_id,
            raw_metadata=message.metadata,
            image_urls=message.image_urls,
            attribute_schema=message.attribute_schema,
            attribute_list_uri=message.attribute_list_uri,
            category=message.metadata.get("category"),
            price=message.metadata.get("price"),
            currency=message.metadata.get("currency"),
            brand=message.metadata.get("brand"),
            status=ProductStatus.ENRICHING,
            version=version,
        )
        self.store.upsert_record(record)

        try:
            image_bytes = await self._download_images(message.image_urls)
            validate_image_bytes(image_bytes, expected_count=len(message.image_urls))
            llm_response = await maybe_await(self.enrich_fn(
                message.product_id,
                message.metadata,
                image_bytes,
                message.attribute_schema,
            ))
            attrs = parse_enrichment_response(llm_response, message.attribute_schema)
            validate_enriched_attrs(attrs)
            text = enriched_text(message.metadata, attrs)
            embedding = await maybe_await(self.embed_fn(text))
            validate_embedding(embedding)
            record.enriched_attrs = attrs
            record.enriched_text = text
            record.embedding = embedding
            record.status = ProductStatus.ENRICHED
            record.updated_at = utc_now()
            self.store.upsert_record(record)
            self.store.upsert_index(record)
            return record
        except Exception:
            record.status = ProductStatus.FAILED
            record.updated_at = utc_now()
            self.store.upsert_record(record)
            raise

    async def drain_once(self) -> list[ProductRecord]:
        processed: list[ProductRecord] = []
        for message in self.store.drain_queue():
            try:
                record = await self.process_message(message)
            except Exception:
                logger.exception(
                    "Failed to process product %s in collection %s",
                    message.product_id,
                    message.collection_id,
                )
                continue
            self._ack_message(message)
            if record is not None:
                processed.append(record)
        return processed

    def _ack_message(self, message: IngestionMessage) -> None:
        ack = getattr(self.store, "ack_message", None)
        if ack:
            ack(message)

    async def _download_images(self, image_urls: list[str]) -> list[bytes]:
        if provider_name() != "openai":
            return [url.encode("utf-8") for url in image_urls]
        return [await asyncio.to_thread(download_image, url) for url in image_urls]


def download_image(url: str, timeout_seconds: int = 10, max_bytes: int = 10_000_000) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "fashion-enrichment-worker/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        content_type = response.headers.get("content-type", "")
        if content_type and not content_type.lower().startswith("image/"):
            raise ValueError(f"URL did not return an image content type: {url}")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"Image exceeds {max_bytes} bytes: {url}")
    return data


def run_worker_once(data_dir: str = "data") -> int:
    worker = EnrichmentWorker(create_store(data_dir))
    return len(asyncio.run(worker.drain_once()))


def parse_enrichment_response(response: str | dict[str, Any], attribute_schema: list[str]) -> dict[str, Any]:
    if isinstance(response, str):
        parsed = json.loads(response)
    else:
        parsed = response
    if not isinstance(parsed, dict):
        raise ValueError("enrich() must return a JSON object string or dict.")
    return {attribute: parsed.get(attribute) for attribute in attribute_schema}


def validate_image_urls(image_urls: list[str]) -> None:
    if not image_urls:
        raise ValueError("Product must include at least one image URL for enrichment.")
    invalid = [url for url in image_urls if not isinstance(url, str) or not url.startswith("https://")]
    if invalid:
        raise ValueError(f"Product contains invalid image URLs: {invalid}")


def validate_enriched_attrs(attrs: dict[str, Any]) -> None:
    if not attrs or not any(has_value(value) for value in attrs.values()):
        raise ValueError("enrich() returned no usable enriched attributes.")


def validate_image_bytes(image_bytes: list[bytes], expected_count: int) -> None:
    if len(image_bytes) != expected_count:
        raise ValueError("Downloaded image count does not match image URL count.")
    if not image_bytes or not all(isinstance(image, bytes) and image for image in image_bytes):
        raise ValueError("One or more product images downloaded as empty bytes.")


def validate_embedding(embedding: list[float]) -> None:
    if not isinstance(embedding, list) or not embedding:
        raise ValueError("embed() returned an empty embedding.")
    if not all(isinstance(value, (int, float)) for value in embedding):
        raise ValueError("embed() returned a non-numeric embedding.")


def has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


async def maybe_await(value):
    if hasattr(value, "__await__"):
        return await value
    return value


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    if os.getenv("PRODUCT_QUEUE_URL"):
        async def main_loop() -> None:
            worker = EnrichmentWorker(create_store())
            while True:
                await worker.drain_once()
                await asyncio.sleep(2)

        asyncio.run(main_loop())
    else:
        print(run_worker_once())
