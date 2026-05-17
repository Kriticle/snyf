from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import uuid
from dataclasses import asdict
from io import StringIO
from pathlib import Path

from services.enrichment_worker.worker import EnrichmentWorker
from shared.attribute_loader import load_attribute_list
from shared.auth import AuthError, AuthService
from shared.csv_ingestion import CsvIngestionService
from shared.models import ProductRecord, ProductStatus
from shared.plans import PlanLimitError, UsageService
from shared.store_factory import create_store

try:
    from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
    from pydantic import BaseModel
except ImportError:  # pragma: no cover - optional runtime adapter
    FastAPI = None
    File = Form = Header = HTTPException = UploadFile = None
    BaseModel = object


if FastAPI is not None:
    class AuthPayload(BaseModel):
        email: str
        password: str


def create_app(data_dir: str = "data"):
    if FastAPI is None:
        raise RuntimeError("FastAPI is not installed. Use the CLI mode or install requirements.txt.")

    app = FastAPI(title="Fashion Attribute Enrichment - Ingestion API")
    store = create_store(data_dir)
    auth_service = AuthService(store)
    usage_service = UsageService(store)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/auth/signup")
    def signup(payload: AuthPayload):
        try:
            return auth_service.signup(payload.email, payload.password)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/auth/login")
    def login(payload: AuthPayload):
        try:
            return auth_service.login(payload.email, payload.password)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.get("/auth/api-keys")
    def list_api_keys(authorization: str | None = Header(default=None)):
        try:
            return {"api_keys": auth_service.list_api_keys(bearer_token(authorization))}
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.post("/auth/api-keys")
    def create_api_key(authorization: str | None = Header(default=None)):
        try:
            return auth_service.create_customer_api_key(bearer_token(authorization))
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.get("/ingestion-jobs/{job_id}")
    def get_ingestion_job(job_id: str, x_api_key: str | None = Header(default=None)):
        customer = authenticate_request(auth_service, x_api_key)
        records = records_for_owned_job(store, job_id, customer.customer_id)
        if not records:
            raise HTTPException(status_code=404, detail="Ingestion job not found.")
        return build_job_status(job_id, records)

    @app.get("/ingestion-jobs/{job_id}/errors")
    def get_ingestion_job_errors(job_id: str, x_api_key: str | None = Header(default=None)):
        customer = authenticate_request(auth_service, x_api_key)
        records = records_for_owned_job(store, job_id, customer.customer_id)
        if not records:
            raise HTTPException(status_code=404, detail="Ingestion job not found.")
        failed = [record for record in records if record.status == ProductStatus.FAILED]
        return {
            "job_id": job_id,
            "failed_rows": len(failed),
            "errors": [job_error_payload(record) for record in failed],
        }

    @app.post("/collections/{collection_id}/upload")
    async def upload_collection(
        collection_id: str,
        csv_file: UploadFile = File(...),
        x_api_key: str | None = Header(default=None),
        attribute_list_id: str | None = Form(None),
        attributes: str | None = Form(None),
        process_now: bool = Form(False),
    ):
        customer = authenticate_request(auth_service, x_api_key)
        existing_collection = store.get_collection_config(collection_id)
        if existing_collection is not None and existing_collection.customer_id != customer.customer_id:
            raise HTTPException(status_code=404, detail="Collection not found.")
        csv_text = (await csv_file.read()).decode("utf-8")
        try:
            usage_service.reserve_ingestion_rows(customer, count_csv_rows(csv_text))
        except PlanLimitError as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        attribute_list_uri = resolve_attribute_list_uri(attribute_list_id)
        resolved_attributes = resolve_attributes(attributes, attribute_list_uri)
        service = CsvIngestionService(
            store,
            customer_id=customer.customer_id,
            attributes=resolved_attributes,
            attribute_list_uri=attribute_list_uri,
            attribute_list_id=attribute_list_id,
            expected_collection_id=collection_id,
        )
        result = service.ingest_csv(csv_text, job_id=f"job_{collection_id}_{uuid.uuid4().hex[:12]}")
        if process_now:
            await EnrichmentWorker(store).drain_once()
        return serialize(result)

    return app


app = create_app() if FastAPI is not None else None


def serialize(value):
    if hasattr(value, "__dataclass_fields__"):
        payload = asdict(value)
    else:
        payload = value
    return json.loads(json.dumps(payload, default=str))


def records_for_owned_job(store, job_id: str, customer_id: str) -> list[ProductRecord]:
    return [
        record
        for record in store.records_for_job(job_id)
        if record.customer_id == customer_id
    ]


