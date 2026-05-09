from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from services.enrichment_worker.worker import EnrichmentWorker
from services.enrichment_worker.worker import parse_enrichment_response
from services.ingestion_api.main import create_app
from services.search_api.main import create_app as create_search_app
from shared.attribute_loader import load_attribute_list
from shared.auth import AuthError, AuthService
from shared.csv_ingestion import CsvIngestionService
from shared.models import CollectionConfig, SearchRequest
from shared.plans import PlanLimitError, UsageService
from shared.search import SearchService
from shared.storage import JsonStore


class PlatformFlowTests(unittest.TestCase):
    def write_attribute_file(self, directory: str, payload: str | None = None) -> str:
        attr_file = Path(directory) / "attributes.json"
        attr_file.write_text(
            payload or '{"attributes":["occasion","silhouette","fabric_feel","trend_vibe","print_energy","style_mood"]}',
            encoding="utf-8",
        )
        return str(attr_file)

    def test_csv_ingestion_processes_valid_rows_and_keeps_row_errors_local(self) -> None:
        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,coll_a,Linen Trouser,bottoms,https://cdn.example.com/p1.jpg,"[""occasion"",""silhouette"",""fabric_feel"",""color""]","Cream linen wide leg holiday trouser",89
,coll_a,Broken Row,bottoms,https://cdn.example.com/p2.jpg,"[""occasion""]","Missing id",10
prod_003,coll_a,Non HTTPS,dresses,http://cdn.example.com/p3.jpg,"[""occasion""]","Bad image scheme",30
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            result = CsvIngestionService(store, "cust_a").ingest_csv(csv_text)

            self.assertEqual(result.accepted_rows, 1)
            self.assertEqual(result.rejected_rows, 2)
            self.assertEqual(result.warnings, 1)
            self.assertEqual(len(store.drain_queue()), 1)

    def test_signup_rejects_duplicate_email_and_login_creates_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auth = AuthService(JsonStore(tmp))
            signup = auth.signup("Buyer@Brand.com", "password123")
            self.assertEqual(signup["customer"]["email"], "buyer@brand.com")
            self.assertIn("refresh_token", signup)

            with self.assertRaises(AuthError):
                auth.signup("buyer@brand.com", "password123")

            login = auth.login("buyer@brand.com", "password123")
            self.assertEqual(login["customer"]["customer_id"], signup["customer"]["customer_id"])
            self.assertIn("session_id", login["session"])

    def test_api_key_authenticates_customer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            auth = AuthService(store)
            signup = auth.signup("buyer@brand.com", "password123")

            customer = auth.authenticate_api_key(signup["api_key"])

            self.assertEqual(customer.customer_id, signup["customer"]["customer_id"])

    def test_customer_can_view_and_create_visible_api_keys_after_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            auth = AuthService(store)
            signup = auth.signup("buyer@brand.com", "password123")
            login = auth.login("buyer@brand.com", "password123")

            api_keys = auth.list_api_keys(login["refresh_token"])

            self.assertEqual(len(api_keys), 1)
            self.assertEqual(api_keys[0]["api_key"], signup["api_key"])

            new_key = auth.create_customer_api_key(login["refresh_token"])

            self.assertTrue(new_key["api_key"].startswith("fep_"))
            self.assertEqual(auth.authenticate_api_key(new_key["api_key"]).customer_id, signup["customer"]["customer_id"])
            self.assertEqual(len(auth.list_api_keys(login["refresh_token"])), 2)

    def test_plan_limits_fail_with_upgrade_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            auth = AuthService(store)
            signup = auth.signup("buyer@brand.com", "password123")
            customer = auth.authenticate_api_key(signup["api_key"])
            usage = UsageService(store)

            usage.reserve_search(customer, amount=100)
            with self.assertRaises(PlanLimitError) as context:
                usage.reserve_search(customer)

            self.assertIn("Please upgrade your plan", str(context.exception))

    def test_worker_enriches_and_search_reranks(self) -> None:
        csv_text = Path("examples/catalog.csv").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            result = CsvIngestionService(
                store,
                "cust_arket",
                attribute_list_uri=self.write_attribute_file(tmp),
            ).ingest_csv(csv_text)
            self.assertEqual(result.accepted_rows, 3)

            processed = asyncio.run(EnrichmentWorker(store).drain_once())
            self.assertEqual(len(processed), 3)

            service = SearchService(store)
            results = service.search_sync(
                SearchRequest(
                    collection_id="coll_ss25",
                    query="flowy cream trousers for holiday",
                    top_k=3,
                )
            )

            self.assertTrue(results)
            self.assertEqual(results[0].product_id, "prod_001")
            self.assertTrue(results[0].image_urls)
            self.assertGreaterEqual(results[0].score, 0.0)
            self.assertLessEqual(results[0].score, 1.0)
            self.assertIn("_retrieval_semantic", results[0].feature_scores)

    def test_api_level_attributes_override_csv_attributes(self) -> None:
        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,coll_a,Linen Trouser,bottoms,https://cdn.example.com/p1.jpg,"[""occasion""]","Cream linen wide leg holiday trouser",89
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            result = CsvIngestionService(
                store,
                "cust_a",
                attributes=["color", "fabric_feel"],
            ).ingest_csv(csv_text)

            self.assertEqual(result.accepted_rows, 1)
            self.assertEqual(result.messages[0].attribute_schema, ["color", "fabric_feel"])

    def test_attribute_list_uri_supplies_customer_taxonomy(self) -> None:
        csv_text = """product_id,collection_id,name,category,image_urls,description,price
prod_001,coll_a,Linen Trouser,bottoms,https://cdn.example.com/p1.jpg,"Cream linen wide leg holiday trouser",89
"""
        with tempfile.TemporaryDirectory() as tmp:
            attr_file = Path(tmp) / "attributes.json"
            attr_file.write_text('{"attributes":["material_story","display_mood"]}', encoding="utf-8")
            store = JsonStore(tmp)
            attributes = load_attribute_list(str(attr_file))
            result = CsvIngestionService(
                store,
                "cust_a",
                attributes=attributes.names,
                attribute_list_uri=str(attr_file),
            ).ingest_csv(csv_text)

            self.assertEqual(result.accepted_rows, 1)
            self.assertEqual(result.messages[0].attribute_schema, ["material_story", "display_mood"])
            self.assertEqual(result.messages[0].attribute_list_uri, str(attr_file))

    def test_upload_path_collection_id_is_enforced(self) -> None:
        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,coll_b,Linen Trouser,bottoms,https://cdn.example.com/p1.jpg,"[""occasion""]","Cream linen wide leg holiday trouser",89
