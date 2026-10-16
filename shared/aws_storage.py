from __future__ import annotations

import json
import os
from dataclasses import asdict
from decimal import Decimal
from typing import Any, Iterable

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.models import (
    ApiKey,
    CollectionConfig,
    Customer,
    IngestionMessage,
    Operation,
    ProductRecord,
    ProductStatus,
    Session,
    StaleProductVersionError,
    UsageCounter,
    utc_now,
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
        job_errors_table_name: str | None = None,
        rate_limits_table_name: str | None = None,
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
        self.job_errors = dynamodb.Table(job_errors_table_name) if job_errors_table_name else None
        self.rate_limits = dynamodb.Table(rate_limits_table_name) if rate_limits_table_name else None
        self.sqs = boto3.client("sqs")
        self.product_queue_url = product_queue_url
        self._receipt_handles: dict[tuple[str, str, int, str], str] = {}
        self._receive_counts: dict[tuple[str, str, int, str], int] = {}
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
            AttributeNames=["ApproximateReceiveCount"],
        )
        messages = []
        for raw in response.get("Messages", []):
            message = self._message_from_dict(json.loads(raw["Body"]))
            key = message_key(message)
            self._receipt_handles[key] = raw["ReceiptHandle"]
            self._receive_counts[key] = int(raw.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
            messages.append(message)
        return messages

    def ack_message(self, message: IngestionMessage) -> None:
        key = message_key(message)
        receipt_handle = self._receipt_handles.pop(key, None)
        self._receive_counts.pop(key, None)
        if receipt_handle:
            self.sqs.delete_message(
                QueueUrl=self.product_queue_url,
                ReceiptHandle=receipt_handle,
            )

    def extend_message_visibility(self, message: IngestionMessage, timeout_seconds: int) -> None:
        receipt_handle = self._receipt_handles.get(message_key(message))
        if receipt_handle:
            self.sqs.change_message_visibility(
                QueueUrl=self.product_queue_url,
                ReceiptHandle=receipt_handle,
                VisibilityTimeout=max(0, min(timeout_seconds, 43200)),
            )

    def message_receive_count(self, message: IngestionMessage) -> int:
        return self._receive_counts.get(message_key(message), 1)

    def record_job_error(self, message: IngestionMessage, record: ProductRecord, exc: Exception) -> None:
        if self.job_errors is None:
            return
        attempt = self.message_receive_count(message)
        self.job_errors.put_item(
            Item=to_dynamo_item(
                {
                    "job_id": message.metadata.get("job_id", "unknown"),
                    "error_id": f"{message.product_id}#v{message.version}#attempt{attempt}",
                    "customer_id": message.customer_id,
                    "collection_id": message.collection_id,
                    "product_id": message.product_id,
                    "product_version": message.version,
                    "attempt": attempt,
                    "error_code": type(exc).__name__,
                    "error_message": str(exc)[:500],
                    "created_at": utc_now(),
                }
            )
        )

    def job_errors_for_job(self, job_id: str) -> list[dict[str, Any]]:
        if self.job_errors is None:
            return []
        items = []
        kwargs = {"KeyConditionExpression": Key("job_id").eq(job_id)}
        while True:
            response = self.job_errors.query(**kwargs)
            items.extend(from_dynamo_item(item) for item in response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                return items
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def reserve_demo_request(self, client_id: str, window_start: int, limit: int, ttl: int) -> bool:
        if self.rate_limits is None:
            return False
        try:
            self.rate_limits.update_item(
                Key={"rate_limit_key": f"{client_id}#{window_start}"},
                UpdateExpression="SET expires_at = :expires_at ADD request_count :one",
                ConditionExpression="attribute_not_exists(request_count) OR request_count < :limit",
                ExpressionAttributeValues={":expires_at": ttl, ":one": 1, ":limit": limit},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def get_record(self, collection_id: str, product_id: str) -> ProductRecord | None:
        response = self.products.get_item(Key={"collection_id": collection_id, "product_id": product_id})
        item = response.get("Item")
        return self._record_from_dict(item) if item else None

    def upsert_record(self, record: ProductRecord) -> None:
        item = asdict(record)
        item["status"] = record.status.value
        item["job_id"] = record.raw_metadata.get("job_id", "unknown")
        try:
            self.products.put_item(
                Item=to_dynamo_item(item),
                ConditionExpression="attribute_not_exists(#version) OR #version <= :incoming_version",
                ExpressionAttributeNames={"#version": "version"},
                ExpressionAttributeValues={":incoming_version": record.version},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise StaleProductVersionError(
                    f"Product {record.document_id} has a newer stored version."
                ) from exc
            raise

    def delete_record(self, collection_id: str, product_id: str, version: int | None = None) -> None:
        existing = self.get_record(collection_id, product_id)
        if existing:
            if version is not None and existing.version > version:
                raise StaleProductVersionError(
                    f"Product {collection_id}#{product_id} is already at version {existing.version}."
                )
            existing.status = ProductStatus.DELETED
            existing.embedding = []
            existing.enriched_text = ""
            if version is not None:
                existing.version = version
            self.upsert_record(existing)
        if self.search and self.search_index:
            kwargs = {
                "index": self.search_index,
                "id": self._document_id(collection_id, product_id),
                "ignore": [404, 409],
            }
            if version is not None:
                kwargs.update({"version": version, "version_type": "external_gte"})
            self.search.delete(**kwargs)

    def upsert_index(self, record: ProductRecord) -> None:
        self.upsert_record(record)
        if self.search and self.search_index:
            self.search.index(
                index=self.search_index,
                id=record.document_id,
                body=self._record_to_document(record),
                version=record.version,
                version_type="external_gte",
                refresh=False,
            )

    def records_for_collection(self, collection_id: str) -> Iterable[ProductRecord]:
        kwargs = {"KeyConditionExpression": Key("collection_id").eq(collection_id)}
        while True:
            response = self.products.query(**kwargs)
            for item in response.get("Items", []):
                record = self._record_from_dict(item)
                if record.status != ProductStatus.DELETED:
                    yield record
            if "LastEvaluatedKey" not in response:
                break
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def records_for_job(self, job_id: str) -> list[ProductRecord]:
        records = []
        kwargs = {
            "IndexName": "job-products",
            "KeyConditionExpression": Key("job_id").eq(job_id),
        }
        while True:
            response = self.products.query(**kwargs)
            records.extend(self._record_from_dict(item) for item in response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                return records
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def prepare_retry(self, record: ProductRecord) -> ProductRecord | None:
        try:
            response = self.products.update_item(
                Key={"collection_id": record.collection_id, "product_id": record.product_id},
                UpdateExpression=(
                    "SET #version = :next_version, #status = :queued, updated_at = :updated_at "
                    "REMOVE last_error_code, last_error_message, last_error_at"
                ),
                ConditionExpression="#version = :current_version AND #status = :failed",
                ExpressionAttributeNames={"#version": "version", "#status": "status"},
                ExpressionAttributeValues={
                    ":next_version": record.version + 1,
                    ":current_version": record.version,
                    ":queued": ProductStatus.QUEUED.value,
                    ":failed": ProductStatus.FAILED.value,
                    ":updated_at": utc_now(),
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return None
            raise
        return self._record_from_dict(response["Attributes"])

    def indexed_documents(self, collection_id: str) -> list[dict[str, Any]]:
        if self.search and self.search_index:
            documents = []
            search_after = None
            while True:
                body = {
                    "size": 500,
                    "query": {"term": {"collection_id": collection_id}},
                    "sort": [{"product_id": "asc"}],
                }
                if search_after is not None:
                    body["search_after"] = search_after
                response = self.search.search(index=self.search_index, body=body)
                hits = response.get("hits", {}).get("hits", [])
                documents.extend(hit["_source"] for hit in hits)
                if len(hits) < body["size"]:
                    return documents
                search_after = hits[-1]["sort"]

        documents = []
        for record in self.records_for_collection(collection_id):
            if record.status != ProductStatus.ENRICHED:
                continue
            documents.append(self._record_to_document(record))
        return documents

    def vector_search_documents(self, collection_id: str, query_vector: list[float], limit: int) -> list[dict[str, Any]]:
        if not self.search or not self.search_index:
            raise RuntimeError("OpenSearch is required for vector retrieval in the AWS store.")
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
        documents = []
        for hit in response.get("hits", {}).get("hits", []):
            document = dict(hit["_source"])
            document["_retrieval_score"] = float(hit.get("_score", 0.0))
            documents.append(document)
        return documents

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
        if items:
            self._remove_legacy_api_key_value(items[0])
        return api_key_from_item(items[0]) if items else None

    def api_keys_for_customer(self, customer_id: str) -> list[ApiKey]:
        items = []
        kwargs = {
            "IndexName": "customer-api-keys",
            "KeyConditionExpression": Key("customer_id").eq(customer_id),
        }
        while True:
            response = self.api_keys.query(**kwargs)
            items.extend(response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                break
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        for item in items:
            self._remove_legacy_api_key_value(item)
        return [api_key_from_item(item) for item in items]

    def _remove_legacy_api_key_value(self, item: dict[str, Any]) -> None:
        if "api_key_value" not in item:
            return
        self.api_keys.update_item(
            Key={"api_key_id": item["api_key_id"]},
            UpdateExpression="REMOVE api_key_value",
        )

    def get_usage_counter(self, customer_id: str, period: str) -> UsageCounter:
        usage_key = f"{customer_id}#{period}"
        response = self.usage.get_item(Key={"usage_key": usage_key})
        item = response.get("Item")
        if item:
            return UsageCounter(**from_dynamo_item(item))
        return UsageCounter(usage_key=usage_key, customer_id=customer_id, period=period)

    def upsert_usage_counter(self, counter: UsageCounter) -> None:
        self.usage.put_item(Item=to_dynamo_item(asdict(counter)))

    def reserve_usage(
        self,
        customer_id: str,
        period: str,
        metric: str,
        amount: int,
        limit: int,
    ) -> UsageCounter | None:
        if metric not in {"search_requests", "ingestion_rows"}:
            raise ValueError(f"Unsupported usage metric: {metric}")
        usage_key = f"{customer_id}#{period}"
        try:
            response = self.usage.update_item(
                Key={"usage_key": usage_key},
                UpdateExpression=(
                    "SET customer_id = if_not_exists(customer_id, :customer_id), "
                    "#period = if_not_exists(#period, :period), updated_at = :updated_at "
                    "ADD #metric :amount"
                ),
                ConditionExpression="attribute_not_exists(#metric) OR #metric <= :max_before",
                ExpressionAttributeNames={"#metric": metric, "#period": "period"},
                ExpressionAttributeValues=to_dynamo_item(
                    {
                        ":customer_id": customer_id,
                        ":period": period,
                        ":updated_at": utc_now(),
                        ":amount": amount,
                        ":max_before": limit - amount,
                    }
                ),
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return None
            raise
        return UsageCounter(**from_dynamo_item(response["Attributes"]))

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


def api_key_from_item(item: dict[str, Any]) -> ApiKey:
    value = from_dynamo_item(item)
    value.pop("api_key_value", None)
    return ApiKey(**value)


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
