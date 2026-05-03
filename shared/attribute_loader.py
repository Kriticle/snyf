from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class AttributeSpec:
    name: str
    weight: float = 1.0


@dataclass(frozen=True)
class AttributeList:
    attributes: list[AttributeSpec]

    @property
    def names(self) -> list[str]:
        return [attribute.name for attribute in self.attributes]

    @property
    def weights(self) -> dict[str, float]:
        return {attribute.name: attribute.weight for attribute in self.attributes}


def load_attribute_list(uri: str) -> AttributeList:
    """Load a customer-defined enrichment taxonomy.

    Supported payloads:
    - ["attribute_a", "attribute_b"]
    - {"attributes": ["attribute_a", "attribute_b"]}

    Local development supports plain paths and file:// URIs. Production uses
    s3://bucket/key URIs and requires boto3 in the runtime image.
    """

    parsed = urlparse(uri)
    if parsed.scheme == "s3":
        payload = _read_s3_text(parsed.netloc, parsed.path.lstrip("/"))
    elif parsed.scheme == "file":
        payload = Path(parsed.path).read_text(encoding="utf-8")
    elif parsed.scheme in {"", None}:
        payload = Path(uri).read_text(encoding="utf-8")
    else:
        raise ValueError(f"Unsupported attribute list URI scheme: {parsed.scheme}")
    return parse_attribute_list_payload(payload)


def parse_attribute_list_payload(payload: str) -> AttributeList:
    parsed = json.loads(payload)
    if isinstance(parsed, dict):
        parsed = parsed.get("attributes")
    if not isinstance(parsed, list):
        raise ValueError("Attribute list must be a JSON array or an object with an 'attributes' array.")
    attributes = [parse_attribute_spec(item) for item in parsed]
    if not attributes:
        raise ValueError("Attribute list cannot be empty.")
    names = [attribute.name for attribute in attributes]
    if len(set(names)) != len(names):
        raise ValueError("Attribute list contains duplicate names.")
    return AttributeList(attributes)


def parse_attribute_spec(item: object) -> AttributeSpec:
    if isinstance(item, str) and item.strip():
        return AttributeSpec(name=item.strip(), weight=1.0)
    if isinstance(item, dict):
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Attribute object must include a non-empty string 'name'.")
        weight = item.get("weight", 1.0)
        try:
            parsed_weight = float(weight)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Attribute '{name}' has invalid weight.") from exc
        if parsed_weight < 0:
            raise ValueError(f"Attribute '{name}' cannot have a negative weight.")
        return AttributeSpec(name=name.strip(), weight=parsed_weight)
    raise ValueError("Each attribute must be a string or an object with name/weight.")


def _read_s3_text(bucket: str, key: str) -> str:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - depends on deployment image
        raise RuntimeError("boto3 is required to load s3:// attribute lists.") from exc
    response = boto3.client("s3").get_object(Bucket=bucket, Key=key)
    return response["Body"].read().decode("utf-8")
