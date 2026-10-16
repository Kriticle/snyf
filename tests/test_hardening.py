from __future__ import annotations

import asyncio
import tempfile
import unittest

from botocore.exceptions import ClientError

from services.enrichment_worker.worker import EnrichmentWorker
from services.search_api.main import DemoRateLimitError, SharedDemoRateLimiter
from shared.aws_storage import AwsStore
from shared.models import IngestionMessage, Operation, ProductRecord, ProductStatus, StaleProductVersionError
from shared.storage import JsonStore


class FakeTable:
    def __init__(self, response=None, error_code: str | None = None):
        self.response = response or {}
        self.error_code = error_code
        self.calls = []

    def update_item(self, **kwargs):
        self.calls.append(kwargs)
        if self.error_code:
            raise ClientError({"Error": {"Code": self.error_code, "Message": "conditional"}}, "UpdateItem")
        return self.response

    def put_item(self, **kwargs):
        self.calls.append(kwargs)
        if self.error_code:
            raise ClientError({"Error": {"Code": self.error_code, "Message": "conditional"}}, "PutItem")
        return self.response


class FakeSearch:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def aws_store_without_init() -> AwsStore:
    return object.__new__(AwsStore)


class ProductionHardeningTests(unittest.TestCase):
    def test_atomic_usage_reservation_uses_conditional_update(self) -> None:
        store = aws_store_without_init()
        store.usage = FakeTable(
            {
                "Attributes": {
                    "usage_key": "cust#2026-10",
                    "customer_id": "cust",
                    "period": "2026-10",
                    "search_requests": 4,
                    "ingestion_rows": 0,
                    "updated_at": "now",
                }
            }
        )

        counter = store.reserve_usage("cust", "2026-10", "search_requests", 1, 100)

        self.assertEqual(counter.search_requests, 4)
        call = store.usage.calls[0]
        self.assertIn("ADD #metric :amount", call["UpdateExpression"])
        self.assertIn("#metric <= :max_before", call["ConditionExpression"])

    def test_atomic_usage_reservation_reports_limit_race(self) -> None:
        store = aws_store_without_init()
        store.usage = FakeTable(error_code="ConditionalCheckFailedException")

        self.assertIsNone(store.reserve_usage("cust", "2026-10", "search_requests", 1, 100))

    def test_dynamo_product_write_rejects_stale_version(self) -> None:
        store = aws_store_without_init()
        store.products = FakeTable(error_code="ConditionalCheckFailedException")
        record = ProductRecord(
            customer_id="cust",
            collection_id="coll",
            product_id="prod",
            raw_metadata={},
            image_urls=[],
            attribute_schema=[],
            version=2,
        )

        with self.assertRaises(StaleProductVersionError):
            store.upsert_record(record)

        self.assertIn("#version <= :incoming_version", store.products.calls[0]["ConditionExpression"])

    def test_filtered_knn_query_supplies_opensearch_score(self) -> None:
        store = aws_store_without_init()
        store.search_index = "products"
        store.search = FakeSearch(
            [{"hits": {"hits": [{"_score": 0.91, "_source": {"product_id": "prod"}}]}}]
        )

        documents = store.vector_search_documents("coll", [0.1, 0.2], 5)

        query = store.search.calls[0]["body"]["query"]["knn"]["embedding"]
        self.assertEqual(query["filter"], {"term": {"collection_id": "coll"}})
        self.assertEqual(documents[0]["_retrieval_score"], 0.91)

    def test_opensearch_collection_read_is_paginated(self) -> None:
        store = aws_store_without_init()
        store.search_index = "products"
        store.search = FakeSearch([{"hits": {"hits": [{"_source": {"product_id": "prod"}, "sort": ["prod"]}]}}])

        documents = store.indexed_documents("coll")

        self.assertEqual(documents, [{"product_id": "prod"}])
        body = store.search.calls[0]["body"]
        self.assertEqual(body["size"], 500)
        self.assertEqual(body["sort"], [{"product_id": "asc"}])

    def test_stale_local_worker_message_cannot_overwrite_newer_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.upsert_record(
                ProductRecord(
                    customer_id="cust",
                    collection_id="coll",
                    product_id="prod",
                    raw_metadata={},
                    image_urls=["https://cdn.example.com/prod.jpg"],
                    attribute_schema=["style"],
                    version=3,
                )
            )
            message = IngestionMessage(
                customer_id="cust",
                collection_id="coll",
                product_id="prod",
                image_urls=["https://cdn.example.com/prod.jpg"],
                metadata={},
                attribute_schema=["style"],
                operation=Operation.UPSERT,
                version=2,
            )

            with self.assertRaises(StaleProductVersionError):
                asyncio.run(EnrichmentWorker(store).process_message(message))

            self.assertEqual(store.get_record("coll", "prod").version, 3)

    def test_worker_persists_retry_metadata_and_job_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            message = IngestionMessage(
                customer_id="cust",
                collection_id="coll",
                product_id="prod",
                image_urls=["https://cdn.example.com/prod.jpg"],
                metadata={"job_id": "job"},
                attribute_schema=["style"],
                operation=Operation.UPSERT,
                version=1,
            )
            worker = EnrichmentWorker(store, enrich_fn=lambda *args: {})

            with self.assertRaises(ValueError):
                asyncio.run(worker.process_message(message))

            record = store.get_record("coll", "prod")
            self.assertEqual(record.status, ProductStatus.FAILED)
            self.assertEqual(record.retry_count, 1)
            self.assertEqual(record.last_error_code, "ValueError")
            self.assertEqual(len(store.job_errors_for_job("job")), 1)

    def test_shared_demo_rate_limit_uses_store_counter(self) -> None:
        class RateStore:
            def __init__(self):
                self.allowed = [True, False]

            def reserve_demo_request(self, **kwargs):
                return self.allowed.pop(0)

        limiter = SharedDemoRateLimiter(RateStore(), limit=1)
        limiter.check("client")
        with self.assertRaises(DemoRateLimitError):
            limiter.check("client")


if __name__ == "__main__":
    unittest.main()
