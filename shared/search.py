from __future__ import annotations

import json
from typing import Any, Callable

from functions.providers import embed_fn as default_embed_fn
from functions.providers import query_attribute_fn as default_query_attribute_fn
from shared.attribute_loader import load_attribute_list
from shared.models import SearchRequest, SearchResult
from shared.storage import JsonStore
from shared.vector_math import cosine_similarity, normalize_cosine

MAX_TOP_K = 100
RETRIEVAL_MULTIPLIER = 5

EmbedFn = Callable[[str], list[float]]
QueryAttributeFn = Callable[[str, list[str]], str | dict[str, Any]]


class SearchService:
    def __init__(
        self,
        store: JsonStore,
        embed_fn: EmbedFn | None = None,
        query_attribute_fn: QueryAttributeFn | None = None,
    ) -> None:
        self.store = store
        self.embed_fn = embed_fn or sync_embed
        self.query_attribute_fn = query_attribute_fn or sync_extract_query_attributes

    def search(self, request: SearchRequest) -> list[SearchResult]:
        validate_top_k(request.top_k)
        attribute_list = self._load_collection_attribute_list(request.collection_id)
        query_vector = run_sync(self.embed_fn(request.query))
        all_documents = self.store.indexed_documents(request.collection_id)
        extracted = parse_query_attribute_response(
            run_sync(self.query_attribute_fn(request.query, attribute_list.names))
        )
        weights = merge_weights(attribute_list.weights, extracted.get("weights", {}))
        candidates = [
            document
            for document in all_documents
            if passes_filters(document, extracted["hard_filters"])
        ]
        candidates.sort(
            key=lambda document: normalize_cosine(cosine_similarity(query_vector, document.get("embedding", []))),
            reverse=True,
        )
        candidate_limit = min(len(candidates), request.top_k * RETRIEVAL_MULTIPLIER)
        top_candidates = candidates[:candidate_limit]
        reranked = []
        for document in top_candidates:
            feature_scores = compute_feature_scores(
                query_vector=query_vector,
                extracted_attributes=extracted["attributes"],
                weights=weights,
                document=document,
                embed_fn=self.embed_fn,
            )
            reranked.append(to_result(document, feature_scores))
        reranked.sort(key=lambda result: result.score, reverse=True)
        return reranked[: request.top_k]

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
