from __future__ import annotations

import os

from shared.storage import JsonStore


def create_store(data_dir: str = "data"):
    required = [
        "PRODUCT_TABLE_NAME",
        "COLLECTION_TABLE_NAME",
        "CUSTOMERS_TABLE_NAME",
        "SESSIONS_TABLE_NAME",
        "API_KEYS_TABLE_NAME",
        "USAGE_TABLE_NAME",
        "PRODUCT_QUEUE_URL",
    ]
    if all(os.getenv(name) for name in required):
        from shared.aws_storage import AwsStore

        return AwsStore(
            product_table_name=os.environ["PRODUCT_TABLE_NAME"],
            collection_table_name=os.environ["COLLECTION_TABLE_NAME"],
            customers_table_name=os.environ["CUSTOMERS_TABLE_NAME"],
            sessions_table_name=os.environ["SESSIONS_TABLE_NAME"],
            api_keys_table_name=os.environ["API_KEYS_TABLE_NAME"],
            usage_table_name=os.environ["USAGE_TABLE_NAME"],
            product_queue_url=os.environ["PRODUCT_QUEUE_URL"],
            opensearch_endpoint=os.getenv("OPENSEARCH_ENDPOINT"),
            opensearch_index=os.getenv("DEFAULT_VECTOR_INDEX"),
        )
    return JsonStore(data_dir)
