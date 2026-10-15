# Fashion Enrichment API

This API lets customers create an account, upload a product catalog CSV for enrichment, and search an enriched collection.

## Availability

This repository documents an MVP and does not provide a live public API. Start the services locally and set the base URLs in your shell:

```bash
export INGESTION_URL=http://localhost:8000
export SEARCH_URL=http://localhost:8001
```

Examples using `API_URL` assume that you have configured a single reverse proxy in front of both services. The repository does not define or endorse a public endpoint.

## Authentication

Catalog ingestion and search use API-key authentication.

Send a customer API key in every protected ingestion/search request:

```http
x-api-key: fep_...
```

Login sessions are separate from catalog API authorization. `POST /auth/signup` starts email verification, `POST /auth/verify-email` activates the customer and returns the initial session/API key, and `POST /auth/login` returns session objects for product UI flows. UI-only API-key management endpoints use the `refresh_token` as a bearer token.

## Errors

Errors use FastAPI's standard response shape:

```json
{
  "detail": "Missing API key."
}
```

Common statuses:

| Status | Meaning |
| --- | --- |
| `200` | Request succeeded |
| `400` | Bad signup/verification input or duplicate email |
| `401` | Missing/invalid API key or login failure |
| `402` | Plan limit exceeded |
| `404` | Collection not found or not owned by customer |
| `422` | Invalid JSON/form request shape |

## Signup

Creates a pending customer and emails a verification code. No session or API key is issued until email verification succeeds.

```http
POST /auth/signup
content-type: application/json
```

Request:

```json
{
  "email": "buyer@example.com",
  "password": "password123"
}
```

Response:

```json
{
  "customer": {
    "customer_id": "cust_...",
    "email": "buyer@example.com",
    "status": "PENDING_EMAIL_VERIFICATION",
    "plan": "FREE",
    "created_at": "2026-05-03T07:58:06.157348+00:00",
    "updated_at": "2026-05-03T07:58:06.157369+00:00"
  },
  "verification_required": true,
  "verification_expires_at": 1780387086
}
```

Example:

```bash
curl -sS -X POST "$API_URL/auth/signup" \
  -H "content-type: application/json" \
  -d '{"email":"buyer@example.com","password":"password123"}'
```

## Verify Email

Activates the customer and returns the initial login session and API key.

```http
POST /auth/verify-email
content-type: application/json
```

Request:

```json
{
  "email": "buyer@example.com",
  "code": "123456"
}
```

Link-based verification can send `token` instead of `code`.

Response:

```json
{
  "customer": {
    "customer_id": "cust_...",
    "email": "buyer@example.com",
    "status": "ACTIVE",
    "plan": "FREE"
  },
  "session": {
    "session_id": "sess_...",
    "customer_id": "cust_...",
    "status": "ACTIVE",
    "expires_at": 1780387086
  },
  "refresh_token": "...",
  "api_key": "fep_...",
  "api_key_id": "key_..."
}
```

## Login

Creates a new login session for an existing customer.

```http
POST /auth/login
content-type: application/json
```

Request:

```json
{
  "email": "buyer@example.com",
  "password": "password123"
}
```

Response:

```json
{
  "customer": {
    "customer_id": "cust_...",
    "email": "buyer@example.com",
    "status": "ACTIVE",
    "plan": "FREE"
  },
  "session": {
    "session_id": "sess_...",
    "customer_id": "cust_...",
    "status": "ACTIVE",
    "expires_at": 1780387086
  },
  "refresh_token": "..."
}
```

Example:

```bash
curl -sS -X POST "$API_URL/auth/login" \
  -H "content-type: application/json" \
  -d '{"email":"buyer@example.com","password":"password123"}'
```

## API Keys

Lists the customer's API keys. New keys include the retrievable `api_key` value. Older keys created before this field existed may have `api_key: null`.

```http
GET /auth/api-keys
authorization: Bearer <refresh_token>
```

Example:

```bash
curl -sS "$API_URL/auth/api-keys" \
  -H "authorization: Bearer $REFRESH_TOKEN"
```

Creates another API key for the logged-in customer and returns the raw key.

```http
POST /auth/api-keys
authorization: Bearer <refresh_token>
```

Example:

```bash
curl -sS -X POST "$API_URL/auth/api-keys" \
  -H "authorization: Bearer $REFRESH_TOKEN"
```

## Upload Catalog CSV

Uploads a catalog CSV, registers the collection's attribute list, and queues one enrichment message per accepted product row.

```http
POST /collections/{collection_id}/upload
x-api-key: fep_...
content-type: multipart/form-data
```

