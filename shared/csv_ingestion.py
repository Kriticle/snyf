from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from io import StringIO
from typing import Any

from shared.attribute_loader import AttributeList, load_attribute_list
from shared.models import CollectionConfig
from shared.models import CsvIssue, IngestionMessage, Operation
from shared.storage import JsonStore


BASE_REQUIRED_COLUMNS = {"product_id", "name", "image_urls"}
REQUIRED_COLUMNS = BASE_REQUIRED_COLUMNS | {"collection_id"}
OPTIONAL_COLUMNS = {
    "category",
    "description",
    "price",
    "currency",
    "brand",
    "attributes",
    "attribute_schema_id",
    "_deleted",
}


@dataclass
class UploadResult:
    job_id: str
    accepted_rows: int = 0
    rejected_rows: int = 0
    warnings: int = 0
    status: str = "QUEUED"
    issues: list[CsvIssue] = field(default_factory=list)
    messages: list[IngestionMessage] = field(default_factory=list)


class CsvIngestionService:
    def __init__(
        self,
        store: JsonStore,
        customer_id: str,
        attributes: list[str] | None = None,
        attribute_list_uri: str | None = None,
        attribute_list_id: str | None = None,
        attribute_schemas: dict[str, list[str]] | None = None,
        expected_collection_id: str | None = None,
    ) -> None:
        self.store = store
        self.customer_id = customer_id
        self.attributes = attributes
        self.attribute_list_uri = attribute_list_uri
        self.attribute_list_id = attribute_list_id
        self.attribute_schemas = attribute_schemas or {}
        self.expected_collection_id = expected_collection_id
        self.attribute_list = self._load_attribute_list()

    def ingest_csv(self, csv_text: str, job_id: str = "local-job") -> UploadResult:
        result = UploadResult(job_id=job_id)
        reader = csv.DictReader(StringIO(csv_text))
        headers = set(reader.fieldnames or [])
        required_columns = BASE_REQUIRED_COLUMNS if self.expected_collection_id else REQUIRED_COLUMNS
        missing_headers = required_columns - headers
        if missing_headers:
            for column in sorted(missing_headers):
                result.issues.append(CsvIssue(0, None, column, "Missing required column header."))
            result.status = "REJECTED"
            return result

        seen: set[tuple[str, str]] = set()
        for index, row in enumerate(reader, start=2):
            message, issues = self._row_to_message(row, index)
            result.issues.extend(issues)
            row_errors = [issue for issue in issues if issue.severity == "error"]
            result.warnings += sum(1 for issue in issues if issue.severity == "warning")
            if row_errors:
                result.rejected_rows += 1
                continue
            assert message is not None
            dedupe_key = (message.collection_id, message.product_id)
            if dedupe_key in seen:
                result.rejected_rows += 1
                result.issues.append(
                    CsvIssue(index, message.product_id, "product_id", "Duplicate product_id within this CSV.")
                )
                continue
            seen.add(dedupe_key)
            self.store.enqueue(message)
            result.messages.append(message)
            self._upsert_collection_config(message.collection_id)
            result.accepted_rows += 1

        if result.accepted_rows == 0 and result.rejected_rows > 0:
            result.status = "REJECTED"
        return result

    def _row_to_message(
        self,
        row: dict[str, str | None],
        row_number: int,
    ) -> tuple[IngestionMessage | None, list[CsvIssue]]:
        issues: list[CsvIssue] = []
        product_id = clean(row.get("product_id"))
        row_collection_id = clean(row.get("collection_id"))
        collection_id = row_collection_id or self.expected_collection_id
        if not product_id:
            issues.append(CsvIssue(row_number, None, "product_id", "product_id is required."))
        if not collection_id:
            issues.append(CsvIssue(row_number, product_id, "collection_id", "collection_id is required."))
        elif self.expected_collection_id and row_collection_id and row_collection_id != self.expected_collection_id:
            issues.append(
                CsvIssue(
                    row_number,
                    product_id,
                    "collection_id",
                    f"collection_id must match upload path collection '{self.expected_collection_id}'.",
                )
            )
        if not clean(row.get("name")):
            issues.append(CsvIssue(row_number, product_id, "name", "name is required."))

        image_urls = parse_image_urls(row.get("image_urls"))
        if not image_urls:
            issues.append(CsvIssue(row_number, product_id, "image_urls", "At least one image URL is required."))
        for url in image_urls:
            if not url.startswith("https://"):
                issues.append(
                    CsvIssue(row_number, product_id, "image_urls", f"Non-HTTPS URL skipped: {url}", "warning")
                )
        image_urls = [url for url in image_urls if url.startswith("https://")]
        if not image_urls:
            issues.append(CsvIssue(row_number, product_id, "image_urls", "At least one usable HTTPS image URL is required."))
        if len(image_urls) > 5:
            issues.append(CsvIssue(row_number, product_id, "image_urls", "Only first 5 image URLs are used.", "warning"))
            image_urls = image_urls[:5]

        operation = Operation.DELETE if parse_bool(row.get("_deleted")) else Operation.UPSERT
        attributes: list[str] = []
        if operation == Operation.UPSERT:
            attributes, attribute_issues = self._resolve_attributes(row, row_number, product_id)
            issues.extend(attribute_issues)

        if any(issue.severity == "error" for issue in issues):
            return None, issues

        assert product_id is not None
        assert collection_id is not None
        collection_config = self.store.get_collection_config(collection_id)
        if collection_config is not None and collection_config.customer_id != self.customer_id:
            issues.append(CsvIssue(row_number, product_id, "collection_id", "Collection not found."))
            return None, issues
        existing = self.store.get_record(collection_id, product_id)
        if operation == Operation.DELETE and existing is None:
            issues.append(
                CsvIssue(row_number, product_id, "_deleted", "Product does not exist; delete skipped.", "warning")
            )
            return None, issues

        metadata: dict[str, Any] = {
            "name": clean(row.get("name")),
            "category": clean(row.get("category")),
            "description": clean(row.get("description")),
            "price": parse_float(row.get("price")),
            "currency": clean(row.get("currency")),
            "brand": clean(row.get("brand")),
        }
        metadata = {key: value for key, value in metadata.items() if value is not None and value != ""}

        return (
            IngestionMessage(
                customer_id=self.customer_id,
                collection_id=collection_id,
                product_id=product_id,
                image_urls=image_urls,
                metadata=metadata,
                attribute_schema=attributes,
                operation=operation,
                attribute_list_uri=self.attribute_list_uri,
                version=existing.version if existing else 0,
            ),
            issues,
        )

    def _resolve_attributes(
        self,
        row: dict[str, str | None],
        row_number: int,
        product_id: str | None,
    ) -> tuple[list[str], list[CsvIssue]]:
        issues: list[CsvIssue] = []
        if self.attribute_list:
            return self.attribute_list.names, issues
        if self.attributes:
            return self.attributes, issues
        schema_id = clean(row.get("attribute_schema_id"))
        if schema_id:
            schema = self.attribute_schemas.get(schema_id)
            if schema:
                return schema, issues
            issues.append(
                CsvIssue(row_number, product_id, "attribute_schema_id", "Unknown schema.", "error")
            )
        raw = clean(row.get("attributes"))
        if not raw:
            issues.append(
                CsvIssue(
                    row_number,
                    product_id,
                    "attributes",
                    "No attribute list was provided by API parameter, attribute_list_uri, schema, or row.",
                )
            )
            return [], issues
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            issues.append(CsvIssue(row_number, product_id, "attributes", "Invalid JSON attribute list."))
            return [], issues
        if not isinstance(parsed, list) or not all(isinstance(item, str) and item.strip() for item in parsed):
            issues.append(CsvIssue(row_number, product_id, "attributes", "Invalid attribute list."))
            return [], issues
        return [item.strip() for item in parsed], issues

    def _load_attribute_list(self) -> AttributeList | None:
        if not self.attribute_list_uri:
            return None
        return load_attribute_list(self.attribute_list_uri)

    def _upsert_collection_config(self, collection_id: str) -> None:
        if not self.attribute_list_uri:
            return
        attribute_list_id = self.attribute_list_id or self.attribute_list_uri.rsplit("/", 1)[-1]
        self.store.upsert_collection_config(
            CollectionConfig(
                collection_id=collection_id,
                customer_id=self.customer_id,
                attribute_list_id=attribute_list_id,
                attribute_list_uri=self.attribute_list_uri,
            )
        )


def clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def parse_image_urls(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.replace(",", "|").split("|") if part.strip()]


def parse_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def parse_float(value: str | None) -> float | None:
    try:
        return float(value) if clean(value) is not None else None
    except ValueError:
        return None
