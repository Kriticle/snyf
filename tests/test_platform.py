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
from services.ingestion_api.main import count_csv_rows, create_app
from services.search_api.main import create_app as create_search_app
from shared.attribute_loader import load_attribute_list
from shared.auth import AuthError, AuthService
from shared.csv_ingestion import CsvIngestionService
from shared.models import CollectionConfig, ProductRecord, ProductStatus, SearchRequest
from shared.plans import PlanLimitError, UsageService
from shared.search import SearchService
from shared.search_cache import NullSearchCache
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
            result = CsvIngestionService(
                store,
                "cust_a",
                attribute_list_uri=self.write_attribute_file(tmp),
            ).ingest_csv(csv_text)

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
            self.assertEqual(
                [key["status"] for key in auth.list_api_keys(login["refresh_token"])].count("ACTIVE"),
                1,
            )
            with self.assertRaises(AuthError):
                auth.authenticate_api_key(signup["api_key"])

    def test_count_csv_rows_excludes_header_for_plan_limits(self) -> None:
        csv_text = "product_id,name,image_urls\n" + "\n".join(
            f"prod_{index},Item {index},https://cdn.example.com/{index}.jpg"
            for index in range(1000)
        )

        self.assertEqual(count_csv_rows(csv_text), 1000)

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

    def test_worker_stores_attribute_embeddings_for_indexed_documents(self) -> None:
        async def fake_enrich(product_id, metadata, image_bytes, attribute_schema):
            return json.dumps({"occasion": "beach wedding", "style_mood": "flowy resort"})

        def fake_embed(text):
            return [1.0, 0.0]

        def fake_embed_many(texts):
            return [[float(index + 1), 0.0] for index, _ in enumerate(texts)]

        csv_text = """product_id,collection_id,name,category,image_urls,description,price
prod_001,coll_a,Look One,looks,https://cdn.example.com/p1.jpg,"Soft draped outfit",100
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(
                store,
                "cust_a",
                attribute_list_uri=self.write_attribute_file(tmp, '{"attributes":["occasion","style_mood"]}'),
            ).ingest_csv(csv_text)
            asyncio.run(
                EnrichmentWorker(
                    store,
                    enrich_fn=fake_enrich,
                    embed_fn=fake_embed,
                    embed_many_fn=fake_embed_many,
                ).drain_once()
            )

            record = store.get_record("coll_a", "prod_001")
            indexed = store.indexed_documents("coll_a")[0]

            self.assertEqual(sorted(record.attribute_embeddings), ["occasion", "style_mood"])
            self.assertEqual(sorted(indexed["attribute_embeddings"]), ["occasion", "style_mood"])

    def test_search_uses_stored_product_attribute_embeddings(self) -> None:
        seen_texts: list[str] = []

        def fake_embed(text):
            seen_texts.append(text)
            if text == "occasion: beach wedding":
                return [1.0, 0.0]
            return [0.5, 0.5]

        def fake_query_parser(query, available_attributes):
            return json.dumps({"attributes": {"occasion": "beach wedding"}, "hard_filters": {}})

        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.upsert_collection_config(
                CollectionConfig(
                    collection_id="coll_a",
                    customer_id="cust_a",
                    attribute_list_id="attributes",
                    attribute_list_uri=self.write_attribute_file(tmp, '{"attributes":["occasion"]}'),
                )
            )
            record = ProductRecord(
                customer_id="cust_a",
                collection_id="coll_a",
                product_id="prod_001",
                raw_metadata={"name": "Look One"},
                image_urls=["https://cdn.example.com/p1.jpg"],
                attribute_schema=["occasion"],
                enriched_attrs={"occasion": "product side value should not be embedded"},
                enriched_text="Look One occasion beach wedding",
                embedding=[1.0, 0.0],
                attribute_embeddings={"occasion": [1.0, 0.0]},
                status=ProductStatus.ENRICHED,
            )
            store.upsert_index(record)

            results = SearchService(store, embed_fn=fake_embed, query_attribute_fn=fake_query_parser).search_sync(
                SearchRequest(collection_id="coll_a", query="beach wedding", top_k=1)
            )

            self.assertEqual(results[0].product_id, "prod_001")
            self.assertIn("occasion: beach wedding", seen_texts)
            self.assertNotIn("occasion: product side value should not be embedded", seen_texts)

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

    def test_path_scoped_upload_accepts_csv_without_collection_id(self) -> None:
        csv_text = """product_id,name,category,image_urls,description,price
