from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from functions.providers import embed_fn as default_embed_fn
from functions.providers import embed_many_fn as default_embed_many_fn
from functions.providers import query_attribute_fn as default_query_attribute_fn
from shared.attribute_loader import load_attribute_list
from shared.models import SearchRequest, SearchResult
from shared.storage import JsonStore
from shared.vector_math import cosine_similarity, normalize_cosine

MAX_TOP_K = 100
RETRIEVAL_MULTIPLIER = 5

EmbedFn = Callable[[str], list[float]]
EmbedManyFn = Callable[[list[str]], list[list[float]]]
QueryAttributeFn = Callable[[str, list[str]], str | dict[str, Any]]


@dataclass
class SearchMetrics:
    total_ms: float = 0.0
    steps_ms: dict[str, float] = field(default_factory=dict)
    document_count: int = 0
    filtered_count: int = 0
    candidate_count: int = 0
    result_count: int = 0
    embedding_calls: int = 0
    embedding_texts: int = 0
    embedding_cache_hits: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_ms": round(self.total_ms, 2),
            "steps_ms": {key: round(value, 2) for key, value in self.steps_ms.items()},
            "document_count": self.document_count,
            "filtered_count": self.filtered_count,
            "candidate_count": self.candidate_count,
            "result_count": self.result_count,
            "embedding_calls": self.embedding_calls,
            "embedding_texts": self.embedding_texts,
            "embedding_cache_hits": self.embedding_cache_hits,
        }


class SearchService:
    def __init__(
        self,
        store: JsonStore,
        embed_fn: EmbedFn | None = None,
        embed_many_fn: EmbedManyFn | None = None,
        query_attribute_fn: QueryAttributeFn | None = None,
    ) -> None:
        self.store = store
        self.embed_fn = embed_fn or sync_embed
        if embed_many_fn:
            self.embed_many_fn = embed_many_fn
        elif embed_fn:
            self.embed_many_fn = lambda texts: [run_sync(self.embed_fn(text)) for text in texts]
        else:
            self.embed_many_fn = sync_embed_many
        self.query_attribute_fn = query_attribute_fn or sync_extract_query_attributes
        self.last_metrics = SearchMetrics()

    def search(self, request: SearchRequest) -> list[SearchResult]:
        results, metrics = self.search_with_metrics(request)
        self.last_metrics = metrics
        return results

    def search_with_metrics(self, request: SearchRequest) -> tuple[list[SearchResult], SearchMetrics]:
        metrics = SearchMetrics()
        started_at = time.perf_counter()
        embedding_cache: dict[str, list[float]] = {}

        def timed_step(name: str, fn):
            step_started = time.perf_counter()
            try:
                return fn()
            finally:
                metrics.steps_ms[name] = elapsed_ms(step_started)

        def embed_cached(text: str) -> list[float]:
            if text in embedding_cache:
                metrics.embedding_cache_hits += 1
                return embedding_cache[text]
            metrics.embedding_calls += 1
            metrics.embedding_texts += 1
            vector = run_sync(self.embed_fn(text))
            embedding_cache[text] = vector
            return vector

        def embed_many_cached(texts: list[str]) -> None:
            missing = []
            seen = set()
            for text in texts:
                if text in embedding_cache:
                    metrics.embedding_cache_hits += 1
                    continue
                if text in seen:
                    metrics.embedding_cache_hits += 1
                    continue
                seen.add(text)
                missing.append(text)
            if not missing:
                return
            metrics.embedding_calls += 1
            metrics.embedding_texts += len(missing)
            vectors = run_sync(self.embed_many_fn(missing))
            if len(vectors) != len(missing):
                raise ValueError("embed_many() must return one embedding per input text.")
            for text, vector in zip(missing, vectors):
                embedding_cache[text] = vector

        timed_step("validate_top_k", lambda: validate_top_k(request.top_k))
        attribute_list = timed_step(
            "load_attribute_list",
            lambda: self._load_collection_attribute_list(request.collection_id),
        )
        with ThreadPoolExecutor(max_workers=3) as executor:
            query_vector_future = executor.submit(
                timed_step,
                "embed_query",
                lambda: embed_cached(request.query),
            )
            documents_future = executor.submit(
                timed_step,
                "load_documents",
                lambda: self.store.indexed_documents(request.collection_id),
            )
            extracted_future = executor.submit(
                timed_step,
                "extract_query_attributes",
                lambda: parse_query_attribute_response(
                    run_sync(self.query_attribute_fn(request.query, attribute_list.names))
                ),
            )
            query_vector = query_vector_future.result()
            all_documents = documents_future.result()
            extracted = extracted_future.result()
        metrics.document_count = len(all_documents)
        weights = timed_step(
            "merge_weights",
            lambda: merge_weights(attribute_list.weights, extracted.get("weights", {})),
        )
        candidates = timed_step(
            "apply_hard_filters",
            lambda: [
                document
                for document in all_documents
                if passes_filters(document, extracted["hard_filters"])
            ],
        )
        metrics.filtered_count = len(candidates)

        def sort_candidates() -> None:
            candidates.sort(
                key=lambda document: normalize_cosine(cosine_similarity(query_vector, document.get("embedding", []))),
                reverse=True,
            )

        timed_step("vector_candidate_sort", sort_candidates)
        candidate_limit = min(len(candidates), request.top_k * RETRIEVAL_MULTIPLIER)
        top_candidates = candidates[:candidate_limit]
        metrics.candidate_count = len(top_candidates)
        timed_step(
            "embed_rerank_attributes",
            lambda: embed_many_cached(
                rerank_embedding_texts(
                    extracted_attributes=extracted["attributes"],
                    documents=top_candidates,
                )
            ),
        )

        def rerank_candidates() -> list[SearchResult]:
            reranked_results = []
            for document in top_candidates:
                feature_scores = compute_feature_scores(
                    query_vector=query_vector,
                    extracted_attributes=extracted["attributes"],
                    weights=weights,
                    document=document,
                    embed_fn=embed_cached,
                )
                reranked_results.append(to_result(document, feature_scores))
            return reranked_results

        reranked = timed_step("rerank_candidates", rerank_candidates)
        timed_step("sort_results", lambda: reranked.sort(key=lambda result: result.score, reverse=True))
        results = reranked[: request.top_k]
        metrics.result_count = len(results)
        metrics.total_ms = elapsed_ms(started_at)
        self.last_metrics = metrics
        return results, metrics

    def search_sync(self, request: SearchRequest) -> list[SearchResult]:
        return self.search(request)

    def _load_collection_attribute_list(self, collection_id: str):
        config = self.store.get_collection_config(collection_id)
        if config is None:
            raise ValueError(f"No attribute list is registered for collection '{collection_id}'.")
        return load_attribute_list(config.attribute_list_uri)