"""
        with tempfile.TemporaryDirectory() as tmp:
            result = CsvIngestionService(
                JsonStore(tmp),
                "cust_a",
                expected_collection_id="coll_a",
            ).ingest_csv(csv_text)

            self.assertEqual(result.accepted_rows, 0)
            self.assertEqual(result.rejected_rows, 1)
            self.assertIn("must match upload path collection", result.issues[0].message)

    def test_existing_collection_owner_is_preserved(self) -> None:
        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,coll_a,Linen Trouser,bottoms,https://cdn.example.com/p1.jpg,"[""occasion""]","Cream linen wide leg holiday trouser",89
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(
                store,
                "cust_owner",
                attribute_list_uri=self.write_attribute_file(tmp),
            ).ingest_csv(csv_text)
            existing = store.get_collection_config("coll_a")

            self.assertEqual(existing.customer_id, "cust_owner")

    def test_upload_rejects_collection_owned_by_another_customer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(tmp))
            attr_file = self.write_attribute_file(tmp)
            csv_text = (
                "product_id,collection_id,name,category,image_urls,description,price\n"
                "prod_001,coll_a,Linen Trouser,bottoms,https://cdn.example.com/p1.jpg,Cream linen wide leg holiday trouser,89\n"
            )

            owner = client.post(
                "/auth/signup",
                json={"email": "owner@example.com", "password": "password123"},
            ).json()
            other = client.post(
                "/auth/signup",
                json={"email": "other@example.com", "password": "password123"},
            ).json()
            with patch.dict("os.environ", {"ATTRIBUTE_LIST_BASE_URI": str(Path(attr_file).parent)}):
                owner_upload = client.post(
                    "/collections/coll_a/upload",
                    headers={"x-api-key": owner["api_key"]},
                    data={"attribute_list_id": "attributes", "process_now": "false"},
                    files={"csv_file": ("catalog.csv", csv_text, "text/csv")},
                )
                other_upload = client.post(
                    "/collections/coll_a/upload",
                    headers={"x-api-key": other["api_key"]},
                    data={"attribute_list_id": "attributes", "process_now": "false"},
                    files={"csv_file": ("catalog.csv", csv_text, "text/csv")},
                )

            self.assertEqual(owner_upload.status_code, 200)
            self.assertEqual(other_upload.status_code, 404)

    def test_upload_accepts_attribute_file_name_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(tmp))
            attr_file = self.write_attribute_file(tmp)
            csv_text = (
                "product_id,collection_id,name,category,image_urls,description,price\n"
                "prod_001,coll_a,Linen Trouser,bottoms,https://cdn.example.com/p1.jpg,Cream linen wide leg holiday trouser,89\n"
            )
            signup = client.post(
                "/auth/signup",
                json={"email": "happy@example.com", "password": "password123"},
            ).json()

            with patch.dict("os.environ", {"ATTRIBUTE_LIST_BASE_URI": str(Path(attr_file).parent)}):
                response = client.post(
                    "/collections/coll_a/upload",
                    headers={"x-api-key": signup["api_key"]},
                    data={"attribute_list_id": "attributes", "process_now": "false"},
                    files={"csv_file": ("catalog.csv", csv_text, "text/csv")},
                )

            payload = response.json()
            collection = JsonStore(tmp).get_collection_config("coll_a")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["accepted_rows"], 1)
            self.assertEqual(payload["rejected_rows"], 0)
            self.assertEqual(collection.attribute_list_id, "attributes")
            self.assertEqual(collection.attribute_list_uri, attr_file)

    def test_upload_rejects_attribute_list_uri_as_file_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(tmp))
            csv_text = (
                "product_id,collection_id,name,category,image_urls,description,price\n"
                "prod_001,coll_a,Linen Trouser,bottoms,https://cdn.example.com/p1.jpg,Cream linen wide leg holiday trouser,89\n"
            )
            signup = client.post(
                "/auth/signup",
                json={"email": "bad-attr@example.com", "password": "password123"},
            ).json()

            response = client.post(
                "/collections/coll_a/upload",
                headers={"x-api-key": signup["api_key"]},
                data={"attribute_list_id": "s3://bucket/attributes/fashion-v1.json", "process_now": "false"},
                files={"csv_file": ("catalog.csv", csv_text, "text/csv")},
            )

            self.assertEqual(response.status_code, 400)
            self.assertIn("file name", response.json()["detail"])

    def test_missing_attribute_taxonomy_rejects_row(self) -> None:
        csv_text = """product_id,collection_id,name,category,image_urls,description,price
