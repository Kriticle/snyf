from __future__ import annotations

import json
from typing import Any


def build_enrichment_prompt(metadata: dict[str, Any], attribute_schema: list[str]) -> str:
    return "\n".join(
        [
            "You enrich product catalog rows using only the requested customer-defined attributes.",
            "Return JSON only. The JSON object must contain exactly the requested attribute keys.",
            "Use null when the product metadata and images do not provide enough evidence.",
            "",
            "Requested attributes:",
            json.dumps(attribute_schema, indent=2, sort_keys=True),
            "",
            "Product metadata:",
            json.dumps(metadata, indent=2, sort_keys=True),
        ]
    )


def build_query_parser_prompt(query: str, available_attributes: list[str]) -> str:
    return "\n".join(
        [
            "Extract product-search intent. Return JSON only:",
            '{"attributes":{},"hard_filters":{}}',
            "Attribute keys must be from attrs. Include only clearly expressed attributes.",
            "hard_filters may only be category, price_min, price_max. Use them only when explicit.",
            "Never return weights.",
            f"attrs={json.dumps(available_attributes, separators=(',', ':'), sort_keys=True)}",
            f"query={query}",
        ]
    )