def build_job_status(job_id: str, records: list[ProductRecord]) -> dict:
    counts = {status.value: 0 for status in ProductStatus}
    for record in records:
        counts[record.status.value] = counts.get(record.status.value, 0) + 1

    accepted_rows = len(records)
    failed_rows = counts[ProductStatus.FAILED.value]
    terminal_rows = (
        counts[ProductStatus.ENRICHED.value]
        + failed_rows
        + counts[ProductStatus.DELETED.value]
    )
    if accepted_rows and terminal_rows == accepted_rows:
        status = "COMPLETED_WITH_ERRORS" if failed_rows else "READY"
    elif counts[ProductStatus.ENRICHING.value] or counts[ProductStatus.ENRICHED.value] or failed_rows:
        status = "PROCESSING"
    else:
        status = "QUEUED"

    return {
        "job_id": job_id,
        "status": status,
        "accepted_rows": accepted_rows,
        "queued_rows": counts[ProductStatus.QUEUED.value],
        "processing_rows": counts[ProductStatus.ENRICHING.value],
        "enriched_rows": counts[ProductStatus.ENRICHED.value],
        "failed_rows": failed_rows,
        "deleted_rows": counts[ProductStatus.DELETED.value],
        "products": [job_product_payload(record) for record in records],
    }


def job_product_payload(record: ProductRecord) -> dict:
    return {
        "collection_id": record.collection_id,
        "product_id": record.product_id,
        "name": record.raw_metadata.get("name"),
        "status": record.status.value,
        "updated_at": record.updated_at,
    }


def job_error_payload(record: ProductRecord) -> dict:
    return {
        **job_product_payload(record),
        "message": "Enrichment failed. Check worker logs for the underlying provider or validation error.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a catalog CSV and process rows immediately.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--customer-id", default="local_customer")
    parser.add_argument("--attributes", help='JSON array or comma-separated list, e.g. \'["occasion","color"]\'')
    parser.add_argument("--attribute-list-id", help="Stable id or file name for the collection attribute list")
    parser.add_argument("--attribute-list-uri", help="s3://bucket/key or local file containing the attribute JSON list")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--job-id", default="local-job")
    args = parser.parse_args()

    from shared.storage import JsonStore

    store = JsonStore(args.data_dir)
    resolved_attributes = resolve_attributes(args.attributes, args.attribute_list_uri)
    service = CsvIngestionService(
        store,
        customer_id=args.customer_id,
        attributes=resolved_attributes,
        attribute_list_uri=args.attribute_list_uri,
        attribute_list_id=args.attribute_list_id,
    )
    result = service.ingest_csv(args.csv_path.read_text(encoding="utf-8"), job_id=args.job_id)
    processed = asyncio.run(EnrichmentWorker(store).drain_once())
    payload = serialize(result)
    payload["processed_rows"] = len(processed)
    print(json.dumps(payload, indent=2))


def parse_attributes(value: str | None) -> list[str] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list) and all(isinstance(item, str) and item.strip() for item in parsed):
            return [item.strip() for item in parsed]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in value.split(",") if item.strip()]


def resolve_attributes(attributes: str | None, attribute_list_uri: str | None) -> list[str] | None:
    if attribute_list_uri:
        return load_attribute_list(attribute_list_uri).names
    return parse_attributes(attributes)


def resolve_attribute_list_uri(attribute_list_id: str | None) -> str | None:
    if not attribute_list_id:
        return None
    file_name = attribute_list_id.strip()
    if not file_name:
        return None
    if "://" in file_name or "/" in file_name or "\\" in file_name:
        raise HTTPException(status_code=400, detail="attribute_list_id must be a file name, not a URI or path.")
    if not file_name.endswith(".json"):
        file_name = f"{file_name}.json"

    base_uri = os.getenv("ATTRIBUTE_LIST_BASE_URI")
    if base_uri:
        return f"{base_uri.rstrip('/')}/{file_name}"

    bucket = os.getenv("ATTRIBUTE_LIST_BUCKET") or os.getenv("UPLOAD_BUCKET")
    prefix = os.getenv("ATTRIBUTE_LIST_PREFIX", "attributes").strip("/")
    if not bucket:
        raise HTTPException(status_code=500, detail="Attribute list bucket is not configured.")
    return f"s3://{bucket}/{prefix}/{file_name}" if prefix else f"s3://{bucket}/{file_name}"


def authenticate_request(auth_service: AuthService, x_api_key: str | None):
    try:
        return auth_service.authenticate_api_key(x_api_key)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def count_csv_rows(csv_text: str) -> int:
    rows = csv.reader(StringIO(csv_text))
    next(rows, None)
    return sum(1 for row in rows if any(cell.strip() for cell in row))


if __name__ == "__main__":
    main()
