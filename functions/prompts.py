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
            "Extract searchable intent from a product search query.",
            "Return JSON only with this shape:",
            '{"attributes": {...}, "hard_filters": {...}}',
            "",
            "Rules:",
            "- Attribute keys must come only from the provided customer attribute list.",
            "- Include an attribute only when the query clearly expresses it.",
            "- hard_filters may include only category, price_min, or price_max.",
            "- Do not infer category unless the query explicitly asks for a category.",
            "- Do not infer price unless the query explicitly contains a price constraint.",
            "- Do not return weights. Weights are configured in the attribute list.",
            "",
            "Customer attribute list:",
            json.dumps(available_attributes, indent=2, sort_keys=True),
            "",
            "Query:",
            query,
        ]
    )