Form fields:

| Field | Required | Description |
| --- | --- | --- |
| `csv_file` | Yes | CSV file containing products |
| `attribute_list_id` | Yes | Attribute-list file name, for example `fashion-v1` or `fashion-v1.json`. The backend resolves this under the configured attribute-list bucket/prefix. |

Required CSV columns:

```text
product_id,name,image_urls
```

Optional CSV columns:

```text
category,description,price,currency,brand,_deleted
```

The upload path collection is the source of truth. Do not include `collection_id` in the CSV.

Attribute-list example:

```json
{
  "attributes": [
    { "name": "occasion", "weight": 1.0 },
    { "name": "silhouette", "weight": 1.0 },
    { "name": "fabric_feel", "weight": 1.0 },
    { "name": "trend_vibe", "weight": 1.0 }
  ]
}
```

Example:

```bash
curl -sS -X POST "$API_URL/collections/coll_ss25/upload" \
  -H "x-api-key: $API_KEY" \
  -F attribute_list_id=fashion-v1 \
  -F csv_file=@catalog.csv
```

Response:

```json
{
  "job_id": "job_coll_ss25_a1b2c3d4e5f6",
  "accepted_rows": 3,
  "rejected_rows": 0,
  "warnings": 0,
  "status": "QUEUED",
  "issues": [],
  "messages": [
    {
      "customer_id": "cust_...",
      "collection_id": "coll_ss25",
      "product_id": "prod_001",
      "image_urls": ["https://cdn.example.com/p1.jpg"],
      "metadata": {
        "name": "Linen Wide-Leg Trouser",
        "category": "bottoms",
        "description": "Relaxed cream linen trousers...",
        "price": 89.0,
        "currency": "USD",
        "brand": "Arket"
      },
      "attribute_schema": ["occasion", "silhouette", "fabric_feel"],
      "operation": "UPSERT",
      "attribute_list_uri": "s3://configured-attribute-bucket/attributes/fashion-v1.json",
      "version": 0
    }
  ]
}
```

Notes:

- Product images are not copied into our S3 bucket. The CSV supplies public image URLs.
- The worker downloads those URLs during enrichment.
- `image_urls` are stored on the product record and denormalized into the OpenSearch document so search can render product cards without a second lookup.
- The enrichment worker stores one full product embedding for OpenSearch kNN retrieval and one embedding per enriched attribute value for low-latency reranking.
- The upload path owns the collection. Do not include `collection_id` in the CSV.

## Ingestion Job Status

Polls an upload job until products are enriched and searchable.

```http
GET /ingestion-jobs/{job_id}
x-api-key: fep_...
```

Response:

```json
{
  "job_id": "job_coll_ss25_a1b2c3d4e5f6",
  "status": "READY",
  "accepted_rows": 3,
  "queued_rows": 0,
  "processing_rows": 0,
  "enriched_rows": 3,
  "failed_rows": 0,
  "deleted_rows": 0,
  "products": [
    {
      "collection_id": "coll_ss25",
      "product_id": "prod_001",
      "name": "Linen Wide-Leg Trouser",
      "status": "ENRICHED",
      "updated_at": "2026-05-10T00:00:00+00:00"
    }
  ]
}
```

Statuses:

- `QUEUED`: accepted but not picked up by the worker yet.
- `PROCESSING`: at least one product is being enriched or some products have completed.
- `READY`: every accepted product was enriched or deleted successfully.
- `COMPLETED_WITH_ERRORS`: processing ended, but one or more products failed enrichment.

Failed rows:

```http
GET /ingestion-jobs/{job_id}/errors
x-api-key: fep_...
```

## Search

Searches a collection and returns ranked product-card results.

```http
POST /search
x-api-key: fep_...
content-type: application/json
```

Request:

```json
{
  "collection_id": "coll_ss25",
  "query": "flowy beach vacation look",
  "top_k": 10
}
```

Parameters:

| Field | Required | Description |
| --- | --- | --- |
| `collection_id` | Yes | Collection to search. Must belong to the API-key customer. |
| `query` | Yes | Natural-language search query. |
| `top_k` | No | Number of results. Defaults to `20`; maximum is `100`. |

Response:

