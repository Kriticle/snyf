from __future__ import annotations

import argparse
import asyncio
import csv
import json
from dataclasses import asdict
from io import StringIO
from pathlib import Path

from services.enrichment_worker.worker import EnrichmentWorker
from shared.attribute_loader import load_attribute_list
from shared.auth import AuthError, AuthService
from shared.csv_ingestion import CsvIngestionService
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

    @app.post("/collections/{collection_id}/upload")
    async def upload_collection(
        collection_id: str,
        csv_file: UploadFile = File(...),
        x_api_key: str | None = Header(default=None),
        attribute_list_id: str | None = Form(None),
        attribute_list_uri: str | None = Form(None),
        attributes: str | None = Form(None),
        process_now: bool = Form(True),
    ):
        customer = authenticate_request(auth_service, x_api_key)
        csv_text = (await csv_file.read()).decode("utf-8")
        try:
            usage_service.reserve_ingestion_rows(customer, count_csv_rows(csv_text))
        except PlanLimitError as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        resolved_attributes = resolve_attributes(attributes, attribute_list_uri)
        service = CsvIngestionService(
            store,
            customer_id=customer.customer_id,
            attributes=resolved_attributes,
            attribute_list_uri=attribute_list_uri,
            attribute_list_id=attribute_list_id,
        )
        result = service.ingest_csv(csv_text, job_id=f"job_{collection_id}")
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


def authenticate_request(auth_service: AuthService, x_api_key: str | None):
    try:
        return auth_service.authenticate_api_key(x_api_key)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def count_csv_rows(csv_text: str) -> int:
    return sum(1 for _ in csv.DictReader(StringIO(csv_text)))


if __name__ == "__main__":
    main()