prod_001,Linen Trouser,bottoms,https://cdn.example.com/p1.jpg,Cream linen wide leg holiday trouser,89
"""
        with tempfile.TemporaryDirectory() as tmp:
            result = CsvIngestionService(
                JsonStore(tmp),
                "cust_a",
                attribute_list_uri=self.write_attribute_file(tmp),
                expected_collection_id="coll_a",
            ).ingest_csv(csv_text)

            self.assertEqual(result.accepted_rows, 1)
            self.assertEqual(result.rejected_rows, 0)
            self.assertEqual(result.messages[0].collection_id, "coll_a")

    def test_upload_returns_pollable_job_status_for_owner_only(self) -> None:
        csv_text = """product_id,name,category,image_urls,description,price
prod_001,Linen Trouser,bottoms,https://cdn.example.com/p1.jpg,Cream linen wide leg holiday trouser,89
"""
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(tmp))
            attr_file = self.write_attribute_file(tmp)
            owner = client.post(
                "/auth/signup",
                json={"email": "job-owner@example.com", "password": "password123"},
            ).json()
            other = client.post(
                "/auth/signup",
                json={"email": "job-other@example.com", "password": "password123"},
            ).json()

            with patch.dict("os.environ", {"ATTRIBUTE_LIST_BASE_URI": str(Path(attr_file).parent)}):
                upload = client.post(
                    "/collections/coll_a/upload",
                    headers={"x-api-key": owner["api_key"]},
                    data={"attribute_list_id": "attributes"},
                    files={"csv_file": ("catalog.csv", csv_text, "text/csv")},
                )

            payload = upload.json()
            owner_status = client.get(
                f"/ingestion-jobs/{payload['job_id']}",
                headers={"x-api-key": owner["api_key"]},
            )
            other_status = client.get(
                f"/ingestion-jobs/{payload['job_id']}",
                headers={"x-api-key": other["api_key"]},
            )

            self.assertEqual(upload.status_code, 200)
            self.assertEqual(payload["accepted_rows"], 1)
            self.assertTrue(payload["job_id"].startswith("job_coll_a_"))
            self.assertEqual(owner_status.status_code, 200)
            self.assertEqual(owner_status.json()["status"], "QUEUED")
            self.assertEqual(owner_status.json()["queued_rows"], 1)
            self.assertEqual(owner_status.json()["products"][0]["collection_id"], "coll_a")
            self.assertEqual(other_status.status_code, 404)

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

    def test_collection_lookup_reports_existing_attribute_list_for_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(tmp))
            attr_file = self.write_attribute_file(tmp)
            signup = client.post(
                "/auth/signup",
                json={"email": "collection@example.com", "password": "password123"},
            ).json()
            JsonStore(tmp).upsert_collection_config(
                CollectionConfig(
                    collection_id="coll_a",
                    customer_id=signup["customer"]["customer_id"],
                    attribute_list_id="attributes",
                    attribute_list_uri=attr_file,
                )
            )

            existing = client.get("/collections/coll_a", headers={"x-api-key": signup["api_key"]})
            missing = client.get("/collections/new_coll", headers={"x-api-key": signup["api_key"]})

            self.assertEqual(existing.status_code, 200)
            self.assertTrue(existing.json()["exists"])
            self.assertTrue(existing.json()["attribute_list_locked"])
            self.assertEqual(existing.json()["attribute_list_id"], "attributes")
            self.assertEqual(missing.status_code, 200)
            self.assertFalse(missing.json()["exists"])

    def test_existing_collection_attribute_list_cannot_be_changed_on_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(tmp))
            attr_file = self.write_attribute_file(tmp)
            signup = client.post(
                "/auth/signup",
                json={"email": "locked@example.com", "password": "password123"},
            ).json()
            JsonStore(tmp).upsert_collection_config(
                CollectionConfig(
                    collection_id="coll_a",
                    customer_id=signup["customer"]["customer_id"],
                    attribute_list_id="attributes",
                    attribute_list_uri=attr_file,
                )
            )
            csv_text = (
                "product_id,collection_id,name,category,image_urls,description,price\n"
                "prod_001,coll_a,Linen Trouser,bottoms,https://cdn.example.com/p1.jpg,Cream linen wide leg holiday trouser,89\n"
            )

            response = client.post(
                "/collections/coll_a/upload",
                headers={"x-api-key": signup["api_key"]},
                data={"attribute_list_id": "fashion-v1"},
                files={"csv_file": ("catalog.csv", csv_text, "text/csv")},
            )

            self.assertEqual(response.status_code, 400)
            self.assertIn("cannot change attribute lists", response.json()["detail"])

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
            self.assertIn("No collection attribute list", result.issues[0].message)

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
            CsvIngestionService(
                store,
                "cust_a",
                attribute_list_uri=self.write_attribute_file(tmp, '{"attributes":["style_mood"]}'),
            ).ingest_csv(csv_text)
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
            CsvIngestionService(
                store,
                "cust_a",
                attribute_list_uri=self.write_attribute_file(tmp, '{"attributes":["style_mood"]}'),
            ).ingest_csv(csv_text)
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
            CsvIngestionService(
                store,
                "cust_a",
                attribute_list_uri=self.write_attribute_file(tmp, '{"attributes":["style_mood"]}'),
            ).ingest_csv(csv_text)
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

    def test_search_uses_store_vector_retrieval_when_available(self) -> None:
        class VectorStore(JsonStore):
            def __init__(self, root):
                super().__init__(root)
                self.vector_calls = 0

            def indexed_documents(self, collection_id):
                raise AssertionError("search should use vector_search_documents instead")

            def vector_search_documents(self, collection_id, query_vector, limit):
                self.vector_calls += 1
                return [
                    {
                        "collection_id": collection_id,
                        "customer_id": "cust_a",
                        "product_id": "prod_001",
                        "name": "Look One",
                        "image_urls": ["https://cdn.example.com/p1.jpg"],
                        "attrs": {"occasion": "beach wedding"},
                        "attribute_embeddings": {"occasion": [1.0, 0.0]},
                        "embedding": [1.0, 0.0],
                    }
                ]

        def fake_embed(text):
            return [1.0, 0.0]

        def fake_query_parser(query, available_attributes):
            return json.dumps({"attributes": {"occasion": "beach wedding"}, "hard_filters": {}})

        with tempfile.TemporaryDirectory() as tmp:
            store = VectorStore(tmp)
            store.upsert_collection_config(
                CollectionConfig(
                    collection_id="coll_a",
                    customer_id="cust_a",
                    attribute_list_id="attributes",
                    attribute_list_uri=self.write_attribute_file(tmp, '{"attributes":["occasion"]}'),
                )
            )

            results, metrics = SearchService(
                store,
                embed_fn=fake_embed,
                query_attribute_fn=fake_query_parser,
            ).search_with_metrics(SearchRequest(collection_id="coll_a", query="beach wedding", top_k=1))

            self.assertEqual(results[0].product_id, "prod_001")
            self.assertEqual(store.vector_calls, 1)
            self.assertIn("vector_retrieve_documents", metrics.steps_ms)

    def test_search_api_returns_latency_metrics_and_reuses_embedding_cache(self) -> None:
        async def fake_enrich(product_id, metadata, image_bytes, attribute_schema):
            return json.dumps({"style_mood": "relaxed beach look"})

        embed_calls = {"count": 0}

        def fake_embed(text):
            embed_calls["count"] += 1
            return [1.0, 0.0] if "relaxed" in text or "beach" in text else [0.0, 1.0]

        def fake_query_parser(query, available_attributes):
            return json.dumps(
                {
                    "attributes": {"style_mood": "relaxed beach look"},
                    "hard_filters": {},
                    "weights": {},
                }
            )

        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,coll_a,Look One,looks,https://cdn.example.com/p1.jpg,"[""style_mood""]","One",100
prod_002,coll_a,Look Two,looks,https://cdn.example.com/p2.jpg,"[""style_mood""]","Two",100
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(
                store,
                "cust_a",
                attribute_list_uri=self.write_attribute_file(tmp, '{"attributes":["style_mood"]}'),
            ).ingest_csv(csv_text)
            asyncio.run(EnrichmentWorker(store, enrich_fn=fake_enrich, embed_fn=fake_embed).drain_once())
            service = SearchService(store, embed_fn=fake_embed, query_attribute_fn=fake_query_parser)

            results, metrics = service.search_with_metrics(
                SearchRequest(collection_id="coll_a", query="relaxed beach", top_k=2)
            )

            self.assertEqual(len(results), 2)
            self.assertIn("extract_query_attributes", metrics.steps_ms)
            self.assertIn("rerank_candidates", metrics.steps_ms)
            self.assertEqual(metrics.candidate_count, 2)
            self.assertGreater(metrics.embedding_cache_hits, 0)
            self.assertLessEqual(metrics.embedding_calls, 3)

    def test_search_uses_persistent_cache_for_embeddings_and_query_parse(self) -> None:
        class MemorySearchCache(NullSearchCache):
            def __init__(self):
                self.embeddings = {}
                self.query_parses = {}

            def get_embedding(self, text, model):
                return self.embeddings.get((model, text))

            def get_embeddings(self, texts, model):
                return {text: self.embeddings[(model, text)] for text in texts if (model, text) in self.embeddings}

            def set_embedding(self, text, model, vector):
                self.embeddings[(model, text)] = vector

            def set_embeddings(self, values, model):
                for text, vector in values.items():
                    self.embeddings[(model, text)] = vector

            def get_query_parse(self, query, attribute_namespace, model):
                return self.query_parses.get((model, attribute_namespace, query))

            def set_query_parse(self, query, attribute_namespace, model, parsed):
                self.query_parses[(model, attribute_namespace, query)] = parsed

        calls = {"embed": 0, "parse": 0}

        def fake_embed(text):
            calls["embed"] += 1
            return [1.0, 0.0] if "beach" in text else [0.0, 1.0]

        def fake_query_parser(query, available_attributes):
            calls["parse"] += 1
            return json.dumps({"attributes": {"occasion": "beach wedding"}, "hard_filters": {}})

        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.upsert_collection_config(
                CollectionConfig(
                    collection_id="coll_a",
                    customer_id="cust_a",
                    attribute_list_id="attributes",
                    attribute_list_uri=self.write_attribute_file(tmp, '{"attributes":["occasion"]}'),
                )
            )
            record = ProductRecord(
                collection_id="coll_a",
                customer_id="cust_a",
                product_id="prod_001",
                image_urls=["https://cdn.example.com/p1.jpg"],
                raw_metadata={"name": "Beach Dress"},
                enriched_attrs={"occasion": "beach wedding"},
                attribute_schema=["occasion"],
                enriched_text="occasion: beach wedding",
                embedding=[1.0, 0.0],
                attribute_embeddings={"occasion": [1.0, 0.0]},
                status=ProductStatus.ENRICHED,
            )
            store.upsert_index(record)
            cache = MemorySearchCache()

            first_service = SearchService(
                store,
                embed_fn=fake_embed,
                query_attribute_fn=fake_query_parser,
                search_cache=cache,
            )
            first_service.search_with_metrics(SearchRequest(collection_id="coll_a", query="beach wedding", top_k=1))
            first_calls = dict(calls)

            second_service = SearchService(
                store,
                embed_fn=fake_embed,
                query_attribute_fn=fake_query_parser,
                search_cache=cache,
            )
            _, metrics = second_service.search_with_metrics(
                SearchRequest(collection_id="coll_a", query="beach wedding", top_k=1)
            )

            self.assertGreater(first_calls["embed"], 0)
            self.assertEqual(calls["embed"], first_calls["embed"])
            self.assertEqual(calls["parse"], first_calls["parse"])
            self.assertEqual(metrics.embedding_calls, 0)
            self.assertGreater(metrics.embedding_cache_hits, 0)

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

    def test_keyword_search_ranks_lexical_matches_without_llm(self) -> None:
        async def fake_enrich(product_id, metadata, image_bytes, attribute_schema):
            values = {
                "prod_001": {"material": "linen", "occasion": "beach wedding"},
                "prod_002": {"material": "wool", "occasion": "work dinner"},
            }
            return json.dumps(values[product_id])

        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,coll_a,Linen Wrap Dress,dresses,https://cdn.example.com/p1.jpg,"[""material"",""occasion""]","Airy beach dress",100
prod_002,coll_a,Structured Blazer,outerwear,https://cdn.example.com/p2.jpg,"[""material"",""occasion""]","Sharp work jacket",100
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(
                store,
                "cust_a",
                attribute_list_uri=self.write_attribute_file(tmp, '{"attributes":["material","occasion"]}'),
            ).ingest_csv(csv_text)
            asyncio.run(EnrichmentWorker(store, enrich_fn=fake_enrich).drain_once())

            results = SearchService(store).keyword_search(
                SearchRequest(collection_id="coll_a", query="linen jacket", top_k=2)
            )

            self.assertEqual(results[0].product_id, "prod_001")
            self.assertGreater(results[0].score, results[1].score)
            self.assertIn("keyword_match", results[0].feature_scores)

    def test_keyword_search_ignores_enriched_text_and_attrs(self) -> None:
        class StoreWithEnrichedOnlyMatch:
            def indexed_documents(self, collection_id):
                return [
                    {
                        "product_id": "prod_001",
                        "name": "Plain Product",
                        "category": "apparel",
                        "brand": "Example",
                        "description": "Customer supplied neutral catalog copy",
                        "enriched_text": "romantic beach wedding dress",
                        "attrs": {"occasion": "beach wedding", "aesthetic": "romantic"},
                        "image_urls": ["https://cdn.example.com/p1.jpg"],
                    }
                ]

        results = SearchService(StoreWithEnrichedOnlyMatch()).keyword_search(
            SearchRequest(collection_id="coll_a", query="beach wedding", top_k=5)
        )

        self.assertEqual(results, [])

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

    def test_keyword_search_api_returns_lexical_results(self) -> None:
        async def fake_enrich(product_id, metadata, image_bytes, attribute_schema):
            values = {
                "prod_001": {"material": "linen", "occasion": "beach wedding"},
                "prod_002": {"material": "wool", "occasion": "work dinner"},
            }
            return json.dumps(values[product_id])

        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,coll_a,Linen Wrap Dress,dresses,https://cdn.example.com/p1.jpg,"[""material"",""occasion""]","Airy beach dress",100
prod_002,coll_a,Structured Blazer,outerwear,https://cdn.example.com/p2.jpg,"[""material"",""occasion""]","Sharp work jacket",100
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            signup = AuthService(store).signup("keyword@example.com", "password123")
            CsvIngestionService(
                store,
                signup["customer"]["customer_id"],
                attribute_list_uri=self.write_attribute_file(tmp, '{"attributes":["material","occasion"]}'),
            ).ingest_csv(csv_text)
            asyncio.run(EnrichmentWorker(store, enrich_fn=fake_enrich).drain_once())
            client = TestClient(create_search_app(tmp))

            response = client.post(
                "/search/keyword",
                headers={"x-api-key": signup["api_key"]},
                json={"collection_id": "coll_a", "query": "linen jacket", "top_k": 2},
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["results"][0]["product_id"], "prod_001")
            self.assertIn("score_documents", payload["metrics"]["search"]["steps_ms"])

    def test_demo_keyword_search_does_not_require_api_key(self) -> None:
        async def fake_enrich(product_id, metadata, image_bytes, attribute_schema):
            return json.dumps({"material": "linen", "occasion": "beach wedding"})

        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,live_smoke_1778332579,Linen Wrap Dress,dresses,https://cdn.example.com/p1.jpg,"[""material"",""occasion""]","Airy beach dress",100
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(
                store,
                "cust_demo",
                attribute_list_uri=self.write_attribute_file(tmp, '{"attributes":["material","occasion"]}'),
            ).ingest_csv(csv_text)
            asyncio.run(EnrichmentWorker(store, enrich_fn=fake_enrich).drain_once())
            client = TestClient(create_search_app(tmp))

            response = client.post(
                "/search/demo/keyword",
                json={"query": "linen dress"},
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["collection_id"], "live_smoke_1778332579")
            self.assertEqual(payload["results"][0]["product_id"], "prod_001")

    def test_demo_search_is_rate_limited_without_api_key(self) -> None:
        async def fake_enrich(product_id, metadata, image_bytes, attribute_schema):
            return json.dumps({"material": "linen", "occasion": "beach wedding"})

        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
prod_001,live_smoke_1778332579,Linen Wrap Dress,dresses,https://cdn.example.com/p1.jpg,"[""material"",""occasion""]","Airy beach dress",100
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(
                store,
                "cust_demo",
                attribute_list_uri=self.write_attribute_file(tmp, '{"attributes":["material","occasion"]}'),
            ).ingest_csv(csv_text)
            asyncio.run(EnrichmentWorker(store, enrich_fn=fake_enrich).drain_once())

            with patch.dict("os.environ", {"DEMO_RATE_LIMIT_PER_MINUTE": "1"}):
                client = TestClient(create_search_app(tmp))
                headers = {"x-forwarded-for": "203.0.113.10"}
                first = client.post(
                    "/search/demo/keyword",
                    json={"query": "linen dress"},
                    headers=headers,
                )
                second = client.post(
                    "/search/demo/keyword",
                    json={"query": "linen dress"},
                    headers=headers,
                )

            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 429)
            self.assertIn("rate limit", second.json()["detail"])

    def test_demo_search_rejects_customer_collection_and_top_k_inputs(self) -> None:
        async def fake_enrich(product_id, metadata, image_bytes, attribute_schema):
            return json.dumps({"material": "linen", "occasion": "beach wedding"})

        csv_text = """product_id,collection_id,name,category,image_urls,attributes,description,price