```json
{
  "metrics": {
    "api_total_ms": 823.41,
    "api_steps_ms": {
      "authenticate_api_key": 12.3,
      "validate_top_k": 0.01,
      "reserve_usage": 8.11,
      "load_collection": 6.44,
      "search_service": 790.02
    },
    "search": {
      "total_ms": 790.0,
      "steps_ms": {
        "load_attribute_list": 35.21,
        "embed_query": 96.31,
        "extract_query_attributes": 488.17,
        "vector_retrieve_documents": 42.89,
        "merge_weights": 0.01,
        "apply_hard_filters": 0.02,
        "embed_query_rerank_attributes": 126.48,
        "rerank_candidates": 0.42,
        "sort_results": 0.01
      },
      "document_count": 4,
      "filtered_count": 4,
      "candidate_count": 4,
      "result_count": 3,
      "embedding_calls": 2,
      "embedding_texts": 4,
      "embedding_cache_hits": 4
    }
  },
  "results": [
    {
      "product_id": "prod_003",
      "name": "Printed Beach Dress",
      "image_urls": ["https://cdn.example.com/p3.jpg"],
      "score": 0.620096,
      "feature_scores": {
        "_retrieval_semantic": 0.620096,
        "_weight_total": 0.0
      },
      "enriched_attrs": {
        "occasion": null,
        "silhouette": null,
        "fabric_feel": null,
        "trend_vibe": null,
        "print_energy": "dress dresses bold red print midi dress made for"
      },
      "category": "dresses",
      "price": 129.0,
      "currency": "USD",
      "brand": "Arket"
    }
  ]
}
```

Metrics are returned on every search response and are also written to the search API logs. Use them to identify whether latency is coming from auth/usage checks, collection lookup, attribute-list loading, query embedding, query-attribute LLM parsing, OpenSearch kNN retrieval, query-attribute embedding, or reranking.

Example:

```bash
curl -sS -X POST "$API_URL/search" \
  -H "content-type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d '{
    "collection_id": "coll_ss25",
    "query": "flowy beach vacation look",
    "top_k": 10
  }'
```

Search behavior:

- The API accepts only `collection_id`, `query`, and `top_k`.
- Search loads the collection's registered attribute list.
- The query parser LLM extracts query attributes and optional hard filters.
- Only `category`, `price_min`, and `price_max` can be hard filters.
- If no hard filter is extracted, nothing is excluded based on category or price.
- Attribute weights come from the attribute list.
- Query embeddings, extracted query attributes, and query-attribute embeddings use an in-memory cache plus a shared DynamoDB persistent cache.
- OpenSearch performs collection-scoped kNN retrieval over the stored product embedding.
- Product-side attribute embeddings are precomputed during ingestion and stored in the OpenSearch product document.
- Results are reranked by weighted attribute similarity; if no query attributes are extracted, the result falls back to retrieval semantic score.

## Public Website Demo Search

The marketing-site live demo must not expose a customer API key in browser JavaScript. It uses public demo-only routes that are locked server-side to the configured demo collection.

```http
POST /search/demo
content-type: application/json
```

```http
POST /search/demo/keyword
content-type: application/json
```

Request:

```json
{
  "query": "linen beach dress"
}
```

Notes:

- These endpoints do not accept `collection_id`, `top_k`, or `x-api-key`.
- The backend uses `DEMO_COLLECTION_ID` and a fixed server-side `DEMO_TOP_K`.
- Public demo requests are still rate limited per client and return `429` when exceeded.
- Customer products must use protected `/search` and `/search/keyword`.

## Plan Limits

Plan limits are enforced for protected ingestion and search requests.

Current defaults:

| Plan | Search requests/month | Ingestion rows/month | Active API keys |
| --- | ---: | ---: | ---: |
| `FREE` | 100 | 1,000 | 1 |
| `PRO` | 10,000 | 100,000 | 5 |
| `ENTERPRISE` | 1,000,000 | 10,000,000 | 25 |

Ingestion row limits count product rows only. The CSV header is not counted. API keys can be regenerated, but each plan caps the number of active keys; when the cap is reached, the oldest active key is deactivated.

Limit error example:

```json
{
  "detail": "search_requests_per_month exceeded for FREE plan. Please upgrade your plan."
}
```

## AWS Architecture Notes

- The Terraform reference architecture uses API Gateway HTTP API for routing.
- Customer API auth is app-level via `x-api-key`.
- Product images remain hosted by the customer/CDN; we store and return their URLs.
- The AWS storage adapter reads attribute-list files from S3.
- The OpenAI provider expects `OPENAI_API_KEY` to be supplied through a secret manager in any deployment.
- Search uses OpenSearch kNN vector retrieval and a DynamoDB `SearchCache` table for persistent query/embedding cache entries with TTL.
- `llm_provider=fake` supports deterministic local and infrastructure-only smoke tests.