def passes_filters(document: dict[str, Any], filters: dict[str, Any]) -> bool:
    for field, expected in filters.items():
        if field == "price_max":
            price = document.get("price")
            if price is None or price > expected:
                return False
            continue
        if field == "price_min":
            price = document.get("price")
            if price is None or price < expected:
                return False
            continue
        if field not in {"category"}:
            raise ValueError("Only category, price_min, and price_max are supported as hard filters.")
        actual = lookup_field(document, field)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def lookup_field(document: dict[str, Any], field: str) -> Any:
    return document.get(field)


def compute_feature_scores(
    query_vector: list[float],
    extracted_attributes: dict[str, Any],
    weights: dict[str, float],
    document: dict[str, Any],
    embed_fn: EmbedFn,
) -> dict[str, float]:
    scores: dict[str, float] = {
        "_retrieval_semantic": normalize_cosine(cosine_similarity(query_vector, document.get("embedding", []))),
        "_weight_total": 0.0,
    }
    document_attrs = document.get("attrs", {})
    for attribute, query_value in extracted_attributes.items():
        document_value = document_attrs.get(attribute)
        if query_value is None or document_value is None:
            continue
        query_attr_vector = run_sync(embed_fn(f"{attribute}: {query_value}"))
        document_attr_vector = run_sync(embed_fn(f"{attribute}: {document_value}"))
        similarity = normalize_cosine(cosine_similarity(query_attr_vector, document_attr_vector))
        weight = weights.get(attribute, 1.0)
        positive_weight = max(weight, 0.0)
        if positive_weight == 0.0:
            continue
        scores[attribute] = similarity * positive_weight
        scores["_weight_total"] += positive_weight
    return scores