demo_001,live_smoke_1778332579,Linen Wrap Dress,dresses,https://cdn.example.com/p1.jpg,"[""material"",""occasion""]","Airy beach dress",100
other_001,other_collection,Private Catalog Dress,dresses,https://cdn.example.com/p2.jpg,"[""material"",""occasion""]","Should not be searched",100
"""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            CsvIngestionService(
                store,
                "cust_demo",
                attribute_list_uri=self.write_attribute_file(tmp, '{"attributes":["material","occasion"]}'),
            ).ingest_csv(csv_text)
            asyncio.run(EnrichmentWorker(store, enrich_fn=fake_enrich).drain_once())

            with patch.dict("os.environ", {"DEMO_TOP_K": "1"}):
                client = TestClient(create_search_app(tmp))
                valid = client.post(
                    "/search/demo/keyword",
                    json={"query": "dress"},
                )
                invalid = client.post(
                    "/search/demo/keyword",
                    json={"query": "dress", "collection_id": "other_collection", "top_k": 100},
                )

            self.assertEqual(valid.status_code, 200)
            payload = valid.json()
            self.assertEqual(payload["collection_id"], "live_smoke_1778332579")
            self.assertEqual(len(payload["results"]), 1)
            self.assertEqual(payload["results"][0]["product_id"], "demo_001")
            self.assertEqual(invalid.status_code, 422)


if __name__ == "__main__":
    unittest.main()
