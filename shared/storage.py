from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from shared.models import ApiKey, CollectionConfig, Customer, IngestionMessage, Operation, ProductRecord, ProductStatus, Session, UsageCounter


class JsonStore:
    """Small local persistence layer that mirrors the DynamoDB/OpenSearch split.

    It intentionally stores records and vector documents in separate JSON files so
    local development exercises the same write path as production: product state
    plus searchable vector/index state.
    """

    def __init__(self, root: str | Path = "data") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.records_path = self.root / "product_records.json"
        self.index_path = self.root / "vector_index.json"
        self.queue_path = self.root / "queue.jsonl"
        self.collections_path = self.root / "collections.json"
        self.customers_path = self.root / "customers.json"
        self.sessions_path = self.root / "sessions.json"
        self.api_keys_path = self.root / "api_keys.json"
        self.usage_path = self.root / "usage_counters.json"

    def enqueue(self, message: IngestionMessage) -> None:
        payload = asdict(message)
        payload["operation"] = message.operation.value
        with self.queue_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def drain_queue(self) -> list[IngestionMessage]:
        if not self.queue_path.exists():
            return []
        lines = self.queue_path.read_text(encoding="utf-8").splitlines()
        self.queue_path.write_text("", encoding="utf-8")
        return [self._message_from_dict(json.loads(line)) for line in lines if line.strip()]

    def ack_message(self, message: IngestionMessage) -> None:
        return None

    def get_record(self, collection_id: str, product_id: str) -> ProductRecord | None:
        return self._records().get(self._key(collection_id, product_id))

    def upsert_record(self, record: ProductRecord) -> None:
        records = self._records()
        records[record.document_id] = record
        self._write_records(records)

    def delete_record(self, collection_id: str, product_id: str) -> None:
        key = self._key(collection_id, product_id)
        records = self._records()
        existing = records.get(key)
        if existing:
            existing.status = ProductStatus.DELETED
            existing.embedding = []
            existing.enriched_text = ""
            records[key] = existing
            self._write_records(records)
        index = self._index()
        index.pop(key, None)
        self._write_index(index)

    def upsert_index(self, record: ProductRecord) -> None:
        index = self._index()
        index[record.document_id] = {
            "collection_id": record.collection_id,
            "customer_id": record.customer_id,
            "product_id": record.product_id,
            "enriched_text": record.enriched_text,
            "embedding": record.embedding,
            "attrs": record.enriched_attrs,
            "image_urls": record.image_urls,
            "category": record.category,
            "price": record.price,
            "currency": record.currency,
            "brand": record.brand,
            "name": record.raw_metadata.get("name"),
        }
        self._write_index(index)

    def records_for_collection(self, collection_id: str) -> Iterable[ProductRecord]:
        for record in self._records().values():
            if record.collection_id == collection_id and record.status != ProductStatus.DELETED:
                yield record

    def indexed_documents(self, collection_id: str) -> list[dict]:
        return [
            document
            for document in self._index().values()
            if document.get("collection_id") == collection_id
        ]

    def upsert_collection_config(self, config: CollectionConfig) -> None:
        configs = self._collection_configs()
        configs[config.collection_id] = config
        payload = {key: asdict(value) for key, value in configs.items()}
        self.collections_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def get_collection_config(self, collection_id: str) -> CollectionConfig | None:
        return self._collection_configs().get(collection_id)

    def get_customer_by_email(self, email: str) -> Customer | None:
        normalized = email.strip().lower()
        for customer in self._customers().values():
            if customer.email == normalized:
                return customer
        return None

    def get_customer(self, customer_id: str) -> Customer | None:
        return self._customers().get(customer_id)

    def upsert_customer(self, customer: Customer) -> None:
        customers = self._customers()
        customers[customer.customer_id] = customer
        self.customers_path.write_text(
            json.dumps({key: asdict(value) for key, value in customers.items()}, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def upsert_session(self, session: Session) -> None:
        sessions = self._sessions()
        sessions[session.session_id] = session
        self.sessions_path.write_text(
            json.dumps({key: asdict(value) for key, value in sessions.items()}, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def get_session_by_refresh_hash(self, refresh_token_hash: str) -> Session | None:
        for session in self._sessions().values():
            if session.refresh_token_hash == refresh_token_hash:
                return session
        return None

    def upsert_api_key(self, api_key: ApiKey) -> None:
        api_keys = self._api_keys()
        api_keys[api_key.api_key_id] = api_key
        self.api_keys_path.write_text(
            json.dumps({key: asdict(value) for key, value in api_keys.items()}, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def get_api_key_by_hash(self, api_key_hash: str) -> ApiKey | None:
        for api_key in self._api_keys().values():
            if api_key.api_key_hash == api_key_hash:
                return api_key
        return None

    def api_keys_for_customer(self, customer_id: str) -> list[ApiKey]:
        return [
            api_key
            for api_key in self._api_keys().values()
            if api_key.customer_id == customer_id
        ]

    def get_usage_counter(self, customer_id: str, period: str) -> UsageCounter:
        usage_key = f"{customer_id}#{period}"
        return self._usage_counters().get(
            usage_key,
            UsageCounter(usage_key=usage_key, customer_id=customer_id, period=period),
        )

    def upsert_usage_counter(self, counter: UsageCounter) -> None:
        counters = self._usage_counters()
        counters[counter.usage_key] = counter
        self.usage_path.write_text(
            json.dumps({key: asdict(value) for key, value in counters.items()}, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _records(self) -> dict[str, ProductRecord]:
        if not self.records_path.exists():
            return {}
        data = json.loads(self.records_path.read_text(encoding="utf-8") or "{}")
        return {key: self._record_from_dict(value) for key, value in data.items()}

    def _index(self) -> dict[str, dict]:
        if not self.index_path.exists():
            return {}
        return json.loads(self.index_path.read_text(encoding="utf-8") or "{}")

    def _collection_configs(self) -> dict[str, CollectionConfig]:
        if not self.collections_path.exists():
            return {}
        data = json.loads(self.collections_path.read_text(encoding="utf-8") or "{}")
        return {key: CollectionConfig(**value) for key, value in data.items()}

    def _customers(self) -> dict[str, Customer]:
        if not self.customers_path.exists():
            return {}
        data = json.loads(self.customers_path.read_text(encoding="utf-8") or "{}")
        return {key: Customer(**value) for key, value in data.items()}

    def _sessions(self) -> dict[str, Session]:
        if not self.sessions_path.exists():
            return {}
        data = json.loads(self.sessions_path.read_text(encoding="utf-8") or "{}")
        return {key: Session(**value) for key, value in data.items()}

    def _api_keys(self) -> dict[str, ApiKey]:
        if not self.api_keys_path.exists():
            return {}
        data = json.loads(self.api_keys_path.read_text(encoding="utf-8") or "{}")
        return {key: ApiKey(**value) for key, value in data.items()}

    def _usage_counters(self) -> dict[str, UsageCounter]:
        if not self.usage_path.exists():
            return {}
        data = json.loads(self.usage_path.read_text(encoding="utf-8") or "{}")
        return {key: UsageCounter(**value) for key, value in data.items()}

    def _write_records(self, records: dict[str, ProductRecord]) -> None:
        payload = {}
        for key, record in records.items():
            item = asdict(record)
            item["status"] = record.status.value
            payload[key] = item
        self.records_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _write_index(self, index: dict[str, dict]) -> None:
        self.index_path.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")

    def _record_from_dict(self, data: dict) -> ProductRecord:
        data = dict(data)
        data["status"] = ProductStatus(data.get("status", ProductStatus.QUEUED))
        return ProductRecord(**data)

    def _message_from_dict(self, data: dict) -> IngestionMessage:
        data = dict(data)
        data["operation"] = Operation(data["operation"])
        return IngestionMessage(**data)

    def _key(self, collection_id: str, product_id: str) -> str:
        return f"{collection_id}#{product_id}"
