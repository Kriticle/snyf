from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from shared.models import SearchRequest
from shared.auth import AuthError, AuthService
from shared.plans import PlanLimitError, UsageService
from shared.search import SearchService
from shared.store_factory import create_store
from shared.storage import JsonStore

try:
    from fastapi import FastAPI, Header, HTTPException
    from pydantic import BaseModel
except ImportError:  # pragma: no cover - optional runtime adapter
    FastAPI = None
    Header = HTTPException = None
    BaseModel = object
    Field = None


if FastAPI is not None:
    class SearchPayload(BaseModel):
        collection_id: str
        query: str
        top_k: int = 20


def create_app(data_dir: str = "data"):
    if FastAPI is None:
        raise RuntimeError("FastAPI is not installed. Use the CLI mode or install requirements.txt.")

    app = FastAPI(title="Fashion Attribute Enrichment - Search API")
    store = create_store(data_dir)
    service = SearchService(store)
    auth_service = AuthService(store)
    usage_service = UsageService(store)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/search")
    def search(payload: SearchPayload, x_api_key: str | None = Header(default=None)):
        customer = authenticate_request(auth_service, x_api_key)
        try:
            usage_service.reserve_search(customer)
        except PlanLimitError as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        collection = store.get_collection_config(payload.collection_id)
        if collection is None or collection.customer_id != customer.customer_id:
            raise HTTPException(status_code=404, detail="Collection not found.")
        request = SearchRequest(
            collection_id=payload.collection_id,
            query=payload.query,
            top_k=payload.top_k,
        )
        results = service.search(request)
        return {"results": [serialize(result) for result in results]}

    @app.post("/search/filters")
    async def filters(payload: dict):
        collection_id = payload["collection_id"]
        docs = JsonStore(data_dir).indexed_documents(collection_id)
        facets: dict[str, dict[str, int]] = {"category": {}}
        for doc in docs:
            category = doc.get("category")
            if category:
                facets["category"][category] = facets["category"].get(category, 0) + 1
            for key, value in doc.get("attrs", {}).items():
                if value is None:
                    continue
                facet_key = f"attrs.{key}"
                facets.setdefault(facet_key, {})
                facets[facet_key][str(value)] = facets[facet_key].get(str(value), 0) + 1
        return {"facets": facets, "total_products": len(docs)}

    return app


app = create_app() if FastAPI is not None else None


def serialize(value):
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value


def authenticate_request(auth_service: AuthService, x_api_key: str | None):
    try:
        return auth_service.authenticate_api_key(x_api_key)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Search a locally enriched collection.")
    parser.add_argument("query")
    parser.add_argument("--collection-id", required=True)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()

    service = SearchService(JsonStore(args.data_dir))
    results = service.search_sync(
        SearchRequest(collection_id=args.collection_id, query=args.query, top_k=args.top_k)
    )
    print(json.dumps({"results": [serialize(result) for result in results]}, indent=2))


if __name__ == "__main__":
    main()
