from __future__ import annotations

import argparse
import json
from collections.abc import Awaitable
from typing import Any

from functions.providers import embed_many_fn
from shared.attribute_embeddings import build_attribute_embedding_inputs
from shared.models import ProductRecord, ProductStatus
from shared.store_factory import create_store


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill product attribute embeddings for one or more collections.")
    parser.add_argument("collection_ids", nargs="+", help="Collection ids to backfill.")
    parser.add_argument("--data-dir", default="data", help="Local data dir. Ignored when AWS env vars are configured.")
    parser.add_argument("--dry-run", action="store_true", help="Report eligible records without writing them.")
    parser.add_argument("--force", action="store_true", help="Recompute records that already have attribute embeddings.")
    args = parser.parse_args()

    store = create_store(args.data_dir)
    embed_many = embed_many_fn()
    updated = 0
    skipped = 0
    eligible = 0

    for collection_id in args.collection_ids:
        for record in store.records_for_collection(collection_id):
            if record.status != ProductStatus.ENRICHED:
                skipped += 1
                continue
            if record.attribute_embeddings and not args.force:
                skipped += 1
                continue
            eligible += 1
            if args.dry_run:
                continue
            attribute_embeddings = build_embeddings(embed_many, record)
            if not attribute_embeddings:
                skipped += 1
                continue
            record.attribute_embeddings = attribute_embeddings
            store.upsert_index(record)
            updated += 1

    print(
        json.dumps(
            {"dry_run": args.dry_run, "eligible": eligible, "updated": updated, "skipped": skipped},
            sort_keys=True,
        )
    )


def build_embeddings(embed_many, record: ProductRecord) -> dict[str, list[float]]:
    attributes, texts = build_attribute_embedding_inputs(record.enriched_attrs)
    if not texts:
        return {}
    vectors = resolve(embed_many(texts))
    if len(vectors) != len(texts):
        raise ValueError("embed_many() must return one embedding per attribute value.")
    return {attribute: vector for attribute, vector in zip(attributes, vectors)}


def resolve(value: Awaitable[Any] | Any) -> Any:
    if hasattr(value, "__await__"):
        import asyncio

        return asyncio.run(value)
    return value


if __name__ == "__main__":
    main()
