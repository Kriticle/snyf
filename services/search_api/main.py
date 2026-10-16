from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import asdict

from shared.models import SearchRequest
from shared.auth import AuthError, AuthService
from shared.plans import PlanLimitError, UsageService
from shared.search import SearchService, validate_top_k
from shared.store_factory import create_store
from shared.storage import JsonStore

logger = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except ImportError:  # pragma: no cover - optional runtime adapter
    FastAPI = None
    CORSMiddleware = None
    Header = HTTPException = Request = None
    BaseModel = object
    Field = None


if FastAPI is not None:
    class SearchPayload(BaseModel):
        collection_id: str
        query: str
        top_k: int = 20


    class DemoSearchPayload(BaseModel):
        query: str

        class Config:
            extra = "forbid"


def create_app(data_dir: str = "data"):
    if FastAPI is None:
        raise RuntimeError("FastAPI is not installed. Use the CLI mode or install requirements.txt.")

    app = FastAPI(title="Fashion Attribute Enrichment - Search API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in os.getenv("SNYF_CORS_ORIGINS", "http://127.0.0.1:4173").split(",")],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type", "x-api-key"],
    )
    store = create_store(data_dir)
    service = SearchService(store)
    auth_service = AuthService(store)
    usage_service = UsageService(store)
    demo_rate_limiter = demo_rate_limiter_for(store)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/search")
    def search(payload: SearchPayload, x_api_key: str | None = Header(default=None)):
        return execute_search(
            payload=payload,
            x_api_key=x_api_key,
            auth_service=auth_service,
            usage_service=usage_service,
            store=store,
            search_fn=service.search_with_metrics,
            metric_name="search",
        )

    @app.post("/search/keyword")
    def keyword_search(payload: SearchPayload, x_api_key: str | None = Header(default=None)):
        return execute_search(
            payload=payload,
            x_api_key=x_api_key,
            auth_service=auth_service,
            usage_service=usage_service,
            store=store,
            search_fn=service.keyword_search_with_metrics,
            metric_name="keyword_search",
        )

    @app.post("/search/demo")
    def demo_search(payload: DemoSearchPayload, request: Request):
        return execute_demo_search(
            payload=payload,
            request=request,
            rate_limiter=demo_rate_limiter,
            search_fn=service.search_with_metrics,
            metric_name="demo_search",
        )

    @app.post("/search/demo/keyword")
    def demo_keyword_search(payload: DemoSearchPayload, request: Request):
        return execute_demo_search(
            payload=payload,
            request=request,
            rate_limiter=demo_rate_limiter,
            search_fn=service.keyword_search_with_metrics,
            metric_name="demo_keyword_search",
        )

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


def execute_search(
    *,
    payload,
    x_api_key: str | None,
    auth_service: AuthService,
    usage_service: UsageService,
    store,
    search_fn,
    metric_name: str,
):
    request_started = time.perf_counter()
    api_steps: dict[str, float] = {}

    def timed_step(name: str, fn):
        step_started = time.perf_counter()
        try:
            return fn()
        finally:
            api_steps[name] = elapsed_ms(step_started)

    customer = timed_step("authenticate_api_key", lambda: authenticate_request(auth_service, x_api_key))
    try:
        timed_step("validate_top_k", lambda: validate_top_k(payload.top_k))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        timed_step("reserve_usage", lambda: usage_service.reserve_search(customer))
    except PlanLimitError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    collection = timed_step("load_collection", lambda: store.get_collection_config(payload.collection_id))
    if collection is None or collection.customer_id != customer.customer_id:
        raise HTTPException(status_code=404, detail="Collection not found.")
    request = SearchRequest(
        collection_id=payload.collection_id,
        query=payload.query,
        top_k=payload.top_k,
    )
    results, search_metrics = timed_step("search_service", lambda: search_fn(request))
    api_total_ms = elapsed_ms(request_started)
    metrics = {
        "api_total_ms": round(api_total_ms, 2),
        "api_steps_ms": {key: round(value, 2) for key, value in api_steps.items()},
        "search": search_metrics.to_dict(),
    }
    logger.info(
        "%s latency metrics customer_id=%s collection_id=%s top_k=%s metrics=%s",
        metric_name,
        customer.customer_id,
        payload.collection_id,
        payload.top_k,
        json.dumps(metrics, sort_keys=True),
    )
    return {"results": [serialize(result) for result in results], "metrics": metrics}


def execute_demo_search(*, payload, request: Request, rate_limiter, search_fn, metric_name: str):
    demo_collection_id = os.getenv("DEMO_COLLECTION_ID", "live_smoke_1778332579")
    demo_top_k = int(os.getenv("DEMO_TOP_K", "10"))
    client_id = demo_client_id(request)
    try:
        rate_limiter.check(client_id)
        validate_top_k(demo_top_k)
    except DemoRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request = SearchRequest(
        collection_id=demo_collection_id,
        query=payload.query,
        top_k=demo_top_k,
    )
    try:
        results, search_metrics = search_fn(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "%s client_id=%s collection_id=%s top_k=%s metrics=%s",
        metric_name,
        client_id,
        demo_collection_id,
        demo_top_k,
        json.dumps(search_metrics.to_dict(), sort_keys=True),
    )
    return {
        "collection_id": demo_collection_id,
        "results": [serialize(result) for result in results],
        "metrics": {"search": search_metrics.to_dict()},
    }


class DemoRateLimitError(ValueError):
    pass


class DemoRateLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.requests: dict[str, deque[float]] = defaultdict(deque)

    @classmethod
    def from_environment(cls) -> "DemoRateLimiter":
        limit = int(os.getenv("DEMO_RATE_LIMIT_PER_MINUTE", "30"))
        return cls(limit=max(limit, 1))

    def check(self, client_id: str) -> None:
        now = time.monotonic()
        bucket = self.requests[client_id]
        cutoff = now - self.window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.limit:
            raise DemoRateLimitError("Demo search rate limit exceeded. Please try again in a minute.")
        bucket.append(now)


class SharedDemoRateLimiter:
    def __init__(self, store, limit: int, window_seconds: int = 60) -> None:
        self.store = store
        self.limit = limit
        self.window_seconds = window_seconds

    def check(self, client_id: str) -> None:
        now = int(time.time())
        window_start = now - (now % self.window_seconds)
        allowed = self.store.reserve_demo_request(
            client_id=client_id,
            window_start=window_start,
            limit=self.limit,
            ttl=window_start + (self.window_seconds * 2),
        )
        if not allowed:
            raise DemoRateLimitError("Demo search rate limit exceeded. Please try again in a minute.")


def demo_rate_limiter_for(store):
    limit = max(int(os.getenv("DEMO_RATE_LIMIT_PER_MINUTE", "30")), 1)
    if callable(getattr(store, "reserve_demo_request", None)):
        return SharedDemoRateLimiter(store, limit=limit)
    return DemoRateLimiter(limit=limit)


def demo_client_id(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


app = create_app(os.getenv("SNYF_DATA_DIR", "data")) if FastAPI is not None else None


def serialize(value):
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value


def elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000


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