def rerank_embedding_texts(
    extracted_attributes: dict[str, Any],
    documents: list[dict[str, Any]],
) -> list[str]:
    texts: list[str] = []
    for attribute, query_value in extracted_attributes.items():
        if query_value is None:
            continue
        texts.append(f"{attribute}: {query_value}")
        for document in documents:
            document_value = document.get("attrs", {}).get(attribute)
            if document_value is not None:
                texts.append(f"{attribute}: {document_value}")
    return texts


def to_result(document: dict[str, Any], feature_scores: dict[str, float]) -> SearchResult:
    ranking_scores = {
        key: value
        for key, value in feature_scores.items()
        if not key.startswith("_")
    }
    if ranking_scores:
        weight_total = feature_scores.get("_weight_total", 0.0)
        score = sum(ranking_scores.values()) / weight_total if weight_total else 0.0
    else:
        score = feature_scores.get("_retrieval_semantic", 0.0)
    return SearchResult(
        product_id=document["product_id"],
        name=document.get("name"),
        image_urls=document.get("image_urls", []),
        score=round(score, 6),
        feature_scores={key: round(value, 6) for key, value in feature_scores.items()},
        enriched_attrs=document.get("attrs", {}),
        category=document.get("category"),
        price=document.get("price"),
        currency=document.get("currency"),
        brand=document.get("brand"),
    )


def available_attributes(documents: list[dict[str, Any]]) -> list[str]:
    attributes: set[str] = set()
    for document in documents:
        attributes.update(document.get("attrs", {}).keys())
    return sorted(attributes)


def validate_top_k(top_k: int) -> None:
    if top_k < 1:
        raise ValueError("top_k must be at least 1.")
    if top_k > MAX_TOP_K:
        raise ValueError(f"top_k cannot exceed {MAX_TOP_K}.")


def parse_query_attribute_response(response: str | dict[str, Any]) -> dict[str, Any]:
    parsed = json.loads(response) if isinstance(response, str) else response
    if not isinstance(parsed, dict):
        raise ValueError("Query attribute extractor must return a JSON object string or dict.")
    attributes = parsed.get("attributes", {})
    hard_filters = parsed.get("hard_filters", {})
    weights = parsed.get("weights", {})
    if not isinstance(attributes, dict):
        raise ValueError("Query attribute extractor field 'attributes' must be an object.")
    if not isinstance(hard_filters, dict):
        raise ValueError("Query attribute extractor field 'hard_filters' must be an object.")
    if not isinstance(weights, dict):
        raise ValueError("Query attribute extractor field 'weights' must be an object.")
    clean_attributes = {
        str(key): value
        for key, value in attributes.items()
        if isinstance(key, str) and key.strip() and value is not None
    }
    clean_weights: dict[str, float] = {}
    for key, value in weights.items():
        try:
            clean_weights[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return {
        "attributes": clean_attributes,
        "hard_filters": clean_hard_filters(hard_filters),
        "weights": clean_weights,
    }


def merge_weights(attribute_weights: dict[str, float], extracted_weights: dict[str, float]) -> dict[str, float]:
    merged = {key: value for key, value in attribute_weights.items()}
    for key, value in extracted_weights.items():
        if key in merged:
            merged[key] = value
    return merged


def clean_hard_filters(filters: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    if "category" in filters and filters["category"] not in (None, ""):
        clean["category"] = filters["category"]
    for field in ("price_min", "price_max"):
        if field not in filters or filters[field] in (None, ""):
            continue
        try:
            clean[field] = float(filters[field])
        except (TypeError, ValueError):
            continue
    return clean


def sync_embed(text: str) -> list[float]:
    return run_sync(default_embed_fn()(text))


def sync_embed_many(texts: list[str]) -> list[list[float]]:
    return run_sync(default_embed_many_fn()(texts))


def sync_extract_query_attributes(query: str, available_attributes: list[str]) -> str | dict[str, Any]:
    return run_sync(default_query_attribute_fn()(query, available_attributes))


def run_sync(value):
    if hasattr(value, "__await__"):
        try:
            import asyncio

            return asyncio.run(value)
        except RuntimeError as exc:
            raise RuntimeError("Async default functions cannot run inside an existing event loop.") from exc
    return value


def elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000