prod_001,coll_a,Linen Trouser,bottoms,https://cdn.example.com/p1.jpg,"Cream linen wide leg holiday trouser",89
"""
        with tempfile.TemporaryDirectory() as tmp:
            result = CsvIngestionService(JsonStore(tmp), "cust_a").ingest_csv(csv_text)

            self.assertEqual(result.accepted_rows, 0)
            self.assertEqual(result.rejected_rows, 1)
            self.assertIn("No attribute list", result.issues[0].message)

    def test_worker_parses_llm_json_string_and_keeps_requested_keys(self) -> None:
        parsed = parse_enrichment_response(
            '{"color":"cream","fabric_feel":"linen","extra":"ignored"}',
            ["color", "fabric_feel", "occasion"],
        )

        self.assertEqual(
            parsed,
            {"color": "cream", "fabric_feel": "linen", "occasion": None},
        )

    def test_worker_fails_empty_enriched_attributes_before_indexing(self) -> None:
        async def empty_enrich(product_id, metadata, image_bytes, attribute_schema):
            return "{}"

        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,coll_a,Look One,looks,https://cdn.example.com/p1.jpg,"[""style_mood""]","Soft draped outfit",100
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(store, "cust_a").ingest_csv(csv_text)
            processed = asyncio.run(EnrichmentWorker(store, enrich_fn=empty_enrich).drain_once())

            record = store.get_record("coll_a", "prod_001")
            self.assertEqual(processed, [])
            self.assertEqual(record.status.value, "FAILED")
            self.assertEqual(store.indexed_documents("coll_a"), [])

    def test_worker_fails_empty_embedding_before_indexing(self) -> None:
        async def valid_enrich(product_id, metadata, image_bytes, attribute_schema):
            return json.dumps({"style_mood": "soft draped outfit"})

        def empty_embed(text):
            return []

        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,coll_a,Look One,looks,https://cdn.example.com/p1.jpg,"[""style_mood""]","Soft draped outfit",100
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(store, "cust_a").ingest_csv(csv_text)
            processed = asyncio.run(EnrichmentWorker(store, enrich_fn=valid_enrich, embed_fn=empty_embed).drain_once())

            record = store.get_record("coll_a", "prod_001")
            self.assertEqual(processed, [])
            self.assertEqual(record.status.value, "FAILED")
            self.assertEqual(store.indexed_documents("coll_a"), [])

    def test_worker_fails_empty_downloaded_image_before_llm(self) -> None:
        class EmptyImageWorker(EnrichmentWorker):
            async def _download_images(self, image_urls):
                return [b""]

        async def should_not_run(product_id, metadata, image_bytes, attribute_schema):
            raise AssertionError("LLM should not run for empty image bytes.")

        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,coll_a,Look One,looks,https://cdn.example.com/p1.jpg,"[""style_mood""]","Soft draped outfit",100
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(store, "cust_a").ingest_csv(csv_text)
            processed = asyncio.run(EmptyImageWorker(store, enrich_fn=should_not_run).drain_once())

            record = store.get_record("coll_a", "prod_001")
            self.assertEqual(processed, [])
            self.assertEqual(record.status.value, "FAILED")
            self.assertEqual(store.indexed_documents("coll_a"), [])

    def test_delete_message_removes_index_document(self) -> None:
        csv_text = Path("examples/catalog.csv").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(
                store,
                "cust_arket",
                attribute_list_uri=self.write_attribute_file(tmp),
            ).ingest_csv(csv_text)
            asyncio.run(EnrichmentWorker(store).drain_once())
            self.assertEqual(len(store.indexed_documents("coll_ss25")), 3)

            delete_csv = """product_id,collection_id,name,image_urls,_deleted
