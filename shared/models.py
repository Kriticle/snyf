from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Operation(str, Enum):
    UPSERT = "UPSERT"
    DELETE = "DELETE"


class ProductStatus(str, Enum):
    QUEUED = "QUEUED"
    ENRICHING = "ENRICHING"
    ENRICHED = "ENRICHED"
    FAILED = "FAILED"
    DELETED = "DELETED"


@dataclass(frozen=True)
class CsvIssue:
    row_number: int
    product_id: str | None
    field: str
    message: str
    severity: str = "error"


@dataclass
class IngestionMessage:
    customer_id: str
    collection_id: str
    product_id: str
    image_urls: list[str]
    metadata: dict[str, Any]
    attribute_schema: list[str]
    operation: Operation
    attribute_list_uri: str | None = None
    version: int = 0


@dataclass
class ProductRecord:
    customer_id: str
    collection_id: str
    product_id: str
    raw_metadata: dict[str, Any]
    image_urls: list[str]
    attribute_schema: list[str]
    attribute_list_uri: str | None = None
    enriched_attrs: dict[str, Any] = field(default_factory=dict)
    enriched_text: str = ""
    embedding: list[float] = field(default_factory=list)
    category: str | None = None
    price: float | None = None
    currency: str | None = None
    brand: str | None = None
    status: ProductStatus = ProductStatus.QUEUED
    version: int = 0
    created_at: str = field(default_factory=lambda: utc_now())
    updated_at: str = field(default_factory=lambda: utc_now())

    @property
    def document_id(self) -> str:
        return f"{self.collection_id}#{self.product_id}"


@dataclass
class CollectionConfig:
    collection_id: str
    customer_id: str
    attribute_list_id: str
    attribute_list_uri: str


@dataclass
class Customer:
    customer_id: str
    email: str
    password_hash: str
    status: str
    plan: str = "FREE"
    created_at: str = field(default_factory=lambda: utc_now())
    updated_at: str = field(default_factory=lambda: utc_now())


@dataclass
class Session:
    session_id: str
    customer_id: str
    refresh_token_hash: str
    status: str
    expires_at: int
    created_at: str = field(default_factory=lambda: utc_now())


@dataclass
class ApiKey:
    api_key_id: str
    customer_id: str
    api_key_hash: str
    status: str
    api_key_value: str | None = None
    created_at: str = field(default_factory=lambda: utc_now())
    last_used_at: str | None = None


@dataclass
class UsageCounter:
    usage_key: str
    customer_id: str
    period: str
    search_requests: int = 0
    ingestion_rows: int = 0
    updated_at: str = field(default_factory=lambda: utc_now())


@dataclass(frozen=True)
class SearchRequest:
    collection_id: str
    query: str
    top_k: int = 20


@dataclass
class SearchResult:
    product_id: str
    name: str | None
    image_urls: list[str]
    score: float
    feature_scores: dict[str, float]
    enriched_attrs: dict[str, Any]
    category: str | None = None
    price: float | None = None
    currency: str | None = None
    brand: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
