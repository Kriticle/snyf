from __future__ import annotations

import json
import os
from dataclasses import asdict
from decimal import Decimal
from typing import Any, Iterable

import boto3
from boto3.dynamodb.conditions import Key

from shared.models import (
    ApiKey,
    CollectionConfig,
    Customer,
    IngestionMessage,
    Operation,
    ProductRecord,
    ProductStatus,
    Session,
    UsageCounter,
)


class AwsStore:
    """DynamoDB/SQS-backed store used by ECS runtime services."""

    def __init__(
        self,
        product_table_name: str,
        collection_table_name: str,
        customers_table_name: str,
        sessions_table_name: str,
        api_keys_table_name: str,
        usage_table_name: str,
        product_queue_url: str,
        opensearch_endpoint: str | None = None,
        opensearch_index: str | None = None,
    ) -> None:
        dynamodb = boto3.resource("dynamodb")
        self.products = dynamodb.Table(product_table_name)
        self.collections = dynamodb.Table(collection_table_name)
        self.customers = dynamodb.Table(customers_table_name)
        self.sessions = dynamodb.Table(sessions_table_name)
        self.api_keys = dynamodb.Table(api_keys_table_name)
        self.usage = dynamodb.Table(usage_table_name)
        self.sqs = boto3.client("sqs")
        self.product_queue_url = product_queue_url
        self._receipt_handles: dict[tuple[str, str, int, str], str] = {}
        self.search = create_opensearch_client(opensearch_endpoint)
        self.search_index = opensearch_index
        if self.search and self.search_index:
            ensure_index(self.search, self.search_index)

    def enqueue(self, message: IngestionMessage) -> None:
        payload = asdict(message)
        payload["operation"] = message.operation.value
        self.sqs.send_message(QueueUrl=self.product_queue_url, MessageBody=json.dumps(payload))

    def drain_queue(self) -> list[IngestionMessage]:
        response = self.sqs.receive_message(
            QueueUrl=self.product_queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=5,
        )
        messages = []
        for raw in response.get("Messages", []):
            message = self._message_from_dict(json.loads(raw["Body"]))
            self._receipt_handles[message_key(message)] = raw["ReceiptHandle"]
            messages.append(message)
        return messages

    def ack_message(self, message: IngestionMessage) -> None:
        receipt_handle = self._receipt_handles.pop(message_key(message), None)
        if receipt_handle:
            self.sqs.delete_message(
                QueueUrl=self.product_queue_url,
                ReceiptHandle=receipt_handle,
            )

    def get_record(self, collection_id: str, product_id: str) -> ProductRecord | None:
        response = self.products.get_item(Key={"collection_id": collection_id, "product_id": product_id})
        item = response.get("Item")
        return self._record_from_dict(item) if item else None

    def upsert_record(self, record: ProductRecord) -> None:
        item = asdict(record)
        item["status"] = record.status.value
        item["job_id"] = record.raw_metadata.get("job_id", "unknown")
        self.products.put_item(Item=to_dynamo_item(item))

    def delete_record(self, collection_id: str, product_id: str) -> None:
        existing = self.get_record(collection_id, product_id)
        if existing:
            existing.status = ProductStatus.DELETED
            existing.embedding = []
            existing.enriched_text = ""
            self.upsert_record(existing)
        if self.search and self.search_index:
            self.search.delete(index=self.search_index, id=self._document_id(collection_id, product_id), ignore=[404])

    def upsert_index(self, record: ProductRecord) -> None:
        self.upsert_record(record)
        if self.search and self.search_index:
            self.search.index(
                index=self.search_index,
                id=record.document_id,
                body=self._record_to_document(record),
                refresh=False,
            )

    def records_for_collection(self, collection_id: str) -> Iterable[ProductRecord]:
        response = self.products.query(KeyConditionExpression=Key("collection_id").eq(collection_id))
        for item in response.get("Items", []):
            record = self._record_from_dict(item)
            if record.status != ProductStatus.DELETED:
                yield record

    def records_for_job(self, job_id: str) -> list[ProductRecord]:
        response = self.products.query(
            IndexName="job-products",
            KeyConditionExpression=Key("job_id").eq(job_id),
        )
        return [self._record_from_dict(item) for item in response.get("Items", [])]

    def indexed_documents(self, collection_id: str) -> list[dict[str, Any]]:
        if self.search and self.search_index:
            response = self.search.search(
                index=self.search_index,
                body={
                    "size": 10000,
                    "query": {"term": {"collection_id": collection_id}},
                },
            )
            return [hit["_source"] for hit in response.get("hits", {}).get("hits", [])]

        documents = []
        for record in self.records_for_collection(collection_id):
            if record.status != ProductStatus.ENRICHED:
                continue
            documents.append(self._record_to_document(record))
        return documents

    def vector_search_documents(self, collection_id: str, query_vector: list[float], limit: int) -> list[dict[str, Any]]:
        if not self.search or not self.search_index:
            documents = self.indexed_documents(collection_id)
            documents.sort(key=lambda document: dot_product(query_vector, document.get("embedding", [])), reverse=True)
            return documents[:limit]
        response = self.search.search(
            index=self.search_index,
            body={
                "size": limit,
                "query": {
                    "knn": {
                        "embedding": {
                            "vector": query_vector,
                            "k": limit,
                            "filter": {
                                "term": {
                                    "collection_id": collection_id,
                                }
                            },
                        }
                    }
                },
            },
        )
        return [hit["_source"] for hit in response.get("hits", {}).get("hits", [])]

    def keyword_search_documents(self, collection_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        if not self.search or not self.search_index:
            return self.indexed_documents(collection_id)
        response = self.search.search(
            index=self.search_index,
            body={
                "size": limit,
                "query": {
                    "bool": {
                        "filter": [{"term": {"collection_id": collection_id}}],
                        "must": [
                            {
                                "multi_match": {
                                    "query": query,
                                    "fields": [
                                        "name^3",
                                        "category^2",
                                        "brand",
                                        "description^2",
                                    ],
                                    "type": "best_fields",
                                    "operator": "or",
                                }
                            }
                        ],
                    }
                },
            },
        )
        return [hit["_source"] for hit in response.get("hits", {}).get("hits", [])]

    def upsert_collection_config(self, config: CollectionConfig) -> None:
        self.collections.put_item(Item=to_dynamo_item(asdict(config)))

    def get_collection_config(self, collection_id: str) -> CollectionConfig | None:
        response = self.collections.get_item(Key={"collection_id": collection_id})
        item = response.get("Item")
        return CollectionConfig(**from_dynamo_item(item)) if item else None

    def get_customer_by_email(self, email: str) -> Customer | None:
        response = self.customers.query(
            IndexName="email-index",
            KeyConditionExpression=Key("email").eq(email.strip().lower()),
            Limit=1,
        )
        items = response.get("Items", [])
        return Customer(**from_dynamo_item(items[0])) if items else None

    def get_customer(self, customer_id: str) -> Customer | None:
        response = self.customers.get_item(Key={"customer_id": customer_id})
        item = response.get("Item")
        return Customer(**from_dynamo_item(item)) if item else None

    def upsert_customer(self, customer: Customer) -> None:
        self.customers.put_item(Item=to_dynamo_item(asdict(customer)))

    def upsert_session(self, session: Session) -> None:
        self.sessions.put_item(Item=to_dynamo_item(asdict(session)))

    def get_session_by_refresh_hash(self, refresh_token_hash: str) -> Session | None:
        response = self.sessions.query(
            IndexName="refresh-token-index",
            KeyConditionExpression=Key("refresh_token_hash").eq(refresh_token_hash),
            Limit=1,
        )
        items = response.get("Items", [])
        return Session(**from_dynamo_item(items[0])) if items else None

    def upsert_api_key(self, api_key: ApiKey) -> None:
        self.api_keys.put_item(Item=to_dynamo_item(asdict(api_key)))

    def get_api_key_by_hash(self, api_key_hash: str) -> ApiKey | None:
        response = self.api_keys.query(
            IndexName="api-key-hash-index",
            KeyConditionExpression=Key("api_key_hash").eq(api_key_hash),
            Limit=1,
        )
        items = response.get("Items", [])
        return ApiKey(**from_dynamo_item(items[0])) if items else None

    def api_keys_for_customer(self, customer_id: str) -> list[ApiKey]:
        response = self.api_keys.query(
            IndexName="customer-api-keys",
            KeyConditionExpression=Key("customer_id").eq(customer_id),
        )
        return [ApiKey(**from_dynamo_item(item)) for item in response.get("Items", [])]

    def get_usage_counter(self, customer_id: str, period: str) -> UsageCounter:
        usage_key = f"{customer_id}#{period}"
        response = self.usage.get_item(Key={"usage_key": usage_key})
        item = response.get("Item")
        if item:
            return UsageCounter(**from_dynamo_item(item))
        return UsageCounter(usage_key=usage_key, customer_id=customer_id, period=period)

    def upsert_usage_counter(self, counter: UsageCounter) -> None:
        self.usage.put_item(Item=to_dynamo_item(asdict(counter)))

    def _record_from_dict(self, data: dict[str, Any]) -> ProductRecord:
        item = from_dynamo_item(data)
        item.pop("job_id", None)
        item["status"] = ProductStatus(item.get("status", ProductStatus.QUEUED))
        return ProductRecord(**item)

    def _message_from_dict(self, data: dict[str, Any]) -> IngestionMessage:
        data = dict(data)
        data["operation"] = Operation(data["operation"])
        return IngestionMessage(**data)

    def _record_to_document(self, record: ProductRecord) -> dict[str, Any]:
        return {
            "collection_id": record.collection_id,
            "customer_id": record.customer_id,
            "product_id": record.product_id,
            "enriched_text": record.enriched_text,
            "embedding": record.embedding,
            "attribute_embeddings": record.attribute_embeddings,
            "attrs": record.enriched_attrs,
            "image_urls": record.image_urls,
            "category": record.category,
            "price": record.price,
            "currency": record.currency,
            "brand": record.brand,
            "name": record.raw_metadata.get("name"),
            "description": record.raw_metadata.get("description"),
        }

    def _document_id(self, collection_id: str, product_id: str) -> str:
        return f"{collection_id}#{product_id}"


def to_dynamo_item(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: to_dynamo_item(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [to_dynamo_item(item) for item in value]
    return value


def from_dynamo_item(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {key: from_dynamo_item(item) for key, item in value.items()}
    if isinstance(value, list):
        return [from_dynamo_item(item) for item in value]
    return value


def message_key(message: IngestionMessage) -> tuple[str, str, int, str]:
    return (message.collection_id, message.product_id, message.version, message.operation.value)


def create_opensearch_client(endpoint: str | None):
    if not endpoint:
        return None
    try:
        from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("opensearch-py is required when OPENSEARCH_ENDPOINT is configured.") from exc

    session = boto3.Session()
    credentials = session.get_credentials()
    region = session.region_name or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    auth = AWSV4SignerAuth(credentials, region, "es")
    host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    return OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=10,
        max_retries=2,
        retry_on_timeout=True,
    )


def ensure_index(client, index_name: str) -> None:
    if client.indices.exists(index=index_name):
        return
    client.indices.create(
        index=index_name,
        body={
            "settings": {
                "index": {
                    "knn": True,
                }
            },
            "mappings": {
                "properties": {
                    "collection_id": {"type": "keyword"},
                    "customer_id": {"type": "keyword"},
                    "product_id": {"type": "keyword"},
                    "category": {"type": "keyword"},
                    "brand": {"type": "keyword"},
                    "currency": {"type": "keyword"},
                    "price": {"type": "float"},
                    "name": {"type": "text"},
                    "enriched_text": {"type": "text"},
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": int(os.getenv("VECTOR_DIMENSION", "1536")),
                        "method": {
                            "name": "hnsw",
                            "space_type": "innerproduct",
                            "engine": "faiss",
                            "parameters": {
                                "ef_construction": 128,
                                "m": 16,
                            },
                        },
                    },
                    "attribute_embeddings": {"type": "object", "enabled": True},
                    "attrs": {"type": "object", "enabled": True},
                }
            }
        },
    )


def dot_product(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))