prod_001,coll_ss25,Linen Wide-Leg Trouser,https://cdn.example.com/p1a.jpg,true
"""
            result = CsvIngestionService(store, "cust_arket").ingest_csv(delete_csv)
            self.assertEqual(result.accepted_rows, 1)
            asyncio.run(EnrichmentWorker(store).drain_once())

            product_ids = {doc["product_id"] for doc in store.indexed_documents("coll_ss25")}
            self.assertNotIn("prod_001", product_ids)

    def test_query_text_does_not_apply_hard_filters_without_llm_filters(self) -> None:
        csv_text = Path("examples/catalog.csv").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(
                store,
                "cust_arket",
                attribute_list_uri=self.write_attribute_file(tmp),
            ).ingest_csv(csv_text)
            asyncio.run(EnrichmentWorker(store).drain_once())

            results = SearchService(store).search_sync(
                SearchRequest(
                    collection_id="coll_ss25",
                    query="flowy beach wedding look under 50 dollars",
                    top_k=10,
                )
            )

            self.assertEqual(len(results), 3)

    def test_query_llm_hard_filters_are_optional_and_limited(self) -> None:
        async def fake_query_parser(query, available_attributes):
            return json.dumps(
                {
                    "attributes": {},
                    "hard_filters": {
                        "category": "bottoms",
                        "price_max": 100,
                        "attrs.occasion": "holiday",
                    },
                    "weights": {},
                }
            )

        csv_text = Path("examples/catalog.csv").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(
                store,
                "cust_arket",
                attribute_list_uri=self.write_attribute_file(tmp),
            ).ingest_csv(csv_text)
            asyncio.run(EnrichmentWorker(store).drain_once())

            results = SearchService(store, query_attribute_fn=fake_query_parser).search_sync(
                SearchRequest(
                    collection_id="coll_ss25",
                    query="bottoms under 100",
                    top_k=10,
                )
            )

            self.assertEqual([result.product_id for result in results], ["prod_001"])

    def test_query_llm_price_filter_excludes_missing_price_only_when_present(self) -> None:
        async def fake_query_parser(query, available_attributes):
            if "under" not in query:
                return json.dumps({"attributes": {}, "hard_filters": {}, "weights": {}})
            return json.dumps({"attributes": {}, "hard_filters": {"price_max": 100}, "weights": {}})

        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,coll_a,Priced Look,looks,https://cdn.example.com/p1.jpg,"[""style_mood""]","Soft draped outfit",80
prod_002,coll_a,No Price Look,looks,https://cdn.example.com/p2.jpg,"[""style_mood""]","Sharp work outfit",
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(
                store,
                "cust_a",
                attribute_list_uri=self.write_attribute_file(tmp, '{"attributes":["style_mood"]}'),
            ).ingest_csv(csv_text)
            asyncio.run(EnrichmentWorker(store).drain_once())

            no_filter_results = SearchService(store, query_attribute_fn=fake_query_parser).search_sync(
                SearchRequest(collection_id="coll_a", query="nice look", top_k=10)
            )
            filtered_results = SearchService(store, query_attribute_fn=fake_query_parser).search_sync(
                SearchRequest(collection_id="coll_a", query="nice look under 100", top_k=10)
            )

            self.assertEqual(len(no_filter_results), 2)
            self.assertEqual([result.product_id for result in filtered_results], ["prod_001"])

    def test_query_llm_attributes_drive_weighted_reranking(self) -> None:
        async def fake_enrich(product_id, metadata, image_bytes, attribute_schema):
            values = {
                "prod_001": {
                    "occasion": "beach wedding",
                    "style_mood": "flowy light look",
                },
                "prod_002": {
                    "occasion": "office meeting",
                    "style_mood": "structured tailored look",
                },
            }
            return json.dumps(values[product_id])

        async def fake_query_parser(query, available_attributes):
            self.assertEqual(available_attributes, ["occasion", "style_mood"])
            return json.dumps(
                {
                    "attributes": {
                        "occasion": "beach wedding",
                        "style_mood": "flowy",
                    },
                    "weights": {
                        "occasion": 2.0,
                        "style_mood": 1.0,
                    },
                }
            )

        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,coll_a,Look One,looks,https://cdn.example.com/p1.jpg,"[""occasion"",""style_mood""]","Soft draped outfit",100
prod_002,coll_a,Look Two,looks,https://cdn.example.com/p2.jpg,"[""occasion"",""style_mood""]","Sharp work outfit",100
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(
                store,
                "cust_a",
                attribute_list_uri=self.write_attribute_file(
                    tmp,
                    '{"attributes":[{"name":"occasion","weight":2.0},{"name":"style_mood","weight":1.0}]}',
                ),
            ).ingest_csv(csv_text)
            asyncio.run(EnrichmentWorker(store, enrich_fn=fake_enrich).drain_once())

            results = SearchService(store, query_attribute_fn=fake_query_parser).search_sync(
                SearchRequest(
                    collection_id="coll_a",
                    query="flowy beach wedding look",
                    top_k=2,
                )
            )

            self.assertEqual(results[0].product_id, "prod_001")
            self.assertIn("occasion", results[0].feature_scores)
            self.assertIn("style_mood", results[0].feature_scores)

    def test_attribute_list_weights_are_used_when_llm_omits_weights(self) -> None:
        async def fake_enrich(product_id, metadata, image_bytes, attribute_schema):
            values = {
                "prod_001": {"primary_signal": "match", "secondary_signal": "miss"},
                "prod_002": {"primary_signal": "miss", "secondary_signal": "match"},
            }
            return json.dumps(values[product_id])

        def fake_embed(text):
            if "primary_signal: match" in text or "secondary_signal: match" in text:
                return [1.0, 0.0]
            if "primary_signal: miss" in text or "secondary_signal: miss" in text:
                return [0.0, 1.0]
            return [1.0, 0.0]

        def fake_query_parser(query, available_attributes):
            return json.dumps(
                {
                    "attributes": {
                        "primary_signal": "match",
                        "secondary_signal": "match",
                    },
                    "hard_filters": {},
                    "weights": {},
                }
            )

        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,coll_a,Look One,looks,https://cdn.example.com/p1.jpg,"[""primary_signal"",""secondary_signal""]","One",100
prod_002,coll_a,Look Two,looks,https://cdn.example.com/p2.jpg,"[""primary_signal"",""secondary_signal""]","Two",100
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(
                store,
                "cust_a",
                attribute_list_uri=self.write_attribute_file(
                    tmp,
                    '{"attributes":[{"name":"primary_signal","weight":3.0},{"name":"secondary_signal","weight":1.0}]}',
                ),
            ).ingest_csv(csv_text)
            asyncio.run(EnrichmentWorker(store, enrich_fn=fake_enrich, embed_fn=fake_embed).drain_once())

            results = SearchService(
                store,
                embed_fn=fake_embed,
                query_attribute_fn=fake_query_parser,
            ).search(
                SearchRequest(collection_id="coll_a", query="match", top_k=2)
            )

            self.assertEqual(results[0].product_id, "prod_001")

    def test_top_k_is_bounded_and_collection_smaller_than_k_is_ok(self) -> None:
        csv_text = Path("examples/catalog.csv").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(
                store,
                "cust_arket",
                attribute_list_uri=self.write_attribute_file(tmp),
            ).ingest_csv(csv_text)
            asyncio.run(EnrichmentWorker(store).drain_once())

            results = SearchService(store).search(SearchRequest(collection_id="coll_ss25", query="anything", top_k=10))
            self.assertEqual(len(results), 3)

            with self.assertRaises(ValueError):
                SearchService(store).search(SearchRequest(collection_id="coll_ss25", query="anything", top_k=101))

    def test_search_api_rejects_top_k_above_limit_as_client_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            signup = AuthService(store).signup("search@example.com", "password123")
            store.upsert_collection_config(
                CollectionConfig(
                    collection_id="coll_a",
                    customer_id=signup["customer"]["customer_id"],
                    attribute_list_id="attributes",
                    attribute_list_uri=self.write_attribute_file(tmp),
                )
            )
            client = TestClient(create_search_app(tmp))

            response = client.post(
                "/search",
                headers={"x-api-key": signup["api_key"]},
                json={"collection_id": "coll_a", "query": "dress", "top_k": 101},
            )

            self.assertEqual(response.status_code, 400)
            self.assertIn("top_k cannot exceed", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
