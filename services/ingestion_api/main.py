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
from shared.email import create_email_sender
from shared.models import ProductRecord, ProductStatus
from shared.plans import PlanLimitError, UsageService
from shared.store_factory import create_store

try:
    from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except ImportError:  # pragma: no cover - optional runtime adapter
    FastAPI = None
    CORSMiddleware = None
    File = Form = Header = HTTPException = UploadFile = None
    BaseModel = object


if FastAPI is not None:
    class AuthPayload(BaseModel):
        email: str
        password: str

    class VerifyEmailPayload(BaseModel):
        email: str
        code: str | None = None
        token: str | None = None


def create_app(data_dir: str = "data", expose_verification_code: bool = False):
    if FastAPI is None:
        raise RuntimeError("FastAPI is not installed. Use the CLI mode or install requirements.txt.")

    app = FastAPI(title="Fashion Attribute Enrichment - Ingestion API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    store = create_store(data_dir)
    auth_service = AuthService(
        store,
        email_sender=create_email_sender(
            allow_console_fallback=expose_verification_code or allow_console_email_fallback()
        ),
        verification_url_base=os.getenv("SNYF_VERIFICATION_URL_BASE"),
        expose_verification_code=expose_verification_code,
        require_email_verification=require_email_verification(),
    )
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

    @app.post("/auth/verify-email")
    def verify_email(payload: VerifyEmailPayload):
        try:
            return auth_service.verify_email(payload.email, code=payload.code, token=payload.token)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/auth/verify-email")
    def verify_email_link(email: str, token: str | None = None, verification_token: str | None = None):
        try:
            auth_service.verify_email(email, token=token or verification_token, issue_credentials=False)
            return {"verified": True, "message": "Email verified. Return to Snyf and log in."}
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

    @app.get("/collections/{collection_id}")
    def get_collection(collection_id: str, x_api_key: str | None = Header(default=None)):
        customer = authenticate_request(auth_service, x_api_key)
        collection = store.get_collection_config(collection_id)
        if collection is None:
            return {
                "collection_id": collection_id,
                "exists": False,
                "attribute_list_locked": False,
                "attribute_list_id": None,
            }
        if collection.customer_id != customer.customer_id:
            raise HTTPException(status_code=404, detail="Collection not found.")
        return {
            "collection_id": collection.collection_id,
            "exists": True,
            "attribute_list_locked": True,
            "attribute_list_id": collection.attribute_list_id,
        }

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
        attribute_list_id: str = Form(...),
        process_now: bool = Form(False),
    ):
        customer = authenticate_request(auth_service, x_api_key)
        existing_collection = store.get_collection_config(collection_id)
        if existing_collection is not None and existing_collection.customer_id != customer.customer_id:
            raise HTTPException(status_code=404, detail="Collection not found.")
        if existing_collection is not None and existing_collection.attribute_list_id != attribute_list_id:
            raise HTTPException(
                status_code=400,
                detail="Collection already has an attribute_list_id. Existing collections cannot change attribute lists during upload.",
            )
        csv_text = (await csv_file.read()).decode("utf-8")
        try:
            usage_service.reserve_ingestion_rows(customer, count_csv_rows(csv_text))
        except PlanLimitError as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        attribute_list_uri = existing_collection.attribute_list_uri if existing_collection else resolve_attribute_list_uri(attribute_list_id)
        resolved_attributes = resolve_attributes(attribute_list_uri)
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


def allow_console_email_fallback() -> bool:
    if os.getenv("ALLOW_CONSOLE_EMAIL_FALLBACK", "").lower() in {"1", "true", "yes"}:
        return True
    environment = os.getenv("ENVIRONMENT") or os.getenv("APP_ENV") or os.getenv("STAGE")
    if (environment or "").lower() in {"local", "test", "dev"}:
        return True
    resource_names = " ".join(
        [
            os.getenv("PRODUCT_TABLE_NAME", ""),
            os.getenv("COLLECTION_TABLE_NAME", ""),
            os.getenv("PRODUCT_QUEUE_URL", ""),
            os.getenv("UPLOAD_BUCKET", ""),
        ]
    ).lower()
    if "dev" in resource_names:
        return True
    return not (os.getenv("AWS_EXECUTION_ENV") or os.getenv("ECS_CONTAINER_METADATA_URI_V4"))


def require_email_verification() -> bool:
    value = os.getenv("REQUIRE_EMAIL_VERIFICATION")
    if value is None:
        return True
    return value.lower() not in {"0", "false", "no"}


app = (
    create_app(expose_verification_code=os.getenv("EXPOSE_VERIFICATION_CODE", "").lower() in {"1", "true", "yes"})
    if FastAPI is not None
    else None
)


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
    parser.add_argument("--attribute-list-id", required=True, help="Stable id or file name for the collection attribute list")
    parser.add_argument("--attribute-list-uri", required=True, help="s3://bucket/key or local file containing the attribute JSON list")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--job-id", default="local-job")
    args = parser.parse_args()

    from shared.storage import JsonStore

    store = JsonStore(args.data_dir)
    resolved_attributes = resolve_attributes(args.attribute_list_uri)
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


def resolve_attributes(attribute_list_uri: str | None) -> list[str] | None:
    if attribute_list_uri:
        return load_attribute_list(attribute_list_uri).names
    return None


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
